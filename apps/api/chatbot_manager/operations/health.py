from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session, select

from chatbot_manager.knowledge.client import KnowledgeHealth, KnowledgeServiceClient
from chatbot_manager.knowledge.service import build_knowledge_client
from chatbot_manager.models import (
    Bot,
    BotConfigVersion,
    ChannelConnection,
    KnowledgeService,
    utc_now,
)


@dataclass(frozen=True)
class HealthSignal:
    source_type: str
    source_id: str
    status: str
    severity: str | None
    code: str
    latency_ms: int | None = None
    affected_bot_ids: tuple[int, ...] = ()


class HealthService:
    def __init__(
        self,
        session: Session,
        knowledge_client_factory: Callable[
            [Session, int], KnowledgeServiceClient
        ] = build_knowledge_client,
    ) -> None:
        self._session = session
        self._knowledge_client_factory = knowledge_client_factory

    async def check_system(self) -> list[HealthSignal]:
        try:
            self._session.exec(text("SELECT 1")).one()
        except Exception:
            return [
                HealthSignal(
                    "system",
                    "database",
                    "unavailable",
                    "critical",
                    "database_unavailable",
                )
            ]
        return [HealthSignal("system", "database", "healthy", None, "database_ok")]

    async def check_knowledge_services(self) -> list[HealthSignal]:
        signals: list[HealthSignal] = []
        for service in self._session.exec(
            select(KnowledgeService).where(KnowledgeService.enabled == True)  # noqa: E712
        ).all():
            if service.id is None:
                continue
            affected_bot_ids = self._affected_bot_ids(service.id)
            try:
                client = self._knowledge_client_factory(self._session, service.id)
                result = await client.health()
            except Exception:
                result = KnowledgeHealth(
                    "unavailable",
                    None,
                    "knowledge_service_unavailable",
                )
            signals.append(
                self._knowledge_signal(service.id, result, affected_bot_ids)
            )
        return signals

    async def check_channels(self) -> list[HealthSignal]:
        return [
            self._channel_signal(connection)
            for connection in self._session.exec(select(ChannelConnection)).all()
            if connection.id is not None
        ]

    def _affected_bot_ids(self, knowledge_service_id: int) -> tuple[int, ...]:
        bot_ids: list[int] = []
        for bot in self._session.exec(select(Bot)).all():
            if bot.id is None or bot.live_config_version_id is None:
                continue
            live = self._session.get(BotConfigVersion, bot.live_config_version_id)
            if live is not None and live.knowledge_service_id == knowledge_service_id:
                bot_ids.append(bot.id)
        return tuple(sorted(bot_ids))

    @staticmethod
    def _knowledge_signal(
        service_id: int,
        result: KnowledgeHealth,
        affected_bot_ids: tuple[int, ...],
    ) -> HealthSignal:
        if result.status == "healthy":
            return HealthSignal(
                "knowledge_service",
                str(service_id),
                "healthy",
                None,
                result.detail_code or "knowledge_service_healthy",
                result.latency_ms,
                affected_bot_ids,
            )
        if result.status in {"unavailable", "unauthorized", "invalid"}:
            return HealthSignal(
                "knowledge_service",
                str(service_id),
                "unavailable",
                "critical",
                result.detail_code or f"knowledge_service_{result.status}",
                result.latency_ms,
                affected_bot_ids,
            )
        return HealthSignal(
            "knowledge_service",
            str(service_id),
            "unknown",
            "warning",
            "knowledge_service_unknown",
            result.latency_ms,
            affected_bot_ids,
        )

    @staticmethod
    def _channel_signal(connection: ChannelConnection) -> HealthSignal:
        affected = (connection.bot_id,)
        if not connection.enabled:
            return HealthSignal(
                "channel",
                str(connection.id),
                "unknown",
                None,
                "channel_disabled",
                affected_bot_ids=affected,
            )
        if connection.status != "ready":
            return HealthSignal(
                "channel",
                str(connection.id),
                "degraded",
                "warning",
                "channel_not_ready",
                affected_bot_ids=affected,
            )

        metadata = HealthService._metadata(connection.metadata_json)
        if metadata.get("last_error"):
            return HealthSignal(
                "channel",
                str(connection.id),
                "degraded",
                "warning",
                "channel_last_error",
                affected_bot_ids=affected,
            )
        if HealthService._has_recent_activity(metadata):
            return HealthSignal(
                "channel",
                str(connection.id),
                "unknown",
                None,
                "channel_recent_activity",
                affected_bot_ids=affected,
            )
        return HealthSignal(
            "channel",
            str(connection.id),
            "unknown",
            None,
            "channel_configured_no_active_probe",
            affected_bot_ids=affected,
        )

    @staticmethod
    def _metadata(raw: str) -> dict[str, object]:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _has_recent_activity(metadata: dict[str, object]) -> bool:
        cutoff = utc_now() - timedelta(hours=1)
        for key in ("last_inbound_at", "last_outbound_at"):
            raw = metadata.get(key)
            if not isinstance(raw, str):
                continue
            try:
                timestamp = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
            if timestamp >= cutoff:
                return True
        return False
