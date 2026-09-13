# Chatbot Operations Console Master Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved CIFSChatbotManager redesign as six independently reviewable, deployable phases without a big-bang rewrite.

**Architecture:** Keep the existing FastAPI + Jinja + SQLModel application and evolve it incrementally from a single-assistant admin panel into a bot-centric control plane. CIFS owns Bot configuration, channels, conversations, testing, operations, users, and audit; external RAG-Anything/LightRAG owns documents, indexing, retrieval, graph storage, and final grounded answer generation.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, Jinja2, httpx, cryptography/Fernet, pwdlib/Argon2, pytest, pytest-asyncio, respx, vanilla HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Follow `expand -> migrate -> switch -> verify -> contract`; do not remove legacy paths before their replacements are verified.
- Preserve existing user data and current LINE/Messenger/Telegram behavior through migration checkpoints.
- Every production message resolves to exactly one Bot and one immutable published `BotConfigVersion` for the duration of the request.
- One `BotConfigVersion` binds to one Knowledge Service; many Bots may share the same Knowledge Service.
- External RAG generates the final grounded answer; CIFS must not run a second LLM rewrite by default.
- CIFS must not own document upload, parsing, indexing, graph storage, document deletion, or RAG backup after Phase 2 product-flow migration.
- Credentials are encrypted at rest and masked/replace-only in UI; secret values must not enter logs, traces, errors, or audit snapshots.
- LightRAG deployment used with conversation history must meet the validated safe version floor; design baseline is `>=1.5.5`, and Phase 2 must re-check the pinned deployment version/API contract before integration.
- Provider webhook verification and inbound idempotency are mandatory before conversation state or RAG calls are created.
- Human takeover is a hard runtime state: `human_active` conversations must not call Bot rules or RAG.
- Published Bot versions are immutable; restore creates a new Draft rather than mutating history.
- Regression `FAIL` blocks publish; `WARNING` requires explicit acknowledgement.
- Owner/Admin/Operator authorization is enforced server-side; hiding UI controls is never sufficient authorization.
- Keep relational storage and lightweight background scheduling until measured scale proves a need for larger infrastructure.
- Preserve unrelated untracked tool/config folders and unrelated dirty worktree changes.
- Use `rtk` for shell commands where available.
- Before each implementation phase, use `superpowers:using-git-worktrees` if an isolated worktree is needed; implementation must use TDD and `verification-before-completion` before completion claims.

## Plan Set

1. `docs/superpowers/plans/2026-09-13-phase-1-bot-foundation.md`
   - Bot/config/rule schema.
   - Default Bot migration.
   - Bot-centric read-only workspace foundation.
   - Current runtime remains behaviorally equivalent.

2. `docs/superpowers/plans/2026-09-13-phase-2-external-knowledge-service.md`
   - `Credential` and `KnowledgeService` registry.
   - External LightRAG client.
   - Draft Knowledge binding.
   - Remove local Knowledge/Graph management from normal navigation while retaining legacy code temporarily.

3. `docs/superpowers/plans/2026-09-13-phase-3-runtime-conversations.md`
   - Multi-channel `ChannelConnection` model and channel-to-Bot routing.
   - Conversation/message/decision/handoff model.
   - BotRuntime using external RAG.
   - Unified Inbox and human takeover.
   - Provider idempotency and delivery state.

4. `docs/superpowers/plans/2026-09-13-phase-4-draft-test-publish.md`
   - Draft editing and Setup Wizard.
   - Interactive Test Center and regression suites.
   - Live-vs-Draft comparison.
   - Readiness/publish gate, immutable publish, restore-as-draft.

5. `docs/superpowers/plans/2026-09-13-phase-5-operations-monitoring.md`
   - Health checks and incident engine.
   - Telegram alerts.
   - Command Center.
   - Operations/Quality analytics with conversation drill-down.

6. `docs/superpowers/plans/2026-09-13-phase-6-multi-user-hardening-cleanup.md`
   - Owner/Admin/Operator and Bot-scoped authorization.
   - Audit trail, retention/archive, final secret/redaction hardening.
   - Migrate remaining channel credentials to common credential service where needed.
   - Remove obsolete single-assistant/local-RAG legacy paths and dependencies after dependency/test proof.

## Shared File Structure Direction

The implementation should converge toward these focused modules while preserving existing patterns during migration:

```text
apps/api/chatbot_manager/
  admin/
    routes.py                  # legacy routes, shrinks phase-by-phase
    dependencies.py            # shared auth/template dependencies
    bots.py
    knowledge_services.py
    conversations.py
    command_center.py
    analytics.py
    incidents.py
    users.py
  bots/
    service.py
    versions.py
  knowledge/
    client.py
    service.py
  runtime/
    engine.py
    conversations.py
    handoff.py
    delivery.py
  operations/
    health.py
    incidents.py
    metrics.py
    alerts.py
  channels/
    line.py
    messenger.py
    telegram.py
  models.py
  db.py
  security.py
  settings.py
  webhooks.py
```

Do not create all modules up front. Each phase creates only the files it actively uses.

## Baseline Before Phase 1

- [ ] Run the existing full suite before implementation.

```bash
rtk uv run pytest -q
rtk uv lock --check
rtk python -m compileall -q apps/api
git diff --check
```

Expected baseline from the last verified release state: `184 passed`; if the count differs, record the new factual baseline before changing code.

- [ ] Confirm only intentional planning/doc changes and pre-existing untracked tool/config files are present.

```bash
git status --short
git log -3 --oneline
```

## Phase Gates

Every phase is blocked until all of the following are true:

- [ ] The phase-specific focused tests pass.
- [ ] Full `rtk uv run pytest -q` passes.
- [ ] `rtk python -m compileall -q apps/api` exits 0.
- [ ] `rtk uv lock --check` passes if dependencies changed or remains unchanged if they did not.
- [ ] `git diff --check` passes.
- [ ] Manual checkpoint listed in the phase plan is completed or explicitly deferred with reason.
- [ ] `HANDOFF.md` is updated with completed tasks, verification evidence, known limitations, and next phase.
- [ ] Only the phase's intended files are staged/committed; unrelated tool/config files remain untouched.

## Cross-Phase Interface Contracts

These names are fixed across the six plans unless a reviewed implementation issue requires an explicit plan amendment:

```python
# Phase 1
Bot
BotConfigVersion
BotConfigRule
ensure_default_bot(session) -> Bot
get_live_config(session, bot_id) -> BotConfigVersion
ensure_draft_config(session, bot_id, actor) -> BotConfigVersion

# Phase 2
Credential
KnowledgeService
KnowledgeQuery
KnowledgeAnswer
KnowledgeHealth
KnowledgeServiceClient
build_knowledge_client(session, knowledge_service_id) -> KnowledgeServiceClient

# Phase 3
ChannelConnection
Conversation
ConversationMessage
BotDecision
ConversationHandoffEvent
RuntimeRequest
RuntimeResult
BotRuntime.run(request: RuntimeRequest) -> RuntimeResult
ConversationService
HandoffService
DeliveryService

# Phase 4
BotTestCase
BotTestRun
BotTestResult
ReadinessResult
PublishService
RegressionService

# Phase 5
Incident
HealthSignal
IncidentService
MetricService
TelegramAlertService

# Phase 6
User
UserBotAccess
AuditEvent
CurrentUser
require_role(*allowed_roles: str)
require_bot_access(bot_id: int, current_user: CurrentUser, session: Session) -> Bot
```

## Migration Invariants

- The first migrated `Default Bot` owns the current global assistant/rules/channel/event data.
- Phase 1 copies current `AssistantSettings` + `Rule` data to the first immutable published `BotConfigVersion`/`BotConfigRule` set without switching production runtime yet.
- Phase 2 introduces external Knowledge Services without deleting old `KnowledgeDocument`/in-process RAG code.
- Phase 3 switches production runtime to BotConfig + external Knowledge Service and new conversations; old `ChatEvent` remains compatibility/history data.
- Phase 4 switches admin configuration edits from legacy Assistant/Rules pages to Draft configuration workflow.
- Phase 6 removes legacy tables/routes/services only after source search and tests prove they have no active consumer.

## Manual Checkpoints

```text
Phase 1: Bots list + Default Bot Workspace + current live provider behavior.
Phase 2: Authorized external RAG connection + Test Connection/Test Retrieval + Open RAG Manager.
Phase 3: Real or provider-authorized webhook smoke + Unified Inbox take/reply/return; Bot silent during human_active.
Phase 4: Edit Draft -> test -> regression -> publish -> restore old version as new Draft.
Phase 5: Simulated RAG/channel outage -> one deduplicated incident -> Telegram policy -> recovery -> drill-down.
Phase 6: Owner/Admin/Operator route/API checks + Bot scope + audit/archive + legacy removal verification.
```

## Execution Order

Do not parallelize phases that depend on data contracts from earlier phases. Within a phase, independent UI/test/documentation tasks may use subagents only after the phase's core interfaces are fixed.

```text
Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6
```

At the start of each phase, read both this roadmap and that phase's plan. At the end of each phase, update `HANDOFF.md` before moving to the next phase.
