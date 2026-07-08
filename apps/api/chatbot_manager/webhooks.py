import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from chatbot_manager.channels.line import IncomingMessage, LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule
from chatbot_manager.rag.service import RagAnythingService
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


def rag_service_from_assistant(settings: AssistantSettings) -> RagAnythingService:
    return RagAnythingService(
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
        vision_model=settings.vision_model,
        embedding_model=settings.embedding_model,
    )


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
        session.add(
            ChatEvent(
                provider=message.provider,
                external_user_id=message.external_user_id,
                incoming_text=message.text,
                decision_source=decision.source,
                reply_text=decision.reply_text,
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
    adapter = LineAdapter(settings.line_channel_secret, settings.line_channel_access_token)
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
) -> PlainTextResponse:
    settings = get_settings()
    adapter = MessengerAdapter(
        settings.messenger_verify_token,
        settings.messenger_page_access_token,
        settings.messenger_app_secret,
    )
    challenge = adapter.verify(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Invalid Messenger verification")
    return PlainTextResponse(challenge)


@router.post("/messenger")
async def messenger_webhook(request: Request, session: Session = Depends(get_session)) -> dict[str, int]:
    settings = get_settings()
    adapter = MessengerAdapter(
        settings.messenger_verify_token,
        settings.messenger_page_access_token,
        settings.messenger_app_secret,
    )
    payload = await request.json()
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}
