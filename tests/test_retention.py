from datetime import timedelta
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    AuditEvent,
    Bot,
    BotConfigVersion,
    BotDecision,
    BotTestCase,
    ChannelConnection,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
    Incident,
    KnowledgeService,
    User,
    utc_now,
)
from chatbot_manager.retention import RetentionError, RetentionService
from chatbot_manager.settings import Settings


def login_owner(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/bots")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def owner_id() -> int:
    with Session(get_engine()) as session:
        owner = session.exec(
            select(User).where(User.email == "admin@example.local")
        ).one()
        assert owner.id is not None
        return owner.id


def create_bot(session: Session, *, status: str) -> Bot:
    bot = Bot(name=f"Retention {status}", lifecycle_status=status)
    session.add(bot)
    session.commit()
    session.refresh(bot)
    assert bot.id is not None
    return bot


def create_conversation(
    session: Session,
    *,
    status: str,
    age_days: int,
    bot_id: int | None = None,
) -> Conversation:
    if bot_id is None:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
    connection = ChannelConnection(
        bot_id=bot_id,
        provider="line",
        display_name="LINE",
        webhook_key=f"retention-{status}-{age_days}-{bot_id}",
        enabled=True,
        status="ready",
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    cutoff = utc_now() - timedelta(days=age_days)
    conversation = Conversation(
        bot_id=bot_id,
        channel_connection_id=connection.id,
        external_user_id=f"{status}-{age_days}",
        status=status,
        started_at=cutoff,
        last_message_at=cutoff,
        closed_at=cutoff if status == "closed" else None,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    assert conversation.id is not None
    return conversation


def test_retention_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.conversation_retention_days == 365
    assert settings.incident_retention_days == 730
    assert settings.audit_retention_days == 1095


def test_active_bot_must_pause_before_archive(client) -> None:
    with Session(get_engine()) as session:
        bot = create_bot(session, status="active")
        with pytest.raises(RetentionError, match="bot_must_be_paused"):
            RetentionService(session).archive_bot(bot.id, actor_user_id=owner_id())


def test_archive_paused_bot_disables_channels_and_preserves_history(client) -> None:
    actor_user_id = owner_id()
    with Session(get_engine()) as session:
        bot = create_bot(session, status="paused")
        assert bot.id is not None
        config = BotConfigVersion(
            bot_id=bot.id,
            version_number=1,
            status="published",
        )
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="telegram",
            display_name="Telegram",
            webhook_key="archive-telegram",
            enabled=True,
            status="ready",
        )
        test_case = BotTestCase(
            bot_id=bot.id,
            name="Archive history",
            input_message="history",
        )
        session.add(config)
        session.add(connection)
        session.add(test_case)
        session.commit()
        conversation = create_conversation(
            session,
            status="closed",
            age_days=1,
            bot_id=bot.id,
        )
        config_id = config.id
        case_id = test_case.id
        conversation_id = conversation.id

        archived = RetentionService(session).archive_bot(
            bot.id,
            actor_user_id=actor_user_id,
        )

        assert archived.lifecycle_status == "archived"
        assert session.get(BotConfigVersion, config_id) is not None
        assert session.get(BotTestCase, case_id) is not None
        assert session.get(Conversation, conversation_id) is not None
        channels = session.exec(
            select(ChannelConnection).where(ChannelConnection.bot_id == bot.id)
        ).all()
        assert channels
        assert all(not row.enabled and row.status == "disabled" for row in channels)
        audit = session.exec(
            select(AuditEvent).where(AuditEvent.action == "bot.archive")
        ).one()
        assert audit.bot_id == bot.id
        assert "archived" in audit.after_json


def test_referenced_knowledge_service_cannot_be_removed(client) -> None:
    with Session(get_engine()) as session:
        service = KnowledgeService(
            name="Referenced RAG",
            api_base_url="https://rag.test",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        bot = create_bot(session, status="active")
        version = BotConfigVersion(
            bot_id=bot.id,
            version_number=1,
            status="published",
            knowledge_service_id=service.id,
        )
        session.add(version)
        session.commit()

        with pytest.raises(RetentionError, match="knowledge_service_in_use"):
            RetentionService(session).remove_knowledge_service(service.id)


def test_disable_knowledge_service_preserves_referenced_service(client) -> None:
    actor_user_id = owner_id()
    with Session(get_engine()) as session:
        service = KnowledgeService(
            name="Disable RAG",
            api_base_url="https://disable-rag.test",
            enabled=True,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id
        disabled = RetentionService(session).disable_knowledge_service(
            service_id,
            actor_user_id=actor_user_id,
        )
        assert disabled.enabled is False
        assert session.get(KnowledgeService, service_id) is not None
        audit = session.exec(
            select(AuditEvent).where(AuditEvent.action == "knowledge.disable")
        ).one()
        assert audit.object_id == str(service_id)


def test_conversation_retention_never_deletes_open_conversations(client) -> None:
    actor_user_id = owner_id()
    with Session(get_engine()) as session:
        old_closed = create_conversation(session, status="closed", age_days=400)
        old_open = create_conversation(session, status="bot_active", age_days=400)
        assert old_closed.id is not None
        assert old_open.id is not None

        message = ConversationMessage(
            conversation_id=old_closed.id,
            sender_type="user",
            content="purge me",
        )
        handoff = ConversationHandoffEvent(
            conversation_id=old_closed.id,
            event_type="closed",
        )
        session.add(message)
        session.add(handoff)
        session.commit()
        session.refresh(message)
        decision = BotDecision(
            message_id=message.id,
            bot_id=old_closed.bot_id,
            config_version_id=1,
            decision_type="fallback",
        )
        session.add(decision)
        session.commit()
        closed_id = old_closed.id
        open_id = old_open.id
        message_id = message.id
        handoff_id = handoff.id
        decision_id = decision.id

        result = RetentionService(session).purge_closed_conversations(
            older_than_days=365,
            actor_user_id=actor_user_id,
        )

        assert result.deleted_conversations == 1
        assert result.deleted_messages == 1
        assert result.deleted_decisions == 1
        assert result.deleted_handoff_events == 1
        assert session.get(Conversation, closed_id) is None
        assert session.get(Conversation, open_id) is not None
        assert session.get(ConversationMessage, message_id) is None
        assert session.get(ConversationHandoffEvent, handoff_id) is None
        assert session.get(BotDecision, decision_id) is None
        audit = session.exec(
            select(AuditEvent).where(AuditEvent.action == "retention.conversations")
        ).one()
        assert "purge me" not in audit.after_json
        assert '"deleted_conversations": 1' in audit.after_json


def test_incident_retention_only_deletes_old_resolved_incidents(client) -> None:
    actor_user_id = owner_id()
    old = utc_now() - timedelta(days=800)
    with Session(get_engine()) as session:
        resolved = Incident(
            severity="warning",
            source_type="system",
            source_id="old-resolved",
            incident_type="old",
            deduplication_key="old-resolved",
            status="resolved",
            created_at=old,
            resolved_at=old,
        )
        opened = Incident(
            severity="critical",
            source_type="system",
            source_id="old-open",
            incident_type="old",
            deduplication_key="old-open",
            status="open",
            created_at=old,
        )
        session.add(resolved)
        session.add(opened)
        session.commit()
        resolved_id = resolved.id
        opened_id = opened.id

        result = RetentionService(session).purge_incidents(
            older_than_days=730,
            actor_user_id=actor_user_id,
        )

        assert result.deleted_incidents == 1
        assert session.get(Incident, resolved_id) is None
        assert session.get(Incident, opened_id) is not None
        audit = session.exec(
            select(AuditEvent).where(AuditEvent.action == "retention.incidents")
        ).one()
        assert '"deleted_incidents": 1' in audit.after_json


def test_archive_and_disable_admin_routes_use_retention_service(
    client: TestClient,
) -> None:
    csrf_token = login_owner(client)
    with Session(get_engine()) as session:
        paused = create_bot(session, status="paused")
        active = create_bot(session, status="active")
        service = KnowledgeService(
            name="Route RAG",
            api_base_url="https://route-rag.test",
            enabled=True,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        paused_id = paused.id
        active_id = active.id
        service_id = service.id

    paused_page = client.get(f"/bots/{paused_id}")
    assert paused_page.status_code == 200
    assert f'action="/bots/{paused_id}/archive"' in paused_page.text

    archived = client.post(
        f"/bots/{paused_id}/archive",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert archived.status_code == 303

    rejected = client.post(
        f"/bots/{active_id}/archive",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert rejected.status_code == 409

    registry = client.get("/knowledge-services")
    assert registry.status_code == 200
    assert f'action="/knowledge-services/{service_id}/disable"' in registry.text

    disabled = client.post(
        f"/knowledge-services/{service_id}/disable",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert disabled.status_code == 303

    with Session(get_engine()) as session:
        archived_bot = session.get(Bot, paused_id)
        kept_service = session.get(KnowledgeService, service_id)
        assert archived_bot is not None
        assert archived_bot.lifecycle_status == "archived"
        assert kept_service is not None
        assert kept_service.enabled is False


def test_audit_retention_deletes_old_rows_and_records_count_only_event(client) -> None:
    actor_user_id = owner_id()
    old = utc_now() - timedelta(days=1200)
    with Session(get_engine()) as session:
        row = AuditEvent(
            actor_user_id=actor_user_id,
            action="old.audit",
            object_type="system",
            object_id="old",
            summary="Old row",
            before_json='{"password":"should-not-survive"}',
            created_at=old,
        )
        session.add(row)
        session.commit()
        session.expunge(row)
        result = RetentionService(session).purge_audit(
            older_than_days=1095,
            actor_user_id=actor_user_id,
        )

        assert result.deleted_audit_events == 1
        assert session.exec(
            select(AuditEvent).where(AuditEvent.action == "old.audit")
        ).first() is None
        audit = session.exec(
            select(AuditEvent).where(AuditEvent.action == "retention.audit")
        ).one()
        assert "should-not-survive" not in audit.after_json
        assert '"deleted_audit_events": 1' in audit.after_json
