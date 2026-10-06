from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import AuditEvent


def test_audit_event_round_trip(client) -> None:
    with Session(get_engine()) as session:
        event = AuditEvent(
            actor_user_id=None,
            action="bot.pause",
            object_type="bot",
            object_id="1",
            bot_id=1,
            summary="Paused Bot 1",
            before_json='{"status":"active"}',
            after_json='{"status":"paused"}',
            request_metadata_json="{}",
        )
        session.add(event)
        session.commit()
        stored = session.exec(select(AuditEvent)).one()

        assert stored.action == "bot.pause"
        assert stored.bot_id == 1
        assert stored.summary == "Paused Bot 1"
        assert stored.before_json == '{"status":"active"}'
        assert stored.after_json == '{"status":"paused"}'
        assert stored.request_metadata_json == "{}"
        assert stored.created_at is not None
