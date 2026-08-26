# Phase 5 Admin UX and Operations Design

Date: 2026-08-26

## Goal
Complete the existing admin and operator experience without changing the FastAPI/Jinja/SQLite architecture. Close GAP-013 through GAP-016, align visible UI claims with tested behavior, and make Windows/Tailscale/RAG workflows recoverable by a nontechnical administrator.

## Starting Point
Phase 5 starts from verified Phase 4 commit `d446c7a` on an isolated branch/worktree. Fresh baseline: `165 passed`. No merge or push is part of this phase.

## Windows and RAG Guidance — GAP-013
- Document a short-path Windows environment instead of changing global long-path policy.
- Show a writable explicit pytest `--basetemp`.
- Separate core/test environment from optional full RAG/parser dependencies.
- Explain safe behavior when optional RAG dependencies are unavailable.
- Include recovery for WinError 206, inaccessible pytest temp directories, and RAG initialization failures.

## Telegram/Tailscale Operations — GAP-014
Move URL/command policy into `apps/api/chatbot_manager/admin/telegram_ops.py` so setup and disable share one contract.

The helper will:
- parse configured URLs with `urllib.parse.urlsplit`;
- accept only `http`/`https` URLs with a hostname;
- derive explicit port, otherwise 443 for HTTPS or 80 for HTTP;
- build Windows commands without `sudo`;
- build POSIX commands without assuming interactive sudo;
- keep discovery as `tailscale status --json`;
- convert missing CLI, invalid status JSON, timeout, access denial, and non-zero commands into stable safe outcomes;
- use the same port/platform policy for setup and disable;
- never expose raw subprocess stderr in browser URLs.

Routes keep responsibility for CSRF/auth, credentials, Telegram setWebhook/deleteWebhook, and persistence.

## Admin UX and Accessibility
Do not redesign the UI. Harden existing workflows:
- success feedback uses `role="status"`; errors use `role="alert"`;
- navigation has an accessible label and current page uses `aria-current="page"`;
- secret inputs stay password fields with safe autocomplete semantics;
- copy/webhook buttons have explicit button types and accessible names;
- focus-visible styling is obvious for links/buttons/form controls;
- narrow layouts do not depend on the fixed sidebar or overflow forms;
- unavailable/future functionality is explicitly labeled instead of appearing ready.

Browser checks cover Dashboard, Channels, Rules, Assistant, Knowledge, and Test Chat at desktop and narrow viewport, with keyboard navigation through Channels as the representative workflow.

## Documentation Accuracy — GAP-015
README must:
- list LINE, Messenger verification/events, and Telegram endpoints exactly once;
- document environment setup and safe local defaults;
- document Windows short-path/test-temp workflow;
- document tunnels/webhooks and Telegram/Tailscale platform behavior;
- document RAG prerequisites and optional-dependency failure behavior;
- document backup/recovery for SQLite, uploaded knowledge, and RAG working directory;
- document common failures/recovery and repeatable test commands.

## Dependency/TestClient Warning — GAP-016
First reproduce the warning and record installed package versions. Fix the cause rather than suppressing it. Prefer the smallest compatible dependency constraint or TestClient usage change. Update `uv.lock` only if dependency metadata changes. Warning filters that merely hide the deprecation are not acceptable.

## Files and Boundaries
- Create `apps/api/chatbot_manager/admin/telegram_ops.py`.
- Modify `apps/api/chatbot_manager/admin/routes.py` only to consume the helper and map stable outcomes.
- Modify `base.html`, `channels.html`, and only other templates proven to need fixes.
- Modify `styles.css` only for required focus/responsive behavior.
- Create `tests/test_phase5_admin_operations.py`.
- Modify `README.md`; add operator docs only if useful.
- Modify `pyproject.toml`/`uv.lock` only if required for GAP-016.

## Error Handling
Operator-facing errors are stable and actionable. Raw provider/subprocess exception text is not placed in redirect query strings. Telegram API failure stays distinct from Tailscale command failure. Disable clears stored webhook state only after Telegram deleteWebhook succeeds; Tailscale cleanup failure is reported safely.

## Testing Strategy
1. TDD for Telegram URL/platform/command contracts and mocked route behavior.
2. Template contract tests for status/alert/current-page/secret-field semantics.
3. Documentation contract checks for exact endpoints and recovery sections.
4. Reproduce dependency warning, apply the smallest root-cause fix, verify warning-free behavior.
5. Focused tests, full pytest, compileall, `git diff --check`, and real browser desktop/narrow/keyboard checks.

External Telegram, Tailscale, LLM, and RAG credentials are not required for automated tests.

## Completion Gate
- GAP-013 through GAP-016 are satisfied and recorded.
- Full suite passes without the targeted TestClient deprecation warning.
- Telegram setup/disable tests prove platform-safe commands and structured URL parsing.
- Primary admin workflows pass desktop/narrow/keyboard browser checks.
- README/UI claims match tested behavior.
- `compileall` and `git diff --check` pass.
- Final Git status is clean after verification docs are committed.
- No merge or push.
