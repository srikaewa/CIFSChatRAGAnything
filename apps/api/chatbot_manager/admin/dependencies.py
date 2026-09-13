from pathlib import Path

from fastapi import Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates

from chatbot_manager.security import make_csrf_token, read_session_token, verify_csrf_token
from chatbot_manager.settings import get_settings


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["csrf_token_for"] = make_csrf_token


def require_admin(request: Request) -> str:
    settings = get_settings()
    email = read_session_token(
        request.cookies.get("admin_session"),
        settings.admin_session_max_age_seconds,
    )
    if not email:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return email


def require_csrf(
    csrf_token: str = Form(""),
    admin_email: str = Depends(require_admin),
) -> str:
    if not verify_csrf_token(csrf_token, admin_email):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return admin_email
