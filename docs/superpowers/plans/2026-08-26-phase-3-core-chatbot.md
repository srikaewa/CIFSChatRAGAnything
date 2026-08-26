# Phase 3 Core Chatbot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete deterministic provider configuration, inbound-to-outbound, escalation, RAG/fallback, and Test Chat contracts for LINE, Messenger, and Telegram.

**Architecture:** Preserve the existing FastAPI/Jinja/SQLModel application. Add an explicit derived channel state contract in `channel_config.py`, enforce that contract at webhook boundaries, make Telegram escalation notification validation/results explicit, and prove Test Chat/live parity through the existing `ChatbotEngine`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLite, Jinja2, httpx/respx, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-26-phase-3-core-chatbot-design.md`

## Global Constraints

- Preserve Phase 2 authenticity, CSRF, session, encryption, upload, and safe-error controls.
- Do not add providers beyond LINE, Messenger, and Telegram.
- Telegram is the only supported admin notification destination in Phase 3.
- Do not introduce a database migration framework or new database columns.
- External provider/LLM/RAG calls remain mocked in automated tests.
- Use the existing Python 3.12 environment and an explicit writable pytest `--basetemp` path on this host.

---

### Task 1: Explicit Channel State Contract

**Files:**
- Create: `tests/test_phase3_provider_contracts.py`
- Modify: `apps/api/chatbot_manager/channel_config.py`
- Modify: `apps/api/chatbot_manager/templates/channels.html`

**Interfaces:**
- Produces: `ResolvedChannel.state: str` with values `disabled|incomplete|ready|failed`.
- Produces: `channel_state(saved: Channel | None, enabled: bool, configured: bool) -> str`.
- `channel_cards()` adds `state` for operator-facing rendering.

- [ ] **Step 1: Write failing state tests**

Add parameterized tests that save channels in disabled, incomplete, ready, and explicit failed states and assert `resolve_channel(...).state`. Also assert the Channels page renders `Disabled`, `Incomplete`, `Ready`, and `Failed` labels from `card.state`.

```python
@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "expected"),
    [
        (False, {}, "not_configured", "disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "incomplete"),
        (True, {"channel_secret": "secret", "channel_access_token": "token"}, "configured", "ready"),
        (True, {"channel_secret": "secret", "channel_access_token": "token"}, "failed", "failed"),
    ],
)
def test_line_resolved_channel_state(enabled, credentials, saved_status, expected):
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()
        resolved = resolve_channel(session, get_settings(), "line")
    assert resolved.state == expected
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
& $PY -m pytest -q tests/test_phase3_provider_contracts.py --basetemp "$ROOT\pytest-tmp-phase3-state-red"
```

Expected: failures because `ResolvedChannel` has no `state` and the template does not render four states.

- [ ] **Step 3: Implement minimal state derivation**

In `channel_config.py`, extend `ResolvedChannel` with `state: str`, add:

```python
def channel_state(saved: Channel | None, enabled: bool, configured: bool) -> str:
    if not enabled:
        return "disabled"
    if saved is not None and saved.status == "failed":
        return "failed"
    return "ready" if configured else "incomplete"
```

Use it in `resolve_channel()`. Persist new saves as `disabled`, `ready`, or `incomplete`, while the derived function remains compatible with legacy `configured` / `not_configured` rows. Add `state` to `channel_cards()` and render the four labels in `channels.html`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the Task 1 test file plus existing channel/admin tests.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/channel_config.py apps/api/chatbot_manager/templates/channels.html tests/test_phase3_provider_contracts.py
git commit -m "feat: expose provider readiness states"
```

---

### Task 2: Provider Request Contracts and Messenger End-to-End Flow

**Files:**
- Modify: `tests/test_phase3_provider_contracts.py`
- Modify: `tests/test_webhooks.py`
- Modify: `tests/test_telegram_webhooks.py`
- Modify: `apps/api/chatbot_manager/webhooks.py`

**Interfaces:**
- Produces: `_require_ready(channel: ResolvedChannel, provider: str) -> None` that raises HTTP 503 for `incomplete`/`failed`.
- Produces: `async _json_payload(request: Request) -> dict[str, Any]` that converts authenticated malformed JSON to HTTP 400.

- [ ] **Step 1: Write failing request-contract tests**

Add tests proving for each provider:

```python
# enabled but missing required credentials
response = client.post("/webhooks/line", content=b"not-json", headers={"x-line-signature": "anything"})
assert response.status_code == 503
```

Add an explicit saved `status="failed"` case returning 503 before parsing. Add signed malformed JSON tests that configure complete credentials, compute the valid provider signature/secret, submit malformed bytes, and expect 400. Update legacy blank-secret tests whose old 401/403 expectation is superseded by the new explicit incomplete-state 503 contract.

Add Messenger inbound-to-outbound coverage with a matching rule and mocked `https://graph.facebook.com/v20.0/me/messages`, asserting `processed == 1`, sent reply text, and persisted log content.

- [ ] **Step 2: Run focused tests and confirm RED**

Run Phase 3 provider tests plus webhook suites. Expected failures: incomplete routes currently reach signature validation, failed status is ignored, malformed JSON is not normalized, and Messenger lacks the new full-flow assertion.

- [ ] **Step 3: Implement request boundary helpers**

In `webhooks.py`:

```python
def _require_ready(channel: ResolvedChannel, provider: str) -> None:
    if channel.state in {"incomplete", "failed"}:
        raise HTTPException(status_code=503, detail=f"{provider} channel is not ready")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed JSON payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Malformed JSON payload")
    return payload
```

Keep the existing disabled fast-path first. Call `_require_ready()` before reading the body. Authenticate the raw body before `_json_payload()`. Apply the same state check to Messenger GET verification.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run provider/webhook/security/failure suites and ensure Phase 2 disabled/authenticity behavior remains green.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/webhooks.py tests/test_phase3_provider_contracts.py tests/test_webhooks.py tests/test_telegram_webhooks.py
git commit -m "feat: enforce provider request contracts"
```

---

### Task 3: Safe Escalation Destination Validation and Notification Outcomes

**Files:**
- Modify: `apps/api/chatbot_manager/webhooks.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/templates/assistant.html`
- Modify: `tests/test_failure_handling.py`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_phase3_provider_contracts.py`

**Interfaces:**
- Produces: `AdminNotificationResult(status: str, error_code: str = "")`.
- Produces: `validate_admin_notification_settings(channel: str, destination: str) -> tuple[str, str]`.
- `_notify_admin(...) -> AdminNotificationResult` never performs unchecked destination conversion.

- [ ] **Step 1: Write failing escalation tests**

Add tests for:

```python
assert validate_admin_notification_settings("telegram", "-100123") == ("telegram", "-100123")
```

and that unsupported channel / non-integer Telegram destination are rejected through `/assistant` with stable redirect errors. Add direct `_notify_admin` tests for blank destination -> `skipped`, invalid destination -> `failed` with `admin_notification_invalid_destination`, missing Telegram token -> `failed` with `admin_notification_not_configured`, and successful mocked Telegram send -> `sent`.

Add a `process_messages` test proving a notification failure code is persisted while the user reply remains sent.

- [ ] **Step 2: Run focused tests and confirm RED**

Expected failures: no validator/result type exists and `_notify_admin` currently relies on `int(chat_id)` exceptions.

- [ ] **Step 3: Implement minimal validation and result handling**

In `admin/routes.py`:

```python
SUPPORTED_ADMIN_NOTIFY_CHANNELS = {"telegram"}


def validate_admin_notification_settings(channel: str, destination: str) -> tuple[str, str]:
    normalized_channel = channel.strip().lower()
    normalized_destination = destination.strip()
    if normalized_channel not in SUPPORTED_ADMIN_NOTIFY_CHANNELS:
        raise ValueError("unsupported_notification_channel")
    if normalized_destination and re.fullmatch(r"-?\d+", normalized_destination) is None:
        raise ValueError("invalid_notification_destination")
    return normalized_channel, normalized_destination
```

Accept an optional `error` query parameter on `assistant_page`, map stable codes to messages, and validate before saving. Keep only Telegram as an enabled option in `assistant.html`; replace coming-soon pseudo-options with explanatory text.

In `webhooks.py`, add:

```python
@dataclass(frozen=True)
class AdminNotificationResult:
    status: str
    error_code: str = ""
```

Make `_notify_admin()` return `skipped`, `failed`, or `sent`. Convert the chat ID only after signed-integer validation. `process_messages()` logs the result and appends only stable failure codes to `ChatEvent.error`; retain a final exception guard for unexpected adapter failures.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run escalation, admin, webhook, and Phase 2 failure-handling suites.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/webhooks.py apps/api/chatbot_manager/admin/routes.py apps/api/chatbot_manager/templates/assistant.html tests/test_failure_handling.py tests/test_admin_routes.py tests/test_phase3_provider_contracts.py
git commit -m "feat: validate escalation notifications"
```

---

### Task 4: Prove Shared Engine, RAG/Fallback, and Test Chat Parity

**Files:**
- Modify: `tests/test_chatbot_engine.py`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_phase3_provider_contracts.py`
- Modify: `apps/api/chatbot_manager/templates/test_chat.html`

**Interfaces:**
- No new production engine interface. Both live and Test Chat continue to call `ChatbotEngine.answer(ChatbotInput, rules, settings)`.

- [ ] **Step 1: Add parity/decision tests**

Add explicit engine ordering tests proving command > escalating rule > RAG > fallback and assert escalation keeps `source="rule"`, user-facing `escalate_message`, and original `rule_reply` for notification context.

Add a Test Chat/live parity integration test using the same stored rule/settings: submit an equivalent Test Chat message and process an `IncomingMessage` through `process_messages()`, then assert both persisted events have the same `decision_source` and `reply_text`.

Assert RAG exception continues to produce fallback plus `response_generation_failed`.

- [ ] **Step 2: Run tests and confirm whether behavior is already GREEN**

If a new behavioral test unexpectedly passes immediately, verify it is a characterization test of existing required behavior, not a missing feature. No production code is added unless a genuinely failing contract requires it.

- [ ] **Step 3: Document intentional Test Chat side-effect difference**

Add to `test_chat.html`: Test Chat uses the same decision engine but does not call messaging-provider APIs or send admin escalation notifications.

- [ ] **Step 4: Run focused tests**

Run chatbot engine, admin, Phase 3 provider, and RAG service tests.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/templates/test_chat.html tests/test_chatbot_engine.py tests/test_admin_routes.py tests/test_phase3_provider_contracts.py
git commit -m "test: prove live and test chat parity"
```

---

### Task 5: Phase 3 Gate and Documentation

**Files:**
- Create: `docs/reviews/2026-08-26-phase-3-verification.md`
- Modify: `docs/reviews/2026-08-26-gap-register.md`

**Interfaces:**
- GAP-011 becomes completed in Phase 3 only after fresh evidence passes.
- GAP-012 through GAP-016 remain unchanged and assigned to later phases.

- [ ] **Step 1: Run focused Phase 3 suite**

Run all provider, webhook, engine, admin, RAG, and failure suites with an explicit writable `--basetemp`.

- [ ] **Step 2: Run full regression suite**

```powershell
& $PY -m pytest -q --basetemp "$ROOT\pytest-tmp-phase3-full"
```

Expected: all tests pass.

- [ ] **Step 3: Run static gates**

```powershell
& $PY -m compileall -q apps/api
git diff --check
```

Expected: exit code 0 and no output from either validation.

- [ ] **Step 4: Write verification evidence**

Record exact test counts, commands/results, provider-state behavior, escalation outcomes, Test Chat parity, and remaining risks in `docs/reviews/2026-08-26-phase-3-verification.md`. Update the gap register Phase 2 stale totals to the canonical 135-test Phase 2 evidence while moving GAP-011 to completed Phase 3.

- [ ] **Step 5: Final review and commit**

Review the diff for accidental credentials/data/generated artifacts, then commit:

```bash
git add docs/reviews/2026-08-26-phase-3-verification.md docs/reviews/2026-08-26-gap-register.md
git commit -m "docs: record phase 3 verification"
```
