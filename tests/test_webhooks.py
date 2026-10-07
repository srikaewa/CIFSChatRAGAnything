import base64
import hashlib
import hmac
import json

import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotDecision,
    ChannelConnection,
    Conversation,
    ConversationMessage,
)


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_bot(session: Session, name: str, reply: str = "Runtime reply") -> Bot:
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


def add_connection(
    session: Session,
    bot_id: int,
    provider: str,
    webhook_key: str,
    credentials: dict[str, str],
    *,
    enabled: bool = True,
) -> ChannelConnection:
    credential = store_credential(
        session,
        f"channel:{provider}",
        credentials,
    )
    connection = ChannelConnection(
        bot_id=bot_id,
        provider=provider,
        display_name=provider.title(),
        webhook_key=webhook_key,
        credential_id=credential.id,
        enabled=enabled,
        status="ready" if enabled else "disabled",
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


def line_body(message_id: str, text: str = "price please", user_id: str = "user-1") -> bytes:
    return json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "timestamp": 1700000000000,
                    "replyToken": f"reply-{message_id}",
                    "source": {"userId": user_id},
                    "message": {
                        "id": message_id,
                        "type": "text",
                        "text": text,
                    },
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def test_keyed_messenger_verification_uses_connection_credential(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Messenger Verify")
        add_connection(
            session,
            bot.id,
            "messenger",
            "messenger-key",
            {
                "verify_token": "verify",
                "page_access_token": "page-token",
                "app_secret": "app-secret",
            },
        )

    response = client.get(
        "/webhooks/messenger/messenger-key",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify",
            "hub.challenge": "challenge-1",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge-1"


def test_keyed_telegram_rejects_bad_secret(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Telegram Signed")
        add_connection(
            session,
            bot.id,
            "telegram",
            "telegram-key",
            {"bot_token": "token", "webhook_secret": "correct-secret"},
        )

    response = client.post(
        "/webhooks/telegram/telegram-key",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 403


def test_keyed_messenger_rejects_malformed_json_after_auth(
    client: TestClient,
) -> None:
    body = b'{"broken":'
    with Session(get_engine()) as session:
        bot = make_bot(session, "Messenger JSON")
        add_connection(
            session,
            bot.id,
            "messenger",
            "messenger-json-key",
            {
                "verify_token": "verify",
                "page_access_token": "page-token",
                "app_secret": "app-secret",
            },
        )

    response = client.post(
        "/webhooks/messenger/messenger-json-key",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": messenger_signature(body, "app-secret"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed JSON payload"


@respx.mock
def test_keyed_line_webhook_resolves_correct_bot(client: TestClient) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Second Bot", reply="Second bot reply")
        connection = add_connection(
            session,
            bot.id,
            "line",
            "second-line-key",
            {
                "channel_secret": "second-secret",
                "channel_access_token": "second-token",
            },
        )
        bot_id = bot.id
        connection_id = connection.id

    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = line_body("m-keyed")
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
        bot = make_bot(session, "Signed Bot")
        add_connection(
            session,
            bot.id,
            "line",
            "signed-line-key",
            {
                "channel_secret": "correct-secret",
                "channel_access_token": "token",
            },
        )

    body = line_body("m-bad-signature")
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
def test_duplicate_external_message_runs_runtime_and_delivery_once(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Idempotent Bot", reply="Only once")
        add_connection(
            session,
            bot.id,
            "line",
            "idempotent-line-key",
            {
                "channel_secret": "idempotent-secret",
                "channel_access_token": "idempotent-token",
            },
        )

    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = line_body("m-duplicate")
    headers = {"x-line-signature": line_signature(body, "idempotent-secret")}

    first = client.post(
        "/webhooks/line/idempotent-line-key",
        content=body,
        headers=headers,
    )
    second = client.post(
        "/webhooks/line/idempotent-line-key",
        content=body,
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert route.call_count == 1
    with Session(get_engine()) as session:
        assert len(
            session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.sender_type == "user"
                )
            ).all()
        ) == 1
        assert len(
            session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.sender_type == "bot"
                )
            ).all()
        ) == 1
        assert len(session.exec(select(BotDecision)).all()) == 1


@respx.mock
def test_human_active_inbound_persists_without_bot_reply(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Human Bot", reply="Should stay silent")
        connection = add_connection(
            session,
            bot.id,
            "line",
            "human-line-key",
            {
                "channel_secret": "human-secret",
                "channel_access_token": "human-token",
            },
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
    body = line_body("m-human", user_id="user-human")
    response = client.post(
        "/webhooks/line/human-line-key",
        content=body,
        headers={"x-line-signature": line_signature(body, "human-secret")},
    )

    assert response.status_code == 200
    assert route.call_count == 0
    with Session(get_engine()) as session:
        messages = session.exec(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id
            )
        ).all()
        assert [(message.sender_type, message.content) for message in messages] == [
            ("user", "price please")
        ]
        assert session.exec(select(BotDecision)).all() == []


@respx.mock
def test_escalating_runtime_result_moves_conversation_to_needs_human(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = make_bot(session, "Escalation Bot")
        config = session.get(BotConfigVersion, bot.live_config_version_id)
        rules = session.exec(
            select(BotConfigRule).where(
                BotConfigRule.config_version_id == config.id
            )
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
        add_connection(
            session,
            bot.id,
            "line",
            "escalate-line-key",
            {
                "channel_secret": "escalate-secret",
                "channel_access_token": "escalate-token",
            },
        )

    respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    body = line_body("m-escalate", text="I need a human")
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


def test_legacy_alias_rejects_multiple_enabled_connections(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        first = make_bot(session, "Alias Bot 1")
        second = make_bot(session, "Alias Bot 2")
        add_connection(
            session,
            first.id,
            "line",
            "alias-one",
            {"channel_secret": "one-secret", "channel_access_token": "one-token"},
        )
        add_connection(
            session,
            second.id,
            "line",
            "alias-two",
            {"channel_secret": "two-secret", "channel_access_token": "two-token"},
        )

    response = client.post("/webhooks/line", content=b'{"events":[]}')

    assert response.status_code == 409
    assert response.json()["detail"] == "ambiguous_legacy_webhook"
