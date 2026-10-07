import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from chatbot_manager.knowledge.client import KnowledgeHealth
from chatbot_manager.models import (
    Bot,
    BotConfigVersion,
    ChannelConnection,
    KnowledgeService,
    utc_now,
)
from chatbot_manager.operations.health import HealthService


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def seed_shared_knowledge_service(session: Session) -> KnowledgeService:
    service = KnowledgeService(
        name="Shared RAG",
        api_base_url="https://rag.example.test",
        enabled=True,
    )
    session.add(service)
    session.commit()
    session.refresh(service)
    assert service.id is not None

    for number in range(1, 4):
        bot = Bot(name=f"Bot {number}", lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)
        assert bot.id == number

        live = BotConfigVersion(
            bot_id=bot.id,
            version_number=1,
            status="published",
            knowledge_service_id=service.id,
        )
        session.add(live)
        session.commit()
        session.refresh(live)
        assert live.id is not None

        bot.live_config_version_id = live.id
        session.add(bot)
        session.commit()

    return service


class FakeHealthClient:
    def __init__(self, result: KnowledgeHealth) -> None:
        self.result = result
        self.calls = 0

    async def health(self) -> KnowledgeHealth:
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_shared_rag_outage_reports_one_root_signal_with_all_affected_bots() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        knowledge_service = seed_shared_knowledge_service(session)
        fake = FakeHealthClient(
            KnowledgeHealth(
                status="unavailable",
                latency_ms=321,
                detail_code="knowledge_service_unavailable",
            )
        )
        health = HealthService(
            session,
            knowledge_client_factory=lambda _session, _service_id: fake,
        )

        signals = await health.check_knowledge_services()

        assert len(signals) == 1
        signal = signals[0]
        assert signal.source_type == "knowledge_service"
        assert signal.source_id == str(knowledge_service.id)
        assert signal.status == "unavailable"
        assert signal.severity == "critical"
        assert signal.code == "knowledge_service_unavailable"
        assert signal.latency_ms == 321
        assert signal.affected_bot_ids == (1, 2, 3)
        assert fake.calls == 1


@pytest.mark.parametrize(
    ("upstream_status", "detail_code", "expected_status", "expected_severity", "expected_code"),
    [
        ("healthy", "", "healthy", None, "knowledge_service_healthy"),
        (
            "unauthorized",
            "knowledge_service_unauthorized",
            "unavailable",
            "critical",
            "knowledge_service_unauthorized",
        ),
        (
            "invalid",
            "knowledge_service_http_error",
            "unavailable",
            "critical",
            "knowledge_service_http_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_knowledge_health_maps_to_stable_operational_signal(
    upstream_status: str,
    detail_code: str,
    expected_status: str,
    expected_severity: str | None,
    expected_code: str,
) -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        knowledge_service = KnowledgeService(
            name="RAG",
            api_base_url="https://rag.example.test",
            enabled=True,
        )
        session.add(knowledge_service)
        session.commit()
        session.refresh(knowledge_service)
        assert knowledge_service.id is not None

        fake = FakeHealthClient(
            KnowledgeHealth(
                status=upstream_status,
                latency_ms=12,
                detail_code=detail_code,
            )
        )
        health = HealthService(
            session,
            knowledge_client_factory=lambda _session, _service_id: fake,
        )

        signal = (await health.check_knowledge_services())[0]

        assert signal.status == expected_status
        assert signal.severity == expected_severity
        assert signal.code == expected_code
        assert signal.latency_ms == 12


@pytest.mark.asyncio
async def test_recent_channel_activity_is_not_called_active_health_probe() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Channel Bot", lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)
        assert bot.id is not None

        now = utc_now()
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="LINE",
            webhook_key="health-line",
            enabled=True,
            status="ready",
            metadata_json=json.dumps(
                {
                    "last_inbound_at": (now - timedelta(minutes=2)).isoformat(),
                    "last_outbound_at": (now - timedelta(minutes=1)).isoformat(),
                }
            ),
        )
        session.add(connection)
        session.commit()

        signal = (await HealthService(session).check_channels())[0]

        assert signal.status in {"unknown", "degraded"}
        assert signal.status != "healthy"
        assert signal.severity is None
        assert signal.code == "channel_recent_activity"
        assert signal.affected_bot_ids == (bot.id,)


@pytest.mark.asyncio
async def test_offset_aware_stale_channel_activity_is_not_treated_as_recent() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Offset Bot", lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)
        assert bot.id is not None

        stale_utc = utc_now().replace(tzinfo=timezone.utc) - timedelta(hours=2)
        local_plus_seven = stale_utc.astimezone(timezone(timedelta(hours=7)))
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="LINE",
            webhook_key="health-offset-line",
            enabled=True,
            status="ready",
            metadata_json=json.dumps({"last_inbound_at": local_plus_seven.isoformat()}),
        )
        session.add(connection)
        session.commit()

        signal = (await HealthService(session).check_channels())[0]

        assert signal.code == "channel_configured_no_active_probe"


@pytest.mark.asyncio
async def test_database_check_reports_healthy_only_after_successful_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        health = HealthService(session)
        healthy = (await health.check_system())[0]

        assert healthy.source_type == "system"
        assert healthy.source_id == "database"
        assert healthy.status == "healthy"
        assert healthy.severity is None
        assert healthy.code == "database_ok"

        def fail_exec(*_args, **_kwargs):
            raise RuntimeError("secret database detail")

        monkeypatch.setattr(session, "exec", fail_exec)
        unavailable = (await health.check_system())[0]

        assert unavailable.status == "unavailable"
        assert unavailable.severity == "critical"
        assert unavailable.code == "database_unavailable"
        assert "secret database detail" not in repr(unavailable)
