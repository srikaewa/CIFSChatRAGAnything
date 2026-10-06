from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, templates
from chatbot_manager.auth.authorization import require_role
from chatbot_manager.channel_config import channel_cards
from chatbot_manager.db import get_session
from chatbot_manager.settings import get_settings
from chatbot_manager.models import (
    Bot,
    ChannelConnection,
    Conversation,
    Incident,
    KnowledgeService,
    utc_now,
)


router = APIRouter(
    dependencies=[Depends(require_role("owner", "admin"))],
)


def _affected_bot_ids(incident: Incident) -> tuple[int, ...]:
    try:
        value = json.loads(incident.affected_bot_ids_json)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, int))


def _system_status(
    active_bot_ids: set[int],
    incidents: list[Incident],
    *,
    has_unverified_active_channels: bool,
) -> str:
    for incident in incidents:
        if incident.severity != "critical":
            continue
        affected = set(_affected_bot_ids(incident))
        if not affected or affected & active_bot_ids:
            return "Critical"
    if incidents or has_unverified_active_channels:
        return "Degraded"
    return "Healthy"


def _source_action(incident: Incident) -> tuple[str, str]:
    affected = _affected_bot_ids(incident)
    if incident.incident_type == "human_wait_sla":
        return "/conversations?status=needs_human", "Open Inbox"
    if incident.source_type == "channel" and affected:
        return f"/bots/{affected[0]}/channels", "Open Channels"
    if incident.source_type == "knowledge_service":
        return "/knowledge-services", "Open Knowledge Services"
    if incident.incident_type == "rag_latency_sla":
        return "/analytics", "Open Analytics"
    return f"/incidents/{incident.id}", "View source"


@router.get("/", response_class=HTMLResponse)
def command_center(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bots = list(session.exec(select(Bot).order_by(Bot.name)).all())
    active_bot_ids = {
        bot.id for bot in bots if bot.id is not None and bot.lifecycle_status == "active"
    }
    incidents = list(
        session.exec(
            select(Incident).where(Incident.status.in_(("open", "acknowledged")))
        ).all()
    )
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    incidents.sort(
        key=lambda row: (
            severity_rank.get(row.severity, 9),
            -row.first_seen_at.timestamp(),
        )
    )

    bot_by_id = {bot.id: bot for bot in bots if bot.id is not None}
    attention: list[dict[str, object]] = []
    incident_count_by_bot: dict[int, int] = {}
    for incident in incidents:
        affected_ids = _affected_bot_ids(incident)
        affected_bots = [
            bot_by_id[bot_id] for bot_id in affected_ids if bot_id in bot_by_id
        ]
        for bot_id in affected_ids:
            incident_count_by_bot[bot_id] = incident_count_by_bot.get(bot_id, 0) + 1
        source_url, source_label = _source_action(incident)
        attention.append(
            {
                "incident": incident,
                "affected_bots": affected_bots,
                "source_url": source_url,
                "source_label": source_label,
            }
        )

    channels = list(session.exec(select(ChannelConnection)).all())
    services = list(session.exec(select(KnowledgeService)).all())
    now = utc_now()
    today_start = datetime(now.year, now.month, now.day)
    today_conversations = len(
        session.exec(
            select(Conversation).where(Conversation.started_at >= today_start)
        ).all()
    )
    human_queue_count = len(
        session.exec(
            select(Conversation).where(Conversation.status == "needs_human")
        ).all()
    )

    fleet = [
        {
            "bot": bot,
            "incident_count": incident_count_by_bot.get(bot.id or -1, 0),
        }
        for bot in bots
    ]
    return templates.TemplateResponse(
        request,
        "command_center.html",
        {
            "admin_email": admin_email,
            "active_page": "command_center",
            "system_status": _system_status(
                active_bot_ids,
                incidents,
                has_unverified_active_channels=any(
                    connection.bot_id in active_bot_ids for connection in channels
                ),
            ),
            "bots_total": len(bots),
            "bots_active": sum(bot.lifecycle_status == "active" for bot in bots),
            "bots_paused": sum(bot.lifecycle_status == "paused" for bot in bots),
            "channels_total": len(channels),
            "channels_ready": sum(
                connection.enabled and connection.status == "ready"
                for connection in channels
            ),
            "channel_cards": channel_cards(session, get_settings()),
            "knowledge_total": len(services),
            "knowledge_healthy": sum(
                service.enabled and service.health_status == "healthy"
                for service in services
            ),
            "today_conversations": today_conversations,
            "human_queue_count": human_queue_count,
            "open_incident_count": len(incidents),
            "attention": attention,
            "fleet": fleet,
        },
    )
