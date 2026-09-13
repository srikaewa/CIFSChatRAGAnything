from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.bots.service import ensure_draft_config, get_live_config
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot, BotConfigVersion, KnowledgeService


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


def _service_for_config(
    session: Session,
    config: BotConfigVersion | None,
) -> KnowledgeService | None:
    if config is None or config.knowledge_service_id is None:
        return None
    return session.get(KnowledgeService, config.knowledge_service_id)


@router.get("/{bot_id}/knowledge", response_class=HTMLResponse)
def bot_knowledge_page(
    bot_id: int,
    request: Request,
    saved: str | None = None,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    live = get_live_config(session, bot_id) if bot.live_config_version_id else None
    draft = (
        session.get(BotConfigVersion, bot.draft_config_version_id)
        if bot.draft_config_version_id is not None
        else None
    )
    services = session.exec(
        select(KnowledgeService)
        .where(KnowledgeService.enabled == True)  # noqa: E712
        .order_by(KnowledgeService.name)
    ).all()
    live_service = _service_for_config(session, live)
    draft_service = _service_for_config(session, draft)
    selected_service = draft_service or live_service

    return templates.TemplateResponse(
        request,
        "bot_knowledge.html",
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "bot": bot,
            "live": live,
            "draft": draft,
            "services": services,
            "live_service": live_service,
            "draft_service": draft_service,
            "selected_service": selected_service,
            "saved": saved == "1",
            "error": (
                "The selected Knowledge Service is not available."
                if error == "knowledge_service_unavailable"
                else ""
            ),
        },
    )


@router.post("/{bot_id}/knowledge")
def update_bot_knowledge(
    bot_id: int,
    knowledge_service_id: int = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    service = session.get(KnowledgeService, knowledge_service_id)
    if service is None or not service.enabled:
        return RedirectResponse(
            f"/bots/{bot_id}/knowledge?error=knowledge_service_unavailable",
            status_code=303,
        )

    draft = ensure_draft_config(session, bot_id, admin_email)
    draft.knowledge_service_id = service.id
    session.add(draft)
    session.commit()

    return RedirectResponse(f"/bots/{bot_id}/knowledge?saved=1", status_code=303)
