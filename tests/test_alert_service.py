import json
from datetime import datetime, timedelta

import pytest
import respx
from httpx import Response
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from chatbot_manager.channels.telegram import TELEGRAM_API
from chatbot_manager.models import Bot, Incident
from chatbot_manager.operations.alerts import AlertPolicy, TelegramAlertService
from chatbot_manager.settings import Settings


NOW = datetime(2026, 10, 6, 9, 0, 0)


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def incident(
    *,
    severity: str = "critical",
    status: str = "open",
    first_seen_at: datetime = NOW,
    external_notified_at: datetime | None = None,
    resolved_at: datetime | None = None,
    affected_bot_ids: tuple[int, ...] = (),
) -> Incident:
    return Incident(
        severity=severity,
        source_type="knowledge_service",
        source_id="3",
        incident_type="unreachable",
        deduplication_key="unreachable:knowledge_service_3",
        status=status,
        first_seen_at=first_seen_at,
        last_seen_at=NOW,
        resolved_at=resolved_at,
        affected_bot_ids_json=json.dumps(list(affected_bot_ids)),
        external_notified_at=external_notified_at,
    )


def test_critical_notifies_immediately() -> None:
    assert AlertPolicy(warning_persist_minutes=15).should_notify(incident(), NOW) is True


def test_warning_waits_until_persistence_threshold() -> None:
    policy = AlertPolicy(warning_persist_minutes=15)
    recent = incident(
        severity="warning",
        first_seen_at=NOW - timedelta(minutes=5),
    )
    persistent = incident(
        severity="warning",
        first_seen_at=NOW - timedelta(minutes=16),
    )

    assert policy.should_notify(recent, NOW) is False
    assert policy.should_notify(persistent, NOW) is True


def test_info_does_not_notify_telegram() -> None:
    assert AlertPolicy().should_notify(incident(severity="info"), NOW) is False


def test_cooldown_suppresses_duplicate_active_notification() -> None:
    policy = AlertPolicy(cooldown_minutes=15)
    recently_notified = incident(
        external_notified_at=NOW - timedelta(minutes=5),
    )
    cooled_down = incident(
        external_notified_at=NOW - timedelta(minutes=16),
    )

    assert policy.should_notify(recently_notified, NOW) is False
    assert policy.should_notify(cooled_down, NOW) is True


def test_recovery_only_notifies_once_after_external_incident_notification() -> None:
    policy = AlertPolicy()
    never_notified = incident(
        status="resolved",
        resolved_at=NOW - timedelta(minutes=1),
    )
    recovery_pending = incident(
        status="resolved",
        resolved_at=NOW - timedelta(minutes=1),
        external_notified_at=NOW - timedelta(minutes=5),
    )
    recovery_already_sent = incident(
        status="resolved",
        resolved_at=NOW - timedelta(minutes=5),
        external_notified_at=NOW - timedelta(minutes=1),
    )

    assert policy.should_notify(never_notified, NOW) is False
    assert policy.should_notify(recovery_pending, NOW) is True
    assert policy.should_notify(recovery_already_sent, NOW) is False


@pytest.mark.asyncio
@respx.mock
async def test_sender_includes_incident_context_bot_names_and_relative_route() -> None:
    route = respx.post(f"{TELEGRAM_API}ops-token/sendMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        first = Bot(name="Admissions Bot")
        second = Bot(name="Scholarship Bot")
        session.add_all([first, second])
        session.commit()
        session.refresh(first)
        session.refresh(second)
        assert first.id is not None
        assert second.id is not None

        row = incident(affected_bot_ids=(first.id, second.id))
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None

        result = await TelegramAlertService(
            session,
            bot_token="ops-token",
            chat_id="-100123",
        ).notify_incident(row)

        assert result.sent is True
        assert result.code == "sent"
        request = route.calls[0].request
        body = request.content.decode()
        assert "CRITICAL" in body
        assert "unreachable" in body
        assert "knowledge_service/3" in body
        assert "Admissions Bot" in body
        assert "Scholarship Bot" in body
        assert f"/incidents/{row.id}" in body
        assert "2026-10-06" in body
        assert row.external_notified_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_sender_cooldown_and_recovery_each_emit_at_most_once() -> None:
    route = respx.post(f"{TELEGRAM_API}ops-token/sendMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = incident()
        session.add(row)
        session.commit()
        session.refresh(row)

        service = TelegramAlertService(
            session,
            bot_token="ops-token",
            chat_id="-100123",
        )
        first = await service.notify_incident(row)
        duplicate = await service.notify_incident(row)

        assert first.sent is True
        assert duplicate.sent is False
        assert duplicate.code == "suppressed"
        assert route.call_count == 1

        notified_at = row.external_notified_at
        assert notified_at is not None
        row.status = "resolved"
        row.resolved_at = notified_at + timedelta(seconds=1)
        session.add(row)
        session.commit()
        session.refresh(row)

        recovery = await service.notify_incident(row)
        duplicate_recovery = await service.notify_incident(row)

        assert recovery.sent is True
        assert duplicate_recovery.sent is False
        assert duplicate_recovery.code == "suppressed"
        assert route.call_count == 2
        assert row.external_notified_at >= row.resolved_at


@pytest.mark.asyncio
@respx.mock
async def test_sender_failure_returns_stable_error_without_token_leak() -> None:
    secret = "super-secret-ops-token"
    respx.post(f"{TELEGRAM_API}{secret}/sendMessage").mock(
        return_value=Response(500, text="upstream private detail")
    )
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = incident()
        session.add(row)
        session.commit()
        session.refresh(row)

        result = await TelegramAlertService(
            session,
            bot_token=secret,
            chat_id="-100123",
        ).notify_incident(row)

        assert result.sent is False
        assert result.code == "telegram_alert_failed"
        assert secret not in repr(result)
        assert row.external_notified_at is None


def test_operations_telegram_credentials_are_explicit() -> None:
    settings = Settings(
        alert_telegram_bot_token="ops-token",
        alert_telegram_chat_id="-100123",
    )

    assert settings.alert_telegram_bot_token == "ops-token"
    assert settings.alert_telegram_chat_id == "-100123"
    assert not hasattr(settings, "telegram_bot_token")
