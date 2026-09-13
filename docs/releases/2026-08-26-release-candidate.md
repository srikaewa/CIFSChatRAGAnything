# CIFS Chatbot Manager Release Candidate

Date: 2026-08-26
Release branch: `codex/phase6-release-verification`
Completion-series base: `3afbc42` (`main`)
Verified Phase 5 product tip: `a993766`

## Summary

This release candidate completes the six-phase codebase completion program for CIFS Chatbot Manager. It keeps the existing FastAPI/Jinja/SQLModel/SQLite architecture while hardening provider webhooks and admin security, making provider readiness explicit, unifying live/Test Chat decision behavior, improving RAG/knowledge and graph resilience, and completing Windows/Telegram operator guidance.

## Delivered capabilities

- LINE, Facebook Messenger, and Telegram webhook adapters with fail-closed readiness/authenticity checks and safe malformed-request handling.
- Explicit provider states: `disabled`, `incomplete`, `ready`, and `failed`.
- Shared chatbot decision engine for live providers and Test Chat: commands, priority rules/conditions, escalation decision, RAG, then fallback.
- Telegram admin escalation with validated signed integer chat IDs and explicit sent/skipped/failed outcomes that do not break user replies.
- CSRF protection, time-limited sessions, safer non-local startup defaults, encrypted persisted provider/LLM secrets, safe error disclosure, bounded/collision-safe uploads, and resilient webhook failure logging.
- Knowledge upload/index/reindex/delete recovery behavior plus bounded knowledge-graph APIs and browser-verified Cytoscape interactions.
- Telegram/Tailscale Funnel operations using structured URL parsing and direct non-interactive CLI calls without an application-level `sudo` assumption.
- Admin accessibility/responsive improvements and operator documentation for Windows short-path setup, RAG, Tailscale, backup, restore, and troubleshooting.

## Setup and migration notes

- Python 3.11+ remains the project target. The verified release environment used Python 3.12.13.
- Copy `.env.example` to `.env` for real local configuration. `.env` is intentionally not tracked. Production/non-local startup rejects unsafe development defaults.
- Existing legacy plaintext persisted provider/LLM credential rows are handled by the Phase 2 encryption migration path; new persisted secrets use authenticated encryption.
- On Windows, use the short-path environment and explicit writable pytest `--basetemp` guidance in `docs/operations.md` if the optional RAG/parser dependency tree hits WinError 206/path limits.
- Full RAG ingestion requires the optional RAG/parser dependencies plus compatible LLM/embedding settings. Core admin/provider/rule/Test Chat behavior and mocked verification do not require live RAG services.
- Telegram automatic webhook exposure requires a locally authenticated/authorized Tailscale CLI and Funnel permission.

## Release verification

Fresh Phase 6 evidence before release-note commit:

- Full warning-enabled automated suite after manual-review remediation: **184 passed**.
- Functional/admin/provider smoke suite: **116 passed**.
- Focused security regression suite: **49 passed**.
- `python -m compileall -q apps/api`: PASS.
- `git diff --check`: PASS.
- Tracked-file audit after remediation: 86 tracked files, 0 local artifact flags.
- `.env` tracked check: none.
- Conservative private-key/credential-assignment scan: 0 candidates.
- Completion-series product diff was reviewed manually, including security, provider operations, admin/configuration, RAG, and release artifacts.

A final post-evidence full suite/static/status gate is recorded in `docs/reviews/2026-08-26-phase-6-release-verification.md`.

## Manual release review remediation

A manual release review found three pre-merge issues and all were remediated before integration:

- Telegram webhook setup now catches provider transport failures and rolls Tailscale Funnel back when setup fails.
- Normal Telegram channel saves preserve the stored active `webhook_url`.
- The tracked `codex-session-*` export was removed and `codex-session-*/` is now ignored.

Three regression tests were added for these behaviors. The full warning-enabled suite now contains **184 passing tests**.

## Post-RC Reconciliation — 2026-09-13

A follow-up repository reconciliation was performed after the Knowledge Graph browser-verification handoff:

- The gap register still has **0 open P0-P3 gaps**.
- A real-browser Knowledge Graph verification was completed on 2026-09-12 with JJ ACC managed Chrome, covering nonblank Cytoscape render, entity/depth/max-node controls, search/type filters, layout switching, node and edge detail selection, and one-hop expansion.
- The full automated suite was rerun on 2026-09-13: **184 passed in 4.08s**.
- `uv lock --check` passed on 2026-09-13; the current lock resolves 175 packages.
- Two test-hermeticity improvements remain as working-tree changes: `tests/test_session_security.py` isolates the production-default settings assertion from `.env`, and `tests/test_rag_service.py` prevents LightRAG's import-time `.env` load from contaminating later tests. No production behavior was changed by those fixes.
- The feature inventory was reconciled with the completed Phase 2-6 evidence and the 2026-09-12 Knowledge Graph browser verification.
- Repository-wide executable-bit/CRLF churn was reviewed separately from real content changes; proven noise was discarded without changing application behavior.

## External-service verification

Automated release verification uses mocks for LINE, Messenger, Telegram, RAG/LLM, and Tailscale behavior. No production credentials were used and no provider webhook or public tunnel was changed.

Optional deployment smoke checks after integration, using authorized non-production credentials, are:

1. Register/reach each provider webhook and send one text event through LINE, Messenger, and Telegram.
2. Enable Tailscale Funnel and confirm the generated public HTTPS Telegram webhook is reachable, then disable it.
3. Index a small document with the real RAG/parser stack and verify a retrieval answer plus Knowledge Graph render.
4. Exercise the configured LLM fallback and verify logs contain safe outcome/status information without credential material.

## Known limitations

- The application remains a local/single-admin SQLite deployment; multi-tenancy, billing, and organization RBAC are outside scope.
- Supported messaging providers remain LINE, Messenger, and Telegram.
- Admin escalation notifications are Telegram-only.
- Real provider/RAG/LLM/Tailscale services were not exercised during automated release verification.
- Production infrastructure expansion and deployment orchestration are outside this completion program.

## Integration state

The completion/release series is present on local `main`. During the 2026-09-13 reconciliation, local `main` was **38 commits ahead of `origin/main`**. This cleanup did not push, tag, or publish anything.
