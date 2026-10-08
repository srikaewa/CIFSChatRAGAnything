import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import BotDecision, Conversation, Incident
from chatbot_manager.operations.alerts import AlertResult
from chatbot_manager.operations.health import HealthSignal
from chatbot_manager.operations.incidents import IncidentService, IncidentSignal
from chatbot_manager.operations.scheduler import OperationsScheduler
from chatbot_manager.settings import Settings


NOW = datetime(2026, 10, 6, 10, 0, 0)


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class FakeHealthService:
    def __init__(
        self,
        *,
        system: list[HealthSignal] | Exception | None = None,
        knowledge: list[HealthSignal] | Exception | None = None,
        channels: list[HealthSignal] | Exception | None = None,
    ) -> None:
        self.system = system if system is not None else []
        self.knowledge = knowledge if knowledge is not None else []
        self.channels = channels if channels is not None else []

    async def check_system(self) -> list[HealthSignal]:
        if isinstance(self.system, Exception):
            raise self.system
        return self.system

    async def check_knowledge_services(self) -> list[HealthSignal]:
        if isinstance(self.knowledge, Exception):
            raise self.knowledge
        return self.knowledge

    async def check_channels(self) -> list[HealthSignal]:
        if isinstance(self.channels, Exception):
            raise self.channels
        return self.channels


class FakeAlertService:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[int | None, str]] = []
        self.fail = fail

    async def notify_incident(self, incident: Incident) -> AlertResult:
        self.calls.append((incident.id, incident.status))
        if self.fail:
            raise RuntimeError("provider detail that must not escape")
        return AlertResult(True, "sent")


def settings(**overrides) -> Settings:
    return Settings(
        app_env="test",
        cookie_secure=False,
        ops_poll_seconds=60,
        warning_persist_minutes=15,
        human_wait_warning_minutes=10,
        human_wait_critical_minutes=30,
        rag_latency_warning_ms=3000,
        rag_latency_critical_ms=8000,
        **overrides,
    )


@pytest.mark.asyncio
async def test_run_once_observes_failure_and_notifies_policy() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        alerts = FakeAlertService()
        health = FakeHealthService(
            knowledge=[
                HealthSignal(
                    "knowledge_service",
                    "3",
                    "unavailable",
                    "critical",
                    "knowledge_service_unavailable",
                    latency_ms=321,
                    affected_bot_ids=(1, 2, 3),
                )
            ]
        )
        scheduler = OperationsScheduler(
            session,
            settings(),
            health_service=health,
            alert_service=alerts,
            now_factory=lambda: NOW,
        )

        await scheduler.run_once()

        incidents = session.exec(select(Incident)).all()
        assert len(incidents) == 1
        row = incidents[0]
        assert row.source_type == "knowledge_service"
        assert row.source_id == "3"
        assert row.incident_type == "knowledge_service_unavailable"
        assert row.severity == "critical"
        assert row.status == "open"
        assert alerts.calls == [(row.id, "open")]


@pytest.mark.asyncio
async def test_run_once_resolves_recovered_incident() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = IncidentService(session).observe(
            IncidentSignal(
                source_type="knowledge_service",
                source_id="3",
                incident_type="knowledge_service_unavailable",
                severity="critical",
                details={},
                affected_bot_ids=(1, 2),
            )
        )
        row.external_notified_at = NOW - timedelta(minutes=5)
        session.add(row)
        session.commit()

        alerts = FakeAlertService()
        scheduler = OperationsScheduler(
            session,
            settings(),
            health_service=FakeHealthService(
                knowledge=[
                    HealthSignal(
                        "knowledge_service",
                        "3",
                        "healthy",
                        None,
                        "knowledge_service_healthy",
                        affected_bot_ids=(1, 2),
                    )
                ]
            ),
            alert_service=alerts,
            now_factory=lambda: NOW,
        )

        await scheduler.run_once()

        session.refresh(row)
        assert row.status == "resolved"
        assert row.resolved_at is not None
        assert alerts.calls == [(row.id, "resolved")]


@pytest.mark.parametrize(
    "code",
    ("channel_recent_activity", "channel_configured_no_active_probe"),
)
@pytest.mark.asyncio
async def test_ready_channel_signal_resolves_stale_not_ready_incident(code: str) -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = IncidentService(session).observe(
            IncidentSignal(
                source_type="channel",
                source_id="7",
                incident_type="channel_not_ready",
                severity="warning",
                details={},
                affected_bot_ids=(2,),
            )
        )
        alerts = FakeAlertService()
        scheduler = OperationsScheduler(
            session,
            settings(),
            health_service=FakeHealthService(
                channels=[
                    HealthSignal(
                        "channel",
                        "7",
                        "unknown",
                        None,
                        code,
                        affected_bot_ids=(2,),
                    )
                ]
            ),
            alert_service=alerts,
            now_factory=lambda: NOW,
        )

        await scheduler.run_once()

        session.refresh(row)
        assert row.status == "resolved"
        assert row.resolved_at is not None
        assert alerts.calls == [(row.id, "resolved")]


@pytest.mark.asyncio
async def test_component_failures_do_not_abort_other_health_processing() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        scheduler = OperationsScheduler(
            session,
            settings(),
            health_service=FakeHealthService(
                system=RuntimeError("database probe internal detail"),
                knowledge=[
                    HealthSignal(
                        "knowledge_service",
                        "9",
                        "unavailable",
                        "critical",
                        "knowledge_service_unavailable",
                    )
                ],
            ),
            alert_service=FakeAlertService(fail=True),
            now_factory=lambda: NOW,
        )

        await scheduler.run_once()

        row = session.exec(
            select(Incident).where(Incident.source_id == "9")
        ).one()
        assert row.status == "open"


@pytest.mark.asyncio
async def test_run_once_observes_human_wait_and_rag_latency_thresholds() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Conversation(
                bot_id=2,
                channel_connection_id=1,
                external_user_id="waiting-user",
                status="needs_human",
                started_at=NOW - timedelta(hours=2),
                last_message_at=NOW - timedelta(minutes=31),
            )
        )
        session.add(
            BotDecision(
                message_id=1,
                bot_id=2,
                config_version_id=1,
                decision_type="rag",
                retrieval_latency_ms=9000,
                total_latency_ms=9100,
                created_at=NOW - timedelta(seconds=30),
            )
        )
        session.commit()

        scheduler = OperationsScheduler(
            session,
            settings(),
            health_service=FakeHealthService(),
            alert_service=FakeAlertService(),
            now_factory=lambda: NOW,
        )

        await scheduler.run_once()

        rows = session.exec(select(Incident).order_by(Incident.incident_type)).all()
        by_type = {row.incident_type: row for row in rows}
        assert by_type["human_wait_sla"].severity == "critical"
        assert by_type["rag_latency_sla"].severity == "critical"
        assert by_type["rag_latency_sla"].affected_bot_ids_json == "[2]"


def test_app_lifespan_starts_and_stops_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatbot_manager import db, main
    from chatbot_manager.settings import get_settings

    state = {"started": False, "stop_set": False, "cancelled": False}

    class FakeScheduler:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_forever(self, stop_event: asyncio.Event) -> None:
            state["started"] = True
            try:
                await stop_event.wait()
            except asyncio.CancelledError:
                state["cancelled"] = True
                state["stop_set"] = stop_event.is_set()
                raise

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()
    monkeypatch.setattr(main, "OperationsScheduler", FakeScheduler)

    app = main.create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert state["started"] is True
        assert app.state.operations_scheduler_task is not None

    assert state["stop_set"] is True
    assert state["cancelled"] is True
    db.reset_engine()
    get_settings.cache_clear()


def test_health_endpoint_remains_available_if_scheduler_task_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatbot_manager import db, main
    from chatbot_manager.settings import get_settings

    class FailingScheduler:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_forever(self, _stop_event: asyncio.Event) -> None:
            raise RuntimeError("scheduler internal failure")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()
    monkeypatch.setattr(main, "OperationsScheduler", FailingScheduler)

    app = main.create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    db.reset_engine()
    get_settings.cache_clear()
