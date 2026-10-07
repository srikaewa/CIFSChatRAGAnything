from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import templates
from chatbot_manager.auth.authorization import (
    CurrentUser,
    require_bot_access,
    require_current_user,
)
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot, utc_now
from chatbot_manager.operations.metrics import MetricService


router = APIRouter(prefix="/analytics")


@router.get("", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    bot_id: int | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    now = utc_now()
    start = from_at or datetime(now.year, now.month, now.day)
    end = to_at or now
    if start > end:
        raise HTTPException(status_code=400, detail="analytics_date_range_invalid")
    if current_user.role == "operator":
        if bot_id is None:
            raise HTTPException(status_code=403, detail="Forbidden")
        require_bot_access(bot_id, current_user, session)

    summary = MetricService(session).summary(start, end, bot_id=bot_id)
    bot_statement = select(Bot).order_by(Bot.name)
    if current_user.role == "operator":
        bot_statement = bot_statement.where(
            Bot.id.in_(current_user.allowed_bot_ids)
        )
    bots = list(session.exec(bot_statement).all())
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "admin_email": current_user.email,
            "active_page": "analytics",
            "summary": summary,
            "bots": bots,
            "selected_bot_id": bot_id,
            "start": start,
            "end": end,
            "operations_labels": {
                "message_count": "Messages",
                "avg_response_ms": "Average response (ms)",
                "human_wait_count": "Human queue",
            },
            "quality_labels": {
                "bot_resolution_rate": "Bot resolution rate",
                "fallback_rate": "Fallback rate",
                "escalation_rate": "Escalation rate",
                "rag_failure_rate": "RAG failure rate",
            },
        },
    )
