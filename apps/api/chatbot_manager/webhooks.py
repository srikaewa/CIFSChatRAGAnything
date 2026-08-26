import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from chatbot_manager.channel_config import ResolvedChannel, channel_credentials, resolve_channel
from chatbot_manager.channels.line import IncomingMessage, LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput, RESPONSE_GENERATION_FAILED
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule
from chatbot_manager.rag.service import rag_service_from_assistant
from chatbot_manager.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")
Sender = Callable[[dict[str, Any], str], Awaitable[None]]


def _require_ready(channel: ResolvedChannel, provider: str) -> None:
    if channel.state in {"incomplete", "failed"}:
        raise HTTPException(status_code=503, detail=f"{provider} channel is not ready")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed JSON payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Malformed JSON payload")
    return payload


def get_assistant_settings(session: Session) -> AssistantSettings:
    settings = session.get(AssistantSettings, 1)
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


@dataclass(frozen=True)
class AdminNotificationResult:
    status: str
    error_code: str = ""


async def _notify_admin(
    settings: Any, session: Session, message: IncomingMessage, rule_reply: str
) -> AdminNotificationResult:
    chat_id = str(getattr(settings, "admin_notify_chat_id", "") or "").strip()
    if not chat_id:
        return AdminNotificationResult(status="skipped")
    channel = str(getattr(settings, "admin_notify_channel", "telegram") or "").strip().lower()
    if channel != "telegram":
        return AdminNotificationResult(status="failed", error_code="admin_notification_unsupported_channel")
    if re.fullmatch(r"-?\d+", chat_id) is None:
        return AdminNotificationResult(status="failed", error_code="admin_notification_invalid_destination")
    app_settings = get_settings()
    creds = channel_credentials(session, app_settings, "telegram")
    bot_token = creds.get("bot_token", "")
    if not bot_token:
        return AdminNotificationResult(status="failed", error_code="admin_notification_not_configured")
    adapter = TelegramAdapter(bot_token, "")
    text = (
        f"Need human help\n"
        f"Channel: {message.provider}\n"
        f"User: {message.external_user_id}\n"
        f"Message: {message.text}\n"
        f"Rule: {rule_reply}"
    )
    try:
        await adapter.send_reply({"chat_id": int(chat_id)}, text)
    except Exception as exc:
        logger.error(
            "Admin notification failed provider=%s error_type=%s",
            message.provider,
            type(exc).__name__,
        )
        return AdminNotificationResult(status="failed", error_code="admin_notification_failed")
    return AdminNotificationResult(status="sent")


def append_event_error(current: str, code: str) -> str:
    return ";".join(part for part in (current, code) if part)


async def process_messages(messages: list[IncomingMessage], sender: Sender, session: Session) -> int:
    rules = list(session.exec(select(Rule).order_by(Rule.priority)).all())
    settings = get_assistant_settings(session)
    engine = ChatbotEngine(rag_service=rag_service_from_assistant(settings))
    processed = 0
    for message in messages:
        decision = await engine.answer(
            ChatbotInput(text=message.text, provider=message.provider, external_user_id=message.external_user_id),
            rules=rules,
            settings=settings,
        )
        event = ChatEvent(
            provider=message.provider,
            external_user_id=message.external_user_id,
            incoming_text=message.text,
            decision_source=decision.source,
            reply_text=decision.reply_text,
            error=RESPONSE_GENERATION_FAILED if decision.error else "",
            raw_event=json.dumps(message.raw_event, ensure_ascii=False),
        )
        session.add(event)
        session.commit()
        session.refresh(event)

        try:
            await sender(message.reply_context, decision.reply_text)
        except Exception as exc:
            logger.error(
                "Provider reply failed provider=%s error_type=%s",
                message.provider,
                type(exc).__name__,
            )
            event.error = append_event_error(event.error, "reply_send_failed")
            session.add(event)
            session.commit()
            processed += 1
            continue

        if decision.escalate:
            try:
                notification = await _notify_admin(settings, session, message, decision.rule_reply)
            except Exception as exc:
                logger.error(
                    "Admin notification failed provider=%s error_type=%s",
                    message.provider,
                    type(exc).__name__,
                )
                notification = AdminNotificationResult(
                    status="failed", error_code="admin_notification_failed"
                )
            logger.info(
                "Admin notification status provider=%s status=%s",
                message.provider,
                notification.status,
            )
            if notification.error_code:
                event.error = append_event_error(event.error, notification.error_code)

        session.add(event)
        session.commit()
        processed += 1
    return processed


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    settings = get_settings()
    channel = resolve_channel(session, settings, "line")
    if not channel.enabled:
        return {"processed": 0}
    _require_ready(channel, "line")
    body = await request.body()
    adapter = LineAdapter(channel.credentials["channel_secret"], channel.credentials["channel_access_token"])
    if not adapter.validate_signature(body, x_line_signature):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")
    payload = await _json_payload(request)
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}


@router.get("/messenger", response_class=PlainTextResponse)
def messenger_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    session: Session = Depends(get_session),
) -> PlainTextResponse:
    settings = get_settings()
    channel = resolve_channel(session, settings, "messenger")
    if not channel.enabled:
        raise HTTPException(status_code=403, detail="Messenger channel disabled")
    _require_ready(channel, "messenger")
    adapter = MessengerAdapter(
        channel.credentials["verify_token"],
        channel.credentials["page_access_token"],
        channel.credentials["app_secret"],
    )
    challenge = adapter.verify(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Invalid Messenger verification")
    return PlainTextResponse(challenge)


@router.post("/messenger")
async def messenger_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    settings = get_settings()
    channel = resolve_channel(session, settings, "messenger")
    if not channel.enabled:
        return {"processed": 0}
    _require_ready(channel, "messenger")
    adapter = MessengerAdapter(
        channel.credentials["verify_token"],
        channel.credentials["page_access_token"],
        channel.credentials["app_secret"],
    )
    body = await request.body()
    if not adapter.validate_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid Messenger signature")
    payload = await _json_payload(request)
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    settings = get_settings()
    channel = resolve_channel(session, settings, "telegram")
    if not channel.enabled:
        return {"processed": 0}
    _require_ready(channel, "telegram")
    adapter = TelegramAdapter(channel.credentials["bot_token"], channel.credentials["webhook_secret"])
    body = await request.body()
    if not adapter.validate_webhook(body, x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    payload = await _json_payload(request)
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}
