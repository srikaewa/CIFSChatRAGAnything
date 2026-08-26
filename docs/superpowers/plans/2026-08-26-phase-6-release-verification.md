# Phase 6 Release Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the completed CIFS Chatbot Manager release candidate is regression-safe, operationally documented, free of accidental artifacts/secrets, and ready for an integration decision.

**Architecture:** Phase 6 makes no planned product-code changes. It verifies the clean Phase 5 tip in a fresh isolated worktree using the documented Python environment, runs broad and focused smoke/security suites, audits the complete completion-series diff and tracked files, then records release notes and final verification evidence.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, SQLModel, pytest, Git, mocked provider/RAG/LLM/Tailscale services.

**Spec:** `docs/superpowers/specs/2026-08-26-codebase-completion-phases-design.md`

## Global Constraints
- Do not merge or push.
- Do not use live provider, RAG, LLM, or Tailscale credentials.
- Do not suppress warnings or weaken security tests.
- Do not include `.env`, SQLite databases, uploads, RAG work data, credentials, or generated local artifacts.
- Product-code changes are allowed only if a release gate exposes a real defect; any such fix must use TDD and rerun the full gate.

---

### Task 1: Clean release-candidate baseline

**Files:** none.

- [x] Verify branch/worktree status is clean and HEAD is based on Phase 5 tip `a993766`.
- [x] Run `python -m pytest -q -W default --basetemp <writable-temp>` using the documented compatible environment.
- [x] Run `python -m compileall -q apps/api` and `git diff --check`.

### Task 2: Functional and security smoke suites

**Files:** none unless a defect is found.

- [x] Run focused admin/functional smoke tests covering health/boot, login, configuration, rules, Test Chat, upload/knowledge, graph, logs, and channel behavior.
- [x] Run focused external-integration tests using mocked LINE, Messenger, Telegram, RAG/LLM, and Tailscale behavior.
- [x] Run focused security regressions covering authentication/session hardening, CSRF, webhook signatures/fail-closed behavior, upload safety, encrypted secrets, and safe error disclosure.

### Task 3: Final diff and artifact audit

**Files:** none.

- [x] Review the completion-series diff from the pre-Phase-2 base through HEAD for unrelated changes.
- [x] Scan tracked filenames for local DB/upload/RAG/temp/generated artifacts.
- [x] Scan tracked diff/content for credential-like assignments and private-key markers, excluding documented placeholders/tests where appropriate.
- [x] Confirm `.env` and local data files are not tracked.

### Task 4: Release evidence and notes

**Files:**
- Create: `docs/releases/2026-08-26-release-candidate.md`
- Create: `docs/reviews/2026-08-26-phase-6-release-verification.md`
- Modify: `docs/reviews/2026-08-26-gap-register.md`
- Modify: this plan checkbox state.

- [x] Record features, setup/migration notes, verification evidence, optional live-service checks, and known limitations in release notes.
- [x] Record exact command results and release-gate decision in the Phase 6 verification report.
- [x] Add Phase 6 closure to the gap register without inventing new closed gaps.
- [x] Commit release evidence.
- [x] Rerun the full suite, compileall, `git diff --check`, and `git status --short` after the final evidence commit.
