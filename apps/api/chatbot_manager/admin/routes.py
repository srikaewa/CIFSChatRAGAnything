import json
import logging
import re
from pathlib import Path
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from chatbot_manager.channel_config import CHANNEL_DEFINITIONS, channel_cards, channel_credentials, get_channel, save_channel, update_channel_credentials
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput, RESPONSE_GENERATION_FAILED_MESSAGE
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, KnowledgeDocument, Rule, utc_now
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.rag.service import rag_service_from_assistant
from chatbot_manager.security import decrypt_secret, encrypt_secret, make_csrf_token, make_session_token, mask_secret, read_session_token, verify_admin, verify_csrf_token
from chatbot_manager.settings import get_settings

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["csrf_token_for"] = make_csrf_token
router = APIRouter()
SUPPORTED_KNOWLEDGE_EXTENSIONS = {
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".tiff",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
}


class UploadTooLargeError(Exception):
    pass


async def write_bounded_upload(file: UploadFile, target: Path, max_bytes: int) -> None:
    total = 0
    try:
        with target.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise

LLM_MODELS = [
    "gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5.1", "gpt-5",
    "gpt-5-mini", "gpt-5-nano",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-4o", "gpt-4o-mini",
    "o3", "o3-mini", "o4-mini", "o1",
]

VISION_MODELS = [
    "gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5.1", "gpt-5",
    "gpt-4o", "gpt-4o-mini",
    "o3", "o4-mini",
    "gpt-image-2.0",
]

EMBEDDING_MODELS = [
    "text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002",
]

SUPPORTED_ADMIN_NOTIFY_CHANNELS = {"telegram"}


def validate_admin_notification_settings(channel: str, destination: str) -> tuple[str, str]:
    normalized_channel = channel.strip().lower()
    normalized_destination = destination.strip()
    if normalized_channel not in SUPPORTED_ADMIN_NOTIFY_CHANNELS:
        raise ValueError("unsupported_notification_channel")
    if normalized_destination and re.fullmatch(r"-?\d+", normalized_destination) is None:
        raise ValueError("invalid_notification_destination")
    return normalized_channel, normalized_destination


def require_admin(request: Request) -> str:
    settings = get_settings()
    email = read_session_token(request.cookies.get("admin_session"), settings.admin_session_max_age_seconds)
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


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)) -> Response:
    if not verify_admin(email, password):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid login"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    settings = get_settings()
    response.set_cookie(
        "admin_session",
        make_session_token(email),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.admin_session_max_age_seconds,
    )
    return response


@router.post("/logout")
def logout(admin_email: str = Depends(require_csrf)) -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin_email": admin_email,
            "settings": settings,
            "active_page": "dashboard",
            "channel_cards": channel_cards(session, settings),
        },
    )


@router.get("/channels", response_class=HTMLResponse)
def channels_page(
    request: Request,
    saved: str | None = None,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    cards = channel_cards(session, settings)
    telegram_card = next((c for c in cards if c["provider"] == "telegram"), {})
    base_url = settings.api_public_url
    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "admin_email": admin_email,
            "active_page": "channels",
            "channel_cards": cards,
            "webhook_urls": {c["provider"]: f"{base_url}/webhooks/{c['provider']}" for c in cards},
            "telegram_webhook_active": telegram_card.get("webhook_url", ""),
            "saved": saved == "1",
            "error": {
                "channel_save_failed": "Could not save channel settings. Check the configuration and retry.",
            }.get(error or "", error or ""),
        },
    )


@router.post("/channels/{provider}")
def update_channel(
    provider: str,
    enabled: str | None = Form(None),
    channel_secret: str = Form(""),
    channel_access_token: str = Form(""),
    verify_token: str = Form(""),
    page_access_token: str = Form(""),
    app_secret: str = Form(""),
    bot_token: str = Form(""),
    webhook_secret: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    if provider not in CHANNEL_DEFINITIONS:
        raise HTTPException(status_code=404, detail="Unknown channel")
    try:
        save_channel(
            session,
            provider=provider,
            enabled=enabled == "on",
            incoming_credentials={
                "channel_secret": channel_secret,
                "channel_access_token": channel_access_token,
                "verify_token": verify_token,
                "page_access_token": page_access_token,
                "app_secret": app_secret,
                "bot_token": bot_token,
                "webhook_secret": webhook_secret,
            },
        )
    except Exception as exc:
        logger.error("Channel save failed provider=%s error_type=%s", provider, type(exc).__name__)
        return RedirectResponse("/channels?error=channel_save_failed", status_code=303)
    return RedirectResponse("/channels?saved=1", status_code=303)


@router.post("/channels/telegram/setup-webhook")
async def telegram_setup_webhook(
    request: Request,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    channel = get_channel(session, "telegram")
    if channel is None:
        return RedirectResponse("/channels?error=Save+Telegram+channel+credentials+first", status_code=303)
    credentials = channel_credentials(session, settings, "telegram")
    if not credentials.get("bot_token"):
        return RedirectResponse("/channels?error=Telegram+bot+token+is+not+configured", status_code=303)
    if not credentials.get("webhook_secret"):
        return RedirectResponse("/channels?error=Telegram+webhook+secret+is+not+configured", status_code=303)
    adapter = TelegramAdapter(credentials["bot_token"], credentials["webhook_secret"])
    import subprocess, json
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return RedirectResponse(f"/channels?error=Tailscale+status+failed", status_code=303)
        ts_status = json.loads(result.stdout)
        dns_name = ts_status.get("Self", {}).get("DNSName", "")
        if not dns_name:
            return RedirectResponse(f"/channels?error=Could+not+determine+Tailscale+DNS+name", status_code=303)
        funnel_host = dns_name.rstrip(".")
    except FileNotFoundError:
        return RedirectResponse(f"/channels?error=Tailscale+CLI+not+found", status_code=303)
    except json.JSONDecodeError:
        return RedirectResponse(f"/channels?error=Invalid+Tailscale+status+output", status_code=303)
    port = settings.api_public_url.split(":")[-1] if ":" in settings.api_public_url else "8000"
    funnel_url = f"https://{funnel_host}/webhooks/telegram"
    funnel_result = subprocess.run(
        ["sudo", "tailscale", "funnel", "--bg", port],
        capture_output=True, text=True, timeout=15,
    )
    if funnel_result.returncode != 0:
        detail = funnel_result.stderr.strip()
        if "Access denied" in detail:
            return RedirectResponse(f"/channels?error=Tailscale+funnel+failed:+Access+denied.+Run+sudo+tailscale+set+--operator=$USER", status_code=303)
        return RedirectResponse(f"/channels?error=Tailscale+funnel+failed", status_code=303)
    wh_result = await adapter.set_webhook(funnel_url)
    if not wh_result.get("ok"):
        return RedirectResponse(f"/channels?error=Telegram+setWebhook+failed", status_code=303)
    update_channel_credentials(session, "telegram", {"webhook_url": funnel_url})
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/telegram/disable-webhook")
async def telegram_disable_webhook(
    request: Request,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    credentials = channel_credentials(session, settings, "telegram")
    if not credentials.get("bot_token"):
        return RedirectResponse("/channels?error=Telegram+bot+token+is+not+configured", status_code=303)
    adapter = TelegramAdapter(credentials["bot_token"], credentials["webhook_secret"])
    wh_result = await adapter.delete_webhook()
    if not wh_result.get("ok"):
        return RedirectResponse(f"/channels?error=Telegram+deleteWebhook+failed", status_code=303)
    import subprocess
    port = settings.dashboard_url.split(":")[-1] if ":" in settings.dashboard_url else "8000"
    subprocess.run(
        ["sudo", "tailscale", "funnel", "off", port],
        capture_output=True, text=True, timeout=15,
    )
    update_channel_credentials(session, "telegram", {"webhook_url": ""})
    return RedirectResponse("/channels", status_code=303)




def assistant_settings(session: Session) -> AssistantSettings:
    settings = session.get(AssistantSettings, 1)
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def clamp_query_value(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def normalize_graph_response(graph: dict[str, object], center_node_id: str | None, depth: int) -> dict[str, object]:
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    edges = raw_edges if isinstance(raw_edges, list) else []
    degree_by_node: dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        degree_by_node[source] = degree_by_node.get(source, 0) + 1
        degree_by_node[target] = degree_by_node.get(target, 0) + 1

    normalized_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        properties = node.get("properties", {})
        metadata = properties if isinstance(properties, dict) else {}
        normalized_nodes.append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id),
                "type": str(node.get("type") or "entity"),
                "graph_type": "concept_graph",
                "degree": degree_by_node.get(node_id, 0),
                "source_count": int(node.get("source_count") or 0),
                "metadata": metadata,
            }
        )

    normalized_edges = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_type = str(edge.get("type") or "related_to")
        properties = edge.get("properties", {})
        metadata = properties if isinstance(properties, dict) else {}
        normalized_edges.append(
            {
                "id": str(edge.get("id") or f"{edge.get('source', '')}-{edge.get('target', '')}-{edge_type}"),
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "type": edge_type,
                "graph_type": "concept_graph",
                "label": str(edge.get("label") or edge_type),
                "direction": str(edge.get("direction") or "directed"),
                "metadata": metadata,
            }
        )

    warnings: list[str] = []
    if not normalized_nodes:
        warnings.append("No graph nodes found." if center_node_id is None else "No graph nodes found for this label.")
    if bool(graph.get("is_truncated", False)):
        warnings.append("Graph truncated. Increase node limit or use focused mode.")

    return {
        "graph_type": "concept_graph",
            "center_node_id": center_node_id,
        "depth": depth,
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "is_truncated": bool(graph.get("is_truncated", False)),
        "stats": {
            "node_count": len(normalized_nodes),
            "edge_count": len(normalized_edges),
            "visible_node_count": len(normalized_nodes),
            "visible_edge_count": len(normalized_edges),
            "filtered": False,
        },
        "warnings": warnings,
    }


def rag_doc_id_for(document: KnowledgeDocument) -> str:
    if document.id is None:
        raise RuntimeError("Knowledge document must be saved before assigning a RAG document id.")
    return document.rag_doc_id or f"knowledge-{document.id}"


@router.get("/rules", response_class=HTMLResponse)
def rules_page(
    request: Request,
    edit: int | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    rules = session.exec(select(Rule).order_by(Rule.priority)).all()
    editing_rule = session.get(Rule, edit) if edit else None
    extra_conditions = []
    if editing_rule and editing_rule.conditions:
        try:
            extra_conditions = json.loads(editing_rule.conditions)
        except (json.JSONDecodeError, TypeError):
            extra_conditions = []
    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "admin_email": admin_email,
            "active_page": "rules",
            "rules": rules,
            "editing_rule": editing_rule,
            "extra_conditions": extra_conditions,
        },
    )


def _parse_extra_conditions(
    cond_pattern: list[str],
    cond_match_type: list[str],
) -> str:
    extra = []
    for i in range(len(cond_pattern)):
        p = cond_pattern[i].strip()
        if p:
            mt = cond_match_type[i] if i < len(cond_match_type) else "contains"
            extra.append({"pattern": p, "match_type": mt})
    return json.dumps(extra)


@router.post("/rules")
def create_rule(
    pattern: str = Form(...),
    match_type: str = Form(...),
    condition_logic: str = Form("and"),
    cond_pattern: list[str] = Form([]),
    cond_match_type: list[str] = Form([]),
    reply_text: str = Form(...),
    priority: int = Form(100),
    escalate: str | None = Form(None),
    escalate_message: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    session.add(Rule(
        pattern=pattern,
        match_type=match_type,
        condition_logic=condition_logic,
        conditions=_parse_extra_conditions(cond_pattern, cond_match_type),
        reply_text=reply_text,
        priority=priority,
        escalate=escalate == "on",
        escalate_message=escalate_message,
    ))
    session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/update")
def update_rule(
    rule_id: int,
    pattern: str = Form(...),
    match_type: str = Form(...),
    condition_logic: str = Form("and"),
    cond_pattern: list[str] = Form([]),
    cond_match_type: list[str] = Form([]),
    reply_text: str = Form(...),
    priority: int = Form(100),
    escalate: str | None = Form(None),
    escalate_message: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.pattern = pattern
    rule.match_type = match_type
    rule.condition_logic = condition_logic
    rule.conditions = _parse_extra_conditions(cond_pattern, cond_match_type)
    rule.reply_text = reply_text
    rule.priority = priority
    rule.escalate = escalate == "on"
    rule.escalate_message = escalate_message
    rule.updated_at = utc_now()
    session.add(rule)
    session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(
    rule_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(
    request: Request,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = assistant_settings(session)
    return templates.TemplateResponse(
        request,
        "assistant.html",
        {
            "admin_email": admin_email,
            "active_page": "assistant",
            "settings": settings,
            "masked_llm_api_key": mask_secret(decrypt_secret(settings.llm_api_key, get_settings().app_encryption_key)),
            "llm_models": LLM_MODELS,
            "vision_models": VISION_MODELS,
            "embedding_models": EMBEDDING_MODELS,
            "error": {
                "unsupported_notification_channel": "Only Telegram is supported for admin notifications.",
                "invalid_notification_destination": "Telegram chat ID must be an integer.",
            }.get(error or "", ""),
        },
    )


@router.post("/assistant")
def update_assistant(
    system_prompt: str = Form(...),
    fallback_reply: str = Form(...),
    rag_enabled: str | None = Form(None),
    llm_base_url: str = Form(...),
    llm_api_key: str = Form(""),
    llm_model: str = Form(...),
    vision_model: str = Form(...),
    embedding_model: str = Form(...),
    admin_notify_channel: str = Form("telegram"),
    admin_notify_chat_id: str = Form(""),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    settings = assistant_settings(session)
    settings.system_prompt = system_prompt
    settings.fallback_reply = fallback_reply
    settings.rag_enabled = rag_enabled == "on"
    settings.llm_base_url = llm_base_url
    encryption_key = get_settings().app_encryption_key
    current_api_key = decrypt_secret(settings.llm_api_key, encryption_key)
    plaintext_api_key = llm_api_key.strip() or current_api_key
    settings.llm_api_key = encrypt_secret(plaintext_api_key, encryption_key)
    settings.llm_model = llm_model
    settings.vision_model = vision_model
    settings.embedding_model = embedding_model
    try:
        notify_channel, notify_destination = validate_admin_notification_settings(
            admin_notify_channel, admin_notify_chat_id
        )
    except ValueError as exc:
        error_code = str(exc)
        if error_code not in {"unsupported_notification_channel", "invalid_notification_destination"}:
            error_code = "invalid_notification_destination"
        return RedirectResponse(f"/assistant?error={error_code}", status_code=303)
    settings.admin_notify_channel = notify_channel
    settings.admin_notify_chat_id = notify_destination
    settings.updated_at = utc_now()
    session.add(settings)
    session.commit()
    return RedirectResponse("/assistant", status_code=303)


@router.get("/test-chat", response_class=HTMLResponse)
def test_chat_page(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    return templates.TemplateResponse(
        request,
        "test_chat.html",
        {"admin_email": admin_email, "active_page": "test_chat", "message": "", "reply": "", "source": ""},
    )


@router.post("/test-chat", response_class=HTMLResponse)
async def test_chat_submit(
    request: Request,
    message: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    rules = session.exec(select(Rule).order_by(Rule.priority)).all()
    settings = assistant_settings(session)
    engine = ChatbotEngine(rag_service=rag_service_from_assistant(settings))
    decision = await engine.answer(
        ChatbotInput(text=message, provider="test", external_user_id="admin"),
        rules=list(rules),
        settings=settings,
    )
    session.add(
        ChatEvent(
            provider="test",
            external_user_id="admin",
            incoming_text=message,
            decision_source=decision.source,
            reply_text=decision.reply_text,
            error=decision.error,
        )
    )
    session.commit()
    return templates.TemplateResponse(
        request,
        "test_chat.html",
        {
            "admin_email": admin_email,
            "active_page": "test_chat",
            "message": message,
            "reply": decision.reply_text,
            "source": decision.source,
            "error": RESPONSE_GENERATION_FAILED_MESSAGE if decision.error else "",
        },
    )


@router.get("/logs", response_class=HTMLResponse)
def logs_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    events = session.exec(select(ChatEvent).order_by(ChatEvent.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"admin_email": admin_email, "active_page": "logs", "events": events},
    )


async def index_document_task(document_id: int, path: str, reindex: bool = False) -> None:
    from chatbot_manager.db import get_engine

    with Session(get_engine()) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return
        try:
            settings = assistant_settings(session)
            document.rag_doc_id = rag_doc_id_for(document)
            rag_service = rag_service_from_assistant(settings)
            if reindex:
                await rag_service.reindex_document(Path(path), rag_doc_id=document.rag_doc_id)
            else:
                await rag_service.index_document(Path(path), rag_doc_id=document.rag_doc_id)
            document.status = "indexed"
            document.error = ""
            document.indexed_at = utc_now()
        except Exception as exc:
            document.status = "failed"
            logger.error(
                "Knowledge indexing failed document_id=%s error_type=%s",
                document.id,
                type(exc).__name__,
            )
            document.error = "Knowledge indexing failed. Retry indexing."
        document.updated_at = utc_now()
        session.add(document)
        session.commit()


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    error: str | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    documents = session.exec(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).all()
    return templates.TemplateResponse(
        request,
        "knowledge.html",
        {
            "admin_email": admin_email,
            "active_page": "knowledge",
            "documents": documents,
            "error": {
                "file_too_large": "File exceeds the configured upload size limit.",
                "unsupported_file_type": "Unsupported file type.",
                "delete_failed": "Could not remove the document from the knowledge index. Retry deletion.",
            }.get(error or "", ""),
        },
    )


@router.get("/api/knowledge-documents")
def knowledge_documents_api(
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, list[dict[str, str | int | None]]]:
    documents = session.exec(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).all()
    return {
        "documents": [
            {
                "id": document.id,
                "filename": document.filename,
                "rag_doc_id": document.rag_doc_id,
                "status": document.status,
                "indexed_at": document.indexed_at.isoformat(sep=" ") if document.indexed_at else "",
                "error": document.error,
            }
            for document in documents
        ]
    }


@router.get("/knowledge-graph", response_class=HTMLResponse)
def knowledge_graph_page(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    return templates.TemplateResponse(
        request,
        "knowledge_graph.html",
        {
            "admin_email": admin_email,
            "active_page": "knowledge_graph",
            "default_label": "Product",
            "default_max_depth": 2,
            "default_max_nodes": 200,
        },
    )


@router.get("/api/knowledge-graph")
async def knowledge_graph_api(
    label: str = "",
    mode: str = "local",
    max_depth: int = 2,
    max_nodes: int = 200,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    settings = assistant_settings(session)
    graph_mode = mode.strip().lower() or "local"
    node_limit = clamp_query_value(max_nodes, 1, 500)
    try:
        graph_service = rag_service_from_assistant(settings)
        if graph_mode == "all":
            graph = await graph_service.knowledge_graph_all(max_nodes=node_limit)
            return normalize_graph_response(graph, center_node_id=None, depth=0)

        graph_label = label.strip()
        if not graph_label:
            raise HTTPException(status_code=400, detail="Graph label is required in focused mode.")
        depth = clamp_query_value(max_depth, 1, 5)
        graph = await graph_service.knowledge_graph(label=graph_label, max_depth=depth, max_nodes=node_limit)
        return normalize_graph_response(graph, center_node_id=graph_label, depth=depth)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/knowledge-graph/labels")
async def knowledge_graph_labels_api(
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, list[str]]:
    settings = assistant_settings(session)
    try:
        return {"labels": await rag_service_from_assistant(settings).entity_labels()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/knowledge")
async def upload_knowledge(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        if not file.filename:
            continue
        filename = PurePath(file.filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
            return RedirectResponse("/knowledge?error=unsupported_file_type", status_code=303)

        target = settings.upload_dir / f"{uuid4().hex}{suffix}"
        try:
            await write_bounded_upload(file, target, settings.max_upload_bytes)
        except UploadTooLargeError:
            return RedirectResponse("/knowledge?error=file_too_large", status_code=303)

        document = KnowledgeDocument(filename=filename, path=str(target), status="pending")
        session.add(document)
        session.commit()
        session.refresh(document)
        document.rag_doc_id = rag_doc_id_for(document)
        session.add(document)
        session.commit()
        background_tasks.add_task(index_document_task, document.id, str(target))

    return RedirectResponse("/knowledge", status_code=303)


@router.post("/knowledge/{document_id}/reindex")
def reindex_knowledge(
    document_id: int,
    background_tasks: BackgroundTasks,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    document.rag_doc_id = rag_doc_id_for(document)
    document.status = "pending"
    document.error = ""
    document.indexed_at = None
    document.updated_at = utc_now()
    session.add(document)
    session.commit()
    background_tasks.add_task(index_document_task, document.id, document.path, True)
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/knowledge/{document_id}/delete")
async def delete_knowledge(
    document_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    settings = assistant_settings(session)
    rag_service = rag_service_from_assistant(settings)
    try:
        await rag_service.delete_document(document.rag_doc_id)
    except Exception:
        document.status = "failed"
        document.error = "Could not remove the document from the knowledge index. Retry deletion."
        document.updated_at = utc_now()
        session.add(document)
        session.commit()
        return RedirectResponse("/knowledge?error=delete_failed", status_code=303)

    if document.path:
        Path(document.path).unlink(missing_ok=True)

    session.delete(document)
    session.commit()
    return RedirectResponse("/knowledge", status_code=303)
