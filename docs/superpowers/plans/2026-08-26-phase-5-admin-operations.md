# Phase 5 Admin UX and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Phase 5 operations/admin/documentation gaps while preserving the existing FastAPI/Jinja architecture.

**Architecture:** Extract Telegram/Tailscale host policy into one helper module consumed by existing routes, make narrowly scoped template/CSS accessibility fixes, then align operator documentation and dependency constraints with verified behavior. External services stay mocked in automated tests.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, SQLModel, pytest, httpx/TestClient, HTML/CSS, Tailscale CLI.

**Spec:** `docs/superpowers/specs/2026-08-26-phase-5-admin-operations-design.md`

## Global Constraints
- Do not merge or push.
- Do not change FastAPI/Jinja/SQLite architecture.
- Do not require real Telegram/Tailscale/LLM/RAG credentials in automated tests.
- Do not suppress deprecation warnings merely to hide GAP-016.
- Never place raw subprocess stderr or secrets into browser redirect URLs.

---

### Task 1: Telegram/Tailscale operations contract

**Files:**
- Create: `apps/api/chatbot_manager/admin/telegram_ops.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Create: `tests/test_phase5_admin_operations.py`

**Interfaces:**
- Produces: `service_port(url: str) -> int`, `tailscale_command(action: str, port: int, platform_name: str | None = None) -> list[str]`, `discover_tailscale_host(run=...) -> tuple[str | None, str | None]`, `run_funnel(action: str, port: int, run=..., platform_name: str | None = None) -> str | None`.
- Routes consume stable error codes: `invalid_public_url`, `tailscale_cli_missing`, `tailscale_timeout`, `tailscale_status_failed`, `tailscale_status_invalid`, `tailscale_dns_missing`, `tailscale_access_denied`, `tailscale_funnel_failed`.

- [ ] **Step 1: Write failing helper tests**

```python
@pytest.mark.parametrize(("url", "expected"), [
    ("http://localhost", 80),
    ("https://example.test", 443),
    ("http://localhost:8765/path", 8765),
])
def test_service_port(url, expected):
    assert service_port(url) == expected

@pytest.mark.parametrize("url", ["", "localhost:8000", "ftp://example.test", "https:///missing-host"])
def test_service_port_rejects_invalid_url(url):
    with pytest.raises(ValueError):
        service_port(url)


def test_windows_funnel_command_has_no_sudo():
    assert tailscale_command("on", 8000, "Windows") == ["tailscale", "funnel", "--bg", "8000"]


def test_posix_funnel_command_has_no_interactive_sudo_assumption():
    assert tailscale_command("off", 8000, "Linux") == ["tailscale", "funnel", "off", "8000"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest -q tests/test_phase5_admin_operations.py -k "service_port or funnel_command"`
Expected: import/function failures because `telegram_ops.py` does not exist.

- [ ] **Step 3: Implement minimal helper and route integration**

```python
def service_port(url: str) -> int:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_public_url")
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def tailscale_command(action: str, port: int, platform_name: str | None = None) -> list[str]:
    if action == "on":
        return ["tailscale", "funnel", "--bg", str(port)]
    if action == "off":
        return ["tailscale", "funnel", "off", str(port)]
    raise ValueError("unsupported_tailscale_action")
```

Add discovery/run wrappers using `subprocess.run(..., timeout=...)` and map exceptions/return codes to the stable codes above. Replace inline URL splitting and `sudo tailscale` calls in setup/disable routes.

- [ ] **Step 4: Add route-level mocked tests**
Test structured explicit/default ports, Windows command construction, missing CLI, timeout, invalid JSON, access denied, Telegram setWebhook failure, and disable behavior.

- [ ] **Step 5: Run focused tests and commit**
Run: `python -m pytest -q tests/test_phase5_admin_operations.py tests/test_telegram_webhooks.py tests/test_admin_routes.py`
Expected: PASS.
Commit: `feat: harden telegram operations`

---

### Task 2: Admin feedback and accessibility

**Files:**
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `apps/api/chatbot_manager/templates/channels.html`
- Modify if required by tests: `apps/api/chatbot_manager/templates/assistant.html`, `knowledge.html`, `rules.html`, `test_chat.html`
- Modify: `apps/api/chatbot_manager/static/styles.css`
- Test: `tests/test_phase5_admin_operations.py`

**Interfaces:** HTML contracts only; no route schema changes.

- [ ] **Step 1: Write failing template contract tests**

```python
def test_channels_page_has_accessible_feedback_and_secret_fields(client):
    login(client)
    html = client.get("/channels?saved=1").text
    assert 'role="status"' in html
    assert 'aria-label="Primary navigation"' in html
    assert 'aria-current="page"' in html
    assert 'type="password"' in html
    assert 'autocomplete="new-password"' in html
    assert 'type="button"' in html


def test_base_css_keeps_visible_focus_and_narrow_layout():
    css = Path("apps/api/chatbot_manager/static/styles.css").read_text()
    assert ":focus-visible" in css
    assert "@media (max-width: 760px)" in css
```

- [ ] **Step 2: Run tests and verify RED**
Expected: missing navigation/current-page/status/autocomplete/button semantics.

- [ ] **Step 3: Implement minimal HTML/CSS changes**
Add `aria-label`, conditional `aria-current`, correct feedback roles, safe autocomplete, explicit `type="button"`, and only responsive/focus CSS required by tests/browser checks.

- [ ] **Step 4: Run focused tests and commit**
Run: `python -m pytest -q tests/test_phase5_admin_operations.py tests/test_admin_routes.py tests/test_phase3_provider_contracts.py`
Expected: PASS.
Commit: `fix: improve admin accessibility feedback`

---

### Task 3: Operator documentation and Windows recovery

**Files:**
- Modify: `README.md`
- Create: `docs/operations.md`
- Test: `tests/test_phase5_admin_operations.py`

**Interfaces:** README is the entry point; `docs/operations.md` carries detailed Windows/RAG/Tailscale/backup recovery procedures.

- [ ] **Step 1: Write documentation contract test**

```python
def test_readme_lists_each_webhook_once_and_links_operations():
    text = Path("README.md").read_text(encoding="utf-8")
    assert text.count("`/webhooks/line`") == 1
    assert text.count("`/webhooks/messenger`") == 2
    assert text.count("`/webhooks/telegram`") == 1
    assert "docs/operations.md" in text


def test_operations_guide_covers_windows_rag_backup_and_recovery():
    text = Path("docs/operations.md").read_text(encoding="utf-8")
    for phrase in ["WinError 206", "--basetemp", "Tailscale", "RAG", "Backup", "Restore"]:
        assert phrase in text
```

- [ ] **Step 2: Run tests and verify RED**
Expected: duplicate/missing webhook entries and missing operations guide.

- [ ] **Step 3: Rewrite README endpoint/setup sections and add operations guide**
Document short-path Windows venv, explicit pytest temp, optional RAG stack, Tailscale platform behavior, SQLite/uploads/RAG backup/restore, and common recovery paths.

- [ ] **Step 4: Run focused tests and commit**
Run: `python -m pytest -q tests/test_phase5_admin_operations.py`
Expected: PASS.
Commit: `docs: complete operator guidance`

---

### Task 4: Dependency warning and Phase 5 gate

**Files:**
- Modify only if required: `pyproject.toml`, `uv.lock`, tests using TestClient
- Modify: `docs/reviews/2026-08-26-gap-register.md`
- Create: `docs/reviews/2026-08-26-phase-5-verification.md`

**Interfaces:** Dependency changes must remain compatible with Python >=3.11 and the existing test suite.

- [ ] **Step 1: Reproduce warning and record versions**
Run the full suite with default warnings plus a script printing FastAPI, Starlette and httpx versions. Identify the exact warning source.

- [ ] **Step 2: Apply smallest root-cause fix**
If the warning is caused by an incompatible version combination, constrain the compatible package range and refresh `uv.lock`; if current TestClient usage itself is obsolete, update the fixture/client construction. Do not add warning suppression.

- [ ] **Step 3: Verify warning target is gone**
Run: `python -m pytest -q -W default`
Expected: full suite PASS with no GAP-016 TestClient/httpx deprecation warning.

- [ ] **Step 4: Browser gate**
Launch test app with disposable SQLite DB. Verify desktop and <=760px views for Dashboard, Channels, Rules, Assistant, Knowledge and Test Chat; use keyboard Tab navigation on Channels and confirm visible focus/current-page/feedback behavior.

- [ ] **Step 5: Final static and regression gates**
Run full pytest, `python -m compileall -q apps/api`, and `git diff --check`.

- [ ] **Step 6: Update gap register and verification report**
Record fresh counts, warning result, browser evidence, remaining limitations, and close GAP-013 through GAP-016 only when acceptance checks are satisfied.

- [ ] **Step 7: Commit verification**
Commit: `docs: record phase 5 verification`
Final requirement: `git status --short` is empty.
