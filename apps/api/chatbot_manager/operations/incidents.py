from __future__ import annotations

import json
from dataclasses import dataclass

from sqlmodel import Session, select

from chatbot_manager.models import Incident, utc_now


@dataclass(frozen=True)
class IncidentSignal:
    source_type: str
    source_id: str
    incident_type: str
    severity: str
    details: dict[str, object]
    affected_bot_ids: tuple[int, ...] = ()


def incident_key(source_type: str, source_id: str, incident_type: str) -> str:
    return f"{incident_type}:{source_type}_{source_id}"


class IncidentService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def observe(self, signal: IncidentSignal) -> Incident:
        key = incident_key(signal.source_type, signal.source_id, signal.incident_type)
        incident = self._session.exec(
            select(Incident).where(
                Incident.deduplication_key == key,
                Incident.status.in_(("open", "acknowledged")),
            )
        ).first()
        now = utc_now()

        if incident is None:
            incident = Incident(
                severity=signal.severity,
                source_type=signal.source_type,
                source_id=signal.source_id,
                incident_type=signal.incident_type,
                deduplication_key=key,
                first_seen_at=now,
                last_seen_at=now,
            )
        else:
            incident.last_seen_at = now
            incident.severity = signal.severity

        incident.details_json = json.dumps(signal.details, sort_keys=True)
        incident.affected_bot_ids_json = json.dumps(list(signal.affected_bot_ids))
        self._session.add(incident)
        self._session.commit()
        self._session.refresh(incident)
        return incident

    def resolve(
        self,
        source_type: str,
        source_id: str,
        incident_type: str,
    ) -> Incident | None:
        key = incident_key(source_type, source_id, incident_type)
        incident = self._session.exec(
            select(Incident).where(
                Incident.deduplication_key == key,
                Incident.status.in_(("open", "acknowledged")),
            )
        ).first()
        if incident is None:
            return None

        incident.status = "resolved"
        incident.resolved_at = utc_now()
        self._session.add(incident)
        self._session.commit()
        self._session.refresh(incident)
        return incident

    def acknowledge(self, incident_id: int, actor: str) -> Incident:
        del actor
        incident = self._session.get(Incident, incident_id)
        if incident is None:
            raise LookupError("incident_not_found")

        if incident.status == "open":
            incident.status = "acknowledged"
            self._session.add(incident)
            self._session.commit()
            self._session.refresh(incident)
        return incident
