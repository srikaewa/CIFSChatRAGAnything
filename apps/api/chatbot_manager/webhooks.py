import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from chatbot_manager.channel_config import CHANNEL_DEFINITIONS, channel_credentials
from chatbot_manager.channel_connections import (
    migrate_legacy_channels,
    resolve_connection_credentials,
)
from chatbot_manager.channels.line import IncomingMessage, LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput, RESPONSE_GENERATION_FAILED
from chatbot_manager.db import get_session
from chatbot_manager.models import (
    AssistantSettings,
    ChannelConnection,
    ChatEvent,
    ConversationMessage,
    Rule,
    utc_now,
)
from chatbot_manager.rag.service import rag_service_from_assistant
from chatbot_manager.runtime.conversations import ConversationService
from chatbot_manager.runtime.delivery import DeliveryService
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest
from chatbot_manager.runtime.handoff import ConversationStateError, HandoffService
from chatbot_manager.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")
Sender = Callable[[dict[str, Any], str], Awaitable[None]]


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
    """Legacy admin notification path retained until the Unified Inbox replaces it."""
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
    """Legacy single-assistant helper retained for compatibility tests until Phase 6 cleanup.

    Production provider routes below no longer call this function.
    """
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


def _legacy_connection(session: Session, provider: str) -> ChannelConnection:
    migrate_legacy_channels(session)
    connections = list(
        session.exec(
            select(ChannelConnection)
            .where(ChannelConnection.provider == provider)
            .order_by(ChannelConnection.id)
        ).all()
    )
    enabled = [connection for connection in connections if connection.enabled]
    if len(enabled) > 1:
        raise HTTPException(status_code=409, detail="ambiguous_legacy_webhook")
    if len(enabled) == 1:
        return enabled[0]
    if len(connections) > 1:
        raise HTTPException(status_code=409, detail="ambiguous_legacy_webhook")
    if len(connections) == 1:
        return connections[0]
    raise HTTPException(status_code=503, detail=f"{provider} channel is not ready")


def _keyed_connection(session: Session, provider: str, webhook_key: str) -> ChannelConnection:
    connection = session.exec(
        select(ChannelConnection)
        .where(ChannelConnection.provider == provider)
        .where(ChannelConnection.webhook_key == webhook_key)
    ).first()
    if connection is None:
        raise HTTPException(status_code=404, detail="channel_connection_not_found")
    return connection


def _require_connection_ready(
    session: Session,
    connection: ChannelConnection,
    provider: str,
) -> bool:
    if not connection.enabled:
        return False
    if connection.status == "failed":
        raise HTTPException(status_code=503, detail=f"{provider} channel is not ready")
    definition = CHANNEL_DEFINITIONS.get(provider)
    if definition is None:
        raise HTTPException(status_code=404, detail="unsupported_channel_provider")
    credentials = resolve_connection_credentials(session, connection)
    if not all(credentials.get(field, "").strip() for field in definition.required_fields):
        raise HTTPException(status_code=503, detail=f"{provider} channel is not ready")
    return True


def _adapter_for_connection(session: Session, connection: ChannelConnection):
    credentials = resolve_connection_credentials(session, connection)
    if connection.provider == "line":
        return LineAdapter(
            credentials.get("channel_secret", ""),
            credentials.get("channel_access_token", ""),
        )
    if connection.provider == "messenger":
        return MessengerAdapter(
            credentials.get("verify_token", ""),
            credentials.get("page_access_token", ""),
            credentials.get("app_secret", ""),
        )
    if connection.provider == "telegram":
        return TelegramAdapter(
            credentials.get("bot_token", ""),
            credentials.get("webhook_secret", ""),
        )
    raise HTTPException(status_code=404, detail="unsupported_channel_provider")


def _compat_event(
    session: Session,
    *,
    connection: ChannelConnection,
    message: IncomingMessage,
    decision_source: str,
    reply_text: str,
    error: str = "",
) -> None:
    session.add(
        ChatEvent(
            provider=message.provider,
            external_user_id=message.external_user_id,
            incoming_text=message.text,
            decision_source=decision_source,
            reply_text=reply_text,
            raw_event=json.dumps(message.raw_event, ensure_ascii=False),
            error=error,
            bot_id=connection.bot_id,
        )
    )
    session.commit()


async def process_runtime_messages(
    messages: list[IncomingMessage],
    connection: ChannelConnection,
    session: Session,
) -> int:
    conversations = ConversationService(session)
    runtime = BotRuntime(session)
    handoff = HandoffService(session)
    delivery = DeliveryService(session)
    processed = 0

    if connection.id is None:
        raise HTTPException(status_code=503, detail="channel_connection_not_persisted")

    for message in messages:
        conversation = conversations.get_or_create(
            connection.bot_id,
            connection.id,
            message.external_user_id,
        )
        if conversation.id is None:
            raise HTTPException(status_code=503, detail="conversation_not_persisted")

        if conversations.is_duplicate(conversation.id, message.external_message_id):
            processed += 1
            continue

        inbound = conversations.record_inbound(
            conversation,
            message.external_message_id,
            message.text,
            {
                "provider": message.provider,
                "timestamp_ms": message.timestamp_ms,
                "attachments": message.attachments,
                "reply_context": message.reply_context,
            },
        )
        if inbound.id is None:
            raise HTTPException(status_code=503, detail="message_not_persisted")

        session.refresh(conversation)
        if conversation.status == "human_active":
            _compat_event(
                session,
                connection=connection,
                message=message,
                decision_source="human_active",
                reply_text="",
            )
            processed += 1
            continue

        result = await runtime.run(
            RuntimeRequest(
                bot_id=connection.bot_id,
                conversation_id=conversation.id,
                message_id=inbound.id,
                text=message.text,
                provider=message.provider,
                external_user_id=message.external_user_id,
            )
        )

        outbound = ConversationMessage(
            conversation_id=conversation.id,
            sender_type="bot",
            content=result.reply_text,
            external_message_id=f"bot:{inbound.id}",
            delivery_status="pending",
            metadata_json=json.dumps(
                {
                    "decision_type": result.decision_type,
                    "reference_count": result.reference_count,
                },
                sort_keys=True,
            ),
        )
        conversation.last_message_at = utc_now()
        session.add(outbound)
        session.add(conversation)
        session.commit()
        session.refresh(outbound)

        if result.escalate and conversation.status == "bot_active":
            try:
                conversation = handoff.escalate(
                    conversation.id,
                    reason="runtime_escalation",
                )
            except ConversationStateError:
                session.refresh(conversation)

        delivery_error = ""
        if result.reply_text:
            delivery_result = await delivery.send(
                connection,
                message.reply_context,
                result.reply_text,
            )
            outbound.delivery_status = delivery_result.status
            delivery_error = delivery_result.error_code
        else:
            outbound.delivery_status = "skipped"
        session.add(outbound)
        session.commit()

        error = append_event_error(result.error_code, delivery_error)
        _compat_event(
            session,
            connection=connection,
            message=message,
            decision_source=result.decision_type,
            reply_text=result.reply_text,
            error=error,
        )
        processed += 1

    return processed


async def _post_connection_webhook(
    request: Request,
    connection: ChannelConnection,
    session: Session,
    *,
    x_line_signature: str = "",
    x_hub_signature_256: str = "",
    x_telegram_secret: str = "",
) -> dict[str, int]:
    if not _require_connection_ready(session, connection, connection.provider):
        return {"processed": 0}

    adapter = _adapter_for_connection(session, connection)
    body = await request.body()
    if connection.provider == "line":
        if not adapter.validate_signature(body, x_line_signature):
            raise HTTPException(status_code=401, detail="Invalid LINE signature")
    elif connection.provider == "messenger":
        if not adapter.validate_signature(body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid Messenger signature")
    elif connection.provider == "telegram":
        if not adapter.validate_webhook(body, x_telegram_secret):
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    else:
        raise HTTPException(status_code=404, detail="unsupported_channel_provider")

    payload = await _json_payload(request)
    messages = adapter.parse_events(payload)
    processed = await process_runtime_messages(messages, connection, session)
    return {"processed": processed}


def _messenger_verify_connection(
    connection: ChannelConnection,
    session: Session,
    hub_mode: str | None,
    hub_verify_token: str | None,
    hub_challenge: str | None,
) -> PlainTextResponse:
    if not _require_connection_ready(session, connection, "messenger"):
        raise HTTPException(status_code=403, detail="Messenger channel disabled")
    adapter = _adapter_for_connection(session, connection)
    if not isinstance(adapter, MessengerAdapter):
        raise HTTPException(status_code=404, detail="unsupported_channel_provider")
    challenge = adapter.verify(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Invalid Messenger verification")
    return PlainTextResponse(challenge)


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    connection = _legacy_connection(session, "line")
    return await _post_connection_webhook(
        request,
        connection,
        session,
        x_line_signature=x_line_signature,
    )


@router.get("/messenger", response_class=PlainTextResponse)
def messenger_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    session: Session = Depends(get_session),
) -> PlainTextResponse:
    connection = _legacy_connection(session, "messenger")
    return _messenger_verify_connection(
        connection,
        session,
        hub_mode,
        hub_verify_token,
        hub_challenge,
    )


@router.post("/messenger")
async def messenger_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    connection = _legacy_connection(session, "messenger")
    return await _post_connection_webhook(
        request,
        connection,
        session,
        x_hub_signature_256=x_hub_signature_256,
    )


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    connection = _legacy_connection(session, "telegram")
    return await _post_connection_webhook(
        request,
        connection,
        session,
        x_telegram_secret=x_telegram_bot_api_secret_token,
    )


@router.get("/{provider}/{webhook_key}", response_class=PlainTextResponse)
def keyed_messenger_verify(
    provider: str,
    webhook_key: str,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    session: Session = Depends(get_session),
) -> PlainTextResponse:
    if provider != "messenger":
        raise HTTPException(status_code=404, detail="unsupported_webhook_verification")
    connection = _keyed_connection(session, provider, webhook_key)
    return _messenger_verify_connection(
        connection,
        session,
        hub_mode,
        hub_verify_token,
        hub_challenge,
    )


@router.post("/{provider}/{webhook_key}")
async def keyed_webhook(
    provider: str,
    webhook_key: str,
    request: Request,
    x_line_signature: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
    x_telegram_bot_api_secret_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    if provider not in {"line", "messenger", "telegram"}:
        raise HTTPException(status_code=404, detail="unsupported_channel_provider")
    connection = _keyed_connection(session, provider, webhook_key)
    return await _post_connection_webhook(
        request,
        connection,
        session,
        x_line_signature=x_line_signature,
        x_hub_signature_256=x_hub_signature_256,
        x_telegram_secret=x_telegram_bot_api_secret_token,
    )
