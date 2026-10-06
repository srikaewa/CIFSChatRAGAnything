import json

from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import Incident
from chatbot_manager.operations.incidents import IncidentService, IncidentSignal, incident_key


def test_same_root_problem_updates_one_open_incident(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        first = service.observe(
            IncidentSignal(
                "knowledge_service",
                "3",
                "unreachable",
                "warning",
                {"error_count": 1},
                (1, 2, 3),
            )
        )
        assert first.id is not None
        first_seen_at = first.first_seen_at

        updated = service.observe(
            IncidentSignal(
                "knowledge_service",
                "3",
                "unreachable",
                "critical",
                {"error_count": 2},
                (1, 2, 3),
            )
        )
        active = session.exec(
            select(Incident).where(
                Incident.deduplication_key
                == incident_key("knowledge_service", "3", "unreachable"),
                Incident.status.in_(("open", "acknowledged")),
            )
        ).all()

        assert updated.id == first.id
        assert updated.first_seen_at == first_seen_at
        assert updated.last_seen_at >= first_seen_at
        assert updated.severity == "critical"
        assert json.loads(updated.details_json) == {"error_count": 2}
        assert json.loads(updated.affected_bot_ids_json) == [1, 2, 3]
        assert [row.id for row in active] == [first.id]


def test_acknowledgement_does_not_resolve_and_persists(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        incident = service.observe(
            IncidentSignal("channel", "7", "unavailable", "critical", {}, (2,))
        )
        assert incident.id is not None
        incident_id = incident.id

        acknowledged = service.acknowledge(incident_id, "admin@example.local")
        observed_again = service.observe(
            IncidentSignal(
                "channel",
                "7",
                "unavailable",
                "critical",
                {"retry": 1},
                (2,),
            )
        )

        assert acknowledged.status == "acknowledged"
        assert acknowledged.resolved_at is None
        assert observed_again.id == incident_id
        assert observed_again.status == "acknowledged"

    with Session(get_engine()) as session:
        persisted = session.get(Incident, incident_id)
        assert persisted is not None
        assert persisted.status == "acknowledged"
        assert json.loads(persisted.details_json) == {"retry": 1}
        assert json.loads(persisted.affected_bot_ids_json) == [2]


def test_recovery_resolves_existing_incident(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        incident = service.observe(
            IncidentSignal("channel", "7", "unavailable", "critical", {}, (2,))
        )
        assert incident.id is not None

        resolved = service.resolve("channel", "7", "unavailable")

        assert resolved is not None
        assert resolved.id == incident.id
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None
        assert resolved.last_seen_at >= resolved.first_seen_at


def test_resolved_incident_does_not_absorb_later_outage(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        first = service.observe(
            IncidentSignal("system", "database", "unavailable", "critical", {}, ())
        )
        assert first.id is not None
        service.resolve("system", "database", "unavailable")

        second = service.observe(
            IncidentSignal(
                "system",
                "database",
                "unavailable",
                "critical",
                {"attempt": 2},
                (),
            )
        )
        rows = session.exec(
            select(Incident)
            .where(
                Incident.deduplication_key
                == incident_key("system", "database", "unavailable")
            )
            .order_by(Incident.id)
        ).all()

        assert second.id is not None
        assert second.id != first.id
        assert [row.status for row in rows] == ["resolved", "open"]
