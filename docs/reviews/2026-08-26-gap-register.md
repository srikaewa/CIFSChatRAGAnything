# Gap Register

Date: 2026-08-26

## Open Summary

| Priority | Count |
|---|---:|
| P0 | 0 |
| P1 | 0 |
| P2 | 3 |
| P3 | 2 |


## Phase 2 Closure

Fresh evidence is recorded in `docs/reviews/2026-08-26-phase-2-verification.md`.

| Status | Gaps |
|---|---|
| Completed in Phase 2 | GAP-001, GAP-002, GAP-003, GAP-004, GAP-005, GAP-006, GAP-007, GAP-008, GAP-009, GAP-010 |
| Remaining for later phases | GAP-011, GAP-012, GAP-013, GAP-014, GAP-015, GAP-016 |

Phase 2 verification results:

- Full suite: `135 passed`.
- Focused compatibility and security suite: `95 passed`.
- Syntax compilation and `git diff --check`: clean.
- Tracked Phase 2 diff credential-pattern scan: clean.


## Phase 3 Closure

Fresh evidence is recorded in `docs/reviews/2026-08-26-phase-3-verification.md`.

| Status | Gaps |
|---|---|
| Completed in Phase 3 | GAP-011 |
| Remaining for later phases | GAP-012, GAP-013, GAP-014, GAP-015, GAP-016 |

Phase 3 verification results:

- Full suite: `161 passed`.
- Syntax compilation and `git diff --check`: clean.
- LINE, Messenger, and Telegram mocked inbound-to-outbound flows verified.
- Provider disabled/incomplete/ready/failed states verified.
- GAP-011 escalation destination validation and notification outcomes verified.

## Ordered Gaps

| ID | Priority | Phase | Area | Finding | Evidence | User impact | Acceptance check |
|---|---|---|---|---|---|---|---|
| GAP-001 | P1 | 2 | Messenger security | Messenger POST webhooks are processed without validating `X-Hub-Signature-256`. | `webhooks.py:124-136`; no signature method exists in `channels/messenger.py`. | Any network client can forge Messenger messages and trigger replies, logs, RAG calls, and escalation notifications. | Valid signed payload succeeds; missing or invalid signature returns 401 before parsing or sending. |
| GAP-002 | P1 | 2 | Admin security | Every state-changing admin route relies only on a SameSite cookie and has no CSRF token validation. | POST routes at `admin/routes.py:72`, `:81`, `:134`, `:170`, `:219`, `:383`, `:411`, `:443`, `:479`, `:521`, `:691`, `:723`, and `:745`; no CSRF symbol exists. | A logged-in administrator can be induced to change credentials, rules, settings, or documents from another site. | All state-changing forms require a session-bound token; missing or invalid tokens are rejected and tested. |
| GAP-003 | P1 | 2 | Secret storage | Channel tokens and secrets are stored as plaintext JSON in SQLite despite an existing encryption-key setting. | `channel_config.py:70-106`; `Channel.credential_json` in `models.py:13-23`; `Settings.app_encryption_key`. | Database disclosure exposes provider credentials. | Persisted channel credential values are authenticated-encrypted and legacy plaintext rows migrate safely. |
| GAP-004 | P1 | 2 | Webhook fail-closed behavior | LINE and Telegram can accept requests when configured secrets are blank; Messenger verification accepts a blank configured token. | `line.py:21-24`; `telegram.py:16-19`; `messenger.py:16-19`; default secrets in `settings.py`. | An unconfigured public endpoint may accept attacker-controlled events or verification requests. | Enabled providers reject webhook traffic until all authenticity credentials are configured; negative tests cover blank secrets. |
| GAP-005 | P1 | 2 | Session security | Development defaults provide a known admin password and signing key; session cookies lack `secure` and expiry attributes and tokens have no timestamp. | `settings.py:12-16`; `security.py:11-27`; `admin/routes.py:73-79`. | An exposed instance can be accessed with documented defaults, and stolen cookies remain valid indefinitely. | Non-local startup rejects defaults; production cookies are secure and time-limited; expiry and default-credential tests pass. |
| GAP-006 | P1 | 2 | Knowledge consistency | Delete ignores RAG deletion failures and then removes the local file and database row. | `admin/routes.py:745-770`, especially broad `except` followed by unconditional deletion. | RAG storage can retain stale document data while the admin UI loses the record needed to retry cleanup. | RAG deletion failure preserves the document and file, records an actionable error, and has a regression test. |
| GAP-007 | P2 | 2 | Test stability | Six tests fail in the fresh Windows baseline because path assertions are OS-specific and RAG mocks omit the required API key. | BASE-001 and BASE-002; final result `6 failed, 90 passed`. | The suite cannot act as a reliable Phase 2 regression gate. | Tests use path-aware comparisons and explicit dummy keys; all 96 tests pass. |
| GAP-008 | P2 | 2 | Upload safety | Uploads are fully buffered, have no size limit, and reuse the original basename, overwriting an existing file with the same name. | `admin/routes.py:691-721`. | Large uploads can exhaust memory and duplicate names can replace another document's source file. | Size limits, collision-safe storage names, user feedback, and negative tests are present. |
| GAP-009 | P2 | 2 | Error disclosure | Channel save redirects include raw exception text in the query string. | `admin/routes.py:162-165`. | Internal errors can be exposed to the browser, logs, and referrer history. | User receives a stable safe message while detailed context is logged server-side without secrets. |
| GAP-010 | P2 | 2 | Webhook resilience | Provider send or admin-notification errors escape before the event is committed. | `webhooks.py:56-83`. | Webhook retries can duplicate processing while operators lose the failed decision record. | Send/notify failures are recorded with deterministic response behavior and regression tests. |
| GAP-011 | P2 | 3 | Escalation | Admin notification supports Telegram only and converts chat ID with an unchecked `int()` call. | `webhooks.py:33-54`; Assistant template labels Messenger and LINE as coming soon. | Invalid configuration can fail an otherwise valid user webhook; configured non-Telegram choices silently do nothing. | Invalid destinations do not break user replies, notification status is logged, and supported choices are explicit. |
| GAP-012 | P2 | 4 | Knowledge graph | Graph APIs are tested, but the Cytoscape canvas and controls have no fresh browser-level verification. | HANDOFF caveat; graph API tests pass. | A server-side pass can still ship a blank or noninteractive graph. | Automated or recorded browser checks cover nonblank render, empty/error states, filters, depth, expansion, and layouts. |
| GAP-013 | P2 | 5 | Windows environment | Full RAG dependency installation fails at the long worktree path with WinError 206; default pytest temp is inaccessible. | BASE-003 and BASE-004. | A Windows developer cannot follow one documented setup command in this environment. | Documentation provides a short-path environment and writable temp command that completes installation and tests. |
| GAP-014 | P2 | 5 | Telegram operations | Webhook setup invokes `sudo tailscale` and derives the port by string splitting URLs. | `admin/routes.py:170-240`. | Setup is platform-specific and can select the wrong port or fail noninteractively. | URL parsing is structured, platform requirements are explicit, and subprocess failures are tested. |
| GAP-015 | P3 | 5 | Documentation | README duplicates LINE/Messenger endpoint rows and omits Telegram from the endpoint table. | `README.md` Webhooks & Tunnels table. | Operators may configure or troubleshoot the wrong endpoint set. | Endpoint table lists each supported channel exactly once with correct methods and paths. |
| GAP-016 | P3 | 5 | Dependency warning | The current FastAPI/Starlette stack warns that TestClient's `httpx` integration is deprecated in favor of `httpx2`. | collection warning from `fastapi/testclient.py`. | Future dependency upgrades may break tests unexpectedly. | Dependency constraints and TestClient usage are updated with a warning-free suite. |

## Dependency Order

1. Restore the 96-test green baseline with GAP-007.
2. Fix webhook authenticity and fail-closed configuration with GAP-001 and GAP-004.
3. Add CSRF and session hardening with GAP-002 and GAP-005.
4. Protect stored secrets with GAP-003.
5. Fix destructive knowledge deletion, upload safety, error disclosure, and webhook resilience with GAP-006 and GAP-008 through GAP-010.
6. Continue Phase 3 through Phase 6 in numerical phase order.

## Explicitly Out of Scope

- Multi-tenancy, billing, and organization RBAC.
- New providers beyond LINE, Messenger, and Telegram.
- Architectural replacement of FastAPI, Jinja, SQLModel, or SQLite.
- Production infrastructure expansion.
