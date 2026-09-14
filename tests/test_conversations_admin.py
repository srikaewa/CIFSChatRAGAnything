import json

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    ChannelConnection,
    Conversation,
    ConversationMessage,
)
from chatbot_manager.runtime.conversations import ConversationService
from chatbot_manager.runtime.delivery import DeliveryResult, DeliveryService


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


def make_connection(session: Session, provider: str = "line") -> ChannelConnection:
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    if provider == "line":
        payload = {"channel_secret": "secret", "channel_access_token": "token"}
    elif provider == "telegram":
        payload = {"bot_token": "token", "webhook_secret": "secret"}
    else:
        payload = {
            "verify_token": "verify",
            "page_access_token": "token",
            "app_secret": "secret",
        }
    credential = store_credential(session, f"channel:{provider}", payload)
    connection = ChannelConnection(
        bot_id=bot.id,
        provider=provider,
        display_name=provider.title(),
        webhook_key=f"{provider}-inbox-key",
        credential_id=credential.id,
        enabled=True,
        status="ready",
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


def make_conversation(
    session: Session,
    connection: ChannelConnection,
    *,
    user_id: str,
    status: str,
    assigned_operator_id: int | None = None,
) -> Conversation:
    conversation = Conversation(
        bot_id=connection.bot_id,
        channel_connection_id=connection.id,
        external_user_id=user_id,
        status=status,
        assigned_operator_id=assigned_operator_id,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            sender_type="user",
            content=f"Message from {user_id}",
            external_message_id=f"msg-{user_id}",
            metadata_json=json.dumps(
                {"reply_context": {"reply_token": f"reply-{user_id}"}}
            ),
        )
    )
    session.commit()
    return conversation


def test_conversations_page_filters_needs_human(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        connection = make_connection(session)
        make_conversation(session, connection, user_id="waiting-user", status="needs_human")
        make_conversation(session, connection, user_id="active-user", status="bot_active")

    response = client.get("/conversations?status=needs_human")

    assert response.status_code == 200
    assert "waiting-user" in response.text
    assert "active-user" not in response.text
    assert "Needs human" in response.text


def test_take_marks_conversation_human_active(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        connection = make_connection(session)
        conversation = make_conversation(
            session,
            connection,
            user_id="take-user",
            status="needs_human",
        )
        conversation_id = conversation.id

    page = client.get(f"/conversations/{conversation_id}")
    csrf = csrf_from_page(page.text)
    response = client.post(
        f"/conversations/{conversation_id}/take",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        stored = session.get(Conversation, conversation_id)
        assert stored.status == "human_active"
        assert stored.assigned_operator_id == 0


def test_operator_reply_uses_original_channel_and_records_delivery(
    client: TestClient,
    monkeypatch,
) -> None:
    login(client)
    captured: list[tuple[int, dict[str, object], str]] = []

    async def fake_send(self, connection, reply_context, text):
        captured.append((connection.id, reply_context, text))
        return DeliveryResult(status="delivered")

    monkeypatch.setattr(DeliveryService, "send", fake_send)
    with Session(get_engine()) as session:
        connection = make_connection(session)
        conversation = make_conversation(
            session,
            connection,
            user_id="reply-user",
            status="human_active",
            assigned_operator_id=0,
        )
        conversation_id = conversation.id
        connection_id = connection.id

    csrf = csrf_from_page(client.get(f"/conversations/{conversation_id}").text)
    response = client.post(
        f"/conversations/{conversation_id}/reply",
        data={"csrf_token": csrf, "reply_text": "Human response"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert captured == [
        (connection_id, {"reply_token": "reply-reply-user"}, "Human response")
    ]
    with Session(get_engine()) as session:
        operator = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type == "operator")
        ).one()
        assert operator.content == "Human response"
        assert operator.delivery_status == "delivered"


def test_return_to_bot_does_not_generate_replay_reply(
    client: TestClient,
    monkeypatch,
) -> None:
    login(client)
    runtime_calls = 0

    async def counted_run(self, request):
        nonlocal runtime_calls
        runtime_calls += 1
        raise AssertionError("return-to-bot must not replay the last user message")

    monkeypatch.setattr("chatbot_manager.runtime.engine.BotRuntime.run", counted_run)
    with Session(get_engine()) as session:
        connection = make_connection(session)
        conversation = make_conversation(
            session,
            connection,
            user_id="return-user",
            status="human_active",
            assigned_operator_id=0,
        )
        conversation_id = conversation.id

    csrf = csrf_from_page(client.get(f"/conversations/{conversation_id}").text)
    response = client.post(
        f"/conversations/{conversation_id}/return-to-bot",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert runtime_calls == 0
    with Session(get_engine()) as session:
        stored = session.get(Conversation, conversation_id)
        assert stored.status == "bot_active"
        assert stored.assigned_operator_id is None


def test_internal_note_is_not_sent_or_added_to_bot_history(
    client: TestClient,
    monkeypatch,
) -> None:
    login(client)

    async def forbidden_send(*args, **kwargs):
        raise AssertionError("Internal notes must never be sent to providers")

    monkeypatch.setattr(DeliveryService, "send", forbidden_send)
    with Session(get_engine()) as session:
        connection = make_connection(session)
        conversation = make_conversation(
            session,
            connection,
            user_id="note-user",
            status="human_active",
            assigned_operator_id=0,
        )
        conversation_id = conversation.id

    csrf = csrf_from_page(client.get(f"/conversations/{conversation_id}").text)
    response = client.post(
        f"/conversations/{conversation_id}/note",
        data={"csrf_token": csrf, "note_text": "Private internal note"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        notes = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type == "internal_note")
        ).all()
        assert len(notes) == 1
        assert notes[0].content == "Private internal note"
        history = ConversationService(session).history(conversation_id)
        assert all(item["content"] != "Private internal note" for item in history)


def test_conversations_requires_login(client: TestClient) -> None:
    response = client.get("/conversations", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

@respx.mock
def test_line_operator_reply_uses_push_not_consumed_reply_token(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        connection = make_connection(session, provider="line")
        conversation = make_conversation(
            session,
            connection,
            user_id="line-push-user",
            status="human_active",
            assigned_operator_id=0,
        )
        conversation_id = conversation.id
        inbound = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type == "user")
        ).one()
        inbound.metadata_json = json.dumps({
            "reply_context": {
                "reply_token": "already-consumed-token",
                "user_id": "line-push-user",
            }
        })
        session.add(inbound)
        session.commit()

    push = respx.post("https://api.line.me/v2/bot/message/push").mock(
        return_value=Response(200, json={})
    )
    csrf = csrf_from_page(client.get(f"/conversations/{conversation_id}").text)
    response = client.post(
        f"/conversations/{conversation_id}/reply",
        data={"csrf_token": csrf, "reply_text": "Operator via push"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert push.called
    assert b"line-push-user" in push.calls[0].request.content
    assert b"already-consumed-token" not in push.calls[0].request.content

