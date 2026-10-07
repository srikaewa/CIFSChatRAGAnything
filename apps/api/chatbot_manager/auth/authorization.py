from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from chatbot_manager.auth.service import load_active_user
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot, Conversation, UserBotAccess
from chatbot_manager.security import read_session_user_id
from chatbot_manager.settings import get_settings


MANAGER_ROLES = {"owner", "admin"}


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    role: str
    allowed_bot_ids: frozenset[int]


def require_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> CurrentUser:
    settings = get_settings()
    user_id = read_session_user_id(
        request.cookies.get("admin_session"),
        settings.admin_session_max_age_seconds,
    )
    user = load_active_user(session, user_id) if user_id is not None else None
    if user is None or user.id is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    allowed_bot_ids = frozenset()
    if user.role == "operator":
        allowed_bot_ids = frozenset(
            session.exec(
                select(UserBotAccess.bot_id).where(UserBotAccess.user_id == user.id)
            ).all()
        )
    current_user = CurrentUser(
        id=user.id,
        email=user.email,
        role=user.role,
        allowed_bot_ids=allowed_bot_ids,
    )
    request.state.current_user = current_user
    return current_user


def require_role(*allowed_roles: str):
    def dependency(
        current_user: CurrentUser = Depends(require_current_user),
    ) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return current_user

    return dependency


def can_manage_bot(user: CurrentUser, bot_id: int) -> bool:
    if user.role in MANAGER_ROLES:
        return True
    return user.role == "operator" and bot_id in user.allowed_bot_ids


def require_bot_access(
    bot_id: int,
    current_user: CurrentUser,
    session: Session,
) -> Bot:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if not can_manage_bot(current_user, bot_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return bot


def require_bot_route_authorization(
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> CurrentUser:
    bot_id = request.path_params.get("bot_id")
    if bot_id is not None:
        require_bot_access(int(bot_id), current_user, session)
    if request.method != "GET" and current_user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")
    return current_user


def require_conversation_route_authorization(
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> CurrentUser:
    conversation_id = request.path_params.get("conversation_id")
    if conversation_id is None:
        return current_user
    conversation = session.get(Conversation, int(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    require_bot_access(conversation.bot_id, current_user, session)
    return current_user
