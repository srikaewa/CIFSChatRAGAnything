from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.models import Bot, Incident, utc_now


@dataclass(frozen=True)
class AlertResult:
    sent: bool
    code: str


class AlertPolicy:
    def __init__(
        self,
        warning_persist_minutes: int = 15,
        cooldown_minutes: int = 15,
    ) -> None:
        self._warning_persist = timedelta(minutes=warning_persist_minutes)
        self._cooldown = timedelta(minutes=cooldown_minutes)

    def should_notify(self, incident: Incident, now: datetime) -> bool:
        if incident.status == "resolved":
            return (
                incident.resolved_at is not None
                and incident.external_notified_at is not None
                and incident.external_notified_at < incident.resolved_at
            )

        if incident.severity == "critical":
            eligible = True
        elif incident.severity == "warning":
            eligible = now - incident.first_seen_at >= self._warning_persist
        else:
            return False

        if not eligible:
            return False
        return (
            incident.external_notified_at is None
            or now - incident.external_notified_at >= self._cooldown
        )


class TelegramAlertService:
    def __init__(
        self,
        session: Session,
        *,
        bot_token: str,
        chat_id: str,
        policy: AlertPolicy | None = None,
    ) -> None:
        self._session = session
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._policy = policy or AlertPolicy()

    async def notify_incident(self, incident: Incident) -> AlertResult:
        now = utc_now()
        if not self._policy.should_notify(incident, now):
            return AlertResult(False, "suppressed")
        if not self._bot_token or not self._chat_id:
            return AlertResult(False, "not_configured")

        try:
            await TelegramAdapter(self._bot_token).send_reply(
                {"chat_id": self._chat_id},
                self._message(incident),
            )
        except Exception:
            return AlertResult(False, "telegram_alert_failed")

        if incident.status == "resolved" and incident.resolved_at is not None:
            incident.external_notified_at = max(now, incident.resolved_at)
        else:
            incident.external_notified_at = now
        self._session.add(incident)
        self._session.commit()
        self._session.refresh(incident)
        return AlertResult(True, "sent")

    def _message(self, incident: Incident) -> str:
        recovery = incident.status == "resolved"
        heading = "RECOVERED" if recovery else incident.severity.upper()
        bots = ", ".join(self._affected_bot_names(incident)) or "None"
        return "\n".join(
            (
                f"{heading} incident",
                f"Type: {incident.incident_type}",
                f"Source: {incident.source_type}/{incident.source_id}",
                f"Affected Bots: {bots}",
                f"Started: {incident.first_seen_at.isoformat()}",
                f"Admin: /incidents/{incident.id}",
            )
        )

    def _affected_bot_names(self, incident: Incident) -> list[str]:
        try:
            raw_ids = json.loads(incident.affected_bot_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(raw_ids, list):
            return []

        names: list[str] = []
        for raw_id in raw_ids:
            if not isinstance(raw_id, int):
                continue
            bot = self._session.get(Bot, raw_id)
            names.append(bot.name if bot is not None else f"Bot {raw_id}")
        return names
