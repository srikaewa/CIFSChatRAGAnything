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

- Full warning-enabled automated suite: **181 passed**.
- Functional/admin/provider smoke suite: **116 passed**.
- Focused security regression suite: **49 passed**.
- `python -m compileall -q apps/api`: PASS.
- `git diff --check`: PASS.
- Tracked-file audit: 85 tracked files, 0 local artifact flags.
- `.env` tracked check: none.
- Conservative private-key/credential-assignment scan: 0 candidates.
- Completion-series diff reviewed from `3afbc42..a993766`: 67 files changed, 7,517 insertions, 292 deletions.

A final post-evidence full suite/static/status gate is recorded in `docs/reviews/2026-08-26-phase-6-release-verification.md`.

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

The release candidate is on a local Git branch/worktree only. It has not been merged, pushed, tagged, or published.
