import json

import pytest
from sqlmodel import Session, select

from chatbot_manager.bots.versions import (
    PublishBlocked,
    PublishService,
    RuleInput,
    clone_live_to_draft,
    replace_draft_rules,
)
from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.knowledge.client import FakeKnowledgeServiceClient, KnowledgeHealth
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotTestCase,
    ChannelConnection,
    KnowledgeService,
)
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest
from chatbot_manager.testing.regression import RegressionService


class CountingKnowledgeClient(FakeKnowledgeServiceClient):
    def __init__(self) -> None:
        super().__init__(health_result=KnowledgeHealth("healthy", 1))
        self.connection_tests = 0

    async def test_connection(self) -> KnowledgeHealth:
        self.connection_tests += 1
        return await super().test_connection()


def _bot_and_draft(session: Session) -> tuple[Bot, BotConfigVersion]:
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    assert bot.id is not None
    draft = clone_live_to_draft(session, bot.id, "admin@example.local")
    assert draft.id is not None
    return bot, draft


def _case(
    session: Session,
    bot_id: int,
    *,
    expected_decision: str,
    input_message: str = "hello",
) -> BotTestCase:
    case = BotTestCase(
        bot_id=bot_id,
        name=f"expect {expected_decision}",
        input_message=input_message,
        expected_behavior_json=json.dumps({"decision_type": expected_decision}),
        enabled=True,
        created_by="admin@example.local",
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def _ready_channel(session: Session, bot_id: int) -> None:
    credential = store_credential(
        session,
        "channel:line",
        {"channel_secret": "secret", "channel_access_token": "token"},
    )
    assert credential.id is not None
    session.add(
        ChannelConnection(
            bot_id=bot_id,
            provider="line",
            display_name="LINE",
            webhook_key=f"publish-line-{bot_id}",
            credential_id=credential.id,
            enabled=True,
            status="ready",
        )
    )
    session.commit()


def _publish_service(
    session: Session,
    knowledge: CountingKnowledgeClient | None = None,
) -> PublishService:
    client = knowledge or CountingKnowledgeClient()
    runtime_factory = lambda active_session: BotRuntime(  # noqa: E731
        active_session,
        knowledge_client_factory=lambda _session, _service_id: client,
    )
    regression = RegressionService(session, runtime_factory=runtime_factory)
    return PublishService(
        session,
        regression_service=regression,
        knowledge_client_factory=lambda _session, _service_id: client,
    )


@pytest.mark.asyncio
async def test_regression_fail_blocks_publish(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _bot_and_draft(session)
        assert bot.id is not None
        old_live = bot.live_config_version_id
        replace_draft_rules(
            session,
            bot.id,
            [RuleInput(name="hello", pattern="hello", action="RESPOND", reply_text="hello")],
        )
        _case(session, bot.id, expected_decision="escalation")

        with pytest.raises(PublishBlocked, match="regression_failed"):
            await _publish_service(session).publish(bot.id, "admin@example.local", acknowledge_warnings=True)

        session.refresh(bot)
        session.refresh(draft)
        assert bot.live_config_version_id == old_live
        assert bot.draft_config_version_id == draft.id
        assert draft.status == "draft"


@pytest.mark.asyncio
async def test_warning_requires_acknowledgement(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _bot_and_draft(session)
        assert bot.id is not None

        with pytest.raises(PublishBlocked, match="warnings_require_acknowledgement"):
            await _publish_service(session).publish(bot.id, "admin@example.local")

        session.refresh(bot)
        assert bot.draft_config_version_id == draft.id

        published = await _publish_service(session).publish(
            bot.id,
            "admin@example.local",
            acknowledge_warnings=True,
        )
        assert published.id == draft.id
        assert published.status == "published"


@pytest.mark.asyncio
async def test_publish_switches_live_pointer_freezes_version_and_fresh_checks_knowledge(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _bot_and_draft(session)
        assert bot.id is not None
        old_live_id = bot.live_config_version_id

        service = KnowledgeService(
            name="Publish RAG",
            api_base_url="https://publish-rag.example.test",
            enabled=True,
            health_status="healthy",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        assert service.id is not None
        draft.knowledge_service_id = service.id
        session.add(draft)
        session.commit()

        replace_draft_rules(
            session,
            bot.id,
            [RuleInput(name="hello", pattern="hello", action="RESPOND", reply_text="new")],
        )
        _case(session, bot.id, expected_decision="rule")
        _ready_channel(session, bot.id)
        knowledge = CountingKnowledgeClient()

        published = await _publish_service(session, knowledge).publish(
            bot.id,
            "admin@example.local",
        )

        session.refresh(bot)
        old_live = session.get(BotConfigVersion, old_live_id)
        assert knowledge.connection_tests == 1
        assert published.id == draft.id
        assert published.status == "published"
        assert published.published_at is not None
        assert bot.live_config_version_id == draft.id
        assert bot.draft_config_version_id is None
        assert old_live is not None
        assert old_live.status == "published"


@pytest.mark.asyncio
async def test_publish_preserves_runtime_snapshot_for_inflight_version(client) -> None:
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        old_live = session.get(BotConfigVersion, bot.live_config_version_id)
        assert old_live is not None and old_live.id is not None
        session.add(
            BotConfigRule(
                config_version_id=old_live.id,
                name="old",
                priority=1,
                pattern="hello",
                action="RESPOND",
                reply_text="old version",
            )
        )
        session.commit()

        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        replace_draft_rules(
            session,
            bot.id,
            [RuleInput(name="new", priority=1, pattern="hello", action="RESPOND", reply_text="new version")],
        )
        captured_version_id = old_live.id

        published = await _publish_service(session).publish(
            bot.id,
            "admin@example.local",
            acknowledge_warnings=True,
        )
        assert published.id == draft.id

        runtime = BotRuntime(session)
        captured = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=0,
                message_id=0,
                text="hello",
                provider="test",
                external_user_id="snapshot",
                config_version_id=captured_version_id,
                test_mode=True,
            )
        )
        current = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=0,
                message_id=0,
                text="hello",
                provider="test",
                external_user_id="snapshot",
                test_mode=True,
            )
        )

        assert captured.reply_text == "old version"
        assert captured.config_version_id == captured_version_id
        assert current.reply_text == "new version"
        assert current.config_version_id == draft.id
