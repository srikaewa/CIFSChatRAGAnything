# Chatbot Manager Handoff

## Current Repo

`/home/srikaewa/Data-II/Projects/ChatBot/CIFSChatbotManager`

User prefers terse caveman-style replies. Shell commands should use `rtk` prefix.

## Recent Work

Implemented revised Knowledge Graph dashboard based on `graph_module_implementation_guide_for_coding_ai.md`.

## Files Changed

- `apps/api/chatbot_manager/templates/knowledge_graph.html`
- `apps/api/chatbot_manager/admin/routes.py`
- `apps/api/chatbot_manager/static/styles.css`
- `apps/api/chatbot_manager/static/vendor/cytoscape.min.js`
- `tests/test_admin_routes.py`

## Main Behavior

- Knowledge Graph page uses local vendored Cytoscape.js.
- Entity label input changed to dropdown loaded from `/api/knowledge-graph/labels`.
- Controls added: search, node type filter, depth, max nodes, layout selector.
- Graph supports node click details, edge click details, neighbor list, metadata panel, and Expand 1-hop.
- `/api/knowledge-graph` returns normalized guide-style schema:
  - `graph_type`
  - `center_node_id`
  - `depth`
  - `nodes`
  - `edges`
  - `stats`
  - `warnings`
  - `is_truncated`

## Verification

Fresh checks already run:

```bash
rtk uv run pytest -v
```

Result:

```text
63 passed
```

Live checks:

- `http://127.0.0.1:8001/health` returned `{"status":"ok"}`
- `/api/knowledge-graph/labels` returned 44 labels
- `/api/knowledge-graph?label=Blood%20Samples&max_depth=2&max_nodes=120` returned 5 nodes and 4 edges
- Rendered inline JavaScript from `/knowledge-graph` passed `node --check`
- `/static/vendor/cytoscape.min.js` served successfully

Visual browser check was not run because Python `playwright` package is not installed.

## Server

Existing server on `http://127.0.0.1:8001` was running and serving the new graph page.

Manual start command:

```bash
DATABASE_URL=sqlite:///./data/chatbot.sqlite3 rtk uv run uvicorn chatbot_manager.main:app --app-dir apps/api --host 127.0.0.1 --port 8001
```

Admin login:

- Email: `admin@example.local`
- Password: `admin1234!`

## Session Export

Codex session transfer bundle:

`codex-session-20260707-152637-019f3d30`

Import on another machine:

```bash
python3 ~/.codex/skills/codex-session-transfer/scripts/import_session.py codex-session-20260707-152637-019f3d30
codex resume 019f3d30-3662-7a22-9526-4e3f0ac55d87
```

## Caveats

- Git commands from current cwd fail with `not a git repository`.
- Playwright screenshot verification has not been done.

## Potential Next Step

Install or use Playwright, then verify `/knowledge-graph` canvas renders nonblank and controls work in browser.
