from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.auth.authorization import require_role
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot, Incident
from chatbot_manager.operations.incidents import IncidentService


router = APIRouter(
    prefix="/incidents",
    dependencies=[Depends(require_role("owner", "admin"))],
)


def _incident(session: Session, incident_id: int) -> Incident:
    row = session.get(Incident, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return row


def _affected_bots(session: Session, row: Incident) -> list[Bot]:
    try:
        ids = json.loads(row.affected_bot_ids_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(ids, list):
        return []
    return [
        bot
        for bot_id in ids
        if isinstance(bot_id, int)
        and (bot := session.get(Bot, bot_id)) is not None
    ]


@router.get("", response_class=HTMLResponse)
def incidents_page(
    request: Request,
    severity: str = Query(default=""),
    status: str = Query(default=""),
    source: str = Query(default=""),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    statement = select(Incident).order_by(
        Incident.first_seen_at.desc(),
        Incident.id.desc(),
    )
    if severity:
        statement = statement.where(Incident.severity == severity)
    if status:
        statement = statement.where(Incident.status == status)
    if source:
        statement = statement.where(Incident.source_type == source)
    rows = list(session.exec(statement).all())
    return templates.TemplateResponse(
        request,
        "incidents.html",
        {
            "admin_email": admin_email,
            "active_page": "incidents",
            "incidents": rows,
            "filters": {
                "severity": severity,
                "status": status,
                "source": source,
            },
        },
    )


@router.get("/{incident_id}", response_class=HTMLResponse)
def incident_detail(
    incident_id: int,
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    row = _incident(session, incident_id)
    try:
        details = json.loads(row.details_json or "{}")
    except json.JSONDecodeError:
        details = {}
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {
            "admin_email": admin_email,
            "active_page": "incidents",
            "incident": row,
            "affected_bots": _affected_bots(session, row),
            "details": details if isinstance(details, dict) else {},
        },
    )


@router.post("/{incident_id}/acknowledge")
def acknowledge_incident(
    incident_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    IncidentService(session).acknowledge(incident_id, admin_email)
    return RedirectResponse(f"/incidents/{incident_id}", status_code=303)
