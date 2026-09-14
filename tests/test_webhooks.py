import base64
import hashlib
import hmac

import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"



def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    assert marker in page.text
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def test_messenger_verification(client: TestClient) -> None:
    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "challenge-1"},
    )

    assert response.status_code == 503


def test_messenger_verification_uses_saved_channel_verify_token(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                credential_json='{"verify_token": "db-verify", "page_access_token": "page-token", "app_secret": "app-secret"}',
            )
        )
        session.commit()

    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "db-verify", "hub.challenge": "challenge-1"},
    )

    assert response.status_code == 200
    assert response.text == "challenge-1"


def test_line_webhook_rejects_bad_signature(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
    response = client.post("/webhooks/line", content=b'{"events":[]}', headers={"x-line-signature": "bad"})

    assert response.status_code == 401


def test_line_webhook_accepts_empty_events(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
    body = b'{"events":[]}'

    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "db-secret")})

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


def test_messenger_webhook_accepts_empty_entries(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                credential_json='{"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"}',
            )
        )
        session.commit()
    body = b'{"entry":[]}'
    response = client.post(
        "/webhooks/messenger",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": messenger_signature(body, "app-secret"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


@respx.mock
def test_line_webhook_processes_text_rule_and_logs(client: TestClient) -> None:
    csrf_token = login(client)
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
        _add_default_runtime_rule(session)
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    body = (
        b'{"events":[{"type":"message","replyToken":"reply-token","source":{"userId":"user-1"},'
        b'"message":{"id":"legacy-line-message","type":"text","text":"price please"}}]}'
    )

    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "db-secret")})

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content

    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text


@respx.mock
def test_line_webhook_uses_saved_channel_credentials(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
        _add_default_runtime_rule(session)
    csrf_token = login(client)
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    body = (
        b'{"events":[{"type":"message","replyToken":"reply-token","source":{"userId":"user-1"},'
        b'"message":{"id":"legacy-line-message","type":"text","text":"price please"}}]}'
    )

    response = client.post(
        "/webhooks/line",
        content=body,
        headers={"x-line-signature": line_signature(body, "db-secret")},
    )

    assert response.status_code == 200
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer db-token"

# Phase 3 BotRuntime integration -------------------------------------------------
import json
from sqlmodel import select

from chatbot_manager.credentials import store_credential
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotDecision,
    ChannelConnection,
    Conversation,
    ConversationMessage,
)


def _add_default_runtime_rule(session: Session, reply: str = "Price is 100.") -> None:
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    config = session.get(BotConfigVersion, bot.live_config_version_id)
    assert config is not None
    session.add(
        BotConfigRule(
            config_version_id=config.id,
            name="price",
            priority=1,
            match_type="contains",
            pattern="price",
            action="RESPOND",
            reply_text=reply,
        )
    )
    session.commit()


def _line_body(message_id: str, text: str = "price please", user_id: str = "user-1") -> bytes:
    return json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "timestamp": 1700000000000,
                    "replyToken": f"reply-{message_id}",
                    "source": {"userId": user_id},
                    "message": {"id": message_id, "type": "text", "text": text},
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def _make_runtime_bot(session: Session, name: str, reply: str = "Runtime reply") -> Bot:
    bot = Bot(name=name, lifecycle_status="active")
    session.add(bot)
    session.commit()
    session.refresh(bot)
    version = BotConfigVersion(
        bot_id=bot.id,
        version_number=1,
        status="published",
        system_prompt="Use registered knowledge.",
        fallback_reply="Fallback",
        created_by="test",
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    session.add(
        BotConfigRule(
            config_version_id=version.id,
            name="price",
            priority=1,
            match_type="contains",
            pattern="price",
            action="RESPOND",
            reply_text=reply,
        )
    )
    bot.live_config_version_id = version.id
    session.add(bot)
    session.commit()
    session.refresh(bot)
    return bot


def _add_line_connection(
    session: Session,
    bot_id: int,
    *,
    webhook_key: str,
    secret: str,
    token: str,
    enabled: bool = True,
) -> ChannelConnection:
    credential = store_credential(
        session,
        "channel:line",
        {"channel_secret": secret, "channel_access_token": token},
    )
    connection = ChannelConnection(
        bot_id=bot_id,
        provider="line",
        display_name="LINE",
        webhook_key=webhook_key,
        credential_id=credential.id,
        enabled=enabled,
        status="ready" if enabled else "disabled",
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


@respx.mock
def test_keyed_line_webhook_resolves_correct_bot(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = _make_runtime_bot(session, "Second Bot", reply="Second bot reply")
        connection = _add_line_connection(
            session,
            bot.id,
            webhook_key="second-line-key",
            secret="second-secret",
            token="second-token",
        )
        bot_id = bot.id
        connection_id = connection.id

    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = _line_body("m-keyed")
    response = client.post(
        "/webhooks/line/second-line-key",
        content=body,
        headers={"x-line-signature": line_signature(body, "second-secret")},
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.call_count == 1
    assert b"Second bot reply" in route.calls[0].request.content
    with Session(get_engine()) as session:
        conversation = session.exec(select(Conversation)).one()
        assert conversation.bot_id == bot_id
        assert conversation.channel_connection_id == connection_id


def test_bad_keyed_signature_creates_no_conversation(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = _make_runtime_bot(session, "Signed Bot")
        _add_line_connection(
            session,
            bot.id,
            webhook_key="signed-line-key",
            secret="correct-secret",
            token="token",
        )

    body = _line_body("m-bad-signature")
    response = client.post(
        "/webhooks/line/signed-line-key",
        content=body,
        headers={"x-line-signature": "bad"},
    )

    assert response.status_code == 401
    with Session(get_engine()) as session:
        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []


@respx.mock
def test_duplicate_external_message_runs_runtime_and_delivery_once(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = _make_runtime_bot(session, "Idempotent Bot", reply="Only once")
        _add_line_connection(
            session,
            bot.id,
            webhook_key="idempotent-line-key",
            secret="idempotent-secret",
            token="idempotent-token",
        )

    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = _line_body("m-duplicate")
    headers = {"x-line-signature": line_signature(body, "idempotent-secret")}

    first = client.post("/webhooks/line/idempotent-line-key", content=body, headers=headers)
    second = client.post("/webhooks/line/idempotent-line-key", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert route.call_count == 1
    with Session(get_engine()) as session:
        user_messages = session.exec(
            select(ConversationMessage).where(ConversationMessage.sender_type == "user")
        ).all()
        bot_messages = session.exec(
            select(ConversationMessage).where(ConversationMessage.sender_type == "bot")
        ).all()
        decisions = session.exec(select(BotDecision)).all()
        assert len(user_messages) == 1
        assert len(bot_messages) == 1
        assert len(decisions) == 1


@respx.mock
def test_human_active_inbound_persists_without_bot_reply(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = _make_runtime_bot(session, "Human Bot", reply="Should stay silent")
        connection = _add_line_connection(
            session,
            bot.id,
            webhook_key="human-line-key",
            secret="human-secret",
            token="human-token",
        )
        conversation = Conversation(
            bot_id=bot.id,
            channel_connection_id=connection.id,
            external_user_id="user-human",
            status="human_active",
            assigned_operator_id=10,
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = _line_body("m-human", user_id="user-human")
    response = client.post(
        "/webhooks/line/human-line-key",
        content=body,
        headers={"x-line-signature": line_signature(body, "human-secret")},
    )

    assert response.status_code == 200
    assert route.call_count == 0
    with Session(get_engine()) as session:
        messages = session.exec(
            select(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)
        ).all()
        assert [(message.sender_type, message.content) for message in messages] == [
            ("user", "price please")
        ]
        assert session.exec(select(BotDecision)).all() == []


@respx.mock
def test_escalating_runtime_result_moves_conversation_to_needs_human(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = _make_runtime_bot(session, "Escalation Bot")
        config = session.get(BotConfigVersion, bot.live_config_version_id)
        rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == config.id)
        ).all()
        for rule in rules:
            session.delete(rule)
        session.add(
            BotConfigRule(
                config_version_id=config.id,
                name="human",
                priority=1,
                match_type="contains",
                pattern="human",
                action="ESCALATE",
                reply_text="Internal",
                escalate_message="A human will help you.",
            )
        )
        session.commit()
        _add_line_connection(
            session,
            bot.id,
            webhook_key="escalate-line-key",
            secret="escalate-secret",
            token="escalate-token",
        )

    respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = _line_body("m-escalate", text="I need a human")
    response = client.post(
        "/webhooks/line/escalate-line-key",
        content=body,
        headers={"x-line-signature": line_signature(body, "escalate-secret")},
    )

    assert response.status_code == 200
    with Session(get_engine()) as session:
        conversation = session.exec(select(Conversation)).one()
        assert conversation.status == "needs_human"
        assert conversation.handoff_reason == "runtime_escalation"


def test_legacy_alias_rejects_multiple_enabled_connections(client: TestClient) -> None:
    with Session(get_engine()) as session:
        first = _make_runtime_bot(session, "Alias Bot 1")
        second = _make_runtime_bot(session, "Alias Bot 2")
        _add_line_connection(
            session,
            first.id,
            webhook_key="alias-one",
            secret="one-secret",
            token="one-token",
        )
        _add_line_connection(
            session,
            second.id,
            webhook_key="alias-two",
            secret="two-secret",
            token="two-token",
        )

    response = client.post("/webhooks/line", content=b'{"events":[]}')

    assert response.status_code == 409
    assert response.json()["detail"] == "ambiguous_legacy_webhook"

