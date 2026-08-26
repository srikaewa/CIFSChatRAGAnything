from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.channels.line import IncomingMessage
from chatbot_manager.db import get_engine
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule
from chatbot_manager.webhooks import process_messages


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


def incoming(text: str = "price please") -> IncomingMessage:
    return IncomingMessage(
        provider="line",
        external_user_id="user-1",
        text=text,
        reply_context={"reply_token": "reply-1"},
        raw_event={"type": "message"},
    )


def test_channel_save_failure_uses_stable_error_without_exception_text(client: TestClient, monkeypatch) -> None:
    def fail_save(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("provider-token-should-not-leak")

    monkeypatch.setattr("chatbot_manager.admin.routes.save_channel", fail_save)
    csrf_token = login(client)

    response = client.post(
        "/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "line-secret",
            "channel_access_token": "line-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/channels?error=channel_save_failed"
    assert "provider-token-should-not-leak" not in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "Could not save channel settings. Check the configuration and retry." in page.text
    assert "provider-token-should-not-leak" not in page.text


@pytest.mark.asyncio
async def test_reply_send_failure_is_persisted_with_deterministic_success_count(client: TestClient) -> None:
    async def failing_sender(reply_context: dict[str, Any], text: str) -> None:
        raise RuntimeError("provider-token-should-not-leak")

    with Session(get_engine()) as session:
        session.add(AssistantSettings(rag_enabled=False))
        session.add(Rule(pattern="price", match_type="contains", reply_text="Price is 100."))
        session.commit()

        processed = await process_messages([incoming()], failing_sender, session)

        event = session.exec(select(ChatEvent)).one()
        assert processed == 1
        assert event.reply_text == "Price is 100."
        assert event.error == "reply_send_failed"
        assert "provider-token-should-not-leak" not in event.error


@pytest.mark.asyncio
async def test_admin_notification_failure_does_not_suppress_sent_reply(
    client: TestClient,
    monkeypatch,
) -> None:
    sent: list[str] = []

    async def successful_sender(reply_context: dict[str, Any], text: str) -> None:
        sent.append(text)

    async def failing_notification(*args: Any, **kwargs: Any) -> None:
        raise ValueError("invalid-chat-id-secret")

    monkeypatch.setattr("chatbot_manager.webhooks._notify_admin", failing_notification)
    with Session(get_engine()) as session:
        session.add(AssistantSettings(rag_enabled=False, admin_notify_chat_id="invalid"))
        session.add(
            Rule(
                pattern="price",
                match_type="contains",
                reply_text="Internal rule reply.",
                escalate=True,
                escalate_message="A human will follow up.",
            )
        )
        session.commit()

        processed = await process_messages([incoming()], successful_sender, session)

        event = session.exec(select(ChatEvent)).one()
        assert processed == 1
        assert sent == ["A human will follow up."]
        assert event.reply_text == "A human will follow up."
        assert event.error == "admin_notification_failed"
        assert "invalid-chat-id-secret" not in event.error
