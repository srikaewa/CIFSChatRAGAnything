import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.audit import record_audit, sanitize_audit_value
from chatbot_manager.auth.authorization import CurrentUser
from chatbot_manager.auth.service import password_hasher
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    AuditEvent,
    Bot,
    ChannelConnection,
    Conversation,
    Incident,
    User,
    utc_now,
)


def login_owner(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf(client: TestClient, path: str = "/bots") -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def create_conversation() -> int:
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="LINE",
            webhook_key="audit-line",
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        conversation = Conversation(
            bot_id=bot.id,
            channel_connection_id=connection.id,
            external_user_id="audit-user",
            status="needs_human",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        assert conversation.id is not None
        return conversation.id


def create_incident() -> int:
    with Session(get_engine()) as session:
        incident = Incident(
            severity="warning",
            source_type="system",
            source_id="audit-checkpoint",
            incident_type="audit_checkpoint",
            deduplication_key="system:audit-checkpoint:audit_checkpoint",
            status="open",
            details_json="{}",
            affected_bot_ids_json="[]",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        assert incident.id is not None
        return incident.id


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


def test_audit_redacts_sensitive_nested_keys(client) -> None:
    value = {
        "name": "RAG",
        "credential": {"api_key": "super-secret", "endpoint": "https://rag.test"},
        "headers": {"Authorization": "Bearer hidden"},
        "nested": [{"BoT_ToKeN": "token-secret"}],
    }

    sanitized = sanitize_audit_value(value)

    assert sanitized["credential"]["api_key"] == "[REDACTED]"
    assert sanitized["headers"]["Authorization"] == "[REDACTED]"
    assert sanitized["nested"][0]["BoT_ToKeN"] == "[REDACTED]"
    encoded = json.dumps(sanitized)
    assert "super-secret" not in encoded
    assert "Bearer hidden" not in encoded
    assert "token-secret" not in encoded


def test_record_audit_sanitizes_before_after_and_request_metadata(client) -> None:
    actor = CurrentUser(
        id=1,
        email="admin@example.local",
        role="owner",
        allowed_bot_ids=frozenset(),
    )
    with Session(get_engine()) as session:
        row = record_audit(
            session,
            actor,
            action="knowledge.credential_replace",
            object_type="knowledge_service",
            object_id="7",
            summary="Replaced Knowledge Service credential",
            before={"api_key": "old-secret", "name": "RAG"},
            after={"api_key": "new-secret", "name": "RAG"},
            request_metadata={"Authorization": "Bearer hidden", "method": "POST"},
        )
        assert row.id is not None

    with Session(get_engine()) as session:
        stored = session.get(AuditEvent, row.id)
        assert stored is not None
        combined = stored.before_json + stored.after_json + stored.request_metadata_json
        assert "old-secret" not in combined
        assert "new-secret" not in combined
        assert "Bearer hidden" not in combined
        assert "[REDACTED]" in combined


def test_representative_admin_mutations_create_audit_rows(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_owner(client)

    from chatbot_manager.admin import test_center as test_center_module

    async def fake_publish(self, bot_id, actor_email, *, acknowledge_warnings=False):
        return None

    monkeypatch.setattr(
        test_center_module.PublishService,
        "publish",
        fake_publish,
    )

    token = csrf(client)
    user_response = client.post(
        "/users",
        data={
            "csrf_token": token,
            "email": "audit-operator@example.local",
            "password": "audit-password-secret",
            "role": "operator",
        },
        follow_redirects=False,
    )
    assert user_response.status_code == 303

    knowledge_create = client.post(
        "/knowledge-services",
        data={
            "csrf_token": token,
            "name": "Audit RAG",
            "api_base_url": "https://audit-rag.test",
            "webui_url": "",
            "api_key": "original-api-secret",
        },
        follow_redirects=False,
    )
    assert knowledge_create.status_code == 303
    with Session(get_engine()) as session:
        from chatbot_manager.models import KnowledgeService

        service = session.exec(
            select(KnowledgeService).where(KnowledgeService.name == "Audit RAG")
        ).one()
        assert service.id is not None
        service_id = service.id

    knowledge_update = client.post(
        f"/knowledge-services/{service_id}/update",
        data={
            "csrf_token": token,
            "name": "Audit RAG",
            "api_base_url": "https://audit-rag.test",
            "webui_url": "",
            "api_key": "replacement-api-secret",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert knowledge_update.status_code == 303

    publish = client.post(
        "/bots/1/test/publish",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert publish.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, 1)
        assert bot is not None
        bot.lifecycle_status = "active"
        session.add(bot)
        session.commit()

    pause = client.post(
        "/bots/1/pause",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert pause.status_code == 303

    conversation_id = create_conversation()
    assigned = client.post(
        f"/conversations/{conversation_id}/assign",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert assigned.status_code == 303

    incident_id = create_incident()
    acknowledged = client.post(
        f"/incidents/{incident_id}/acknowledge",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert acknowledged.status_code == 303

    with Session(get_engine()) as session:
        rows = session.exec(select(AuditEvent)).all()
        actions = {row.action for row in rows}
        serialized = "\n".join(
            row.before_json + row.after_json + row.request_metadata_json
            for row in rows
        )

    assert {
        "bot.publish",
        "bot.pause",
        "knowledge.create",
        "knowledge.update",
        "knowledge.credential_replace",
        "user.create",
        "conversation.assign",
        "incident.acknowledge",
    } <= actions
    assert "audit-password-secret" not in serialized
    assert "original-api-secret" not in serialized
    assert "replacement-api-secret" not in serialized
    assert "audit-user" not in serialized


def test_audit_page_filters_and_is_manager_only(client: TestClient) -> None:
    login_owner(client)
    with Session(get_engine()) as session:
        owner = session.exec(
            select(User).where(User.email == "admin@example.local")
        ).one()
        assert owner.id is not None
        actor = CurrentUser(
            id=owner.id,
            email=owner.email,
            role=owner.role,
            allowed_bot_ids=frozenset(),
        )
        record_audit(
            session,
            actor,
            action="user.create",
            object_type="user",
            object_id="11",
            summary="Created operator",
            after={"email": "new@example.local", "password": "hidden"},
        )
        record_audit(
            session,
            actor,
            action="bot.pause",
            object_type="bot",
            object_id="1",
            bot_id=1,
            summary="Paused bot",
            after={"status": "paused"},
        )

    page = client.get("/audit?action=bot.pause&actor=admin@example.local&bot_id=1")
    assert page.status_code == 200
    assert "bot.pause" in page.text
    assert "user.create" not in page.text
    assert "admin@example.local" in page.text
    assert "[REDACTED]" not in page.text

    with Session(get_engine()) as session:
        admin = User(
            email="audit-admin@example.local",
            password_hash=password_hasher.hash("password-123"),
            role="admin",
            active=True,
        )
        session.add(admin)
        session.commit()
    client.post(
        "/login",
        data={"email": "audit-admin@example.local", "password": "password-123"},
        follow_redirects=False,
    )
    assert client.get("/audit").status_code == 200

    with Session(get_engine()) as session:
        operator = User(
            email="audit-operator2@example.local",
            password_hash=password_hasher.hash("password-123"),
            role="operator",
            active=True,
        )
        session.add(operator)
        session.commit()
    client.post(
        "/login",
        data={"email": "audit-operator2@example.local", "password": "password-123"},
        follow_redirects=False,
    )
    assert client.get("/audit").status_code == 403


def test_audit_date_filter_excludes_old_rows(client: TestClient) -> None:
    login_owner(client)
    with Session(get_engine()) as session:
        old = AuditEvent(
            actor_user_id=None,
            action="old.action",
            object_type="system",
            object_id="old",
            summary="Old audit",
            created_at=utc_now() - timedelta(days=30),
        )
        session.add(old)
        session.commit()

    today = utc_now().date().isoformat()
    page = client.get(f"/audit?from={today}")
    assert page.status_code == 200
    assert "old.action" not in page.text
