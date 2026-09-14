import pytest
import respx
from httpx import Response
from sqlmodel import Session, select

from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import ChannelConnection, Conversation, ConversationHandoffEvent
from chatbot_manager.runtime.delivery import DeliveryService
from chatbot_manager.runtime.handoff import ConversationStateError, HandoffService


def make_conversation(session: Session, status: str = "bot_active") -> Conversation:
    conversation = Conversation(
        bot_id=1,
        channel_connection_id=1,
        external_user_id="u-handoff",
        status=status,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def test_escalate_transitions_bot_active_to_needs_human_and_records_event(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session)
        result = HandoffService(session).escalate(conversation.id, reason="human requested")

        assert result.status == "needs_human"
        assert result.handoff_reason == "human requested"
        event = session.exec(select(ConversationHandoffEvent)).one()
        assert event.event_type == "escalated"
        assert event.reason == "human requested"


def test_take_transitions_needs_human_to_human_active(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="needs_human")
        result = HandoffService(session).take(conversation.id, operator_id=10)

        assert result.status == "human_active"
        assert result.assigned_operator_id == 10
        event = session.exec(select(ConversationHandoffEvent)).one()
        assert event.event_type == "taken"
        assert event.actor_user_id == 10


def test_second_take_loses_atomic_claim(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="needs_human")
        HandoffService(session).take(conversation.id, 10)

        with pytest.raises(ConversationStateError, match="conversation_not_available"):
            HandoffService(session).take(conversation.id, 11)

        stored = session.get(Conversation, conversation.id)
        assert stored.assigned_operator_id == 10


def test_assign_return_and_close_follow_legal_state_transitions(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="needs_human")
        service = HandoffService(session)

        assigned = service.assign(conversation.id, operator_id=21, actor_user_id=99)
        assert assigned.status == "human_active"
        assert assigned.assigned_operator_id == 21

        returned = service.return_to_bot(conversation.id, actor_user_id=21)
        assert returned.status == "bot_active"
        assert returned.assigned_operator_id is None

        closed = service.close(conversation.id, actor_user_id=99, reason="resolved")
        assert closed.status == "closed"
        assert closed.closed_at is not None

        events = session.exec(
            select(ConversationHandoffEvent).order_by(ConversationHandoffEvent.id)
        ).all()
        assert [event.event_type for event in events] == ["assigned", "returned_to_bot", "closed"]


def test_illegal_return_to_bot_is_rejected(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="bot_active")

        with pytest.raises(ConversationStateError, match="invalid_conversation_state"):
            HandoffService(session).return_to_bot(conversation.id, actor_user_id=10)


@pytest.mark.asyncio
@respx.mock
async def test_delivery_service_uses_connection_credentials(client) -> None:
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(200, json={})
    )
    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "channel:line",
            {"channel_secret": "line-secret", "channel_access_token": "line-token"},
        )
        connection = ChannelConnection(
            bot_id=1,
            provider="line",
            display_name="LINE",
            webhook_key="delivery-line",
            credential_id=credential.id,
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)

        result = await DeliveryService(session).send(
            connection,
            {"reply_token": "reply-token"},
            "Hello from operator",
        )

        assert result.status == "delivered"
        assert result.error_code == ""
        assert route.called
        assert route.calls[0].request.headers["authorization"] == "Bearer line-token"


@pytest.mark.asyncio
@respx.mock
async def test_delivery_failure_returns_stable_error(client) -> None:
    respx.post("https://api.line.me/v2/bot/message/reply").mock(
        return_value=Response(500, text="secret provider detail")
    )
    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "channel:line",
            {"channel_secret": "line-secret", "channel_access_token": "line-token"},
        )
        connection = ChannelConnection(
            bot_id=1,
            provider="line",
            display_name="LINE",
            webhook_key="delivery-fail-line",
            credential_id=credential.id,
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)

        result = await DeliveryService(session).send(
            connection,
            {"reply_token": "reply-token"},
            "Hello",
        )

        assert result.status == "failed"
        assert result.error_code == "provider_delivery_failed"
        assert "secret provider detail" not in result.error_code

@pytest.mark.asyncio
@respx.mock
async def test_line_operator_delivery_uses_push_when_user_id_is_available(client) -> None:
    route = respx.post("https://api.line.me/v2/bot/message/push").mock(
        return_value=Response(200, json={})
    )
    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "channel:line",
            {"channel_secret": "line-secret", "channel_access_token": "line-token"},
        )
        connection = ChannelConnection(
            bot_id=1,
            provider="line",
            display_name="LINE",
            webhook_key="delivery-line-push",
            credential_id=credential.id,
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)

        result = await DeliveryService(session).send(
            connection,
            {"user_id": "line-user-1"},
            "Human follow-up",
        )

        assert result.status == "delivered"
        assert route.called
        request = route.calls[0].request
        assert request.headers["authorization"] == "Bearer line-token"
        assert b"line-user-1" in request.content
        assert b"Human follow-up" in request.content

