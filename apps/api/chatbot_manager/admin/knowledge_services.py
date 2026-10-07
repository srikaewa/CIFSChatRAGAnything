import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.audit import record_audit
from chatbot_manager.auth.authorization import CurrentUser, require_current_user, require_role
from chatbot_manager.credentials import masked_credential, replace_credential, store_credential
from chatbot_manager.db import get_session
from chatbot_manager.knowledge.client import KnowledgeQuery, KnowledgeServiceError
from chatbot_manager.knowledge.service import build_knowledge_client
from chatbot_manager.models import KnowledgeService, utc_now
from chatbot_manager.retention import RetentionError, RetentionService


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/knowledge-services",
    dependencies=[Depends(require_role("owner", "admin"))],
)

_SAFE_ERROR_CODES = {
    "invalid_knowledge_service_url",
    "knowledge_service_unavailable",
    "knowledge_service_unauthorized",
    "knowledge_service_http_error",
    "knowledge_response_invalid",
}

_ERROR_MESSAGES = {
    "invalid_knowledge_service_url": "Knowledge Service URLs must use http or https.",
    "knowledge_service_unavailable": "The Knowledge Service is unavailable. Check the endpoint and try again.",
    "knowledge_service_unauthorized": "The Knowledge Service rejected the configured credentials.",
    "knowledge_service_http_error": "The Knowledge Service returned an unexpected HTTP response.",
    "knowledge_response_invalid": "The Knowledge Service returned an invalid query response.",
}


def _is_http_url(value: str, *, allow_empty: bool = False) -> bool:
    normalized = value.strip()
    if not normalized:
        return allow_empty
    parsed = urlsplit(normalized)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _registry_cards(session: Session) -> list[dict[str, object]]:
    services = session.exec(select(KnowledgeService).order_by(KnowledgeService.name)).all()
    cards: list[dict[str, object]] = []
    for service in services:
        masked_key = ""
        if service.credential_id is not None:
            try:
                masked_key = masked_credential(session, service.credential_id).get("api_key", "")
            except Exception as exc:
                logger.warning(
                    "Knowledge credential mask failed service_id=%s error_type=%s",
                    service.id,
                    type(exc).__name__,
                )
                masked_key = "***"
        cards.append({"service": service, "masked_api_key": masked_key})
    return cards


def _render_registry(
    request: Request,
    admin_email: str,
    session: Session,
    *,
    saved: str | None = None,
    error: str | None = None,
    tested: str | None = None,
    retrieval_result: dict[str, object] | None = None,
) -> Response:
    error_code = error if error in _SAFE_ERROR_CODES else None
    return templates.TemplateResponse(
        request,
        "knowledge_services.html",
        {
            "admin_email": admin_email,
            "active_page": "knowledge_services",
            "cards": _registry_cards(session),
            "saved": saved == "1",
            "tested": tested == "1",
            "error": _ERROR_MESSAGES.get(error_code or "", ""),
            "retrieval_result": retrieval_result,
        },
    )


@router.get("", response_class=HTMLResponse)
def knowledge_services_page(
    request: Request,
    saved: str | None = None,
    error: str | None = None,
    tested: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    return _render_registry(
        request,
        admin_email,
        session,
        saved=saved,
        error=error,
        tested=tested,
    )


@router.post("")
def create_knowledge_service(
    name: str = Form(...),
    api_base_url: str = Form(...),
    webui_url: str = Form(""),
    api_key: str = Form(""),
    admin_email: str = Depends(require_csrf),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    del admin_email
    normalized_api = api_base_url.strip().rstrip("/")
    normalized_webui = webui_url.strip()
    if not name.strip() or not _is_http_url(normalized_api) or not _is_http_url(
        normalized_webui,
        allow_empty=True,
    ):
        return RedirectResponse(
            "/knowledge-services?error=invalid_knowledge_service_url",
            status_code=303,
        )

    credential_id = None
    if api_key:
        credential = store_credential(
            session,
            "knowledge_service",
            {"api_key": api_key},
        )
        credential_id = credential.id

    service = KnowledgeService(
        name=name.strip(),
        service_type="lightrag",
        api_base_url=normalized_api,
        webui_url=normalized_webui,
        credential_id=credential_id,
    )
    session.add(service)
    session.commit()
    session.refresh(service)
    record_audit(
        session,
        current_user,
        "knowledge.create",
        "knowledge_service",
        str(service.id),
        f"Created Knowledge Service {service.name}",
        after={
            "name": service.name,
            "api_base_url": service.api_base_url,
            "webui_url": service.webui_url,
            "enabled": service.enabled,
            "credential_configured": service.credential_id is not None,
        },
    )
    return RedirectResponse("/knowledge-services?saved=1", status_code=303)


@router.post("/{service_id}/update")
def update_knowledge_service(
    service_id: int,
    name: str = Form(...),
    api_base_url: str = Form(...),
    webui_url: str = Form(""),
    api_key: str = Form(""),
    enabled: str | None = Form(None),
    admin_email: str = Depends(require_csrf),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    del admin_email
    service = session.get(KnowledgeService, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Knowledge Service not found")
    before = {
        "name": service.name,
        "api_base_url": service.api_base_url,
        "webui_url": service.webui_url,
        "enabled": service.enabled,
        "credential_configured": service.credential_id is not None,
    }

    normalized_api = api_base_url.strip().rstrip("/")
    normalized_webui = webui_url.strip()
    if not name.strip() or not _is_http_url(normalized_api) or not _is_http_url(
        normalized_webui,
        allow_empty=True,
    ):
        return RedirectResponse(
            "/knowledge-services?error=invalid_knowledge_service_url",
            status_code=303,
        )

    if api_key:
        if service.credential_id is None:
            credential = store_credential(
                session,
                "knowledge_service",
                {"api_key": api_key},
            )
            service.credential_id = credential.id
        else:
            replace_credential(
                session,
                service.credential_id,
                {"api_key": api_key},
            )

    service.name = name.strip()
    service.api_base_url = normalized_api
    service.webui_url = normalized_webui
    service.enabled = enabled == "on"
    service.updated_at = utc_now()
    session.add(service)
    session.commit()
    after = {
        "name": service.name,
        "api_base_url": service.api_base_url,
        "webui_url": service.webui_url,
        "enabled": service.enabled,
        "credential_configured": service.credential_id is not None,
    }
    record_audit(
        session,
        current_user,
        "knowledge.update",
        "knowledge_service",
        str(service_id),
        f"Updated Knowledge Service {service.name}",
        before=before,
        after=after,
    )
    if api_key:
        record_audit(
            session,
            current_user,
            "knowledge.credential_replace",
            "knowledge_service",
            str(service_id),
            f"Replaced credential for Knowledge Service {service.name}",
            after={"credential_replaced": True},
        )
    if before["enabled"] and not service.enabled:
        record_audit(
            session,
            current_user,
            "knowledge.disable",
            "knowledge_service",
            str(service_id),
            f"Disabled Knowledge Service {service.name}",
            before={"enabled": True},
            after={"enabled": False},
        )
    return RedirectResponse("/knowledge-services?saved=1", status_code=303)


@router.post("/{service_id}/disable")
def disable_knowledge_service(
    service_id: int,
    admin_email: str = Depends(require_csrf),
    current_user: CurrentUser = Depends(require_current_user),
    session: Session = Depends(get_session),
) -> Response:
    del admin_email
    try:
        RetentionService(session).disable_knowledge_service(
            service_id,
            actor_user_id=current_user.id,
        )
    except RetentionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse("/knowledge-services?saved=1", status_code=303)


@router.post("/{service_id}/test")
async def test_knowledge_service_connection(
    service_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    del admin_email
    service = session.get(KnowledgeService, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Knowledge Service not found")

    try:
        client = build_knowledge_client(session, service_id)
        health = await client.test_connection()
    except Exception as exc:
        logger.warning(
            "Knowledge Service connection test failed service_id=%s error_type=%s",
            service_id,
            type(exc).__name__,
        )
        service.health_status = "unavailable"
        service.last_health_check = utc_now()
        session.add(service)
        session.commit()
        return RedirectResponse(
            "/knowledge-services?error=knowledge_service_unavailable",
            status_code=303,
        )

    service.health_status = health.status
    service.last_health_check = utc_now()
    session.add(service)
    session.commit()
    if health.status == "healthy":
        return RedirectResponse("/knowledge-services?tested=1", status_code=303)
    error_code = health.detail_code if health.detail_code in _SAFE_ERROR_CODES else "knowledge_service_unavailable"
    return RedirectResponse(f"/knowledge-services?error={error_code}", status_code=303)


@router.post("/{service_id}/test-retrieval", response_class=HTMLResponse)
async def test_knowledge_service_retrieval(
    service_id: int,
    request: Request,
    query: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    service = session.get(KnowledgeService, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Knowledge Service not found")

    try:
        client = build_knowledge_client(session, service_id)
        answer = await client.query(
            KnowledgeQuery(
                question=query.strip(),
                user_prompt="Return a concise grounded answer for this administrator connection test.",
                conversation_history=[],
            )
        )
    except KnowledgeServiceError as exc:
        code = str(exc)
        if code not in _SAFE_ERROR_CODES:
            code = "knowledge_service_unavailable"
        return _render_registry(request, admin_email, session, error=code)
    except Exception as exc:
        logger.warning(
            "Knowledge Service retrieval test failed service_id=%s error_type=%s",
            service_id,
            type(exc).__name__,
        )
        return _render_registry(
            request,
            admin_email,
            session,
            error="knowledge_service_unavailable",
        )

    return _render_registry(
        request,
        admin_email,
        session,
        retrieval_result={
            "service_id": service_id,
            "query": query.strip(),
            "text": answer.text,
            "references": answer.references,
            "latency_ms": answer.latency_ms,
        },
    )
