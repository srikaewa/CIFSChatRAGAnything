# Phase 1 Baseline and Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible test baseline, complete feature inventory, and prioritized evidence-backed gap register without changing application behavior or disturbing pre-existing worktree changes.

**Architecture:** Phase 1 is read-only with respect to application code. It creates three focused review artifacts under `docs/reviews`: an environment/test baseline, a feature inventory, and a prioritized gap register. Evidence comes from Git, source and test inspection, automated test output, and documentation comparison.

**Tech Stack:** PowerShell, Git, Python 3.11+, pytest, FastAPI route inspection, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-26-codebase-completion-phases-design.md`

## Global Constraints

- Preserve every tracked modification and untracked file that existed before Phase 1.
- Do not edit application code, tests, configuration, `.env`, local data, or existing user documentation during Phase 1.
- Do not use destructive Git commands, cleanup commands, or broad staging.
- Never display `.env` contents or secret values; inspect `.env.example` only.
- Use `rtk` for shell commands when available. The current Windows shell has no `rtk` executable; raw commands are permitted only as the repository instruction's debugging fallback, and the absence must be recorded in the baseline.
- Use Git with `-c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager'` because the mapped share has different ownership.
- Stage and commit only the review artifacts named by each task.

## File Structure

- Create `docs/reviews/2026-08-26-phase-1-baseline.md`: repository state, host/runtime discovery, dependency setup, test commands, exact results, warnings, and blockers.
- Create `docs/reviews/2026-08-26-feature-inventory.md`: feature-by-feature implementation and verification status with source/test evidence.
- Create `docs/reviews/2026-08-26-gap-register.md`: prioritized defects and completion gaps, severity, evidence, affected files, and target phase.

---

### Task 1: Repository and Environment Baseline

**Files:**
- Create: `docs/reviews/2026-08-26-phase-1-baseline.md`

**Interfaces:**
- Consumes: repository state at commit `3afbc42` plus all preserved worktree changes.
- Produces: a reproducible baseline report used by Tasks 2 and 3.

- [ ] **Step 1: Capture repository identity and dirty-worktree evidence**

Run from the repository root:

```powershell
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' rev-parse --show-toplevel
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' branch --show-current
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' rev-parse HEAD
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' status --short
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' diff --stat
```

Expected: root resolves to the mapped project; branch is `main`; HEAD begins with `3afbc42`; status remains dirty because pre-existing work is preserved.

- [ ] **Step 2: Capture host and runtime availability without reading secrets**

Run:

```powershell
$PSVersionTable.PSVersion
Get-Command rtk, uv, python, py, wsl -ErrorAction SilentlyContinue | Select-Object Name, Source
Get-Content -Raw pyproject.toml
Get-Content -Raw .venv/pyvenv.cfg
& 'C:\Users\CCS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' --version
```

Expected: document that `rtk` and `uv` are unavailable in the current Windows shell, `.venv` is Linux-format, WSL has Python 3.12 but cannot see the mapped repository, and bundled Windows Python is 3.12.

- [ ] **Step 3: Create an isolated Windows test environment**

Run:

```powershell
& 'C:\Users\CCS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv-windows
& '.\.venv-windows\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv-windows\Scripts\python.exe' -m pip install -e . pytest pytest-asyncio respx
```

Expected: `.venv-windows` contains the project and development test dependencies. If dependency resolution or RAG-Anything fails on Windows, record the full package/error name and use Step 4's minimal test dependency fallback; do not modify `pyproject.toml` in Phase 1.

- [ ] **Step 4: Apply the minimal test dependency fallback only if Step 3 fails**

Run:

```powershell
& '.\.venv-windows\Scripts\python.exe' -m pip install fastapi 'uvicorn[standard]' sqlmodel jinja2 python-multipart httpx itsdangerous 'pwdlib[argon2]' pydantic-settings python-telegram-bot pytest pytest-asyncio respx
```

Expected: core tests can import the application because RAG-Anything imports are lazy. Record that full RAG dependency installation remains unverified if this fallback is required.

- [ ] **Step 5: Run collection and the full automated suite**

Run:

```powershell
& '.\.venv-windows\Scripts\python.exe' -m pytest --collect-only -q
& '.\.venv-windows\Scripts\python.exe' -m pytest -q
```

Expected: record the exact collected count, pass/fail/skip count, warnings, duration, and each failing node ID. Do not fix failures in Phase 1.

- [ ] **Step 6: Run focused suites to localize any failures**

Run:

```powershell
& '.\.venv-windows\Scripts\python.exe' -m pytest tests/test_app_boot.py tests/test_models.py -q
& '.\.venv-windows\Scripts\python.exe' -m pytest tests/test_chatbot_engine.py tests/test_rag_service.py -q
& '.\.venv-windows\Scripts\python.exe' -m pytest tests/test_line_channel.py tests/test_messenger_channel.py tests/test_telegram_channel.py -q
& '.\.venv-windows\Scripts\python.exe' -m pytest tests/test_webhooks.py tests/test_telegram_webhooks.py -q
& '.\.venv-windows\Scripts\python.exe' -m pytest tests/test_admin_routes.py -q
```

Expected: each suite's result is copied into the baseline report. Failures are classified as code, dependency, host-platform, missing external prerequisite, or nondeterministic.

- [ ] **Step 7: Write the baseline report**

Create `docs/reviews/2026-08-26-phase-1-baseline.md` with this exact structure and replace bracketed evidence fields with observed values:

```markdown
# Phase 1 Baseline

Date: 2026-08-26

## Repository State

- Branch: `[observed branch]`
- HEAD: `[full commit]`
- Pre-existing tracked modifications: `[count]`
- Pre-existing untracked paths: `[count]`
- Preservation check: no existing path modified or deleted by Phase 1.

## Host and Tooling

| Tool | Observed version/path | Status |
|---|---|---|
| PowerShell | `[value]` | available |
| rtk | `[value or unavailable]` | `[status]` |
| uv | `[value or unavailable]` | `[status]` |
| Repository `.venv` | Linux format | unavailable to Windows |
| Bundled Windows Python | `[version and path]` | available |
| WSL Python | `[version]` | repository mapping unavailable |

## Reproduction Commands

```powershell
& 'C:\Users\CCS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv-windows
& '.\.venv-windows\Scripts\python.exe' -m pip install -e . pytest pytest-asyncio respx
& '.\.venv-windows\Scripts\python.exe' -m pytest -q
```

## Test Results

- Collected: `[count]`
- Passed: `[count]`
- Failed: `[count]`
- Skipped: `[count]`
- Warnings: `[count and summary]`
- Duration: `[duration]`

## Focused Suite Results

| Suite | Result | Notes |
|---|---|---|
| Boot and models | `[result]` | `[evidence]` |
| Engine and RAG | `[result]` | `[evidence]` |
| Channel adapters | `[result]` | `[evidence]` |
| Webhooks | `[result]` | `[evidence]` |
| Admin routes | `[result]` | `[evidence]` |

## Failures and Blockers

| ID | Category | Evidence | Impact | Target phase |
|---|---|---|---|---|
| BASE-001 | `[category]` | `[command/error]` | `[impact]` | `[phase]` |

## Baseline Conclusion

`[One paragraph stating what is verified, what is not, and whether Phase 1 testing is reproducible.]`
```

- [ ] **Step 8: Verify the report and preservation guarantee**

Run:

```powershell
rg -n 'TBD|TODO|FIXME|\[observed|\[count|\[value|\[status|\[result|\[evidence|\[category|\[command|\[impact|\[phase|\[One paragraph' docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' diff --check -- docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' status --short
```

Expected: the placeholder scan prints nothing; `diff --check` prints nothing; pre-existing worktree entries remain present and only the baseline report plus `.venv-windows` are newly introduced by this task.

- [ ] **Step 9: Commit only the baseline report**

```powershell
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' add -- docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' commit -m 'docs: record phase 1 test baseline' -- docs/reviews/2026-08-26-phase-1-baseline.md
```

Expected: one commit containing only the baseline report. `.venv-windows` remains untracked/ignored and is never committed.

### Task 2: Feature Inventory

**Files:**
- Create: `docs/reviews/2026-08-26-feature-inventory.md`

**Interfaces:**
- Consumes: Task 1 test evidence plus source, templates, tests, README, HANDOFF, and design documents.
- Produces: complete feature/status matrix used by Task 3.

- [ ] **Step 1: Enumerate implementation surfaces**

Run:

```powershell
rg --files apps/api/chatbot_manager tests docs | Sort-Object
rg -n '^@(router|app)\.(get|post|put|patch|delete)' apps/api/chatbot_manager
rg -n '^class |^def |^async def ' apps/api/chatbot_manager/channels apps/api/chatbot_manager/chatbot apps/api/chatbot_manager/rag
rg -n 'href=|action=|fetch\(' apps/api/chatbot_manager/templates
```

Expected: obtain route, module, and UI control inventories without executing external integrations.

- [ ] **Step 2: Compare claims and incomplete markers**

Run:

```powershell
rg -n 'LINE|Messenger|Telegram|RAG|Graph|Rule|Escalat|Webhook|Upload|Test Chat|Logs' README.md HANDOFF.md DESIGN.md docs/superpowers/specs
rg -n 'TODO|FIXME|NotImplemented|pass\s*$|coming soon|disabled' apps tests README.md HANDOFF.md
```

Expected: every public claim and incomplete marker is associated with a feature row or gap ID.

- [ ] **Step 3: Write the feature inventory**

Create `docs/reviews/2026-08-26-feature-inventory.md` with one row for each listed capability and no omitted status:

```markdown
# Feature Inventory

Date: 2026-08-26

Status values: `complete`, `partial`, `broken`, `unverified`, `out-of-scope`.

| Area | Capability | Status | Implementation evidence | Test evidence | Documentation evidence | Target phase |
|---|---|---|---|---|---|---|
| Application | startup and health | `[status]` | `[file:line]` | `[test node/result]` | `[doc reference]` | `[phase]` |
| Authentication | login, session, logout, route protection | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Channels | LINE inbound/outbound | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Channels | Messenger inbound/outbound | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Channels | Telegram inbound/outbound and webhook setup | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Engine | commands, rules, conditions, priorities | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Engine | escalation and admin notification | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| RAG | configuration and query fallback | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Knowledge | upload, indexing, retry, delete | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Graph | API, labels, rendering, controls | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Admin | dashboard and channel configuration | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Admin | rules and assistant configuration | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Admin | Test Chat and logs | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
| Operations | environment setup, tunnels, backup, recovery | `[status]` | `[evidence]` | `[evidence]` | `[evidence]` | `[phase]` |
```

Add rows when source inspection reveals additional user-visible capabilities. Use `unverified`, not `complete`, when only implementation or prior handoff claims exist without fresh evidence.

- [ ] **Step 4: Verify and commit the inventory**

Run:

```powershell
rg -n '\[status\]|\[evidence\]|\[file:line\]|\[test node/result\]|\[doc reference\]|\[phase\]|TBD|TODO|FIXME' docs/reviews/2026-08-26-feature-inventory.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' diff --check -- docs/reviews/2026-08-26-feature-inventory.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' add -- docs/reviews/2026-08-26-feature-inventory.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' commit -m 'docs: inventory chatbot manager features' -- docs/reviews/2026-08-26-feature-inventory.md
```

Expected: scans print nothing; commit contains only the inventory.

### Task 3: Prioritized Gap Register

**Files:**
- Create: `docs/reviews/2026-08-26-gap-register.md`

**Interfaces:**
- Consumes: Tasks 1 and 2 reports.
- Produces: the ordered work queue for Phases 2 through 6.

- [ ] **Step 1: Inspect high-risk boundaries directly**

Run:

```powershell
rg -n 'SessionMiddleware|require_admin|login|logout|Form\(|UploadFile|write_bytes|BackgroundTasks' apps/api/chatbot_manager
rg -n 'signature|secret|token|api_key|credential|mask' apps/api/chatbot_manager
rg -n 'except|raise HTTPException|pass\s*$|RuntimeError' apps/api/chatbot_manager
rg -n 'max_nodes|max_depth|is_truncated|warnings|cytoscape|fetch\(' apps/api/chatbot_manager
```

Expected: each suspected risk is confirmed or dismissed using a precise source location and corresponding test evidence.

- [ ] **Step 2: Assign severity and phase**

Use these definitions exactly:

- `P0`: credential exposure, data loss, authentication bypass, or remote execution requiring immediate containment.
- `P1`: broken core workflow, webhook forgery, persistent corruption, or reproducible crash affecting normal operation.
- `P2`: incomplete feature, misleading status, weak validation, missing recovery, or important unverified behavior.
- `P3`: documentation, accessibility, polish, or maintainability issue without immediate operational risk.

Assign security and deterministic regressions to Phase 2, messaging/RAG/escalation gaps to Phase 3, graph gaps to Phase 4, UX/operations gaps to Phase 5, and final-only verification gaps to Phase 6.

- [ ] **Step 3: Write the gap register**

Create `docs/reviews/2026-08-26-gap-register.md`:

```markdown
# Gap Register

Date: 2026-08-26

## Summary

| Priority | Count |
|---|---:|
| P0 | `[count]` |
| P1 | `[count]` |
| P2 | `[count]` |
| P3 | `[count]` |

## Ordered Gaps

| ID | Priority | Phase | Area | Finding | Evidence | User impact | Acceptance check |
|---|---|---|---|---|---|---|---|
| GAP-001 | `[P0-P3]` | `[2-6]` | `[area]` | `[one factual sentence]` | `[file:line and test/result]` | `[concrete impact]` | `[observable pass condition]` |

## Dependency Order

1. Resolve all P0 and P1 Phase 2 items.
2. Resolve remaining Phase 2 regressions and security controls.
3. Complete Phase 3 channel, engine, escalation, and RAG workflows.
4. Complete Phase 4 graph behavior and browser verification.
5. Complete Phase 5 UX and operations work.
6. Execute Phase 6 release verification.

## Explicitly Out of Scope

- Multi-tenancy, billing, and organization RBAC.
- New providers beyond LINE, Messenger, and Telegram.
- Architectural replacement of FastAPI, Jinja, SQLModel, or SQLite.
- Production infrastructure expansion.
```

Every row must have reproducible evidence and an observable acceptance check. Do not create speculative gaps.

- [ ] **Step 4: Cross-check inventory coverage and commit**

Run:

```powershell
rg -n '\[count\]|\[P0-P3\]|\[2-6\]|\[area\]|\[one factual sentence\]|\[file:line and test/result\]|\[concrete impact\]|\[observable pass condition\]|TBD|TODO|FIXME' docs/reviews/2026-08-26-gap-register.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' diff --check -- docs/reviews/2026-08-26-gap-register.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' add -- docs/reviews/2026-08-26-gap-register.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' commit -m 'docs: prioritize chatbot manager completion gaps' -- docs/reviews/2026-08-26-gap-register.md
```

Expected: scans print nothing; every partial, broken, or unverified inventory row either maps to a gap or explains why no action is needed; commit contains only the gap register.

### Task 4: Phase 1 Gate Review

**Files:**
- Modify: `docs/reviews/2026-08-26-phase-1-baseline.md`

**Interfaces:**
- Consumes: all three Phase 1 artifacts and current Git state.
- Produces: a recorded Phase 1 gate decision and recommended Phase 2 starting order.

- [ ] **Step 1: Re-run the reproducible test command**

Run:

```powershell
& '.\.venv-windows\Scripts\python.exe' -m pytest -q
```

Expected: result matches the baseline or any difference is explained with evidence. Phase 1 does not fix failures.

- [ ] **Step 2: Verify deliverables and repository preservation**

Run:

```powershell
Get-Item docs/reviews/2026-08-26-phase-1-baseline.md, docs/reviews/2026-08-26-feature-inventory.md, docs/reviews/2026-08-26-gap-register.md | Select-Object Name, Length
rg -n 'TBD|TODO|FIXME|\[[a-zA-Z][^]]*\]' docs/reviews/2026-08-26-*.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' status --short
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' log -4 --oneline --decorate
```

Expected: all reports are nonempty; placeholder scan prints nothing except intentional Markdown links if present; the original dirty paths remain preserved; only Phase 1 reports were committed by this plan.

- [ ] **Step 3: Record the gate decision**

Append this completed section to `docs/reviews/2026-08-26-phase-1-baseline.md`:

```markdown
## Phase 1 Gate

- Reproducible environment: `[yes/no with reason]`
- Complete feature inventory: `[yes/no with reason]`
- Prioritized evidence-backed gap register: `[yes/no with reason]`
- Pre-existing worktree preserved: `[yes/no with evidence]`
- Decision: `[PASS or BLOCKED]`
- Phase 2 starting order: `[ordered gap IDs, or blocking condition]`
```

Phase 1 passes only when every value is evidence-backed and the first four gate conditions are `yes`.

- [ ] **Step 4: Verify and commit the gate result**

Run:

```powershell
rg -n '\[yes/no with reason\]|\[yes/no with evidence\]|\[PASS or BLOCKED\]|\[ordered gap IDs, or blocking condition\]' docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' diff --check -- docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' add -- docs/reviews/2026-08-26-phase-1-baseline.md
git -c safe.directory='//192.168.1.153/Projects/ChatBot/CIFSChatbotManager' commit -m 'docs: record phase 1 completion gate' -- docs/reviews/2026-08-26-phase-1-baseline.md
```

Expected: scans print nothing; the final commit contains only the baseline gate update.
