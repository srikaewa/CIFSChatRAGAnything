# CIFS Chatbot Operations Console Redesign

Date: 2026-09-13
Status: Approved design, pending written-spec review

## Goal

Redesign CIFSChatbotManager from a mostly single-assistant admin panel into a bot-centric control plane and operations console for managing multiple production chatbots across LINE, Messenger, and Telegram.

The redesigned product must make daily administration simple: create and configure bots, bind them to external knowledge services, test changes safely before publication, monitor health and quality, inspect conversations, and take over conversations when human assistance is required.

CIFSChatbotManager must not become a document-management or RAG-indexing product. RAG-Anything/LightRAG remains the external knowledge system responsible for documents, parsing, indexing, graph storage, retrieval, and grounded answer generation.

## Design Principles

1. **Bot-centric control plane.** Every chatbot behavior, channel, conversation, test, metric, and permission must resolve to a Bot.
2. **External knowledge ownership.** CIFS binds to external knowledge services; it does not own document ingestion, indexing, graph management, or RAG storage.
3. **Safe configuration changes.** Active bots use immutable published configuration while administrators edit and test a separate draft.
4. **One operational surface.** Admins and operators should not need separate provider consoles for routine chatbot operation and human handoff.
5. **Action-first observability.** Health and analytics must lead to the bot, conversation, integration, or incident that needs attention.
6. **Deterministic control around probabilistic generation.** CIFS controls routing, rules, lifecycle, handoff, permissions, and audit; external RAG performs retrieval and grounded answer generation.
7. **Incremental migration.** Evolve the existing FastAPI/Jinja application without a big-bang rewrite.
8. **YAGNI.** Do not add enterprise IAM, distributed data infrastructure, workflow builders, or full BI capabilities before real usage requires them.

## Approved Product Decisions

- Hybrid multi-bot architecture.
- One `BotConfigVersion` binds to one Knowledge Service. Production uses the service selected by the Live version, while a Draft may bind to a different service for testing; many Bots may share the same service.
- Central Knowledge Service Registry.
- One external channel account belongs to one Bot.
- New Bot creation uses a guided Setup Wizard followed by a persistent Bot Workspace.
- Bot lifecycle: `draft -> ready -> active <-> paused`.
- Global landing page is an Operations Command Center.
- Unified Conversations Inbox with human handoff.
- Roles: Owner, Admin, Operator, with optional per-Bot access.
- Bot behavior changes use Draft -> Test -> Publish, immutable published versions, version history, and restore-as-draft rollback.
- Test Center includes interactive testing, saved regression cases, and Live-vs-Draft comparison.
- Analytics emphasizes operational and quality signals rather than generic BI.
- Alerts use in-app incidents plus Telegram notification in the first release.
- External RAG generates the final grounded answer. CIFS provides bot-specific behavior instructions and conversation history.

---

## 1. System Boundary

CIFSChatbotManager becomes the **Chatbot Control Plane + Operations Console**.

### CIFS owns

- Bot workspaces and lifecycle.
- Bot identity, tone, response style, guardrails, fallback, and escalation behavior.
- Deterministic rules.
- Channel configuration and routing.
- Conversation state.
- Human handoff and operator assignment.
- Draft/Test/Publish workflow and version history.
- Regression tests.
- Health, incidents, analytics, and audit.
- Users, roles, and Bot-scoped authorization.

### External RAG owns

- Documents and document lifecycle.
- Parsing/OCR/table/image/equation handling.
- Indexing.
- Vector/graph storage.
- Knowledge graph management.
- Retrieval.
- Grounded final-answer generation.
- RAG WebUI.

### Topology

```text
Messaging providers
        |
        v
CIFSChatbotManager
  - Command Center
  - Bots
  - Conversations
  - Test Center
  - Analytics
  - Incidents
  - Users/Roles
        |
        | authenticated service call
        v
RAG-Anything / LightRAG
  - Documents
  - Parsing
  - Indexing
  - Knowledge Graph
  - Retrieval
  - Final answer generation
```

CIFS does not expose document upload, reindex, document deletion, or knowledge-graph management as product responsibilities after migration. The Bot Knowledge page becomes a connection/status view with `Test Retrieval` and `Open RAG Manager` actions.

---

## 2. Information Architecture and Admin UX

### Global navigation

```text
Command Center
Bots
Conversations
Knowledge Services
Analytics
Incidents

Administration
Users
System Settings
```

Rules, prompts, channels, Test Chat, and Bot-level analytics must not remain separate global configuration pages. They belong inside a Bot Workspace.

### Command Center

The first page answers:

- Is the system healthy?
- Which Bot or dependency has a problem?
- What action should the administrator take?

It shows compact totals for Bots, channels, Knowledge Services, conversations, human queue, and incidents; a `Needs Attention` area; and a concise Bot fleet view. Detailed charts belong in Analytics.

Every warning or metric shown on the Command Center must link to an actionable destination such as a Bot Workspace, Knowledge Service, filtered conversation set, or incident detail.

### Bots page

Displays Bot cards/rows with:

- name and description;
- lifecycle state;
- connected channels;
- Knowledge Service health;
- current issues;
- basic current activity/quality summary;
- `Open Workspace`, `Continue Setup`, or similar primary action.

`Create Bot` launches the Setup Wizard.

### Setup Wizard

1. Bot Profile.
2. Knowledge Service selection.
3. Behavior: identity/system instructions, rules, fallback, escalation.
4. Channel connections.
5. Test, readiness checks, and activation.

### Bot Workspace

Persistent header shows Bot name, lifecycle state, Knowledge Service health, channel health, and Live/Draft state.

Tabs:

```text
Overview
Behavior
Knowledge
Channels
Test
Conversations
Analytics
Versions
Settings
```

#### Overview

Shows readiness, current live version, unpublished draft status, current health, and a small activity/quality summary.

#### Behavior

Edits the Draft configuration only. Includes:

- identity/system instructions;
- tone;
- primary language/auto-detect policy;
- response style and length;
- deterministic rules;
- fallback behavior;
- escalation behavior;
- custom instructions/guardrails.

Structured controls may compile into a bot-specific prompt rather than requiring every admin to maintain one long prompt manually.

#### Knowledge

Shows selected Knowledge Service, connectivity, latency, service type, and link to external RAG management. The Bot selects a service through its current configuration version; it does not manage documents.

#### Channels

Shows provider connections, provider account identity, runtime status, recent activity, and configure/test actions.

#### Test

Contains interactive Draft testing, decision trace, saved regression suite, and Live-vs-Draft comparison.

#### Conversations

Reuses the global Conversations UI scoped to the current Bot.

#### Analytics

Shows the current Bot's operational and quality metrics with drill-down into conversations.

#### Versions

Shows immutable published versions and current draft. Restoring an old version creates a new Draft; history is never overwritten.

### Legacy navigation mapping

```text
Dashboard             -> Command Center
Assistant Settings    -> Bot / Behavior
Rules                 -> Bot / Behavior
Channels              -> Bot / Channels
Test Chat             -> Bot / Test
Logs                  -> Conversations
Knowledge Upload      -> removed from CIFS
Reindex/Delete Doc    -> removed from CIFS
Knowledge Graph       -> external RAG WebUI
```

---

## 3. Domain and Data Model

### Bot

```text
Bot
- id
- name
- description
- lifecycle_status        # draft | ready | active | paused | archived
- live_config_version_id
- draft_config_version_id
- created_at
- updated_at
```

Knowledge binding is intentionally **not** stored directly on `Bot`; it is part of a configuration version so a Draft can test another Knowledge Service without changing production behavior.

Bot lifecycle and configuration-version lifecycle are distinct. An `active` Bot may simultaneously have a published live version and an unpublished draft version.

### BotConfigVersion

```text
BotConfigVersion
- id
- bot_id
- version_number
- status                  # draft | published | archived
- system_prompt / identity instructions
- tone/language/response-style settings
- fallback_policy
- escalation_policy
- knowledge_service_id
- created_by
- created_at
- published_at
```

Published versions are immutable. Editing a live Bot clones the current published version to a new Draft.

### BotConfigRule

```text
BotConfigRule
- id
- config_version_id
- name
- priority
- condition
- action
- enabled
```

Initial action vocabulary is deliberately small:

```text
RESPOND
ESCALATE
BLOCK
CONTINUE_TO_RAG
```

No general-purpose visual workflow engine is part of this design.

### KnowledgeService

```text
KnowledgeService
- id
- name
- service_type
- api_base_url
- webui_url
- credential_id
- enabled
- health_status
- last_health_check
- metadata
```

Many Bots/configurations may reference the same Knowledge Service.

### Credential

```text
Credential
- id
- credential_type
- encrypted_payload
- created_at
- rotated_at
- last_used_at
```

Credentials are separate from configuration versions so secrets are not copied into version history.

### ChannelConnection

```text
ChannelConnection
- id
- bot_id
- provider                 # line | messenger | telegram
- display_name
- external_account_id
- credential_id
- webhook_secret reference as appropriate
- status
- last_health_check
- metadata
```

Enforce uniqueness on provider account identity so one external account cannot accidentally belong to multiple Bots.

Channel credentials and webhook configuration are runtime infrastructure and are not versioned with Bot behavior.

### Conversation

```text
Conversation
- id
- bot_id
- channel_connection_id
- external_user_id
- status                   # bot_active | needs_human | human_active | closed
- assigned_operator_id
- handoff_reason
- started_at
- last_message_at
- closed_at
```

### ConversationMessage

```text
ConversationMessage
- id
- conversation_id
- sender_type              # user | bot | operator | system | internal_note
- content
- created_at
- external_message_id
- delivery_status
- metadata
```

Internal notes are not sent to the end user and are excluded from Bot prompt context by default.

### ConversationHandoffEvent

```text
ConversationHandoffEvent
- id
- conversation_id
- event_type               # escalated | assigned | taken | returned_to_bot | closed
- actor_user_id
- reason
- created_at
```

### BotDecision

```text
BotDecision
- id
- message_id
- bot_id
- config_version_id
- decision_type            # rule | rag | fallback | escalation | blocked
- rule_id
- knowledge_service_id
- reference_count
- retrieval_latency_ms
- llm_latency_ms           # when exposed by upstream
- total_latency_ms
- error_code
- metadata
```

Decision Trace is the shared foundation for Test Center, debugging, analytics, and incident investigation.

### Test entities

```text
BotTestCase
- id
- bot_id
- name
- input_message
- expected_behavior
- enabled
- tags

BotTestRun
- id
- bot_id
- config_version_id
- started_by
- started_at
- status

BotTestResult
- id
- test_run_id
- test_case_id
- actual_response
- decision_type
- outcome                  # pass | warning | fail
- evaluation_details
- latency_ms
```

### Users and Bot access

```text
User
- id
- email
- role                     # owner | admin | operator
- active

UserBotAccess
- user_id
- bot_id
```

Owner has global access. Admin has configuration access and may initially default to all Bots. Operator access is Bot-scoped.

### Incident

```text
Incident
- id
- severity                 # info | warning | critical
- source_type              # bot | channel | knowledge_service | inbox | system
- source_id
- incident_type
- deduplication_key
- status                   # open | acknowledged | resolved
- first_seen_at
- last_seen_at
- resolved_at
- details
```

---

## 4. Runtime Message Flow and Decision Engine

Every inbound provider message enters CIFS first. External RAG is never called directly by messaging providers.

```text
Provider webhook
      |
      v
Channel Adapter
      |
      v
Resolve Bot + Conversation
      |
      +-- human_active --> save + Unified Inbox; Bot does not answer
      |
      v
Load immutable Live Config
      |
      v
Deterministic Rules
      |
      +-- direct response / block / escalate
      |
      v
External RAG query
      |
      v
Deterministic response-policy checks
      |
      +-- reply
      +-- fallback
      +-- escalate
```

### Normalized inbound message

Provider adapters normalize provider-specific payloads into a common contract containing provider, channel, external user/message IDs, timestamp, text, and attachment metadata.

### Idempotency

`external_message_id` is the inbound idempotency boundary. Provider retries must not cause duplicate RAG calls or duplicate outbound responses.

### Human state checked first

If a conversation is `human_active`, incoming user messages are persisted and surfaced in the Unified Inbox. The Bot runtime must not call rules or RAG until an operator returns the conversation to Bot control.

This is a hard runtime rule, not a prompt convention.

### Live configuration snapshot

A production request resolves and holds one published `config_version_id` for the entire request. Publishing a new version does not change a request already executing.

Production uses the Live version. Test Center can explicitly run the same runtime against a Draft version with real delivery and real human assignment disabled.

### External RAG answer generation

CIFS owns bot behavior but does not run a second final-answer LLM. It sends to the Knowledge Service:

- current user query;
- bot-specific instructions (`user_prompt` or equivalent);
- bounded conversation history;
- request for references where supported;
- response format hints where supported.

Bot instructions combine identity, tone, language, response style, guardrails, fallback/escalation guidance, and custom instructions from the selected ConfigVersion.

LightRAG currently supports per-request `user_prompt`, conversation history, reference return, and query modes suitable for this contract. Upstream global instruction/prefix can remain for universal safety/grounding policy, while Bot personality stays in CIFS.

CIFS performs deterministic post-processing only: output sanitation, platform-length handling, source formatting, policy checks, and delivery. It must not send the generated answer through another LLM rewrite by default.

### Conversation history

CIFS owns conversation state and selects a bounded history window. User, Bot, and operator messages may be included when relevant. System events and internal notes are excluded by default.

Conversation history must remain isolated by Bot and Conversation even when multiple Bots share the same Knowledge Service.

### No fabricated confidence score

Do not show a percentage confidence unless the upstream service supplies a meaningful calibrated score. Initial quality logic uses observable signals such as success/failure, references present, empty/no-context response, latency, rule path, fallback path, and human escalation.

### Failure behavior

External RAG timeout/unavailability follows bounded retry policy, then the Bot's fallback/escalation policy. User-facing fallback and operational incident creation are separate concerns.

RAG failure must not make CIFS unavailable; the Inbox and human reply path remain operational.

### External RAG security baseline

Use an authenticated/private endpoint wherever deployment permits. Current LightRAG deployments used with conversation history must meet the patched security baseline (minimum LightRAG 1.5.5 unless a newer validated minimum supersedes it).

---

## 5. Unified Inbox and Human Handoff

### Conversation state machine

```text
bot_active
   | escalate
   v
needs_human
   | take/assign
   v
human_active
   | return_to_bot
   +-----------------> bot_active
   |
   +-- close --------> closed

bot_active / needs_human may also close.
```

A closed conversation is historical. A later inbound message starts a new conversation. An open `bot_active` conversation may be reused while it remains within the configured inactivity window; default initial policy is 24 hours.

### Escalation reasons

Initial reasons include:

- explicit human request;
- deterministic rule escalation;
- repeated fallback;
- Knowledge Service unavailable per Bot policy;
- manual/admin escalation.

### Queue and assignment

Initial workflow is manual `Take` plus Admin direct assignment. No sophisticated routing/skills engine is required.

Assignment uses an atomic conditional update so two operators cannot take the same conversation simultaneously.

### Human takeover

When status becomes `human_active`:

- Bot generation stops immediately for that conversation;
- inbound messages continue to persist and appear in the Inbox;
- operator responses travel through the original Channel Adapter;
- only one assigned operator owns the conversation at a time.

### Return to Bot

Returning control to the Bot does not replay user messages from the human period. The **next** user message runs through Bot runtime normally.

Recent operator messages may be included in subsequent Bot conversation history so the Bot understands what happened during takeover.

### Internal notes

Operator/Admin notes are visible internally, not delivered to the user, and excluded from Bot history by default.

### Delivery state

Operator outbound messages display sending/delivered/failed state. A failed provider delivery must not be represented as successful and should offer retry where safe.

### Attachments

Initial scope:

- receive and display provider attachment metadata/media where current provider capability permits;
- operator replies are text-first.

Operator-originated file upload/media sending is deferred to a later phase unless a provider-specific requirement makes it necessary.

### Inbox SLA

Initial configurable thresholds:

- warning when a human-waiting conversation exceeds a configured duration (default proposal: 10 minutes);
- critical escalation for severe/persistent backlog (default proposal: 30 minutes or queue threshold).

Thresholds feed the Incident Engine rather than directly spamming notifications.

### Conversation Inspector

Admins/operators with access can inspect Bot, channel, config version, handoff reason, Decision Trace, Knowledge Service, latency, and references for a conversation/message without reading raw server logs.

---

## 6. Test Center, Publish Gate, and Rollback

### Live/Draft isolation

Active Bot:

```text
LIVE  v12  -> production traffic
DRAFT v13  -> Test Center only
```

Saving a Draft never changes production behavior.

### Interactive Test Chat

Runs the same BotRuntime and decision logic as production, with an explicit Draft config and a test context that disables real provider delivery and real human assignments.

Displays full Decision Trace.

### Regression test cases

Tests assert behavior rather than exact generated text where possible. Assertions can include:

- expected decision type;
- RAG must/must not be called;
- must return references;
- must/must not escalate;
- must/must not fallback;
- required concept/phrase class;
- latency warning threshold.

Outcomes:

```text
PASS
WARNING
FAIL
```

### Live-vs-Draft comparison

Run saved cases against both versions and show responses plus decision traces side by side. A semantic judge is not required initially.

### Publish gate

Before publish:

1. configuration completeness;
2. Knowledge Service health;
3. relevant channel readiness;
4. regression suite status.

Policy:

- FAIL blocks publication;
- WARNING allows publication only after explicit acknowledgement;
- PASS publishes normally.

A failing test is not bypassed through a hidden override. If an Admin intentionally considers a test obsolete, the test must be disabled/changed as an audited action.

### Atomic publish

Publishing validates again, creates/finalizes the immutable version snapshot, and atomically switches `live_config_version_id`.

### Restore / rollback

Restoring v11 while v13 is live creates a new Draft v14 copied from v11. It then follows normal Test -> Publish workflow. Historical versions are never mutated or silently reactivated.

Emergency operational response is `Pause Bot`, which is intentionally separate from version rollback.

### Production issue to regression case

Conversation/Decision Inspector offers `Add to Test Suite`, creating a Draft test case from a real problematic user message. This creates a continuous improvement loop:

```text
Production problem -> Regression case -> Draft fix -> Test -> Publish -> Monitor
```

### Optional AI-assisted evaluation

A future evaluator interface may add LLM-based quality review as a warning/review aid. It is not an authoritative release gate in the initial design.

---

## 7. Command Center, Analytics, Health, and Incidents

### Operational signals

Health is broader than HTTP uptime.

1. **Runtime health:** API, database, scheduler/worker.
2. **Integration health:** channels and Knowledge Services.
3. **Behavioral health:** fallback rate, escalation rate, RAG failures, latency, Inbox backlog.

### Bot overall health

Derived deterministically from dependencies:

- critical dependency down -> Critical;
- otherwise warning exists -> Warning/Degraded;
- no known problems -> Healthy.

Do not claim `Healthy` when evidence only proves configuration is present or recent activity exists. Provider capability determines the wording shown.

### Analytics views

**Operations:** conversations, messages, active users, response latency, channel mix, RAG latency, delivery errors, human queue, operator handling time.

**Quality:** Bot resolution rate, fallback rate, escalation rate, RAG failure rate, regression trend, common unanswered topics, escalation reasons.

Every meaningful metric should drill down to filtered conversations or affected incidents.

### Detection strategy

Start with explicit thresholds and simple rolling baselines. Do not introduce ML anomaly detection initially.

### Incident Engine

Health signals produce deduplicated incidents rather than direct notifications.

Deduplication key is based on incident type and root source, e.g. `rag_unreachable:knowledge_service_3`.

A shared Knowledge Service outage produces one root incident listing affected Bots rather than one duplicate incident per Bot.

Lifecycle:

```text
open -> acknowledged -> resolved
```

Recovery may automatically resolve an open/acknowledged incident. `Acknowledged` means seen, not fixed.

### Notification policy

- Critical: Dashboard + Telegram immediately.
- Warning: Dashboard; Telegram only when persistent per policy/cooldown.
- Info: Dashboard/activity history only by default.

Recovery messages may be sent for externally notified incidents.

### Data infrastructure

Initial implementation uses the relational operational database plus indexed queries and scheduled aggregates where needed. Do not add Kafka, ClickHouse, Elasticsearch, or a separate warehouse until measured scale requires it.

---

## 8. Security, Authorization, Audit, and Deployment Boundaries

### Roles

```text
Capability                       Owner   Admin   Operator
Manage users                       yes      no       no
Manage Knowledge Services           yes     yes       no
Create/edit Bots                    yes     yes       no
Publish Bots                        yes     yes       no
Manage channels                     yes     yes       no
Pause Bots                          yes     yes       no
View analytics                      yes     yes    scoped
Use Unified Inbox                   yes     yes    scoped
Take/reply conversations            yes     yes    scoped
View audit log                      yes     yes       no
System settings                     yes      no       no
```

Authorization is enforced server-side. UI hiding is not a security boundary.

### Secrets

Credentials such as provider tokens, channel secrets, RAG API keys, and webhook signing secrets are stored encrypted at rest through the `Credential` abstraction. The application master key remains outside the database in deployment-managed secret/environment configuration.

Existing secrets are masked in UI and are **replace-only**; the UI does not reveal their previous plaintext value.

Secrets must be redacted from logs, traces, errors, audit snapshots, and HTTP-client diagnostic output.

Credential encryption is required as soon as the new Knowledge Service Registry is introduced; it is not deferred until the final hardening phase.

### Webhook security

Provider webhook handling must:

- verify provider signature/authenticity before processing;
- resolve the registered ChannelConnection;
- enforce inbound idempotency;
- reject invalid requests before creating conversation state or calling RAG.

### Runtime isolation

Every runtime resolution carries Bot, Conversation, ConfigVersion, and KnowledgeService identity. Service/database access checks Bot scope, particularly for Operator access.

### Audit trail

Administrative actions such as publish, pause, credential replacement, Knowledge Service endpoint change, user management, operator assignment, and archive actions produce audit events with actor, target, timestamp, sanitized before/after information, and request metadata where useful.

Audit history is distinct from conversation history. Do not duplicate full user message content into audit logs unnecessarily.

### Data minimization and retention

The design supports configurable retention for conversations/messages, metrics, incidents, and audit data. Do not duplicate full conversation payloads into metrics tables.

### Destructive actions

Prefer pause/archive/disable before hard deletion. A Knowledge Service referenced by Bot configurations cannot be removed without resolving dependencies. Production Bot history/configuration should not be destroyed through one-click deletion.

### Background operations

Health checks, incident evaluation, alert delivery, and metric aggregation belong behind explicit service boundaries and may run in a lightweight background scheduler/worker. No distributed job platform is required initially.

### Failure isolation

- RAG outage does not take down CIFS Admin or human reply functions.
- Telegram alert outage does not break Bot runtime.
- Analytics failure does not block webhook response flow.

### Initial exclusions

- custom RBAC designer;
- SSO/SAML;
- organization/multi-tenant model;
- per-field permissions;
- enterprise SIEM integration;
- complex approval chains;
- multi-region deployment.

---

## 9. Migration and Implementation Decomposition

Implementation follows **expand -> migrate -> switch -> verify -> contract**. Existing data and behavior are preserved until replacements are verified.

### Phase 1 — Bot Foundation and Current-Data Migration

Introduce:

- Bot;
- BotConfigVersion;
- BotConfigRule;
- Bot ownership on ChannelConnection/current channel model;
- minimal Bot-centric routes/templates.

Create a `Default Bot` and migrate current AssistantSettings, Rules, Channels, and ChatEvents into Bot context. Current production behavior must remain equivalent after migration.

The first migrated configuration becomes an immutable published version. Draft/Test UI is completed later, but schema boundaries are created now.

Manual checkpoint: Bot list + Default Bot Workspace + existing channel behavior.

### Phase 2 — External Knowledge Service Boundary and Secret Storage

Introduce:

- KnowledgeService;
- Credential abstraction with encryption-at-rest;
- KnowledgeServiceClient interface;
- LightRAG/RAG-Anything client implementation.

Replace in-process product flow that performs indexing/deletion/graph storage access with a thin external-service contract such as:

```text
health()
query()
test_connection()
capabilities()
```

Add Knowledge Services registry and Bot Knowledge binding UI.

Retire local Upload/Reindex/Delete/Knowledge Graph management from navigation/product flow. Keep legacy implementation code temporarily until migration verification proves it has no remaining consumer.

Manual checkpoint: connect actual authorized external RAG service, run Test Connection/Test Retrieval, and open its external WebUI.

### Phase 3 — Runtime Engine, Conversation Model, and Unified Inbox

Introduce focused application services, for example:

```text
ChannelAdapter
ConversationService
BotRuntime
RuleEngine
KnowledgeGateway
HandoffService
DeliveryService
```

Introduce Conversation, ConversationMessage, BotDecision, and ConversationHandoffEvent. Route all channel inbound/outbound flows through Bot runtime and implement Unified Inbox/Human Takeover.

Legacy flat ChatEvent views become compatibility/history sources rather than the core runtime model.

Manual checkpoint: real/provider-authorized channel smoke where available; take/reply/return workflow; verify Bot cannot answer while human_active.

### Phase 4 — Draft/Test/Publish and Regression

Add draft lifecycle and Test Center UI/services plus BotTestCase, BotTestRun, and BotTestResult.

Test Center reuses production runtime with Draft config and side effects disabled. Add publish gate, immutable versions, Live-vs-Draft compare, restore-as-draft, and production-conversation-to-test-case workflow.

Manual checkpoint: edit Draft, run tests, compare to Live, publish, and restore an old version as a new Draft.

### Phase 5 — Operations Layer

Introduce structured health checks, incidents, Telegram alert service, Command Center, and Operations/Quality analytics. Add scheduled aggregates only where direct indexed queries are insufficient.

Manual checkpoint: simulate channel/RAG failures, confirm deduplicated incident and recovery, verify Telegram alert policy, and drill quality metrics to conversations.

### Phase 6 — Multi-user Expansion, Authorization Hardening, and Legacy Cleanup

Expand the current authentication model to Owner/Admin/Operator plus UserBotAccess. Add full server-side Bot-scope authorization tests, audit UI/history, retention/archive workflows, session hardening where still needed, and complete secret/log-redaction review.

Then remove obsolete legacy routes/templates/models/services only after tests and dependency search prove they have no remaining consumer.

Manual checkpoint: verify each role and Bot scope, unauthorized direct-route access, audit events, archive flows, and legacy feature removal.

### Route refactor

Do not perform an unrelated rewrite of `admin/routes.py`. Split responsibilities incrementally as each feature is migrated, for example:

```text
admin/
- command_center.py
- bots.py
- knowledge_services.py
- conversations.py
- analytics.py
- incidents.py
- users.py
```

Business logic belongs in application services, not route handlers.

---

## 10. Testing Strategy

Behavior changes use TDD.

### Unit tests

- config/version lifecycle;
- Bot lifecycle/readiness;
- deterministic rules;
- conversation state machine;
- handoff/assignment concurrency;
- authorization policy;
- incident deduplication;
- prompt/instruction builder;
- Knowledge Service client mapping and failure handling.

### Integration tests

- database migrations and current-data migration;
- runtime + fake KnowledgeServiceClient;
- webhook verification/idempotency;
- Draft vs Live routing;
- publish atomicity;
- Inbox reply through mocked providers;
- credential encryption/redaction boundaries;
- metrics/incident drill-down contracts.

### End-to-end/browser tests

- Setup Wizard -> Bot Workspace;
- Knowledge Service connection test;
- channel configuration/status;
- real Test Center workflow;
- publish/restore workflow;
- Unified Inbox take/reply/return;
- Command Center -> incident/conversation drill-down;
- role-scoped navigation and direct-route enforcement.

Automated CI uses fake/mocked provider, LLM, and RAG services. Optional authorized deployment smoke tests may use real external services and must never expose credentials in logs/artifacts.

Each implementation phase ends with focused tests, full regression suite, syntax/compile checks, `git diff --check`, and a manual checkpoint before the next phase.

---

## 11. Error Handling and Operational Contracts

- External calls use explicit connect/read timeouts and bounded retries.
- Provider/RAG failures map to stable internal error codes; raw exception/secret material is not exposed to browser users.
- Inbound provider retries are idempotent.
- Outbound delivery records success/failure accurately.
- Background health failures create/update incidents but do not crash request handling.
- Publish switch is atomic.
- Operator assignment is atomic.
- Invalid state transitions are rejected in the service layer.
- Shared dependency failures create root incidents with affected-Bot information rather than duplicate incidents.

---

## 12. Explicit Non-Goals

This redesign does not initially provide:

- internal document upload/indexing/knowledge-graph management;
- one Bot federating multiple Knowledge Services;
- one provider account routing to multiple Bots;
- generic visual automation/workflow builder;
- full ticketing/CRM system;
- operator-originated media upload across all providers;
- exact-text regression matching as the primary evaluation method;
- uncalibrated RAG confidence percentages;
- full BI/report designer;
- ML anomaly detection;
- distributed event/data infrastructure;
- custom enterprise RBAC/SSO/multi-tenancy.

These may be revisited through separate designs when concrete requirements emerge.

---

## 13. Completion Criteria for the Redesign

The redesign is complete when:

1. Existing data is migrated into a Bot-centric model without losing current channel/chatbot behavior.
2. Admin can create/manage multiple Bots and each external channel account has an unambiguous Bot owner.
3. Bots use registered external Knowledge Services; CIFS no longer owns document/RAG lifecycle in normal product flows.
4. External RAG generates grounded answers using Bot-specific instructions supplied by CIFS.
5. Production traffic always uses an immutable Live config while Draft changes can be safely tested.
6. Regression failures block publication and published versions remain auditable/restorable.
7. Unified Inbox supports human takeover without Bot/operator race replies.
8. Command Center identifies meaningful operational/quality problems and links to actionable detail.
9. Incidents are deduplicated, dependency-aware, and can notify Telegram according to severity/persistence policy.
10. Owner/Admin/Operator authorization is enforced server-side with Bot scope.
11. Secrets are encrypted at rest, masked in UI, and redacted from logs/audit/error paths.
12. Automated and manual verification covers the supported critical workflows before legacy code is removed.

## References / External Contract Notes

- RAG-Anything: https://github.com/HKUDS/RAG-Anything
- LightRAG API Server/WebUI: https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md
- LightRAG query API implementation/parameters should be re-verified against the pinned deployment version during Phase 2 rather than hard-coding assumptions from upstream `main`.
- LightRAG security advisories and minimum safe version must be re-checked during implementation/deployment; the design baseline is `>=1.5.5` for the 2026 conversation-history cache fix.
