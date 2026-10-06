import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.db import get_session
from chatbot_manager.models import (
    Bot,
    BotDecision,
    BotTestCase,
    ChannelConnection,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
    utc_now,
)
from chatbot_manager.runtime.conversations import ConversationService
from chatbot_manager.runtime.delivery import DeliveryService
from chatbot_manager.runtime.handoff import ConversationStateError, HandoffService


router = APIRouter(prefix="/conversations")
TEMP_OPERATOR_ID = 0
STATUS_LABELS = {
    "bot_active": "Bot active",
    "needs_human": "Needs human",
    "human_active": "Human active",
    "closed": "Closed",
}


def _conversation(session: Session, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _connection(session: Session, conversation: Conversation) -> ChannelConnection:
    connection = session.get(ChannelConnection, conversation.channel_connection_id)
    if connection is None:
        raise HTTPException(status_code=409, detail="conversation_channel_missing")
    return connection


def _bot(session: Session, conversation: Conversation) -> Bot | None:
    return session.get(Bot, conversation.bot_id)


def _wait_label(started_at: datetime) -> str:
    seconds = max(0, int((utc_now() - started_at).total_seconds()))
    if seconds < 60:
        return "<1 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


def _latest_message(session: Session, conversation_id: int) -> ConversationMessage | None:
    return session.exec(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
    ).first()


def _latest_reply_context(session: Session, conversation_id: int) -> dict[str, object]:
    messages = session.exec(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .where(ConversationMessage.sender_type == "user")
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
    ).all()
    for message in messages:
        try:
            metadata = json.loads(message.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        reply_context = metadata.get("reply_context")
        if isinstance(reply_context, dict):
            return reply_context
    return {}


def _system_message(
    session: Session,
    conversation_id: int,
    content: str,
    admin_email: str,
) -> None:
    session.add(
        ConversationMessage(
            conversation_id=conversation_id,
            sender_type="system",
            content=content,
            metadata_json=json.dumps({"actor_email": admin_email}, sort_keys=True),
        )
    )
    session.commit()


def _redirect(conversation_id: int, marker: str) -> RedirectResponse:
    return RedirectResponse(
        f"/conversations/{conversation_id}?{marker}=1",
        status_code=303,
    )


@router.get("", response_class=HTMLResponse)
def conversations_page(
    request: Request,
    bot_id: int | None = Query(default=None),
    channel: str = Query(default=""),
    status: str = Query(default=""),
    decision: str = Query(default=""),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    assigned: str = Query(default=""),
    q: str = Query(default=""),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    statement = select(Conversation).order_by(
        Conversation.last_message_at.desc(),
        Conversation.id.desc(),
    )
    if bot_id is not None:
        statement = statement.where(Conversation.bot_id == bot_id)
    if status:
        statement = statement.where(Conversation.status == status)

    decision_filter = decision.strip().lower()
    if decision_filter:
        decision_statement = select(ConversationMessage.conversation_id).join(
            BotDecision,
            BotDecision.message_id == ConversationMessage.id,
        )
        if decision_filter == "knowledge_error":
            decision_statement = decision_statement.where(
                BotDecision.error_code.like("knowledge_%")
            )
        else:
            decision_statement = decision_statement.where(
                BotDecision.decision_type == decision_filter
            )
        if from_at is not None:
            decision_statement = decision_statement.where(
                BotDecision.created_at >= from_at
            )
        if to_at is not None:
            decision_statement = decision_statement.where(
                BotDecision.created_at <= to_at
            )
        conversation_ids = list(session.exec(decision_statement).all())
        statement = statement.where(Conversation.id.in_(conversation_ids))
    else:
        if from_at is not None:
            statement = statement.where(Conversation.started_at >= from_at)
        if to_at is not None:
            statement = statement.where(Conversation.started_at <= to_at)

    if assigned == "me":
        statement = statement.where(Conversation.assigned_operator_id == TEMP_OPERATOR_ID)
    elif assigned == "unassigned":
        statement = statement.where(Conversation.assigned_operator_id == None)  # noqa: E711

    query = q.strip().lower()
    channel_filter = channel.strip().lower()
    rows: list[dict[str, object]] = []
    for conversation in session.exec(statement).all():
        if conversation.id is None:
            continue
        connection = session.get(ChannelConnection, conversation.channel_connection_id)
        if connection is None:
            continue
        if channel_filter and connection.provider != channel_filter:
            continue
        latest = _latest_message(session, conversation.id)
        if query:
            haystack = " ".join(
                [
                    conversation.external_user_id,
                    latest.content if latest is not None else "",
                ]
            ).lower()
            if query not in haystack:
                continue
        bot = session.get(Bot, conversation.bot_id)
        rows.append(
            {
                "conversation": conversation,
                "bot_name": bot.name if bot is not None else f"Bot {conversation.bot_id}",
                "provider": connection.provider,
                "latest_excerpt": (latest.content[:120] if latest is not None else ""),
                "status_label": STATUS_LABELS.get(conversation.status, conversation.status),
                "wait_label": _wait_label(conversation.last_message_at),
                "assigned_label": (
                    admin_email
                    if conversation.assigned_operator_id == TEMP_OPERATOR_ID
                    else (
                        f"Operator {conversation.assigned_operator_id}"
                        if conversation.assigned_operator_id is not None
                        else "Unassigned"
                    )
                ),
            }
        )

    providers = sorted(
        set(session.exec(select(ChannelConnection.provider)).all())
    )
    return templates.TemplateResponse(
        request,
        "conversations.html",
        {
            "admin_email": admin_email,
            "active_page": "conversations",
            "rows": rows,
            "providers": providers,
            "filters": {
                "bot_id": bot_id or "",
                "channel": channel,
                "status": status,
                "decision": decision,
                "from": from_at.isoformat() if from_at is not None else "",
                "to": to_at.isoformat() if to_at is not None else "",
                "assigned": assigned,
                "q": q,
            },
        },
    )


@router.get("/{conversation_id}", response_class=HTMLResponse)
def conversation_detail(
    conversation_id: int,
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    conversation = _conversation(session, conversation_id)
    connection = _connection(session, conversation)
    bot = _bot(session, conversation)
    messages = session.exec(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    ).all()
    events = session.exec(
        select(ConversationHandoffEvent)
        .where(ConversationHandoffEvent.conversation_id == conversation_id)
        .order_by(ConversationHandoffEvent.created_at, ConversationHandoffEvent.id)
    ).all()
    message_ids = [message.id for message in messages if message.id is not None]
    decisions: list[BotDecision] = []
    if message_ids:
        decisions = session.exec(
            select(BotDecision)
            .where(BotDecision.message_id.in_(message_ids))
            .order_by(BotDecision.created_at, BotDecision.id)
        ).all()

    return templates.TemplateResponse(
        request,
        "conversation_detail.html",
        {
            "admin_email": admin_email,
            "active_page": "conversations",
            "conversation": conversation,
            "connection": connection,
            "bot": bot,
            "messages": messages,
            "events": events,
            "decisions": decisions,
            "status_label": STATUS_LABELS.get(conversation.status, conversation.status),
            "assigned_label": (
                admin_email
                if conversation.assigned_operator_id == TEMP_OPERATOR_ID
                else (
                    f"Operator {conversation.assigned_operator_id}"
                    if conversation.assigned_operator_id is not None
                    else "Unassigned"
                )
            ),
        },
    )


@router.post("/{conversation_id}/messages/{message_id}/add-test-case")
def add_message_to_test_suite(
    conversation_id: int,
    message_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    conversation = _conversation(session, conversation_id)
    message = session.get(ConversationMessage, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_type != "user":
        raise HTTPException(status_code=400, detail="only_user_messages_can_seed_tests")
    session.add(
        BotTestCase(
            bot_id=conversation.bot_id,
            name=f"Conversation {conversation_id} message {message_id}",
            input_message=message.content,
            expected_behavior_json="{}",
            tags_json=json.dumps(["conversation"]),
            enabled=False,
            created_by=admin_email,
        )
    )
    session.commit()
    return RedirectResponse(
        f"/bots/{conversation.bot_id}/test?case_created=1",
        status_code=303,
    )


@router.post("/{conversation_id}/take")
def take_conversation(
    conversation_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    try:
        HandoffService(session).take(conversation_id, TEMP_OPERATOR_ID)
    except ConversationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _system_message(session, conversation_id, f"Taken by {admin_email}", admin_email)
    return _redirect(conversation_id, "taken")


@router.post("/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    try:
        HandoffService(session).assign(
            conversation_id,
            TEMP_OPERATOR_ID,
            actor_user_id=TEMP_OPERATOR_ID,
        )
    except ConversationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _system_message(session, conversation_id, f"Assigned to {admin_email}", admin_email)
    return _redirect(conversation_id, "assigned")


@router.post("/{conversation_id}/reply")
async def reply_to_conversation(
    conversation_id: int,
    reply_text: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    conversation = _conversation(session, conversation_id)
    if conversation.status != "human_active":
        raise HTTPException(status_code=409, detail="conversation_not_human_active")
    text = reply_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="reply_text_required")
    connection = _connection(session, conversation)
    reply_context = _latest_reply_context(session, conversation_id)
    delivery_context = reply_context
    if connection.provider == "line" and reply_context.get("user_id"):
        # Human replies can happen after LINE's one-time reply token was consumed or expired.
        delivery_context = {"user_id": reply_context["user_id"]}

    message = ConversationMessage(
        conversation_id=conversation_id,
        sender_type="operator",
        content=text,
        external_message_id=f"operator:{conversation_id}:{int(utc_now().timestamp() * 1_000_000)}",
        delivery_status="sending",
        metadata_json=json.dumps(
            {"actor_email": admin_email, "reply_context": reply_context},
            sort_keys=True,
        ),
    )
    conversation.last_message_at = utc_now()
    session.add(message)
    session.add(conversation)
    session.commit()
    session.refresh(message)

    delivery = await DeliveryService(session).send(connection, delivery_context, text)
    message.delivery_status = delivery.status
    try:
        metadata = json.loads(message.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if delivery.error_code:
        metadata["error_code"] = delivery.error_code
    message.metadata_json = json.dumps(metadata, sort_keys=True)
    session.add(message)
    session.commit()
    return _redirect(conversation_id, "replied")


@router.post("/{conversation_id}/return-to-bot")
def return_to_bot(
    conversation_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    try:
        HandoffService(session).return_to_bot(
            conversation_id,
            actor_user_id=TEMP_OPERATOR_ID,
        )
    except ConversationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _system_message(session, conversation_id, f"Returned to Bot by {admin_email}", admin_email)
    return _redirect(conversation_id, "returned")


@router.post("/{conversation_id}/close")
def close_conversation(
    conversation_id: int,
    reason: str = Form("resolved"),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    try:
        HandoffService(session).close(
            conversation_id,
            actor_user_id=TEMP_OPERATOR_ID,
            reason=reason.strip() or "resolved",
        )
    except ConversationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _system_message(session, conversation_id, f"Closed by {admin_email}", admin_email)
    return _redirect(conversation_id, "closed")


@router.post("/{conversation_id}/note")
def add_internal_note(
    conversation_id: int,
    note_text: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _conversation(session, conversation_id)
    text = note_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="note_text_required")
    session.add(
        ConversationMessage(
            conversation_id=conversation_id,
            sender_type="internal_note",
            content=text,
            metadata_json=json.dumps({"actor_email": admin_email}, sort_keys=True),
        )
    )
    session.commit()
    return _redirect(conversation_id, "noted")
