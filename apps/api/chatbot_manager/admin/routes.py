from pathlib import Path
from pathlib import PurePath

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, KnowledgeDocument, Rule, utc_now
from chatbot_manager.rag.service import RagAnythingService
from chatbot_manager.security import make_session_token, mask_secret, read_session_token, verify_admin
from chatbot_manager.settings import get_settings

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
router = APIRouter()
SUPPORTED_KNOWLEDGE_EXTENSIONS = {
    ".bmp",
    ".doc",
    ".docx",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".txt",
    ".xls",
    ".xlsx",
}


def require_admin(request: Request) -> str:
    email = read_session_token(request.cookies.get("admin_session"))
    if not email:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return email


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)) -> Response:
    if not verify_admin(email, password):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid login"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("admin_session", make_session_token(email), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin_email": admin_email,
            "settings": settings,
            "active_page": "dashboard",
        },
    )


@router.get("/channels", response_class=HTMLResponse)
def channels_page(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "admin_email": admin_email,
            "active_page": "channels",
            "settings": settings,
            "line_webhook": f"{settings.api_public_url}/webhooks/line",
            "messenger_webhook": f"{settings.api_public_url}/webhooks/messenger",
        },
    )


def assistant_settings(session: Session) -> AssistantSettings:
    settings = session.get(AssistantSettings, 1)
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def rag_service_from_assistant(settings: AssistantSettings) -> RagAnythingService:
    app_settings = get_settings()
    return RagAnythingService(
        llm_base_url=settings.llm_base_url or app_settings.llm_base_url,
        llm_api_key=settings.llm_api_key or app_settings.llm_api_key,
        llm_model=settings.llm_model or app_settings.llm_default_model,
        vision_model=settings.vision_model or app_settings.llm_vision_model,
        embedding_model=settings.embedding_model or app_settings.llm_embedding_model,
    )


def clamp_query_value(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def normalize_graph_response(graph: dict[str, object], center_node_id: str, depth: int) -> dict[str, object]:
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
        warnings.append("No graph nodes found for this label.")
    if bool(graph.get("is_truncated", False)):
        warnings.append("Graph result was truncated. Use filters to narrow view.")

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
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    rules = session.exec(select(Rule).order_by(Rule.priority)).all()
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"admin_email": admin_email, "active_page": "rules", "rules": rules},
    )


@router.post("/rules")
def create_rule(
    pattern: str = Form(...),
    match_type: str = Form(...),
    reply_text: str = Form(...),
    priority: int = Form(100),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    session.add(Rule(pattern=pattern, match_type=match_type, reply_text=reply_text, priority=priority))
    session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(
    request: Request,
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
            "masked_llm_api_key": mask_secret(settings.llm_api_key),
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
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = assistant_settings(session)
    settings.system_prompt = system_prompt
    settings.fallback_reply = fallback_reply
    settings.rag_enabled = rag_enabled == "on"
    settings.llm_base_url = llm_base_url
    if llm_api_key.strip():
        settings.llm_api_key = llm_api_key.strip()
    settings.llm_model = llm_model
    settings.vision_model = vision_model
    settings.embedding_model = embedding_model
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
    admin_email: str = Depends(require_admin),
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
            document.error = str(exc)
        document.updated_at = utc_now()
        session.add(document)
        session.commit()


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    documents = session.exec(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).all()
    return templates.TemplateResponse(
        request,
        "knowledge.html",
        {"admin_email": admin_email, "active_page": "knowledge", "documents": documents},
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
            "default_max_nodes": 120,
        },
    )


@router.get("/api/knowledge-graph")
async def knowledge_graph_api(
    label: str,
    max_depth: int = 2,
    max_nodes: int = 120,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    graph_label = label.strip()
    if not graph_label:
        raise HTTPException(status_code=400, detail="Graph label is required.")

    settings = assistant_settings(session)
    depth = clamp_query_value(max_depth, 1, 5)
    try:
        graph = await rag_service_from_assistant(settings).knowledge_graph(
            label=graph_label,
            max_depth=depth,
            max_nodes=clamp_query_value(max_nodes, 1, 500),
        )
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
    file: UploadFile,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    filename = PurePath(file.filename or "upload.bin").name
    if Path(filename).suffix.lower() not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    target = settings.upload_dir / filename
    target.write_bytes(await file.read())

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
    admin_email: str = Depends(require_admin),
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
