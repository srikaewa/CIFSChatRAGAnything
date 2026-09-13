# Feature Inventory

Original review date: 2026-08-26
Reconciled: 2026-09-13

## Current Status

The six-phase completion program is closed for the approved scope. `docs/reviews/2026-08-26-gap-register.md` records no remaining registered P0-P3 gaps.

Fresh reconciliation evidence:

- Full automated suite on 2026-09-13: **184 passed in 4.08s**.
- `uv lock --check` on 2026-09-13: PASS; 175 packages resolved.
- Latest real-browser Knowledge Graph verification: 2026-09-12 via JJ ACC managed Chrome.
- Phase 6 release evidence remains recorded in `docs/reviews/2026-08-26-phase-6-release-verification.md`.

Status definitions:

- `complete`: implemented and verified for the documented release scope.
- `partial`: meaningful implementation exists, but important behavior or controls are incomplete.
- `broken`: a core requirement has a reproducible defect or unsafe behavior.
- `unverified`: implementation exists without sufficient behavioral evidence.
- `out-of-scope`: excluded by the approved phased design.

| Area | Capability | Status | Current evidence | Closure phase |
|---|---|---|---|---|
| Application | startup, health, database reset and migrations | complete | `main.py`, `db.py`, `test_app_boot.py`; full suite green | existing / verified through Phase 6 |
| Authentication | login, signed time-limited session, logout, route protection | complete | `security.py`, admin routes, `test_session_security.py`, admin route tests | Phase 2 |
| Channels | LINE inbound authenticity, parsing, reply, provider readiness | complete | `channels/line.py`, `webhooks.py`, webhook/provider contract tests | Phases 2-3 |
| Channels | Messenger verification, signed inbound authenticity, parsing, reply | complete | `channels/messenger.py`, `webhooks.py`, `test_webhook_security.py`, provider contract tests | Phases 2-3 |
| Channels | Telegram inbound, reply, configuration, webhook/Tailscale operations | complete | `channels/telegram.py`, `admin/telegram_ops.py`, Telegram channel/webhook/admin tests | Phases 2, 3, 5-6 |
| Engine | commands, priorities, match types, compound conditions | complete | `chatbot/engine.py`, `test_chatbot_engine.py` | existing / verified through Phase 6 |
| Engine | escalation reply and Telegram admin notification outcomes | complete | shared engine/webhook flow, validated destination handling, provider contract tests | Phase 3 |
| RAG | configuration, query, fallback, indexing and reindexing behavior | complete | `rag/service.py`, `test_rag_service.py`, knowledge/admin regression tests; live external service remains an optional deployment smoke check | Phases 2-3 |
| Knowledge | bounded upload, status, retry, safe delete/recovery | complete | admin routes, `test_knowledge_safety.py`, Phase 2/3 verification | Phases 2-3 |
| Graph | labels/API normalization, graph limits, Cytoscape page and controls | complete | `test_phase4_knowledge_graph.py`, admin tests, real browser verification on 2026-09-12 | Phase 4 + 2026-09-12 follow-up |
| Admin | dashboard and channel configuration | complete | admin routes/templates, `test_admin_routes.py`, Phase 5 responsive/browser checks | Phases 2 and 5 |
| Admin | rules and assistant configuration | complete | admin routes/templates and CRUD/settings regression tests | Phases 2 and 5 |
| Admin | Test Chat and event logs | complete | shared decision engine path, admin route/engine tests | Phases 3 and 5 |
| Security | CSRF, cookie/session policy, encrypted secret storage, webhook authenticity/fail-closed behavior | complete | CSRF/session/secret/webhook security suites and Phase 2 verification | Phase 2 |
| Operations | Windows setup, dependency installation guidance, Telegram/Tailscale portability, backup/recovery | complete | `docs/operations.md`, Telegram operations tests, Phase 5 verification | Phase 5-6 |
| Documentation | endpoints, supported providers, operations and release evidence | complete | `README.md`, `docs/operations.md`, Phase 5/6 review and release documents | Phase 5-6 |

## Remaining Limitations / Out-of-Scope Capabilities

These are not registered defects in the approved completion scope:

- The application remains a local/single-admin SQLite deployment.
- Multi-tenancy, billing, and organization RBAC are out of scope.
- Supported messaging providers remain LINE, Messenger, and Telegram.
- Admin escalation notification remains Telegram-only.
- Live provider, RAG/LLM, and Tailscale services are covered by mocked automated verification; authorized non-production deployment smoke checks remain optional.
- Replacement of FastAPI, Jinja, SQLModel/SQLite, and production infrastructure expansion remain out of scope.
