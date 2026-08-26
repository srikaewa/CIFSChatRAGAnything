import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from chatbot_manager.channel_config import channel_credentials
from chatbot_manager.channels.line import IncomingMessage, LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule
from chatbot_manager.rag.service import rag_service_from_assistant
from chatbot_manager.settings import get_settings

router = APIRouter(prefix="/webhooks")
Sender = Callable[[dict[str, Any], str], Awaitable[None]]


def get_assistant_settings(session: Session) -> AssistantSettings:
    settings = session.get(AssistantSettings, 1)
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


async def _notify_admin(settings: Any, session: Session, message: IncomingMessage, rule_reply: str) -> None:
    chat_id = getattr(settings, "admin_notify_chat_id", "")
    if not chat_id:
        return
    channel = getattr(settings, "admin_notify_channel", "telegram")
    if channel != "telegram":
        return
    app_settings = get_settings()
    creds = channel_credentials(session, app_settings, "telegram")
    bot_token = creds.get("bot_token", "")
    if not bot_token:
        return
    adapter = TelegramAdapter(bot_token, "")
    text = (
        f"Need human help\n"
        f"Channel: {message.provider}\n"
        f"User: {message.external_user_id}\n"
        f"Message: {message.text}\n"
        f"Rule: {rule_reply}"
    )
    await adapter.send_reply({"chat_id": int(chat_id)}, text)


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
        await sender(message.reply_context, decision.reply_text)
        if decision.escalate:
            await _notify_admin(settings, session, message, decision.rule_reply)
        session.add(
            ChatEvent(
                provider=message.provider,
                external_user_id=message.external_user_id,
                incoming_text=message.text,
                decision_source=decision.source,
                reply_text=decision.reply_text,
                error=decision.error,
                raw_event=json.dumps(message.raw_event, ensure_ascii=False),
            )
        )
        processed += 1
    session.commit()
    return processed


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    settings = get_settings()
    body = await request.body()
    credentials = channel_credentials(session, settings, "line")
    adapter = LineAdapter(credentials["channel_secret"], credentials["channel_access_token"])
    if not adapter.validate_signature(body, x_line_signature):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")
    payload = await request.json()
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
    credentials = channel_credentials(session, settings, "messenger")
    adapter = MessengerAdapter(
        credentials["verify_token"],
        credentials["page_access_token"],
        credentials["app_secret"],
    )
    challenge = adapter.verify(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Invalid Messenger verification")
    return PlainTextResponse(challenge)


@router.post("/messenger")
async def messenger_webhook(request: Request, session: Session = Depends(get_session)) -> dict[str, int]:
    settings = get_settings()
    credentials = channel_credentials(session, settings, "messenger")
    adapter = MessengerAdapter(
        credentials["verify_token"],
        credentials["page_access_token"],
        credentials["app_secret"],
    )
    payload = await request.json()
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
    credentials = channel_credentials(session, settings, "telegram")
    adapter = TelegramAdapter(credentials["bot_token"], credentials["webhook_secret"])
    body = await request.body()
    if not adapter.validate_webhook(body, x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    payload = await request.json()
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}



