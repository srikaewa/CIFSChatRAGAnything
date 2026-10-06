from pathlib import Path

from fastapi import Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from chatbot_manager.auth.service import load_active_user
from chatbot_manager.db import get_session
from chatbot_manager.security import (
    make_csrf_token,
    read_session_user_id,
    verify_csrf_token,
)
from chatbot_manager.settings import get_settings


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["csrf_token_for"] = make_csrf_token


def require_admin(
    request: Request,
    session: Session = Depends(get_session),
) -> str:
    settings = get_settings()
    user_id = read_session_user_id(
        request.cookies.get("admin_session"),
        settings.admin_session_max_age_seconds,
    )
    user = load_active_user(session, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user.email


def require_csrf(
    csrf_token: str = Form(""),
    admin_email: str = Depends(require_admin),
) -> str:
    if not verify_csrf_token(csrf_token, admin_email):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return admin_email
