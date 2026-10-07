from pathlib import Path

from fastapi.testclient import TestClient


def login_owner(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_removed_legacy_admin_pages_are_not_routes(client: TestClient) -> None:
    login_owner(client)
    for path in (
        "/assistant",
        "/rules",
        "/test-chat",
        "/logs",
        "/knowledge",
        "/knowledge-graph",
        "/channels",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path


def test_removed_local_knowledge_apis_are_not_routes(client: TestClient) -> None:
    login_owner(client)
    for path in (
        "/api/knowledge-documents",
        "/api/knowledge-graph",
        "/api/knowledge-graph/labels",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path


def test_primary_product_has_no_local_knowledge_upload_form(
    client: TestClient,
) -> None:
    login_owner(client)
    for path in ("/", "/bots", "/knowledge-services"):
        html = client.get(path).text
        assert 'type="file"' not in html
        assert "Reindex" not in html


def test_production_runtime_does_not_import_in_process_rag_service() -> None:
    root = Path("apps/api/chatbot_manager")
    source = root.rglob("*.py")
    active_files = [
        path
        for path in source
        if "rag/service.py" not in path.as_posix()
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in active_files)
    assert "rag_service_from_assistant" not in text
    assert "RagAnythingService" not in text


def test_legacy_single_assistant_templates_are_removed() -> None:
    template_dir = Path("apps/api/chatbot_manager/templates")
    for name in (
        "assistant.html",
        "rules.html",
        "test_chat.html",
        "logs.html",
        "knowledge.html",
        "knowledge_graph.html",
        "dashboard.html",
        "channels.html",
    ):
        assert not (template_dir / name).exists(), name
