from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from chatbot_manager.admin.dependencies import require_csrf, templates
from chatbot_manager.auth.service import authenticate_user
from chatbot_manager.db import get_session
from chatbot_manager.security import make_session_token
from chatbot_manager.settings import get_settings


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    user = authenticate_user(session, email, password)
    if user is None or user.id is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid login"},
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    settings = get_settings()
    response.set_cookie(
        "admin_session",
        make_session_token(user.id),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.admin_session_max_age_seconds,
    )
    return response


@router.post("/logout")
def logout(_admin_email: str = Depends(require_csrf)) -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("admin_session")
    return response
