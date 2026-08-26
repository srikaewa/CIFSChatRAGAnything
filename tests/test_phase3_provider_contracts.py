import base64
import hashlib
import hmac
import json

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from chatbot_manager.channel_config import resolve_channel
from chatbot_manager.db import get_engine
from chatbot_manager.channels.line import IncomingMessage
from chatbot_manager.models import AssistantSettings, Channel, ChatEvent
from chatbot_manager.webhooks import _notify_admin, process_messages
from chatbot_manager.settings import get_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    return page.text.split(marker, 1)[1].split('"', 1)[0]


@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "expected"),
    [
        (False, {}, "not_configured", "disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "failed",
        ),
    ],
)
def test_line_resolved_channel_state(client: TestClient, enabled, credentials, saved_status, expected) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()
        resolved = resolve_channel(session, get_settings(), "line")

    assert resolved.state == expected


@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "label"),
    [
        (False, {}, "not_configured", "Disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "Incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "Ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "Failed",
        ),
    ],
)
def test_channels_page_renders_line_state(client: TestClient, enabled, credentials, saved_status, label) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()

    login(client)
    response = client.get("/channels")

    assert response.status_code == 200
    line_panel = response.text.split("<h2>LINE</h2>", 1)[1].split("</article>", 1)[0]
    assert f"Status: {label}" in line_panel



def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.parametrize(
    ("provider", "credentials", "body", "headers"),
    [
        (
            "line",
            {"channel_secret": "line-secret"},
            b'{"events":[]}',
            {"x-line-signature": line_signature(b'{"events":[]}', "line-secret")},
        ),
        (
            "messenger",
            {"verify_token": "verify", "page_access_token": "page-token"},
            b'{"entry":[]}',
            {"x-hub-signature-256": "sha256=missing-app-secret"},
        ),
        (
            "telegram",
            {"bot_token": "bot-token"},
            b'{"update_id":1}',
            {"x-telegram-bot-api-secret-token": "missing-secret"},
        ),
    ],
)
def test_enabled_incomplete_provider_returns_503_before_processing(
    client: TestClient, provider, credentials, body, headers
) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider=provider,
                enabled=True,
                display_name=provider.title(),
                status="incomplete",
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()

    response = client.post(
        f"/webhooks/{provider}",
        content=body,
        headers={"content-type": "application/json", **headers},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == f"{provider} channel is not ready"


def test_failed_provider_returns_503_before_processing(client: TestClient) -> None:
    body = b'{"events":[]}'
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                status="failed",
                credential_json=json.dumps(
                    {"channel_secret": "line-secret", "channel_access_token": "line-token"}
                ),
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/line",
        content=body,
        headers={"x-line-signature": line_signature(body, "line-secret")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "line channel is not ready"


@pytest.mark.parametrize("provider", ["line", "messenger", "telegram"])
def test_authenticated_malformed_json_returns_400(client: TestClient, provider: str) -> None:
    body = b'{"broken":'
    credentials_by_provider = {
        "line": {"channel_secret": "line-secret", "channel_access_token": "line-token"},
        "messenger": {
            "verify_token": "verify",
            "page_access_token": "page-token",
            "app_secret": "app-secret",
        },
        "telegram": {"bot_token": "bot-token", "webhook_secret": "telegram-secret"},
    }
    headers_by_provider = {
        "line": {"x-line-signature": line_signature(body, "line-secret")},
        "messenger": {"x-hub-signature-256": messenger_signature(body, "app-secret")},
        "telegram": {"x-telegram-bot-api-secret-token": "telegram-secret"},
    }
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider=provider,
                enabled=True,
                display_name=provider.title(),
                status="ready",
                credential_json=json.dumps(credentials_by_provider[provider]),
            )
        )
        session.commit()

    safe_client = TestClient(client.app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/webhooks/{provider}",
        content=body,
        headers={"content-type": "application/json", **headers_by_provider[provider]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed JSON payload"


@respx.mock
def test_messenger_webhook_processes_text_rule_end_to_end(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/rules",
        data={
            "csrf_token": csrf_token,
            "pattern": "price",
            "match_type": "contains",
            "reply_text": "Price is 100.",
            "priority": "10",
        },
        follow_redirects=False,
    )
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                status="ready",
                credential_json=json.dumps(
                    {
                        "verify_token": "verify",
                        "page_access_token": "page-token",
                        "app_secret": "app-secret",
                    }
                ),
            )
        )
        session.commit()

    route = respx.post("https://graph.facebook.com/v20.0/me/messages").mock(
        return_value=Response(200, json={"recipient_id": "user-1", "message_id": "m1"})
    )
    body = b'{"entry":[{"messaging":[{"sender":{"id":"user-1"},"message":{"text":"price please"}}]}]}'

    response = client.post(
        "/webhooks/messenger",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": messenger_signature(body, "app-secret"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content
    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text



def assistant_form(csrf_token: str, channel: str, chat_id: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "system_prompt": "Use docs.",
        "fallback_reply": "Ask staff.",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "admin_notify_channel": channel,
        "admin_notify_chat_id": chat_id,
    }


def test_assistant_rejects_unsupported_notification_channel(client: TestClient) -> None:
    csrf_token = login(client)
    response = client.post(
        "/assistant",
        data=assistant_form(csrf_token, "line", "123"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/assistant?error=unsupported_notification_channel"


def test_assistant_rejects_non_integer_telegram_destination(client: TestClient) -> None:
    csrf_token = login(client)
    response = client.post(
        "/assistant",
        data=assistant_form(csrf_token, "telegram", "not-a-chat-id"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/assistant?error=invalid_notification_destination"


@pytest.mark.asyncio
async def test_admin_notification_blank_destination_is_skipped(client: TestClient) -> None:
    with Session(get_engine()) as session:
        result = await _notify_admin(
            AssistantSettings(admin_notify_channel="telegram", admin_notify_chat_id=""),
            session,
            IncomingMessage("line", "user-1", "help", {"reply_token": "r1"}, {}),
            "Internal help reply",
        )
    assert result.status == "skipped"
    assert result.error_code == ""


@pytest.mark.asyncio
async def test_admin_notification_invalid_destination_fails_safely(client: TestClient) -> None:
    with Session(get_engine()) as session:
        result = await _notify_admin(
            AssistantSettings(admin_notify_channel="telegram", admin_notify_chat_id="abc"),
            session,
            IncomingMessage("line", "user-1", "help", {"reply_token": "r1"}, {}),
            "Internal help reply",
        )
    assert result.status == "failed"
    assert result.error_code == "admin_notification_invalid_destination"


@pytest.mark.asyncio
async def test_admin_notification_missing_bot_token_fails_safely(client: TestClient) -> None:
    with Session(get_engine()) as session:
        result = await _notify_admin(
            AssistantSettings(admin_notify_channel="telegram", admin_notify_chat_id="123"),
            session,
            IncomingMessage("line", "user-1", "help", {"reply_token": "r1"}, {}),
            "Internal help reply",
        )
    assert result.status == "failed"
    assert result.error_code == "admin_notification_not_configured"


@pytest.mark.asyncio
async def test_admin_notification_valid_telegram_destination_is_sent(client: TestClient, monkeypatch) -> None:
    sent: list[tuple[dict[str, object], str]] = []

    async def fake_send(self, reply_context, text):
        sent.append((reply_context, text))

    monkeypatch.setattr("chatbot_manager.webhooks.TelegramAdapter.send_reply", fake_send)
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="telegram",
                enabled=True,
                display_name="Telegram",
                status="ready",
                credential_json=json.dumps({"bot_token": "bot-token", "webhook_secret": "secret"}),
            )
        )
        session.commit()
        result = await _notify_admin(
            AssistantSettings(admin_notify_channel="telegram", admin_notify_chat_id="-100123"),
            session,
            IncomingMessage("line", "user-1", "need human", {"reply_token": "r1"}, {}),
            "Internal escalation reply",
        )
    assert result.status == "sent"
    assert result.error_code == ""
    assert sent[0][0] == {"chat_id": -100123}



@pytest.mark.asyncio
async def test_test_chat_and_live_message_use_same_decision_engine(client: TestClient) -> None:
    csrf_token = login(client)
    with Session(get_engine()) as session:
        session.add(AssistantSettings(rag_enabled=False, fallback_reply="Fallback."))
        session.commit()
    client.post(
        "/rules",
        data={
            "csrf_token": csrf_token,
            "pattern": "price",
            "match_type": "contains",
            "reply_text": "Price is 100.",
            "priority": "10",
        },
        follow_redirects=False,
    )

    test_response = client.post(
        "/test-chat",
        data={"csrf_token": csrf_token, "message": "price please"},
    )
    assert test_response.status_code == 200

    sent: list[str] = []

    async def sender(reply_context, text):
        sent.append(text)

    with Session(get_engine()) as session:
        await process_messages(
            [IncomingMessage("line", "user-live", "price please", {"reply_token": "r"}, {})],
            sender,
            session,
        )
        events = list(session.exec(select(ChatEvent).order_by(ChatEvent.id)).all())

    assert len(events) == 2
    assert events[0].provider == "test"
    assert events[1].provider == "line"
    assert events[0].decision_source == events[1].decision_source == "rule"
    assert events[0].reply_text == events[1].reply_text == "Price is 100."
    assert sent == ["Price is 100."]


def test_test_chat_page_documents_provider_side_effect_difference(client: TestClient) -> None:
    login(client)
    response = client.get("/test-chat")
    assert response.status_code == 200
    assert "same decision engine" in response.text.lower()
    assert "does not send provider replies or admin notifications" in response.text.lower()
