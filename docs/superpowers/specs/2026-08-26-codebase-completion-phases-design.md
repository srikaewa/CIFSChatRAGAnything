# Codebase Completion Phases Design

Date: 2026-08-26

## Goal

Bring the existing CIFS Chatbot Manager to a stable, secure, feature-complete, and releasable state without discarding or silently rewriting the current uncommitted work.

## Current Context

The repository contains a working FastAPI chatbot manager and a large uncommitted feature set. Existing work includes LINE and Messenger support, Telegram additions, rule and escalation changes, RAG integration, an admin dashboard, and a knowledge graph. The handoff reports 63 passing tests from an earlier environment, but that result must be reproduced from a documented environment before it is accepted as the current baseline.

The worktree is user-owned. Existing modifications and untracked files must be preserved. Every change made during this effort must be scoped, reviewable, and distinguishable from pre-existing work.

## Delivery Strategy

Work proceeds through six ordered phases. A phase begins only after the preceding phase meets its completion gate. Defects discovered later are assigned to the earliest applicable phase and resolved before release verification continues.

The project remains a single FastAPI application with server-rendered Jinja templates, SQLModel/SQLite persistence, provider-specific channel adapters, a deterministic chatbot engine, and RAG-Anything integration. Improvements follow existing boundaries unless a demonstrated defect requires a focused refactor.

## Phase 1: Baseline and Inventory

### Purpose

Establish an evidence-based picture of the repository before changing behavior.

### Work

- Record the Git branch, commit, tracked modifications, and untracked files without altering them.
- Identify supported development environments and a reproducible command for installing dependencies and running tests on the current host.
- Run the complete automated test suite and record passes, failures, skips, warnings, and execution constraints.
- Map application modules, routes, templates, models, channel adapters, RAG services, and tests.
- Compare implemented behavior with README, design documents, handoff notes, environment examples, and visible UI claims.
- Classify each feature as complete, partial, broken, unverified, or intentionally out of scope.
- Produce a prioritized defect and gap inventory with evidence, affected files, risk, and recommended phase.

### Completion Gate

- The documented baseline can be reproduced with explicit commands.
- Every current feature has an implementation and verification status.
- Known failures and environment blockers are recorded rather than inferred.
- No pre-existing worktree change is overwritten, deleted, or bundled into an unrelated commit.

## Phase 2: Stabilization and Security

### Work

- Fix baseline test failures and deterministic runtime errors.
- Validate webhook authenticity for every supported provider before processing messages.
- Review login, session, logout, authorization, and CSRF behavior for state-changing admin routes.
- Prevent secrets from appearing in templates, logs, errors, or persisted plaintext outside the documented configuration model.
- Validate upload names, file types, sizes, storage paths, and failure cleanup.
- Ensure provider, RAG, parser, database, and background-task failures produce safe behavior and actionable logs.
- Add focused regression tests for every corrected defect.

### Completion Gate

- The core automated suite passes in the documented environment.
- Security-sensitive routes have positive and negative tests.
- Webhook, upload, authentication, and secret-handling controls fail closed.
- Operational failures do not crash webhook processing or expose credentials.

## Phase 3: Core Chatbot Completion

### Work

- Finish LINE, Messenger, and Telegram configuration, parsing, validation, sending, webhook setup, and status reporting.
- Verify decision order for commands, enabled rules, conditions, escalation, RAG, and fallback.
- Complete escalation state, admin notification, and user-facing escalation responses.
- Verify RAG ingestion, indexing status, retry/failure behavior, querying, and fallback handling.
- Ensure Test Chat exercises the same engine as real channels, with intentional provider differences documented.
- Add contract-focused tests using mocked provider and model APIs.

### Completion Gate

- Each supported channel completes a mocked inbound-to-outbound flow.
- Rule, escalation, RAG, and fallback paths have deterministic tests.
- Configuration pages accurately distinguish ready, incomplete, disabled, and failed states.
- Unsupported events and external outages have documented behavior.

## Phase 4: Knowledge Graph Completion

### Work

- Validate graph and label API responses against one documented schema.
- Verify entity and relationship normalization, center selection, depth, truncation, statistics, and warnings.
- Complete loading, empty, error, and truncated-result states.
- Verify search, type filtering, depth, expansion, layout selection, neighbor details, and metadata panels.
- Enforce practical query and rendering limits.
- Run browser checks confirming Cytoscape renders a nonblank graph and responds to controls.

### Completion Gate

- API schema and graph transformation tests pass.
- A real browser renders representative empty, normal, truncated, and error cases.
- All visible controls have verified behavior.
- Large graph requests remain bounded and communicate truncation.

## Phase 5: Admin UX and Operations

### Work

- Align dashboard status, navigation, forms, and help text with actual capabilities.
- Add clear validation, success, pending, and failure feedback.
- Verify keyboard access, labels, focus, contrast, responsive layouts, and safe secret fields.
- Correct README endpoints, supported-channel claims, setup commands, prerequisites, and development guidance.
- Document environment configuration, tunnels/webhooks, RAG dependencies, backup, recovery, and common failures.
- Remove or explicitly label disabled and future functionality.

### Completion Gate

- A nontechnical administrator can configure and test the application from the documentation.
- Primary workflows are usable with keyboard and narrow viewport.
- Documentation and UI claims match tested behavior.
- Common configuration failures provide recovery instructions.

## Phase 6: Release Verification

### Work

- Run the full automated suite in a clean documented environment.
- Run health, login, configuration, rule, Test Chat, upload, graph, logs, and channel smoke checks.
- Exercise external integrations through mocked services and document optional real-service checks.
- Run security regressions for authentication, CSRF, signatures, uploads, secrets, and unsafe error disclosure.
- Review the final diff for unrelated edits, generated artifacts, leaked credentials, and accidental changes.
- Write release notes covering features, setup or migration steps, evidence, and known limitations.

### Completion Gate

- Required automated and smoke checks pass with recorded commands and results.
- The final diff contains only intentional, reviewed changes.
- No credential or local data artifact is included.
- Release notes describe the verified product accurately.

## Testing Strategy

Use test-driven development for behavior changes. Each fix or completed feature starts with a focused failing test, followed by the minimum implementation, relevant module tests, and the full regression suite at the phase gate.

Testing layers are unit tests for adapters and logic, route/integration tests for application flows, browser checks for graph and critical admin workflows, and live-process smoke checks for startup and representative flows. External providers, LLMs, and parsers are mocked in deterministic automated tests. Optional real-service checks require operator-supplied credentials and must not expose them in logs or artifacts.

## Change Management

- Preserve the dirty worktree and inspect before editing overlapping files.
- Do not use destructive Git operations or broad cleanup commands.
- Keep phase changes narrow and reviewable.
- Record out-of-scope ideas separately.
- Do not declare a phase complete without fresh verification evidence.

## Out of Scope

- Multi-tenant architecture, billing, or organization-level RBAC.
- Replacing the FastAPI/Jinja/SQLite architecture without a separately approved design.
- New messaging providers beyond LINE, Messenger, and Telegram.
- Production infrastructure such as Redis workers, Postgres, object storage, or hosted vector databases.
- Real external credentials as a prerequisite for automated tests.
