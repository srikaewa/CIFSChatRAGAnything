import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.bots.versions import (
    clone_live_to_draft,
    publish_draft,
    replace_draft_rules,
    RuleInput,
)
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    BotConfigVersion,
    BotDecision,
    BotTestCase,
    BotTestResult,
    BotTestRun,
    ChannelConnection,
    Conversation,
    ConversationMessage,
)
from chatbot_manager.runtime.delivery import DeliveryService
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest, RuntimeResult


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf_from_page(html: str) -> str:
    marker = 'name="csrf_token" value="'
    assert marker in html
    return html.split(marker, 1)[1].split('"', 1)[0]


def default_bot(session: Session) -> Bot:
    return session.exec(select(Bot).where(Bot.name == "Default Bot")).one()


def counts(session: Session) -> tuple[int, int, int]:
    return (
        len(session.exec(select(Conversation)).all()),
        len(session.exec(select(ConversationMessage)).all()),
        len(session.exec(select(BotDecision)).all()),
    )


def test_interactive_test_uses_draft_and_no_provider_delivery(
    client: TestClient,
    monkeypatch,
) -> None:
    login(client)

    async def fail_if_delivered(*_args, **_kwargs):
        raise AssertionError("interactive Test Center must not deliver to a provider")

    monkeypatch.setattr(DeliveryService, "send", fail_if_delivered)

    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        replace_draft_rules(
            session,
            bot.id,
            [
                RuleInput(
                    name="draft hello",
                    priority=1,
                    pattern="hello",
                    action="RESPOND",
                    reply_text="draft-only reply",
                )
            ],
        )
        bot_id = bot.id
        draft_id = draft.id
        before = counts(session)

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/test/interactive",
        data={"csrf_token": csrf, "input_message": "hello"},
    )

    assert response.status_code == 200
    assert "draft-only reply" in response.text
    assert "Decision Trace" in response.text
    assert f"Config {draft_id}" in response.text

    with Session(get_engine()) as session:
        assert counts(session) == before


def test_saved_case_can_be_created_and_run(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        replace_draft_rules(
            session,
            bot.id,
            [
                RuleInput(
                    name="documents",
                    priority=1,
                    pattern="documents",
                    action="RESPOND",
                    reply_text="Required documents are listed here.",
                )
            ],
        )
        bot_id = bot.id
        draft_id = draft.id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    create = client.post(
        f"/bots/{bot_id}/test/cases",
        data={
            "csrf_token": csrf,
            "name": "documents rule",
            "input_message": "Which documents do I need?",
            "expected_behavior_json": json.dumps(
                {
                    "decision_type": "rule",
                    "must_call_rag": False,
                    "must_have_references": False,
                    "must_escalate": False,
                    "must_fallback": False,
                    "required_terms": ["documents"],
                }
            ),
            "tags_json": '["core"]',
        },
        follow_redirects=False,
    )
    assert create.status_code == 303

    run = client.post(
        f"/bots/{bot_id}/test/run",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert run.status_code == 303

    with Session(get_engine()) as session:
        cases = session.exec(select(BotTestCase).where(BotTestCase.bot_id == bot_id)).all()
        assert len(cases) == 1
        assert cases[0].enabled is True
        runs = session.exec(select(BotTestRun).where(BotTestRun.bot_id == bot_id)).all()
        assert len(runs) == 1
        assert runs[0].config_version_id == draft_id
        assert runs[0].status == "pass"
        results = session.exec(
            select(BotTestResult).where(BotTestResult.test_run_id == runs[0].id)
        ).all()
        assert len(results) == 1
        assert results[0].outcome == "pass"
        assert results[0].config_version_id == draft_id


def test_compare_runs_same_case_against_live_and_draft(
    client: TestClient,
    monkeypatch,
) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        live_id = bot.live_config_version_id
        assert live_id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        bot_id = bot.id
        draft_id = draft.id

    captured: list[tuple[str, int | None, bool]] = []

    async def fake_run(self, request: RuntimeRequest) -> RuntimeResult:
        captured.append((request.text, request.config_version_id, request.test_mode))
        label = "live response" if request.config_version_id == live_id else "draft response"
        assert request.config_version_id is not None
        return RuntimeResult(
            decision_type="rule",
            reply_text=label,
            escalate=False,
            error_code="",
            reference_count=0,
            references=[],
            config_version_id=request.config_version_id,
            knowledge_service_id=None,
            total_latency_ms=1,
        )

    monkeypatch.setattr(BotRuntime, "run", fake_run)

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/test/compare",
        data={"csrf_token": csrf, "input_message": "same question"},
    )

    assert response.status_code == 200
    assert captured == [
        ("same question", live_id, True),
        ("same question", draft_id, True),
    ]
    assert "live response" in response.text
    assert "draft response" in response.text
    assert f"Config {live_id}" in response.text
    assert f"Config {draft_id}" in response.text


def test_publish_fail_is_rendered_as_blocked(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        old_live_id = bot.live_config_version_id
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        replace_draft_rules(
            session,
            bot.id,
            [
                RuleInput(
                    name="hello",
                    priority=1,
                    pattern="hello",
                    action="RESPOND",
                    reply_text="hello",
                )
            ],
        )
        session.add(
            BotTestCase(
                bot_id=bot.id,
                name="must escalate",
                input_message="hello",
                expected_behavior_json=json.dumps({"decision_type": "escalation"}),
                enabled=True,
                created_by="admin@example.local",
            )
        )
        session.commit()
        bot_id = bot.id
        draft_id = draft.id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/test/publish",
        data={"csrf_token": csrf, "acknowledge_warnings": "on"},
    )

    assert response.status_code == 200
    assert "Publish blocked" in response.text
    assert "regression_failed" in response.text

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.live_config_version_id == old_live_id
        assert bot.draft_config_version_id == draft_id
        draft = session.get(BotConfigVersion, draft_id)
        assert draft is not None
        assert draft.status == "draft"


def test_restore_version_creates_new_draft(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        first_live_id = bot.live_config_version_id
        assert first_live_id is not None

        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        draft.system_prompt = "Published v2"
        session.add(draft)
        session.commit()
        published = publish_draft(session, bot.id, "admin@example.local")
        assert published.id is not None
        second_live_id = published.id
        bot_id = bot.id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/versions/{first_live_id}/restore",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.live_config_version_id == second_live_id
        assert bot.draft_config_version_id is not None
        restored = session.get(BotConfigVersion, bot.draft_config_version_id)
        assert restored is not None
        assert restored.status == "draft"
        assert restored.version_number == 3
        assert restored.system_prompt != "Published v2"


def test_conversation_message_can_seed_test_case(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="LINE",
            webhook_key="seed-test-case",
            enabled=False,
            status="not_configured",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        assert connection.id is not None

        conversation = Conversation(
            bot_id=bot.id,
            channel_connection_id=connection.id,
            external_user_id="seed-user",
            status="bot_active",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        assert conversation.id is not None

        message = ConversationMessage(
            conversation_id=conversation.id,
            sender_type="user",
            content="Please explain the admission deadline.",
            external_message_id="seed-user-message",
        )
        session.add(message)
        session.commit()
        session.refresh(message)
        assert message.id is not None
        bot_id = bot.id
        conversation_id = conversation.id
        message_id = message.id

    detail = client.get(f"/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert (
        f'action="/conversations/{conversation_id}/messages/{message_id}/add-test-case"'
        in detail.text
    )
    csrf = csrf_from_page(detail.text)
    response = client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/add-test-case",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/bots/{bot_id}/test")

    with Session(get_engine()) as session:
        cases = session.exec(select(BotTestCase).where(BotTestCase.bot_id == bot_id)).all()
        assert len(cases) == 1
        case = cases[0]
        assert case.input_message == "Please explain the admission deadline."
        assert case.enabled is False
        assert json.loads(case.expected_behavior_json) == {}


def test_publish_success_preserves_paused_lifecycle(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = default_bot(session)
        assert bot.id is not None
        old_live_id = bot.live_config_version_id
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None
        bot.lifecycle_status = "paused"
        session.add(bot)
        session.commit()
        bot_id = bot.id
        draft_id = draft.id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/test/publish",
        data={"csrf_token": csrf, "acknowledge_warnings": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.lifecycle_status == "paused"
        assert bot.live_config_version_id == draft_id
        assert bot.live_config_version_id != old_live_id
        assert bot.draft_config_version_id is None
