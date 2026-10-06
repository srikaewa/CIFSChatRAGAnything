import json
from datetime import datetime, time

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import templates
from chatbot_manager.auth.authorization import CurrentUser, require_current_user, require_role
from chatbot_manager.db import get_session
from chatbot_manager.models import AuditEvent, Bot, User


router = APIRouter(
    prefix="/audit",
    dependencies=[Depends(require_role("owner", "admin"))],
)


def _decode(value: str) -> object:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


@router.get("", response_class=HTMLResponse)
def audit_page(
    request: Request,
    action: str = Query(default=""),
    actor: str = Query(default=""),
    bot_id: int | None = Query(default=None),
    from_date: str = Query(default="", alias="from"),
    to_date: str = Query(default="", alias="to"),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    statement = select(AuditEvent).order_by(
        AuditEvent.created_at.desc(),
        AuditEvent.id.desc(),
    )
    if action:
        statement = statement.where(AuditEvent.action == action)
    if bot_id is not None:
        statement = statement.where(AuditEvent.bot_id == bot_id)
    if actor:
        actor_user = session.exec(
            select(User).where(User.email == actor.strip().lower())
        ).first()
        if actor_user is None or actor_user.id is None:
            rows = []
        else:
            statement = statement.where(AuditEvent.actor_user_id == actor_user.id)
            rows = list(session.exec(statement).all())
    else:
        rows = list(session.exec(statement).all())

    if from_date:
        try:
            lower = datetime.combine(datetime.fromisoformat(from_date).date(), time.min)
            rows = [row for row in rows if row.created_at >= lower]
        except ValueError:
            rows = []
    if to_date:
        try:
            upper = datetime.combine(datetime.fromisoformat(to_date).date(), time.max)
            rows = [row for row in rows if row.created_at <= upper]
        except ValueError:
            rows = []

    user_ids = {row.actor_user_id for row in rows if row.actor_user_id is not None}
    users = {
        user.id: user.email
        for user in session.exec(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}
    rendered = [
        {
            "event": row,
            "actor_email": users.get(row.actor_user_id, "System"),
            "before": _decode(row.before_json),
            "after": _decode(row.after_json),
        }
        for row in rows
    ]
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "admin_email": current_user.email,
            "active_page": "audit",
            "rows": rendered,
            "bots": list(session.exec(select(Bot).order_by(Bot.name)).all()),
            "filters": {
                "action": action,
                "actor": actor,
                "bot_id": bot_id or "",
                "from": from_date,
                "to": to_date,
            },
        },
    )
