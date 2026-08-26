# Phase 4 Knowledge Graph Completion Design

Date: 2026-08-26
Branch: `codex/phase4-knowledge-graph`
Base: `6329542` (`codex/phase3-core-chatbot`)

## Goal

Complete and verify the Knowledge Graph experience so the API exposes one bounded schema and the Cytoscape UI behaves deterministically for normal, empty, error, truncated, filtered, expanded, and layout-changed states.

## Scope

Phase 4 covers the existing `/api/knowledge-graph`, `/api/knowledge-graph/labels`, and `/knowledge-graph` UI only. It closes GAP-012. It does not redesign RAG storage, add graph persistence, change providers, or perform Phase 5 operational/documentation work.

## Canonical API Contract

`GET /api/knowledge-graph` accepts only `mode=all` or `mode=local`.

- `all`: ignores label/depth and returns depth 0.
- `local`: requires a nonblank entity label and clamps depth to 1..5.
- `max_nodes` is clamped to 1..500 in both modes.
- labels longer than 200 characters are rejected with HTTP 400 before the RAG backend is called.

Every successful graph response uses the existing canonical shape:

- `graph_type`
- `center_node_id`
- `depth`
- `nodes`
- `edges`
- `is_truncated`
- `stats`
- `warnings`

Node and edge normalization remains defensive against malformed backend items. Warnings communicate empty and truncated results. Backend-unavailable errors remain HTTP 503 with stable operator-facing detail.

## UI State Model

The graph page has four explicit visual states:

1. **loading** — status says loading; stale selection is cleared.
2. **graph** — Cytoscape instance exists, empty overlay hidden, counts shown.
3. **empty** — any previous Cytoscape instance is destroyed, selection/detail reset, empty overlay shows the warning/reason.
4. **error** — any previous Cytoscape instance is destroyed, selection/detail reset, empty overlay shows the error and status repeats it.

Loading a new graph must never leave stale nodes or stale detail metadata visible after an empty/error response.

## Filtering and Statistics

Search and type filters are actual visibility filters, not only visual fading. Nonmatching nodes and incident edges receive a hidden/filter class. After each filter operation the status reports visible node/edge counts against total loaded counts. Clearing filters restores all elements.

The server response remains the source of total `node_count` / `edge_count`; client filtering computes visible counts without mutating the fetched payload.

## Selection, Details, and Expansion

Selecting a node shows:

- label
- type and degree
- neighbor labels
- metadata

Selecting an edge shows label, endpoints, and metadata. Clearing selection resets all detail fields.

Expansion must request the selected node **label**, not the internal node ID. This matters when backend IDs and human entity labels differ. Expansion switches to local mode, selects the matching label when present, requests one-hop/local graph data through the existing API, and leaves depth within the configured 1..5 bound.

## Layout Behavior

Supported layouts remain `cose`, `concentric`, `breadthfirst`, `circle`, and `grid`. Changing layout reruns Cytoscape layout without re-fetching graph data. The page remains usable at the existing responsive breakpoints.

## Practical Limits

- server max nodes: 500
- server max depth: 5
- entity label length: 200 characters
- no unbounded client expansion: each expansion is a fresh bounded local API request

## Testing Strategy

Use TDD for API and template-contract changes. Pytest covers mode validation, bounds, normalization, warnings, and template hooks. Existing RAG service tests remain backend-normalization coverage.

Browser verification uses managed Chrome against the real FastAPI/Jinja page and bundled `cytoscape.min.js`. Deterministic mock graph objects are injected through page JavaScript after login so rendering/control behavior is tested without live LLM/RAG credentials. Browser checks cover:

- nonblank graph render
- empty state
- error state
- search filter
- type filter
- node selection/details/neighbors
- expansion uses node label
- layout change
- truncated warning display

## Completion Gate

Phase 4 is complete when:

- API contract and normalization tests pass.
- Browser renders a nonblank Cytoscape graph with representative nodes/edge.
- Empty and error transitions remove stale graph/detail state.
- Search/type filters change visible element counts.
- Node details and neighbors render from Cytoscape data.
- Expansion demonstrably uses node label rather than node ID.
- Layout control reruns a supported layout without network reload.
- Truncation is communicated.
- Full regression suite, compileall, and `git diff --check` pass.
- GAP-012 is marked completed with fresh verification evidence.
