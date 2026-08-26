# Phase 2 Stabilization and Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a green cross-platform test baseline and make authentication, admin mutations, stored secrets, provider webhooks, uploads, deletion, and failure handling fail closed.

**Architecture:** Keep the existing FastAPI/Jinja/SQLModel boundaries. Add small security primitives in `security.py`, provider authenticity checks in channel adapters, form dependencies in admin routes, and focused persistence helpers for encrypted settings. Every behavior change begins with a failing test and ends with the full 96-test regression suite.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLModel, itsdangerous, cryptography/Fernet, pytest, httpx/respx.

**Spec:** `docs/superpowers/specs/2026-08-26-codebase-completion-phases-design.md`

**Evidence:** `docs/reviews/2026-08-26-gap-register.md`

## Global Constraints

- Work only in `codex/phase2-stabilization`.
- Preserve the imported product changes; inspect every overlapping diff before editing.
- Use `..\phase2-venv\Scripts\python.exe -m pytest` with an explicit writable `--basetemp`.
- Never read, print, copy, or commit `.env` or real credentials.
- Use test-driven development and commit only files named by each task.

---

### Task 1: Restore the 96-Test Baseline

**Files:**
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_rag_service.py`

**Interfaces:**
- Consumes: current `Path` behavior and `RagAnythingService(..., llm_api_key=...)` constructor.
- Produces: platform-neutral path assertions and explicit test credentials.

- [ ] **Step 1: Confirm the six failures**

Run the three failing admin tests and three failing RAG tests. Expected: POSIX-versus-Windows separator mismatches and missing-key errors.

- [ ] **Step 2: Make path assertions platform-neutral**

Replace string path equality with `Path(actual) == Path("data/uploads/menu.txt")`. Preserve document ID, RAG ID, and reindex-flag assertions.

- [ ] **Step 3: Supply dummy keys only to success-path RAG tests**

Construct success-path services with `llm_api_key="test-key"`. Do not change `test_index_document_requires_llm_api_key`.

- [ ] **Step 4: Verify and commit**

Run the six focused tests, then all 96 tests. Expected: `96 passed`. Commit as `test: restore cross-platform baseline`.

### Task 2: Signed and Fail-Closed Provider Webhooks

**Files:**
- Modify: `apps/api/chatbot_manager/channels/line.py`
- Modify: `apps/api/chatbot_manager/channels/messenger.py`
- Modify: `apps/api/chatbot_manager/channels/telegram.py`
- Modify: `apps/api/chatbot_manager/webhooks.py`
- Modify: `tests/test_line_channel.py`
- Modify: `tests/test_messenger_channel.py`
- Modify: `tests/test_telegram_channel.py`
- Modify: `tests/test_webhooks.py`
- Modify: `tests/test_telegram_webhooks.py`

**Interfaces:**
- Produces: `MessengerAdapter.validate_signature(body: bytes, signature: str) -> bool` using `sha256=<hex>` HMAC.
- Produces: all validation methods return `False` when their configured authenticity secret is blank.

- [ ] **Step 1: Add failing negative tests**

Add tests proving blank LINE secret, blank Telegram webhook secret, blank Messenger verify token, missing Messenger POST signature, and invalid Messenger POST signature are rejected. Add a valid Messenger HMAC test using `hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()`.

- [ ] **Step 2: Implement adapter validation**

Use `hmac.compare_digest`. Messenger accepts only the exact `sha256=` format. Do not parse a POST body before signature validation.

- [ ] **Step 3: Update route tests to configure secrets explicitly**

Tests that expect webhook acceptance must save provider credentials or set test environment values before the request.

- [ ] **Step 4: Verify and commit**

Run all adapter and webhook tests, then the full suite. Commit as `fix: require authentic provider webhooks`.

### Task 3: CSRF Protection for Authenticated Admin Mutations

**Files:**
- Modify: `apps/api/chatbot_manager/security.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `apps/api/chatbot_manager/templates/channels.html`
- Modify: `apps/api/chatbot_manager/templates/rules.html`
- Modify: `apps/api/chatbot_manager/templates/assistant.html`
- Modify: `apps/api/chatbot_manager/templates/test_chat.html`
- Modify: `apps/api/chatbot_manager/templates/knowledge.html`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_webhooks.py`
- Modify: `tests/test_telegram_webhooks.py`

**Interfaces:**
- Produces: `make_csrf_token(email: str) -> str`.
- Produces: `verify_csrf_token(token: str, email: str) -> bool`.
- Produces: `require_csrf(request, csrf_token, admin_email) -> str` dependency for authenticated POST routes.

- [ ] **Step 1: Add failing CSRF tests**

Test that authenticated POSTs without a token and with a token for another user return 403. Test that forms render a token and valid POSTs succeed.

- [ ] **Step 2: Implement signed session-bound tokens**

Use a separate itsdangerous salt `admin-csrf`, bind the token payload to the authenticated email, and reject bad signatures.

- [ ] **Step 3: Protect routes and forms**

Replace `Depends(require_admin)` with `Depends(require_csrf)` on authenticated admin POST routes. Add hidden `csrf_token` inputs to every corresponding form. Keep provider webhook routes exempt.

- [ ] **Step 4: Update test login helper**

Read the rendered token once after login and include it in authenticated form submissions.

- [ ] **Step 5: Verify and commit**

Run admin and webhook tests, then the full suite. Commit as `feat: protect admin mutations with csrf`.

### Task 4: Expiring Sessions and Safe Deployment Defaults

**Files:**
- Modify: `apps/api/chatbot_manager/settings.py`
- Modify: `apps/api/chatbot_manager/security.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/main.py`
- Modify: `.env.example`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_app_boot.py`

**Interfaces:**
- Produces: `Settings.admin_session_max_age_seconds: int = 28800`.
- Produces: `Settings.cookie_secure: bool` with safe non-local behavior.
- Changes: `read_session_token(token, max_age_seconds)` uses `URLSafeTimedSerializer`.

- [ ] **Step 1: Add failing expiry, cookie, and startup tests**

Test expired/tampered tokens, `secure`/`httponly`/`samesite=lax`/`max-age` cookie attributes, and rejection of known default signing key or admin password when `APP_ENV` is not `local` or `test`.

- [ ] **Step 2: Implement timed sessions and deployment validation**

Use `URLSafeTimedSerializer`, constant-time credential comparison, explicit cookie expiry, and startup validation that names the unsafe setting without echoing its value.

- [ ] **Step 3: Verify and commit**

Run boot/auth tests and the full suite. Commit as `fix: expire sessions and reject unsafe deployment defaults`.

### Task 5: Encrypt Persisted Secrets

**Files:**
- Modify: `pyproject.toml`
- Modify: `apps/api/chatbot_manager/security.py`
- Modify: `apps/api/chatbot_manager/channel_config.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_webhooks.py`

**Interfaces:**
- Produces: `encrypt_secret(value: str, key: str) -> str` with prefix `enc:v1:`.
- Produces: `decrypt_secret(value: str, key: str) -> str` supporting both `enc:v1:` and legacy plaintext input.
- Produces: encrypted channel `credential_json` values and encrypted persisted assistant API key.

- [ ] **Step 1: Add failing at-rest and legacy tests**

Assert raw SQLite rows do not contain submitted tokens, round-trip decryption works, legacy plaintext rows remain readable, and the next save rewrites them encrypted.

- [ ] **Step 2: Implement authenticated encryption**

Add explicit `cryptography` dependency. Derive a Fernet key from SHA-256 of `APP_ENCRYPTION_KEY`, encrypt individual secret values, and never return ciphertext to templates.

- [ ] **Step 3: Integrate channel and assistant persistence**

Encrypt on save, decrypt only in server-side helpers, preserve blank-field semantics, and fail startup in non-local environments when the default encryption key remains.

- [ ] **Step 4: Verify and commit**

Run admin/webhook tests, scan the test database values in assertions, then run the full suite. Commit as `feat: encrypt persisted provider and llm secrets`.

### Task 6: Safe Knowledge Upload and Deletion

**Files:**
- Modify: `apps/api/chatbot_manager/settings.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/templates/knowledge.html`
- Modify: `tests/test_admin_routes.py`

**Interfaces:**
- Produces: `Settings.max_upload_bytes: int = 25_000_000`.
- Produces: collision-safe stored filenames while preserving the display filename.

- [ ] **Step 1: Add failing tests**

Test oversize rejection, duplicate basename isolation, unsupported-type feedback, and preservation of the database row and file when RAG deletion fails.

- [ ] **Step 2: Implement bounded streaming upload**

Read chunks, stop above the limit, remove partial files, and use a generated storage name derived from a UUID plus the sanitized extension.

- [ ] **Step 3: Make deletion transactional in behavior**

On RAG deletion failure, retain local state, mark the document failed, store a safe actionable error, and redirect without deleting the source.

- [ ] **Step 4: Verify and commit**

Run knowledge/admin tests and the full suite. Commit as `fix: make knowledge storage bounded and recoverable`.

### Task 7: Safe Errors and Webhook Failure Records

**Files:**
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/webhooks.py`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_webhooks.py`
- Modify: `tests/test_telegram_webhooks.py`

**Interfaces:**
- Produces: stable user-facing channel error codes instead of raw exception query text.
- Produces: `ChatEvent` failure rows for provider send and admin-notification failures.

- [ ] **Step 1: Add failing disclosure and resilience tests**

Test that exception text containing a fake token is absent from redirects/responses, send failures are logged, notification failures do not suppress a successfully generated user reply, and webhook response policy is deterministic.

- [ ] **Step 2: Implement safe error mapping and event persistence**

Log server details without credential values, return stable codes, persist the decision before/around external sends, and update the row with send/notification errors.

- [ ] **Step 3: Verify and commit**

Run admin/webhook tests and the full suite. Commit as `fix: record webhook failures without leaking secrets`.

### Task 8: Phase 2 Security Gate

**Files:**
- Modify: `docs/reviews/2026-08-26-gap-register.md`
- Create: `docs/reviews/2026-08-26-phase-2-verification.md`

- [ ] **Step 1: Run full verification**

Run all tests, `git diff --check`, secret-pattern scans that exclude `.env`, and focused negative security tests. Expected: all tests pass and no real credential-like values appear in tracked diffs.

- [ ] **Step 2: Record evidence**

Write exact commands/results, completed gap IDs, remaining risks, and Phase 3 prerequisites. Mark only evidence-backed gaps complete.

- [ ] **Step 3: Commit reports**

Commit as `docs: record phase 2 security gate`.
