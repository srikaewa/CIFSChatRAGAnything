from fastapi.testclient import TestClient


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_graph_api_rejects_unknown_mode(client: TestClient, monkeypatch) -> None:
    class NoCallService:
        async def knowledge_graph(self, *args, **kwargs):
            raise AssertionError("backend must not be called")

        async def knowledge_graph_all(self, *args, **kwargs):
            raise AssertionError("backend must not be called")

    monkeypatch.setattr(
        "chatbot_manager.admin.routes.rag_service_from_assistant",
        lambda settings: NoCallService(),
    )
    login(client)

    response = client.get(
        "/api/knowledge-graph",
        params={"mode": "invalid", "label": "Product"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Graph mode must be 'all' or 'local'."


def test_graph_api_rejects_overlong_local_label(client: TestClient, monkeypatch) -> None:
    class NoCallService:
        async def knowledge_graph(self, *args, **kwargs):
            raise AssertionError("backend must not be called")

    monkeypatch.setattr(
        "chatbot_manager.admin.routes.rag_service_from_assistant",
        lambda settings: NoCallService(),
    )
    login(client)

    response = client.get(
        "/api/knowledge-graph",
        params={"mode": "local", "label": "x" * 201},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Graph label must be 200 characters or fewer."


def test_graph_api_clamps_local_depth_and_node_limit(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[str, int, int]] = []

    class FakeGraphService:
        async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int):
            calls.append((label, max_depth, max_nodes))
            return {"nodes": [], "edges": [], "is_truncated": False}

    monkeypatch.setattr(
        "chatbot_manager.admin.routes.rag_service_from_assistant",
        lambda settings: FakeGraphService(),
    )
    login(client)

    response = client.get(
        "/api/knowledge-graph",
        params={"mode": "local", "label": "Product", "max_depth": "999", "max_nodes": "9999"},
    )

    assert response.status_code == 200
    assert calls == [("Product", 5, 500)]


def test_graph_page_contains_explicit_state_filter_and_expand_contract(client: TestClient) -> None:
    login(client)
    html = client.get("/knowledge-graph").text

    assert "function resetGraphView(" in html
    assert "function showGraphError(" in html
    assert "function updateVisibleStats(" in html
    assert 'selector: ".filtered-out"' in html
    assert 'style: { display: "none" }' in html
    assert "let selectedNodeLabel" in html
    assert "loadGraph(selectedNodeLabel)" in html
    assert "showGraphError(error.message)" in html
    assert html.count("showGraphError(error.message);") >= 2
    assert "resetGraphView(noEntitiesMessage, noEntitiesMessage);" in html
    assert "resetGraphView(emptyMessage, emptyMessage)" in html
    assert "updateVisibleStats();" in html
