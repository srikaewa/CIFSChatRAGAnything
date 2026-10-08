from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlmodel import Session, select

from chatbot_manager.models import BotDecision, Conversation, Incident, utc_now
from chatbot_manager.settings import Settings

from .alerts import AlertPolicy, TelegramAlertService
from .health import HealthService, HealthSignal
from .incidents import IncidentService, IncidentSignal
from .metrics import MetricService


logger = logging.getLogger(__name__)


class OperationsScheduler:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        health_service: HealthService | None = None,
        incident_service: IncidentService | None = None,
        alert_service: TelegramAlertService | None = None,
        now_factory: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._settings = settings
        self._health = health_service or HealthService(session)
        self._incidents = incident_service or IncidentService(session)
        self._alerts = alert_service or TelegramAlertService(
            session,
            bot_token=settings.alert_telegram_bot_token,
            chat_id=settings.alert_telegram_chat_id,
            policy=AlertPolicy(
                warning_persist_minutes=settings.warning_persist_minutes,
            ),
        )
        self._now = now_factory
        self._poll_seconds = settings.ops_poll_seconds

    async def run_once(self) -> None:
        for component, checker in (
            ("health_system", self._health.check_system),
            ("health_knowledge_services", self._health.check_knowledge_services),
            ("health_channels", self._health.check_channels),
        ):
            try:
                signals = await checker()
            except Exception:
                logger.error("operations_scheduler_component_failed component=%s", component)
                continue
            for signal in signals:
                try:
                    await self._process_health_signal(signal)
                except Exception:
                    logger.error(
                        "operations_scheduler_component_failed component=health_signal"
                    )

        try:
            await self._evaluate_human_queue()
        except Exception:
            logger.error(
                "operations_scheduler_component_failed component=human_queue_metrics"
            )

        try:
            await self._evaluate_rag_latency()
        except Exception:
            logger.error(
                "operations_scheduler_component_failed component=rag_latency_metrics"
            )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("operations_scheduler_component_failed component=iteration")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _process_health_signal(self, signal: HealthSignal) -> None:
        if signal.status == "healthy":
            await self._resolve_source(signal.source_type, signal.source_id)
            return
        if (
            signal.source_type == "channel"
            and signal.status == "unknown"
            and signal.code
            in {"channel_recent_activity", "channel_configured_no_active_probe"}
        ):
            await self._resolve_exact(
                signal.source_type, signal.source_id, "channel_not_ready"
            )
            return
        if signal.status == "unknown" or signal.severity not in {"warning", "critical"}:
            return

        self._resolve_other_source_incidents(
            signal.source_type,
            signal.source_id,
            keep_incident_type=signal.code,
        )
        incident = self._incidents.observe(
            IncidentSignal(
                source_type=signal.source_type,
                source_id=signal.source_id,
                incident_type=signal.code,
                severity=signal.severity,
                details={
                    "status": signal.status,
                    "latency_ms": signal.latency_ms,
                },
                affected_bot_ids=signal.affected_bot_ids,
            )
        )
        await self._notify(incident)

    async def _evaluate_human_queue(self) -> None:
        now = self._now()
        history_start = datetime(1970, 1, 1)
        warning_count = int(
            MetricService(
                self._session,
                human_wait_warning_minutes=self._settings.human_wait_warning_minutes,
            )
            .summary(history_start, now)
            .operations["human_wait_count"]
            .value
        )
        if warning_count == 0:
            await self._resolve_exact("system", "human_queue", "human_wait_sla")
            return

        critical_count = int(
            MetricService(
                self._session,
                human_wait_warning_minutes=self._settings.human_wait_critical_minutes,
            )
            .summary(history_start, now)
            .operations["human_wait_count"]
            .value
        )
        warning_cutoff = now - timedelta(
            minutes=self._settings.human_wait_warning_minutes
        )
        affected_bot_ids = tuple(
            sorted(
                {
                    row.bot_id
                    for row in self._session.exec(
                        select(Conversation).where(
                            Conversation.status == "needs_human",
                            Conversation.last_message_at <= warning_cutoff,
                        )
                    ).all()
                }
            )
        )
        incident = self._incidents.observe(
            IncidentSignal(
                source_type="system",
                source_id="human_queue",
                incident_type="human_wait_sla",
                severity="critical" if critical_count else "warning",
                details={
                    "warning_count": warning_count,
                    "critical_count": critical_count,
                },
                affected_bot_ids=affected_bot_ids,
            )
        )
        await self._notify(incident)

    async def _evaluate_rag_latency(self) -> None:
        now = self._now()
        cutoff = now - timedelta(seconds=self._poll_seconds)
        rows = list(
            self._session.exec(
                select(BotDecision).where(
                    BotDecision.created_at >= cutoff,
                    BotDecision.created_at <= now,
                    BotDecision.retrieval_latency_ms.is_not(None),
                )
            ).all()
        )
        if not rows:
            await self._resolve_exact("system", "rag_latency", "rag_latency_sla")
            return

        maximum = max(row.retrieval_latency_ms or 0 for row in rows)
        if maximum < self._settings.rag_latency_warning_ms:
            await self._resolve_exact("system", "rag_latency", "rag_latency_sla")
            return

        threshold = (
            self._settings.rag_latency_critical_ms
            if maximum >= self._settings.rag_latency_critical_ms
            else self._settings.rag_latency_warning_ms
        )
        affected_bot_ids = tuple(
            sorted(
                {
                    row.bot_id
                    for row in rows
                    if (row.retrieval_latency_ms or 0) >= threshold
                }
            )
        )
        incident = self._incidents.observe(
            IncidentSignal(
                source_type="system",
                source_id="rag_latency",
                incident_type="rag_latency_sla",
                severity=(
                    "critical"
                    if maximum >= self._settings.rag_latency_critical_ms
                    else "warning"
                ),
                details={"max_retrieval_latency_ms": maximum},
                affected_bot_ids=affected_bot_ids,
            )
        )
        await self._notify(incident)

    async def _resolve_source(self, source_type: str, source_id: str) -> None:
        rows = self._active_source_incidents(source_type, source_id)
        for row in rows:
            resolved = self._incidents.resolve(
                row.source_type,
                row.source_id,
                row.incident_type,
            )
            if resolved is not None:
                await self._notify(resolved)

    async def _resolve_exact(
        self,
        source_type: str,
        source_id: str,
        incident_type: str,
    ) -> None:
        resolved = self._incidents.resolve(source_type, source_id, incident_type)
        if resolved is not None:
            await self._notify(resolved)

    def _resolve_other_source_incidents(
        self,
        source_type: str,
        source_id: str,
        *,
        keep_incident_type: str,
    ) -> None:
        for row in self._active_source_incidents(source_type, source_id):
            if row.incident_type != keep_incident_type:
                self._incidents.resolve(
                    row.source_type,
                    row.source_id,
                    row.incident_type,
                )

    def _active_source_incidents(
        self,
        source_type: str,
        source_id: str,
    ) -> list[Incident]:
        return list(
            self._session.exec(
                select(Incident).where(
                    Incident.source_type == source_type,
                    Incident.source_id == source_id,
                    Incident.status.in_(("open", "acknowledged")),
                )
            ).all()
        )

    async def _notify(self, incident: Incident) -> None:
        try:
            await self._alerts.notify_incident(incident)
        except Exception:
            logger.error("operations_scheduler_component_failed component=alert")
