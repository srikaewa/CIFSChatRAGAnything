# Phase 4 Knowledge Graph Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the bounded Knowledge Graph API/UI contract and close GAP-012 with deterministic browser verification.

**Architecture:** Keep the existing FastAPI/Jinja/Cytoscape structure. Harden API validation in `admin/routes.py`, make graph UI state transitions explicit inside `knowledge_graph.html`, and verify actual Cytoscape rendering/control behavior through managed Chrome without live RAG credentials.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, Jinja2, vanilla JavaScript, bundled Cytoscape, pytest, managed Chrome/CDP.

**Spec:** `docs/superpowers/specs/2026-08-26-phase-4-knowledge-graph-design.md`

## Global Constraints

- Preserve the existing graph response schema.
- Accept only `mode=all` and `mode=local`.
- Clamp depth to 1..5 and max nodes to 1..500.
- Reject local labels longer than 200 characters.
- Do not add graph persistence, new providers, or Phase 5 operational changes.
- Use deterministic browser data; no live LLM/RAG credentials are required.

---

### Task 1: Harden Knowledge Graph API Contract

**Files:**
- Create: `tests/test_phase4_knowledge_graph.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`

**Interfaces:**
- Produces: `GRAPH_ALLOWED_MODES = {"all", "local"}`.
- Produces: `GRAPH_MAX_LABEL_LENGTH = 200`.
- `/api/knowledge-graph` returns 400 for unsupported modes and overlong local labels before backend access.

- [ ] **Step 1: Write failing API tests**

```python
def test_graph_api_rejects_unknown_mode(client, monkeypatch):
    class NoCallService:
        async def knowledge_graph(self, *args, **kwargs):
            raise AssertionError("backend must not be called")
        async def knowledge_graph_all(self, *args, **kwargs):
            raise AssertionError("backend must not be called")
    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: NoCallService())
    login(client)
    response = client.get("/api/knowledge-graph", params={"mode": "invalid", "label": "Product"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Graph mode must be 'all' or 'local'."
```

Add equivalent tests for a 201-character local label and for clamping `max_depth=999` / `max_nodes=9999` to 5 / 500 in the backend call.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
& $PY -m pytest -q tests/test_phase4_knowledge_graph.py --basetemp "$ROOT\pytest-tmp-phase4-api-red"
```

Expected: unknown mode currently falls through to local mode and overlong labels are accepted.

- [ ] **Step 3: Implement minimal API validation**

Add:

```python
GRAPH_ALLOWED_MODES = {"all", "local"}
GRAPH_MAX_LABEL_LENGTH = 200
```

In `knowledge_graph_api`, reject unsupported `mode`; in local mode reject label length above 200 before calling the graph service. Keep existing clamps and canonical normalization.

- [ ] **Step 4: Run focused API tests and existing graph route tests**

Run the new Phase 4 file plus graph tests in `tests/test_admin_routes.py` and `tests/test_rag_service.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_phase4_knowledge_graph.py apps/api/chatbot_manager/admin/routes.py
git commit -m "feat: bound knowledge graph api"
```

---

### Task 2: Make Graph UI State, Filtering, and Expansion Deterministic

**Files:**
- Modify: `tests/test_phase4_knowledge_graph.py`
- Modify: `apps/api/chatbot_manager/templates/knowledge_graph.html`

**Interfaces:**
- Produces JS state helpers `resetGraphView(message, statusMessage)`, `showGraphError(message)`, and `updateVisibleStats()`.
- Produces `selectedNodeLabel` in addition to `selectedNodeId`.
- Filtering uses a `.filtered-out` Cytoscape class with `display: none`.

- [ ] **Step 1: Write failing template-contract tests**

```python
def test_graph_page_contains_explicit_state_and_filter_contract(client):
    login(client)
    html = client.get("/knowledge-graph").text
    assert "function resetGraphView(" in html
    assert "function showGraphError(" in html
    assert 'selector: ".filtered-out"' in html
    assert "let selectedNodeLabel" in html
    assert "loadGraph(selectedNodeLabel)" in html
```

Add assertions that error handling calls `showGraphError`, empty graph uses `resetGraphView`, and `updateVisibleStats()` is called by filtering.

- [ ] **Step 2: Run tests and confirm RED**

Expected: helper functions, real hidden filter class, and selected node label do not exist.

- [ ] **Step 3: Implement explicit UI state helpers**

`resetGraphView(message, statusMessage)` must destroy existing `cy`, set `cy=null`, clear current/selected graph state, reset details/neighbors, show the empty overlay, and set its text. `showGraphError(message)` delegates to reset and sets status to the same message.

`drawGraph(graph)` must destroy any previous Cytoscape instance before constructing the new one. For zero nodes, use graph warnings as the overlay/status message and leave no stale graph/detail state.

- [ ] **Step 4: Implement actual filtering and visible counts**

Add Cytoscape style:

```javascript
{
  selector: ".filtered-out",
  style: { display: "none" },
}
```

`applyFilters()` removes the class, applies it to nonmatching nodes and incident edges, then calls `updateVisibleStats()`. `updateVisibleStats()` reports visible/total node and edge counts plus warnings without mutating server payload statistics.

- [ ] **Step 5: Fix expansion identity**

When selecting a node set:

```javascript
selectedNodeId = node.id();
selectedNodeLabel = String(node.data("label") || node.id());
```

Expansion switches to local mode, sets the label select when the label is available, and calls `loadGraph(selectedNodeLabel)` rather than the internal ID.

- [ ] **Step 6: Run focused tests and commit**

```bash
git add tests/test_phase4_knowledge_graph.py apps/api/chatbot_manager/templates/knowledge_graph.html
git commit -m "feat: stabilize knowledge graph ui states"
```

---

### Task 3: Browser-Verify Cytoscape and Controls

**Files:**
- No production file is required unless the browser check finds a defect.
- Create: `docs/reviews/2026-08-26-phase-4-browser-verification.md`

**Interfaces:**
- Browser verification runs against the actual `/knowledge-graph` page and bundled `/static/vendor/cytoscape.min.js`.

- [ ] **Step 1: Start the app in test mode**

Launch Uvicorn on a local high port with test environment and a disposable SQLite database. Login with the existing test admin credentials.

- [ ] **Step 2: Inject representative graph through page JavaScript**

Use managed Chrome `evaluate` to call `drawGraph` with two nodes where one node has `id="node-42"` and `label="Product"`, one relationship, metadata, and a truncation warning.

- [ ] **Step 3: Verify nonblank render and details**

Evaluate that `cy` exists, has 2 nodes and 1 edge, and canvas contains Cytoscape render layers. Programmatically select `node-42`; verify detail title is `Product`, neighbor list is populated, and metadata text contains the expected field.

- [ ] **Step 4: Verify search/type filters and layout**

Set search text to a single node label and dispatch input; verify one node remains visible. Clear search, choose a node type and dispatch change; verify matching visible count. Change layout to `grid`, dispatch change, and verify the same `cy` object remains with no graph fetch required.

- [ ] **Step 5: Verify expansion uses label**

Replace `loadGraph` with a capture function in page JS, select node `node-42`, click Expand, and assert captured override is `Product`, not `node-42`.

- [ ] **Step 6: Verify empty/error transitions**

Call `drawGraph` with an empty response and verify `cy === null`, overlay visible, detail reset. Draw the representative graph again, then call `showGraphError("Graph unavailable")`; verify stale graph is destroyed and error text is visible.

- [ ] **Step 7: Record browser evidence**

Write exact checks/results to `docs/reviews/2026-08-26-phase-4-browser-verification.md`.

---

### Task 4: Phase 4 Gate and GAP-012 Closure

**Files:**
- Create: `docs/reviews/2026-08-26-phase-4-verification.md`
- Modify: `docs/reviews/2026-08-26-gap-register.md`

**Interfaces:**
- GAP-012 moves to completed Phase 4 only after pytest + browser + static gates pass.

- [ ] **Step 1: Run focused graph tests**

Run Phase 4 tests, existing graph route tests, and RAG graph normalization tests.

- [ ] **Step 2: Run full regression suite**

```powershell
& $PY -m pytest -q --basetemp "$ROOT\pytest-tmp-phase4-full"
```

- [ ] **Step 3: Run static gates**

```powershell
& $PY -m compileall -q apps/api
git diff --check
```

- [ ] **Step 4: Write verification and update gap register**

Record fresh counts and browser evidence. Reduce P2 open count from 3 to 2 and add a Phase 4 closure section marking GAP-012 completed.

- [ ] **Step 5: Commit**

```bash
git add docs/reviews/2026-08-26-phase-4-browser-verification.md docs/reviews/2026-08-26-phase-4-verification.md docs/reviews/2026-08-26-gap-register.md
git commit -m "docs: record phase 4 verification"
```
