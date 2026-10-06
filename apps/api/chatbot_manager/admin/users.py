from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, delete, select

from chatbot_manager.admin.dependencies import require_csrf, templates
from chatbot_manager.auth.authorization import CurrentUser, require_current_user, require_role
from chatbot_manager.auth.service import password_hasher
from chatbot_manager.db import get_session
from chatbot_manager.models import Bot, User, UserBotAccess, utc_now


router = APIRouter(
    prefix="/users",
    dependencies=[Depends(require_role("owner"))],
)
VALID_ROLES = {"owner", "admin", "operator"}


def _user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _clear_bot_access(session: Session, user_id: int) -> None:
    session.exec(delete(UserBotAccess).where(UserBotAccess.user_id == user_id))


def _active_owner_count(session: Session, *, exclude_user_id: int | None = None) -> int:
    statement = select(User).where(
        User.role == "owner",
        User.active == True,  # noqa: E712
    )
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return len(session.exec(statement).all())


def _user_rows(session: Session) -> list[dict[str, object]]:
    bots = {bot.id: bot for bot in session.exec(select(Bot).order_by(Bot.name)).all()}
    access_by_user: dict[int, list[Bot]] = {}
    for access in session.exec(select(UserBotAccess)).all():
        bot = bots.get(access.bot_id)
        if bot is not None:
            access_by_user.setdefault(access.user_id, []).append(bot)
    return [
        {
            "user": user,
            "assigned_bots": sorted(
                access_by_user.get(user.id or -1, []),
                key=lambda bot: bot.name,
            ),
        }
        for user in session.exec(select(User).order_by(User.email)).all()
    ]


@router.get("", response_class=HTMLResponse)
def users_page(
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "admin_email": current_user.email,
            "active_page": "users",
            "user_rows": _user_rows(session),
            "bots": list(session.exec(select(Bot).order_by(Bot.name)).all()),
            "roles": ("owner", "admin", "operator"),
        },
    )


@router.post("")
def create_user(
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    _admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    normalized_email = email.strip().lower()
    normalized_role = role.strip().lower()
    if not normalized_email or not password or normalized_role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="invalid_user")
    if session.exec(select(User).where(User.email == normalized_email)).first() is not None:
        raise HTTPException(status_code=400, detail="duplicate_user_email")

    session.add(
        User(
            email=normalized_email,
            password_hash=password_hasher.hash(password),
            role=normalized_role,
            active=True,
        )
    )
    session.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/update")
def update_user(
    user_id: int,
    role: str = Form(...),
    active: str | None = Form(None),
    _admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    user = _user(session, user_id)
    normalized_role = role.strip().lower()
    if normalized_role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="invalid_role")
    new_active = active == "on"

    removes_active_owner = (
        user.role == "owner"
        and user.active
        and (normalized_role != "owner" or not new_active)
    )
    if removes_active_owner and _active_owner_count(session, exclude_user_id=user_id) == 0:
        raise HTTPException(status_code=400, detail="last_active_owner")

    user.role = normalized_role
    user.active = new_active
    user.updated_at = utc_now()
    session.add(user)
    if normalized_role != "operator":
        _clear_bot_access(session, user_id)
    session.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/password")
def reset_password(
    user_id: int,
    password: str = Form(...),
    _admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    if not password:
        raise HTTPException(status_code=400, detail="password_required")
    user = _user(session, user_id)
    user.password_hash = password_hasher.hash(password)
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/bots")
def replace_bot_access(
    user_id: int,
    bot_ids: list[int] = Form(default=[]),
    _admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    user = _user(session, user_id)
    _clear_bot_access(session, user_id)
    if user.role == "operator":
        selected_ids = set(bot_ids)
        existing_ids = set(
            session.exec(select(Bot.id).where(Bot.id.in_(selected_ids))).all()
        )
        if existing_ids != selected_ids:
            raise HTTPException(status_code=400, detail="invalid_bot_assignment")
        for bot_id in sorted(selected_ids):
            session.add(UserBotAccess(user_id=user_id, bot_id=bot_id))
    session.commit()
    return RedirectResponse("/users", status_code=303)
