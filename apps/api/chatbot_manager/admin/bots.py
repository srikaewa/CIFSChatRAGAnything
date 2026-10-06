import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.audit import record_audit
from chatbot_manager.auth.authorization import (
    CurrentUser,
    require_bot_route_authorization,
    require_current_user,
)
from chatbot_manager.channel_config import CHANNEL_DEFINITIONS
from chatbot_manager.credentials import (
    masked_credential,
    read_credential,
    replace_credential,
    store_credential,
)
from chatbot_manager.bots.service import get_live_config
from chatbot_manager.bots.versions import (
    RuleInput,
    get_draft_config,
    replace_draft_rules,
    update_draft_config,
)
from chatbot_manager.db import get_session
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    ChannelConnection,
    Conversation,
    Incident,
    KnowledgeService,
    utc_now,
)
from chatbot_manager.testing.readiness import ReadinessResult, ReadinessService


router = APIRouter(
    prefix="/bots",
    dependencies=[Depends(require_bot_route_authorization)],
)


def _get_bot(session: Session, bot_id: int) -> Bot:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


def _configs(
    session: Session,
    bot: Bot,
) -> tuple[BotConfigVersion | None, BotConfigVersion | None]:
    live = (
        session.get(BotConfigVersion, bot.live_config_version_id)
        if bot.live_config_version_id is not None
        else None
    )
    draft = (
        session.get(BotConfigVersion, bot.draft_config_version_id)
        if bot.draft_config_version_id is not None
        else None
    )
    return live, draft


def _service_for_config(
    session: Session,
    config: BotConfigVersion | None,
) -> KnowledgeService | None:
    if config is None or config.knowledge_service_id is None:
        return None
    return session.get(KnowledgeService, config.knowledge_service_id)


def _readiness_for_config(
    session: Session,
    bot: Bot,
    config: BotConfigVersion | None,
) -> ReadinessResult | None:
    if bot.id is None or config is None or config.id is None:
        return None
    return ReadinessService(session).evaluate(bot.id, config.id)


def _workspace_context(
    session: Session,
    bot: Bot,
    *,
    active_workspace_tab: str,
) -> dict[str, object]:
    live, draft = _configs(session, bot)
    editable = draft or live
    readiness = _readiness_for_config(session, bot, editable)
    readiness_by_key = (
        {check.key: check for check in readiness.checks} if readiness is not None else {}
    )
    knowledge_service = _service_for_config(session, editable)
    channel_check = readiness_by_key.get("channel_readiness")
    return {
        "bot": bot,
        "live": live,
        "draft": draft,
        "editable": editable,
        "workspace_knowledge_service": knowledge_service,
        "workspace_channel_check": channel_check,
        "readiness": readiness,
        "active_workspace_tab": active_workspace_tab,
    }


def _bot_operations_context(session: Session, bot: Bot) -> dict[str, object]:
    channels = list(
        session.exec(
            select(ChannelConnection).where(ChannelConnection.bot_id == bot.id)
        ).all()
    )
    live, _draft = _configs(session, bot)
    live_service = _service_for_config(session, live)

    active_incidents: list[Incident] = []
    for incident in session.exec(
        select(Incident).where(Incident.status.in_(("open", "acknowledged")))
    ).all():
        try:
            affected = json.loads(incident.affected_bot_ids_json)
        except (json.JSONDecodeError, TypeError):
            affected = []
        if isinstance(affected, list) and bot.id in affected:
            active_incidents.append(incident)

    now = utc_now()
    today_start = datetime(now.year, now.month, now.day)
    activity_today = len(
        session.exec(
            select(Conversation).where(
                Conversation.bot_id == bot.id,
                Conversation.started_at >= today_start,
            )
        ).all()
    )
    if any(row.severity == "critical" for row in active_incidents):
        operational_status = "Critical"
    elif active_incidents:
        operational_status = "Degraded"
    else:
        operational_status = "No active incidents"

    return {
        "operational_status": operational_status,
        "operational_incident_count": len(active_incidents),
        "operational_channels_total": len(channels),
        "operational_channels_ready": sum(
            row.enabled and row.status == "ready" for row in channels
        ),
        "operational_knowledge_service": live_service,
        "operational_activity_today": activity_today,
    }


def _rules_for_config(
    session: Session,
    config: BotConfigVersion | None,
) -> list[BotConfigRule]:
    if config is None or config.id is None:
        return []
    return list(
        session.exec(
            select(BotConfigRule)
            .where(BotConfigRule.config_version_id == config.id)
            .order_by(BotConfigRule.priority, BotConfigRule.id)
        ).all()
    )


def _as_rule_input(rule: BotConfigRule) -> RuleInput:
    return RuleInput(
        name=rule.name,
        enabled=rule.enabled,
        priority=rule.priority,
        match_type=rule.match_type,
        pattern=rule.pattern,
        condition_logic=rule.condition_logic,
        conditions=rule.conditions,
        action=rule.action,
        reply_text=rule.reply_text,
        escalate_message=rule.escalate_message,
    )


@router.get("", response_class=HTMLResponse)
def bots_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    statement = select(Bot).order_by(Bot.name)
    if current_user.role == "operator":
        statement = statement.where(Bot.id.in_(current_user.allowed_bot_ids))
    bots = session.exec(statement).all()
    return templates.TemplateResponse(
        request,
        "bots.html",
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "bots": bots,
        },
    )


@router.post("")
def create_bot(
    name: str = Form(...),
    description: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    normalized_name = name.strip()
    if not normalized_name:
        return RedirectResponse("/bots?error=name_required", status_code=303)

    bot = Bot(
        name=normalized_name,
        description=description.strip(),
        lifecycle_status="draft",
    )
    session.add(bot)
    session.flush()
    if bot.id is None:
        raise HTTPException(status_code=500, detail="Bot could not be persisted")

    draft = BotConfigVersion(
        bot_id=bot.id,
        version_number=1,
        status="draft",
        created_by=admin_email,
    )
    session.add(draft)
    session.flush()
    if draft.id is None:
        raise HTTPException(status_code=500, detail="Draft configuration could not be persisted")

    bot.draft_config_version_id = draft.id
    bot.updated_at = utc_now()
    session.add(bot)
    session.commit()
    return RedirectResponse(f"/bots/{bot.id}/setup", status_code=303)


@router.get("/{bot_id}", response_class=HTMLResponse)
def bot_overview(
    bot_id: int,
    request: Request,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="overview")
    context.update(_bot_operations_context(session, bot))
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "error": error or "",
        }
    )
    return templates.TemplateResponse(request, "bot_overview.html", context)


@router.get("/{bot_id}/knowledge", response_class=HTMLResponse)
def bot_knowledge_page(
    bot_id: int,
    request: Request,
    saved: str | None = None,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="knowledge")
    live = context["live"]
    draft = context["draft"]
    assert live is None or isinstance(live, BotConfigVersion)
    assert draft is None or isinstance(draft, BotConfigVersion)

    services = session.exec(
        select(KnowledgeService)
        .where(KnowledgeService.enabled == True)  # noqa: E712
        .order_by(KnowledgeService.name)
    ).all()
    live_service = _service_for_config(session, live)
    draft_service = _service_for_config(session, draft)
    selected_service = draft_service or live_service

    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
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
        }
    )
    return templates.TemplateResponse(request, "bot_knowledge.html", context)


@router.post("/{bot_id}/knowledge")
def update_bot_knowledge(
    bot_id: int,
    knowledge_service_id: int = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _get_bot(session, bot_id)
    service = session.get(KnowledgeService, knowledge_service_id)
    if service is None or not service.enabled:
        return RedirectResponse(
            f"/bots/{bot_id}/knowledge?error=knowledge_service_unavailable",
            status_code=303,
        )

    update_draft_config(session, bot_id, {"knowledge_service_id": service.id})
    return RedirectResponse(f"/bots/{bot_id}/knowledge?saved=1", status_code=303)


@router.get("/{bot_id}/behavior", response_class=HTMLResponse)
def bot_behavior_page(
    bot_id: int,
    request: Request,
    saved: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="behavior")
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "saved": saved == "1",
        }
    )
    return templates.TemplateResponse(request, "bot_behavior.html", context)


@router.post("/{bot_id}/behavior")
def update_bot_behavior(
    bot_id: int,
    system_prompt: str = Form(""),
    tone: str = Form("professional"),
    language: str = Form("auto"),
    response_style: str = Form("concise"),
    fallback_reply: str = Form(""),
    fallback_policy: str = Form("reply"),
    escalation_policy: str | None = Form(None),
    custom_instructions: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _get_bot(session, bot_id)
    changes: dict[str, object] = {
        "system_prompt": system_prompt,
        "tone": tone,
        "language": language,
        "response_style": response_style,
        "fallback_reply": fallback_reply,
        "fallback_policy": fallback_policy,
        "custom_instructions": custom_instructions,
    }
    if escalation_policy is not None:
        changes["escalation_policy"] = escalation_policy
    update_draft_config(session, bot_id, changes)
    return RedirectResponse(f"/bots/{bot_id}/behavior?saved=1", status_code=303)


@router.get("/{bot_id}/rules", response_class=HTMLResponse)
def bot_rules_page(
    bot_id: int,
    request: Request,
    saved: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="behavior")
    editable = context["editable"]
    assert editable is None or isinstance(editable, BotConfigVersion)
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "rules": _rules_for_config(session, editable),
            "saved": saved == "1",
        }
    )
    return templates.TemplateResponse(request, "bot_rules.html", context)


@router.post("/{bot_id}/rules")
def create_bot_rule(
    bot_id: int,
    name: str = Form(""),
    priority: int = Form(100),
    match_type: str = Form("contains"),
    pattern: str = Form(""),
    action: str = Form("RESPOND"),
    reply_text: str = Form(""),
    escalate_message: str = Form(""),
    enabled: str = Form("true"),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    live, draft = _configs(session, bot)
    source = draft or live
    existing = [_as_rule_input(rule) for rule in _rules_for_config(session, source)]
    existing.append(
        RuleInput(
            name=name.strip(),
            enabled=enabled.strip().lower() not in {"0", "false", "off", "no"},
            priority=priority,
            match_type=match_type,
            pattern=pattern,
            action=action,
            reply_text=reply_text,
            escalate_message=escalate_message,
        )
    )
    replace_draft_rules(session, bot_id, existing)
    return RedirectResponse(f"/bots/{bot_id}/rules?saved=1", status_code=303)


@router.get("/{bot_id}/channels", response_class=HTMLResponse)
def bot_channels_page(
    bot_id: int,
    request: Request,
    saved: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="channels")
    cards: list[dict[str, object]] = []
    for definition in CHANNEL_DEFINITIONS.values():
        connection = session.exec(
            select(ChannelConnection)
            .where(ChannelConnection.bot_id == bot_id)
            .where(ChannelConnection.provider == definition.provider)
        ).first()
        masked = (
            masked_credential(session, connection.credential_id)
            if connection is not None and connection.credential_id is not None
            else {}
        )
        cards.append(
            {
                "definition": definition,
                "connection": connection,
                "masked": masked,
            }
        )
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "channel_cards": cards,
            "saved": saved == "1",
        }
    )
    return templates.TemplateResponse(request, "bot_channels.html", context)


@router.post("/{bot_id}/channels/{provider}")
def update_bot_channel(
    bot_id: int,
    provider: str,
    enabled: str | None = Form(None),
    channel_secret: str = Form(""),
    channel_access_token: str = Form(""),
    verify_token: str = Form(""),
    page_access_token: str = Form(""),
    app_secret: str = Form(""),
    bot_token: str = Form(""),
    webhook_secret: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    del admin_email
    _get_bot(session, bot_id)
    definition = CHANNEL_DEFINITIONS.get(provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="Unknown channel")

    connection = session.exec(
        select(ChannelConnection)
        .where(ChannelConnection.bot_id == bot_id)
        .where(ChannelConnection.provider == provider)
    ).first()
    current = (
        read_credential(session, connection.credential_id)
        if connection is not None and connection.credential_id is not None
        else {}
    )
    incoming = {
        "channel_secret": channel_secret,
        "channel_access_token": channel_access_token,
        "verify_token": verify_token,
        "page_access_token": page_access_token,
        "app_secret": app_secret,
        "bot_token": bot_token,
        "webhook_secret": webhook_secret,
    }
    credentials = {
        field: incoming[field].strip() or current.get(field, "")
        for field in definition.fields
    }

    credential_id = connection.credential_id if connection is not None else None
    if credential_id is None:
        credential = store_credential(session, f"channel:{provider}", credentials)
        credential_id = credential.id
    else:
        replace_credential(session, credential_id, credentials)

    is_enabled = enabled == "on"
    configured = all(credentials.get(field, "").strip() for field in definition.required_fields)
    status = "disabled" if not is_enabled else ("ready" if configured else "incomplete")
    if connection is None:
        connection = ChannelConnection(
            bot_id=bot_id,
            provider=provider,
            display_name=definition.display_name,
            webhook_key=uuid4().hex,
        )
    connection.credential_id = credential_id
    connection.enabled = is_enabled
    connection.status = status
    connection.updated_at = utc_now()
    session.add(connection)
    session.commit()
    return RedirectResponse(f"/bots/{bot_id}/channels?saved=1", status_code=303)


@router.get("/{bot_id}/setup", response_class=HTMLResponse)
def bot_setup_page(
    bot_id: int,
    request: Request,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="setup")
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "error": error or "",
            "setup_steps": (
                ("Profile", f"/bots/{bot_id}"),
                ("Knowledge", f"/bots/{bot_id}/knowledge"),
                ("Behavior", f"/bots/{bot_id}/behavior"),
                ("Channels", f"/bots/{bot_id}/channels"),
                ("Test", f"/bots/{bot_id}/test"),
            ),
        }
    )
    return templates.TemplateResponse(request, "bot_setup.html", context)


def _set_lifecycle(bot: Bot, status: str, session: Session) -> None:
    bot.lifecycle_status = status
    bot.updated_at = utc_now()
    session.add(bot)
    session.commit()


def _readiness_allows_transition(
    session: Session,
    bot: Bot,
    config: BotConfigVersion | None,
) -> bool:
    readiness = _readiness_for_config(session, bot, config)
    return readiness is not None and readiness.can_publish


@router.post("/{bot_id}/ready")
def mark_bot_ready(
    bot_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    if bot.lifecycle_status != "draft":
        return RedirectResponse(
            f"/bots/{bot_id}?error=invalid_lifecycle_transition", status_code=303
        )
    draft = get_draft_config(session, bot_id)
    if not _readiness_allows_transition(session, bot, draft):
        return RedirectResponse(f"/bots/{bot_id}/setup?error=readiness_failed", status_code=303)
    _set_lifecycle(bot, "ready", session)
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)


@router.post("/{bot_id}/activate")
def activate_bot(
    bot_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    if bot.lifecycle_status != "ready" or bot.live_config_version_id is None:
        return RedirectResponse(
            f"/bots/{bot_id}?error=invalid_lifecycle_transition", status_code=303
        )
    live = get_live_config(session, bot_id)
    if not _readiness_allows_transition(session, bot, live):
        return RedirectResponse(f"/bots/{bot_id}?error=readiness_failed", status_code=303)
    _set_lifecycle(bot, "active", session)
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)


@router.post("/{bot_id}/pause")
def pause_bot(
    bot_id: int,
    admin_email: str = Depends(require_csrf),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    if bot.lifecycle_status != "active":
        return RedirectResponse(
            f"/bots/{bot_id}?error=invalid_lifecycle_transition", status_code=303
        )
    _set_lifecycle(bot, "paused", session)
    record_audit(
        session,
        current_user,
        "bot.pause",
        "bot",
        str(bot_id),
        f"Paused Bot {bot.name}",
        bot_id=bot_id,
        before={"status": "active"},
        after={"status": "paused"},
    )
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)


@router.post("/{bot_id}/resume")
def resume_bot(
    bot_id: int,
    admin_email: str = Depends(require_csrf),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    if bot.lifecycle_status != "paused" or bot.live_config_version_id is None:
        return RedirectResponse(
            f"/bots/{bot_id}?error=invalid_lifecycle_transition", status_code=303
        )
    live = get_live_config(session, bot_id)
    if not _readiness_allows_transition(session, bot, live):
        return RedirectResponse(f"/bots/{bot_id}?error=readiness_failed", status_code=303)
    _set_lifecycle(bot, "active", session)
    record_audit(
        session,
        current_user,
        "bot.resume",
        "bot",
        str(bot_id),
        f"Resumed Bot {bot.name}",
        bot_id=bot_id,
        before={"status": "paused"},
        after={"status": "active"},
    )
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)
