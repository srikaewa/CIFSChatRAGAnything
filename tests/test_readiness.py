from sqlmodel import Session, select

from chatbot_manager.bots.versions import clone_live_to_draft
from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotTestCase,
    ChannelConnection,
    KnowledgeService,
)
from chatbot_manager.testing.readiness import ReadinessService


def _draft(session: Session) -> tuple[Bot, BotConfigVersion]:
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    assert bot.id is not None
    draft = clone_live_to_draft(session, bot.id, "admin@example.local")
    assert draft.id is not None
    return bot, draft


def _check(result, key: str):
    return next(check for check in result.checks if check.key == key)


def _healthy_service(session: Session) -> KnowledgeService:
    service = KnowledgeService(
        name="Healthy RAG",
        api_base_url="https://rag.example.test",
        enabled=True,
        health_status="healthy",
    )
    session.add(service)
    session.commit()
    session.refresh(service)
    assert service.id is not None
    return service


def _enable_saved_case(session: Session, bot_id: int) -> None:
    session.add(
        BotTestCase(
            bot_id=bot_id,
            name="smoke",
            input_message="hello",
            expected_behavior_json="{}",
            enabled=True,
            created_by="admin@example.local",
        )
    )
    session.commit()


def _ready_line_connection(session: Session, bot_id: int) -> None:
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
            webhook_key=f"line-{bot_id}",
            credential_id=credential.id,
            enabled=True,
            status="ready",
        )
    )
    session.commit()


def test_readiness_fails_without_knowledge_service_for_rag_bot(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _draft(session)
        session.add(
            BotConfigRule(
                config_version_id=draft.id,
                name="rag",
                enabled=True,
                priority=1,
                pattern="documents",
                action="CONTINUE_TO_RAG",
            )
        )
        session.commit()

        result = ReadinessService(session).evaluate(bot.id, draft.id)

        assert _check(result, "knowledge_service").status == "fail"
        assert result.can_publish is False


def test_readiness_fails_when_selected_knowledge_service_unhealthy(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _draft(session)
        service = KnowledgeService(
            name="Unavailable RAG",
            api_base_url="https://rag-unavailable.example.test",
            enabled=True,
            health_status="unavailable",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        draft.knowledge_service_id = service.id
        session.add(draft)
        session.commit()

        result = ReadinessService(session).evaluate(bot.id, draft.id)

        assert _check(result, "knowledge_service").status == "fail"
        assert result.can_publish is False


def test_readiness_warns_when_bot_has_no_enabled_channel(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _draft(session)
        service = _healthy_service(session)
        draft.knowledge_service_id = service.id
        session.add(draft)
        session.commit()
        _enable_saved_case(session, bot.id)

        result = ReadinessService(session).evaluate(bot.id, draft.id)

        assert _check(result, "channel_readiness").status == "warning"
        assert all(check.status != "fail" for check in result.checks)
        assert result.can_publish is True


def test_readiness_fails_for_enabled_incomplete_channel(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _draft(session)
        service = _healthy_service(session)
        draft.knowledge_service_id = service.id
        session.add(draft)
        session.add(
            ChannelConnection(
                bot_id=bot.id,
                provider="line",
                display_name="Broken LINE",
                webhook_key="broken-line",
                enabled=True,
                status="ready",
                credential_id=None,
            )
        )
        session.commit()
        _enable_saved_case(session, bot.id)

        result = ReadinessService(session).evaluate(bot.id, draft.id)

        assert _check(result, "channel_readiness").status == "fail"
        assert result.can_publish is False


def test_readiness_passes_complete_active_configuration(client) -> None:
    with Session(get_engine()) as session:
        bot, draft = _draft(session)
        service = _healthy_service(session)
        draft.knowledge_service_id = service.id
        session.add(draft)
        session.commit()
        _ready_line_connection(session, bot.id)
        _enable_saved_case(session, bot.id)

        result = ReadinessService(session).evaluate(bot.id, draft.id)

        assert {check.key for check in result.checks} == {
            "bot_profile",
            "behavior",
            "knowledge_service",
            "channel_readiness",
            "regression_presence",
        }
        assert all(check.status == "pass" for check in result.checks)
        assert result.can_publish is True
