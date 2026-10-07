from pathlib import Path

from fastapi import Depends, Form, HTTPException
from fastapi.templating import Jinja2Templates

from chatbot_manager.auth.authorization import (
    CurrentUser,
    MANAGER_ROLES,
    require_current_user,
)
from chatbot_manager.security import make_csrf_token, verify_csrf_token


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["csrf_token_for"] = make_csrf_token


def require_admin(
    current_user: CurrentUser = Depends(require_current_user),
) -> str:
    return current_user.email


def require_csrf(
    csrf_token: str = Form(""),
    current_user: CurrentUser = Depends(require_current_user),
) -> str:
    if not verify_csrf_token(csrf_token, current_user.email):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return current_user.email


def require_manager_csrf(
    csrf_token: str = Form(""),
    current_user: CurrentUser = Depends(require_current_user),
) -> str:
    if current_user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not verify_csrf_token(csrf_token, current_user.email):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return current_user.email
