# Feature Inventory

Date: 2026-08-26

Status definitions:

- `complete`: implemented and freshly verified for the documented scope.
- `partial`: meaningful implementation exists, but important behavior or controls are incomplete.
- `broken`: a core requirement has a reproducible defect or unsafe behavior.
- `unverified`: implementation exists without fresh behavioral evidence.
- `out-of-scope`: excluded by the approved phased design.

| Area | Capability | Status | Implementation evidence | Test evidence | Documentation evidence | Target phase |
|---|---|---|---|---|---|---|
| Application | startup, health, database reset and migrations | complete | `main.py`, `db.py` | `test_app_boot.py`: 4 passing | README run instructions | none |
| Authentication | login, signed session, logout, route protection | partial | `security.py`; `admin/routes.py:60-85` | login and redirect tests pass | Design requires authenticated local admin | 2 |
| Channels | LINE inbound, signature, parsing, reply | partial | `channels/line.py`; `webhooks.py:86-101` | adapter and webhook tests pass | README lists LINE | 2 and 3 |
| Channels | Messenger verification, inbound, parsing, reply | broken | `channels/messenger.py`; `webhooks.py:104-136` | current tests pass but do not require POST authenticity | README lists Messenger | 2 |
| Channels | Telegram inbound, reply, configuration, webhook setup | partial | `channels/telegram.py`; `webhooks.py:139-156`; `admin/routes.py:170-240` | Telegram adapter and route tests pass | README describes Telegram and Tailscale Funnel | 2, 3, and 5 |
| Engine | commands, priorities, match types, compound conditions | complete | `chatbot/engine.py:24-115` | engine suite passes | README decision order | none |
| Engine | escalation reply and admin notification | partial | `chatbot/engine.py:46-50`; `webhooks.py:33-54` | decision behavior passes; notification failure behavior is untested | Assistant UI exposes notification destination | 3 |
| RAG | configuration, query, fallback, indexing, reindexing | partial | `rag/service.py`; `admin/routes.py:575-598` | query/fallback pass; 3 indexing tests fail from stale key setup | README and design describe RAG-Anything | 2 and 3 |
| Knowledge | upload, status, retry, delete | broken | `admin/routes.py:691-770` | 3 path-contract tests fail on Windows | Knowledge page is documented | 2 and 3 |
| Graph | labels/API normalization, limits, graph page and controls | partial | `admin/routes.py:255-334`, `:636-688`; `knowledge_graph.html` | API tests pass; browser rendering has no fresh verification | HANDOFF reports prior API checks | 4 |
| Admin | dashboard and channel configuration | partial | `admin/routes.py:88-168`; dashboard/channels templates | page, save, mask, and status tests pass | README lists dashboard and channels | 2 and 5 |
| Admin | rules and assistant configuration | partial | `admin/routes.py:342-510`; rules/assistant templates | CRUD and settings tests pass | README lists rules and assistant | 2 and 5 |
| Admin | Test Chat and logs | partial | `admin/routes.py:512-573` | rule answer and event logging test passes | README lists both pages | 3 and 5 |
| Security | CSRF, cookie policy, secret storage, webhook authenticity | broken | no CSRF implementation; `channel_config.py:70-106`; `models.py:13-51`; `webhooks.py:124-136` | negative security coverage is incomplete | Approved design requires fail-closed controls | 2 |
| Operations | Windows setup, dependency installation, tunnels, recovery | partial | `pyproject.toml`; Telegram subprocess setup | automated tests do not cover host setup | README lacks Windows path/temp guidance | 5 |
| Documentation | endpoint and supported-feature accuracy | partial | README contains repeated LINE/Messenger rows and no Telegram table row | not applicable | `README.md` | 5 |

## Out-of-Scope Capabilities

Multi-tenancy, billing, organization RBAC, new providers, replacement of the FastAPI/Jinja/SQLite architecture, and production infrastructure expansion remain out of scope.
