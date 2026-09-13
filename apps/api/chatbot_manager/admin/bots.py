from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, templates
from chatbot_manager.bots.service import get_live_config
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot


router = APIRouter(prefix="/bots")


@router.get("", response_class=HTMLResponse)
def bots_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bots = session.exec(select(Bot).order_by(Bot.name)).all()
    return templates.TemplateResponse(
        request,
        "bots.html",
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "bots": bots,
        },
    )


@router.get("/{bot_id}", response_class=HTMLResponse)
def bot_overview(
    bot_id: int,
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    live = get_live_config(session, bot_id) if bot.live_config_version_id else None
    return templates.TemplateResponse(
        request,
        "bot_overview.html",
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "bot": bot,
            "live": live,
        },
    )
