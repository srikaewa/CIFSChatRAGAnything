from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, templates
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
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    now = utc_now()
    start = from_at or datetime(now.year, now.month, now.day)
    end = to_at or now
    if start > end:
        raise HTTPException(status_code=400, detail="analytics_date_range_invalid")

    summary = MetricService(session).summary(start, end, bot_id=bot_id)
    bots = list(session.exec(select(Bot).order_by(Bot.name)).all())
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "admin_email": admin_email,
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
