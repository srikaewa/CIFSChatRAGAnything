from datetime import timedelta

from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    BotDecision,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
    utc_now,
)
from chatbot_manager.runtime.conversations import ConversationService


def test_same_user_reuses_open_conversation_within_24_hours(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session, inactivity_hours=24)
        first = service.get_or_create(bot_id=1, channel_connection_id=1, external_user_id="u1")
        second = service.get_or_create(bot_id=1, channel_connection_id=1, external_user_id="u1")
        assert first.id == second.id


def test_inactive_or_closed_conversation_starts_new_session(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session, inactivity_hours=24)
        stale = service.get_or_create(1, 1, "u1")
        stale.last_message_at = utc_now() - timedelta(hours=25)
        session.add(stale)
        session.commit()

        newer = service.get_or_create(1, 1, "u1")
        assert newer.id != stale.id

        newer.status = "closed"
        newer.closed_at = utc_now()
        session.add(newer)
        session.commit()

        reopened = service.get_or_create(1, 1, "u1")
        assert reopened.id not in {stale.id, newer.id}
        assert reopened.status == "bot_active"


def test_duplicate_external_message_is_not_inserted_twice(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        first = service.record_inbound(conversation, "m-1", "hello", {"provider": "line"})
        second = service.record_inbound(conversation, "m-1", "hello", {"provider": "line"})
        assert first.id == second.id
        rows = session.exec(select(ConversationMessage)).all()
        assert len(rows) == 1
        assert rows[0].sender_type == "user"
        assert rows[0].external_message_id == "m-1"


def test_history_excludes_internal_notes_and_system_events(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="user", content="question"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="operator", content="human answer"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="internal_note", content="private note"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="system", content="taken by operator"))
        session.commit()
        history = service.history(conversation.id)

    assert history == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "human answer"},
    ]


def test_history_is_bounded_and_returns_oldest_to_newest(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        for index in range(5):
            session.add(
                ConversationMessage(
                    conversation_id=conversation.id,
                    sender_type="user" if index % 2 == 0 else "bot",
                    content=f"m{index}",
                    created_at=utc_now() + timedelta(seconds=index),
                )
            )
        session.commit()

        history = service.history(conversation.id, limit=3)

    assert [item["content"] for item in history] == ["m2", "m3", "m4"]
    assert [item["role"] for item in history] == ["user", "assistant", "user"]


def test_decision_and_handoff_event_models_persist(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        message = service.record_inbound(conversation, "m-1", "hello", {})
        decision = BotDecision(
            message_id=message.id,
            bot_id=1,
            config_version_id=1,
            decision_type="rag",
            reference_count=2,
            total_latency_ms=25,
        )
        handoff = ConversationHandoffEvent(
            conversation_id=conversation.id,
            event_type="escalated",
            reason="human requested",
        )
        session.add(decision)
        session.add(handoff)
        session.commit()
        session.refresh(decision)
        session.refresh(handoff)

        assert decision.id is not None
        assert decision.reference_count == 2
        assert handoff.id is not None
        assert handoff.event_type == "escalated"
