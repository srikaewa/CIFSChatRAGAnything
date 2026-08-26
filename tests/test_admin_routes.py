import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session

from chatbot_manager.admin.routes import index_document_task, rag_service_from_assistant
from chatbot_manager.db import get_engine
from chatbot_manager.models import AssistantSettings, Channel, KnowledgeDocument
from chatbot_manager.settings import get_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    assert marker in page.text
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def test_dashboard_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_with_default_admin(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_rejects_bad_password(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "wrong"},
    )

    assert response.status_code == 401
    assert "Invalid login" in response.text


def test_rule_create_and_list(client: TestClient) -> None:
    csrf_token = login(client)

    created = client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    page = client.get("/rules")
    assert "price" in page.text
    assert "Price is 100." in page.text


def test_assistant_settings_update(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/assistant",
        data={
            "csrf_token": csrf_token,
            "system_prompt": "Use shop docs.",
            "fallback_reply": "Ask staff.",
            "rag_enabled": "on",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "secret-key-123456",
            "llm_model": "gpt-4o-mini",
            "vision_model": "gpt-4o-mini",
            "embedding_model": "text-embedding-3-small",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get("/assistant")
    assert "Use shop docs." in page.text
    assert "https://llm.example/v1" in page.text
    assert "sec...456" in page.text
    assert "secret-key-123456" not in page.text
    assert "text-embedding-3-small" in page.text


def test_rag_service_from_assistant_uses_env_api_key_when_assistant_key_empty(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    get_settings.cache_clear()

    service = rag_service_from_assistant(AssistantSettings(llm_api_key=""))

    assert service.llm_api_key == "env-key"
    get_settings.cache_clear()


def test_test_chat_uses_rule_and_logs_event(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )

    response = client.post("/test-chat", data={"csrf_token": csrf_token, "message": "what price"}, follow_redirects=False)

    assert response.status_code == 200
    assert "Price is 100." in response.text
    assert "rule" in response.text

    logs = client.get("/logs")
    assert "what price" in logs.text
    assert "Price is 100." in logs.text


def test_knowledge_page_loads(client: TestClient) -> None:
    login(client)

    response = client.get("/knowledge")

    assert response.status_code == 200
    assert "Upload documents" in response.text


def test_knowledge_upload_records_document(client: TestClient, monkeypatch) -> None:
    async def fake_index(document_id: int, path: str) -> None:
        return None

    monkeypatch.setattr("chatbot_manager.admin.routes.index_document_task", fake_index)
    csrf_token = login(client)

    response = client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("menu.txt", b"Menu content", "text/plain"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get("/knowledge")
    assert "menu.txt" in page.text


def test_knowledge_status_api_returns_documents(client: TestClient, monkeypatch) -> None:
    async def fake_index(document_id: int, path: str) -> None:
        return None

    monkeypatch.setattr("chatbot_manager.admin.routes.index_document_task", fake_index)
    csrf_token = login(client)
    client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("menu.txt", b"Menu content", "text/plain"))],
        follow_redirects=False,
    )

    response = client.get("/api/knowledge-documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["filename"] == "menu.txt"
    assert response.json()["documents"][0]["status"] == "pending"
    assert response.json()["documents"][0]["rag_doc_id"] == "knowledge-1"


def test_knowledge_document_can_be_reindexed(client: TestClient, monkeypatch) -> None:
    queued: list[tuple[int, str, bool]] = []

    async def fake_index(document_id: int, path: str, reindex: bool = False) -> None:
        queued.append((document_id, path, reindex))

    monkeypatch.setattr("chatbot_manager.admin.routes.index_document_task", fake_index)
    csrf_token = login(client)
    client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("menu.txt", b"Menu content", "text/plain"))],
        follow_redirects=False,
    )

    response = client.post(
        "/knowledge/1/reindex",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge"
    status = client.get("/api/knowledge-documents").json()["documents"][0]
    assert status["status"] == "pending"
    assert status["error"] == ""
    assert status["rag_doc_id"] == "knowledge-1"
    document_id, queued_path, reindex = queued[-1]
    assert document_id == 1
    assert Path(queued_path) == Path("data/uploads/menu.txt")
    assert reindex is True


@pytest.mark.asyncio
async def test_index_document_task_passes_rag_doc_id_to_index(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeRagService:
        async def index_document(self, path, rag_doc_id: str):
            calls.append((str(path), rag_doc_id))

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeRagService())
    with Session(get_engine()) as session:
        document = KnowledgeDocument(filename="menu.txt", path="data/uploads/menu.txt", rag_doc_id="knowledge-9")
        session.add(document)
        session.commit()
        session.refresh(document)
        document_id = document.id

    await index_document_task(document_id, "data/uploads/menu.txt")

    assert len(calls) == 1
    assert Path(calls[0][0]) == Path("data/uploads/menu.txt")
    assert calls[0][1] == "knowledge-9"


@pytest.mark.asyncio
async def test_index_document_task_uses_reindex_mode(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeRagService:
        async def reindex_document(self, path, rag_doc_id: str):
            calls.append((str(path), rag_doc_id))

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeRagService())
    with Session(get_engine()) as session:
        document = KnowledgeDocument(filename="menu.txt", path="data/uploads/menu.txt", rag_doc_id="knowledge-9")
        session.add(document)
        session.commit()
        session.refresh(document)
        document_id = document.id

    await index_document_task(document_id, "data/uploads/menu.txt", reindex=True)

    assert len(calls) == 1
    assert Path(calls[0][0]) == Path("data/uploads/menu.txt")
    assert calls[0][1] == "knowledge-9"


def test_knowledge_upload_skips_unsupported_file_type(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("script.exe", b"bad", "application/octet-stream"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get("/knowledge")
    assert "script.exe" not in page.text


def test_knowledge_graph_page_requires_login(client: TestClient) -> None:
    response = client.get("/knowledge-graph", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_knowledge_graph_page_loads(client: TestClient) -> None:
    login(client)

    response = client.get("/knowledge-graph")

    assert response.status_code == 200
    assert "Knowledge Graph" in response.text
    assert 'id="graph-mode-select"' in response.text
    assert 'value="all"' in response.text
    assert '<select id="entity-label-select" name="label" required>' in response.text
    assert '<script src="/static/vendor/cytoscape.min.js"></script>' in response.text
    assert 'id="graph-layout-select"' in response.text
    assert 'id="graph-search"' in response.text
    assert 'id="graph-node-type-filter"' in response.text
    assert 'id="graph-canvas"' in response.text
    assert "function graphErrorMessage(" in response.text


def test_knowledge_graph_labels_api_returns_labels(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def entity_labels(self):
            return ["DNA", "Forensic Science"]

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph/labels")

    assert response.status_code == 200
    assert response.json() == {"labels": ["DNA", "Forensic Science"]}


def test_knowledge_graph_api_returns_graph(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int):
            return {
                "nodes": [
                    {"id": label, "label": "Product", "properties": {"description": "Main product"}},
                    {"id": "price", "label": "Price", "properties": {}},
                ],
                "edges": [
                    {"id": "product-price", "source": label, "target": "price", "type": "HAS_PRICE", "properties": {}}
                ],
                "is_truncated": False,
            }

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph", params={"label": "Product", "max_depth": "2", "max_nodes": "20"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["graph_type"] == "concept_graph"
    assert payload["center_node_id"] == "Product"
    assert payload["depth"] == 2
    assert payload["stats"] == {
        "node_count": 2,
        "edge_count": 1,
        "visible_node_count": 2,
        "visible_edge_count": 1,
        "filtered": False,
    }
    assert payload["warnings"] == []
    assert payload["nodes"][0] == {
        "id": "Product",
        "label": "Product",
        "type": "entity",
        "graph_type": "concept_graph",
        "degree": 1,
        "source_count": 0,
        "metadata": {"description": "Main product"},
    }
    assert payload["edges"][0] == {
        "id": "product-price",
        "source": "Product",
        "target": "price",
        "type": "HAS_PRICE",
        "graph_type": "concept_graph",
        "label": "HAS_PRICE",
        "direction": "directed",
        "metadata": {},
    }


def test_knowledge_graph_api_returns_all_graph(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def knowledge_graph_all(self, max_nodes: int):
            return {
                "nodes": [
                    {"id": "DNA", "label": "DNA", "properties": {}},
                    {"id": "STR", "label": "STR", "properties": {}},
                ],
                "edges": [
                    {"id": "dna-str", "source": "DNA", "target": "STR", "type": "RELATED", "properties": {}}
                ],
                "is_truncated": True,
            }

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph", params={"mode": "all", "max_nodes": "200"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["center_node_id"] is None
    assert payload["depth"] == 0
    assert payload["stats"]["node_count"] == 2
    assert payload["warnings"] == ["Graph truncated. Increase node limit or use focused mode."]


def test_knowledge_graph_api_local_mode_requires_label(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int):
            return {"nodes": [], "edges": [], "is_truncated": False}

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph", params={"mode": "local", "label": ""})

    assert response.status_code == 400
    assert response.json()["detail"] == "Graph label is required in focused mode."


def test_knowledge_graph_api_reports_unavailable_backend(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int):
            raise RuntimeError("Knowledge graph is not available. Index documents before opening the graph.")

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph", params={"label": "Product"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Knowledge graph is not available. Index documents before opening the graph."


def test_knowledge_graph_api_warns_when_empty(client: TestClient, monkeypatch) -> None:
    class FakeGraphService:
        async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int):
            return {"nodes": [], "edges": [], "is_truncated": False}

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FakeGraphService())
    login(client)

    response = client.get("/api/knowledge-graph", params={"label": "Missing"})

    assert response.status_code == 200
    assert response.json()["stats"]["node_count"] == 0
    assert response.json()["warnings"] == ["No graph nodes found for this label."]


def test_channels_page_shows_webhook_urls(client: TestClient) -> None:
    login(client)

    response = client.get("/channels")

    assert response.status_code == 200
    assert "/webhooks/line" in response.text
    assert "/webhooks/messenger" in response.text


def test_channel_config_can_be_saved_and_masked(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "line-secret-123456",
            "channel_access_token": "line-token-654321",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] in ("/channels?saved=1", "/channels")
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert channel.provider == "line"
        assert channel.enabled is True
        assert json.loads(channel.credential_json) == {
            "channel_secret": "line-secret-123456",
            "channel_access_token": "line-token-654321",
        }

    page = client.get("/channels")
    assert "lin...456" in page.text
    assert "line-secret-123456" not in page.text
    assert "line-token-654321" not in page.text


def test_channel_config_blank_secret_keeps_existing_value(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "line-secret",
            "channel_access_token": "line-token",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "",
            "channel_access_token": "new-line-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert json.loads(channel.credential_json) == {
            "channel_secret": "line-secret",
            "channel_access_token": "new-line-token",
        }


def test_dashboard_uses_saved_channel_config_state(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/channels/line",
        data={"csrf_token": csrf_token, "enabled": "on", "channel_secret": "line-secret", "channel_access_token": "line-token"},
        follow_redirects=False,
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "LINE" in response.text
    assert "Configured" in response.text



