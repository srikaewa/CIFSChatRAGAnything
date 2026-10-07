# CIFS Chatbot Manager Operations Guide

## 1. Architecture and ownership boundary

CIFS is the orchestration and operations system. It owns:

- Command Center and operational scheduler
- Bots, Draft/Live versions, Behavior/Rules, Test Center, publish/restore
- Bot-scoped channel connections and encrypted credentials
- Conversations, human handoff, operator replies, notes, and decision history
- external Knowledge Service registration/binding and retrieval health
- Analytics, Incidents, Users, Audit, archive, and retention

The external LightRAG-compatible service owns:

- source documents and ingestion
- parsing/indexing
- vector/index/knowledge-graph storage
- retrieval implementation and grounded-answer service
- external RAG WebUI
- its own model/provider configuration, upgrade, backup, and restore

Do not upload, reindex, delete, or graph knowledge inside CIFS. Those controls were removed from the final product.

## 2. Roles and access checks

- **Owner:** all Bots and conversations, Knowledge Services, Analytics, Incidents, Audit, and Owner-only Users/system administration.
- **Admin:** Bot create/edit/test/publish, all conversations, Knowledge Services, Analytics, Incidents, and Audit. Users is denied.
- **Operator:** only assigned Bots, their conversations/Inbox actions, replies, and assigned-Bot Analytics. Direct URLs for unassigned Bots/conversations return 403. Bot configuration and publish actions are denied.

Role and Bot-scope authorization is enforced server-side on direct requests.

## 3. Bot setup and publish workflow

For each Bot:

1. Open **Setup** and confirm readiness.
2. Configure **Behavior** and deterministic **Rules**.
3. Bind a registered external service under **Knowledge** when retrieval is required.
4. Configure provider credentials under **Channels**.
5. Use **Test** for interactive Draft testing and saved regression cases.
6. Compare Live vs Draft and satisfy the publish gate.
7. Publish. New messages use the new Live version.
8. Use **Versions** to inspect immutable published versions or restore an older version into a new Draft.

A paused Bot may be archived. Archive changes lifecycle to `archived`, disables its ChannelConnections, and preserves configuration/version history, conversations, tests, and audit records.

## 4. Provider webhooks

New deployments should register the Bot-specific keyed URL shown by the deployment/configuration process:

- LINE: `POST /webhooks/line/{webhook_key}`
- Messenger verify: `GET /webhooks/messenger/{webhook_key}`
- Messenger events: `POST /webhooks/messenger/{webhook_key}`
- Telegram: `POST /webhooks/telegram/{webhook_key}`

Provider credentials are stored through the common encrypted Credential model.

CIFS does **not** create a public tunnel or call provider setup commands. Configure HTTPS routing/reverse proxy/Tailscale Funnel outside CIFS, then register the resulting public keyed URL in the provider console. Ensure the reverse proxy preserves provider signature/secret headers.

Unkeyed `/webhooks/{provider}` aliases exist only to migrate a single legacy default connection. Do not use them for new multi-bot deployments.

## 5. External Knowledge Services

From **Knowledge Services**:

1. Add the external API base URL.
2. Add the optional RAG manager/WebUI URL.
3. Store the API key if the service requires one.
4. Run **Test Connection**.
5. Run a harmless **Test Retrieval**.
6. Bind the service in the Bot **Knowledge** tab.

CIFS treats external retrieval failures as stable safe error codes. Raw external exception text and API credentials must not be persisted or rendered.

For ingestion/index/graph problems, open the external RAG manager and use that system's runbook. CIFS intentionally has no local upload/reindex/graph recovery procedure.

## 6. Conversations and human handoff

Normal runtime:

```text
provider message
  -> ChannelConnection
  -> Conversation + inbound ConversationMessage
  -> Bot Runtime (command/rule/external RAG/fallback/escalation)
  -> BotDecision + outbound ConversationMessage
  -> provider reply
```

Escalation/human flow:

```text
bot_active
  -> needs_human
  -> Operator Take/Assign
  -> human_active (Bot remains silent)
  -> human reply
  -> Return to Bot
  -> bot_active
```

The second concurrent Take loses the atomic claim. Operators can act only on conversations belonging to assigned Bots.

## 7. Command Center, incidents, and alerts

The scheduler runs at `OPS_POLL_SECONDS` (default 60) and evaluates:

- database/system health
- external Knowledge Service health
- channel readiness/evidence
- human-wait SLA
- RAG retrieval latency

Incidents are deduplicated by source/type. Lifecycle is `open -> acknowledged -> resolved`.

Default thresholds:

- warning persistence: 15 minutes
- human wait: warning 10 minutes, critical 30 minutes
- RAG latency: warning 3000 ms, critical 8000 ms

Operations Telegram alerts use only `ALERT_TELEGRAM_BOT_TOKEN` and `ALERT_TELEGRAM_CHAT_ID`. Critical incidents are eligible immediately; warnings wait for persistence; information-level events remain dashboard/history unless policy changes. Alert failure does not break runtime or incident persistence.

## 8. Archive and retention

Defaults:

- closed conversations: 365 days
- resolved incidents: 730 days
- audit: 1095 days

Rules:

- retention days must be positive
- conversation cleanup deletes only closed conversations older than the cutoff and removes dependent messages/decisions/handoff events first
- incident cleanup deletes only resolved incidents older than the cutoff
- audit cleanup deletes only old audit rows, then records a fresh count-only maintenance audit
- Knowledge Services referenced by any BotConfigVersion history cannot be hard-removed
- purges are explicit maintenance operations; there is no automatic destructive retention scheduler

## 9. Backup and restore

### CIFS backup

Stop writes or stop the application for a consistent SQLite copy. Back up:

- SQLite file configured by `DATABASE_URL`
- secure deployment secrets (`.env` or secret-store export) separately
- reverse-proxy/public-routing configuration
- application/version metadata needed to reproduce the deployed CIFS revision

CIFS does not require a local knowledge upload directory or local RAG working directory.

### External RAG backup

Use the external RAG product's documented backup procedure for:

- source documents
- indexes/vector stores
- graph data
- external service configuration
- model/provider settings and credentials

Record the external RAG product/version together with the CIFS Git revision used in production validation.

### Restore order

1. Restore CIFS database and secure deployment configuration.
2. Restore/start the external RAG service independently.
3. Start CIFS.
4. Verify Owner login and Command Center.
5. Test external Knowledge Service connection and harmless retrieval.
6. Verify a Bot's channel credentials/readiness.
7. Run a provider inbound/reply smoke test.
8. Run handoff/return-to-bot and publish/regression smoke tests.
9. Confirm incident creation/recovery with a controlled dependency failure.

## 10. Common recovery boundaries

- **Provider signature/secret failure:** verify the Bot's Channel credential and provider-console webhook URL; secrets remain masked.
- **Channel not ready:** correct the Bot-scoped ChannelConnection credential/state; do not add global environment provider credentials.
- **Knowledge Service unavailable:** verify external service/network/API credential, then use Test Connection/Retrieval. Do not reindex locally in CIFS.
- **Human-wait incident:** inspect Unified Inbox, assign/take the conversation, and address queue capacity.
- **RAG-latency incident:** inspect the external RAG service and network path; CIFS records safe latency/error metadata.
- **Alert delivery failure:** incident persistence remains authoritative; correct operations Telegram credentials/connectivity separately.
- **Archived Bot needs traffic again:** archive is intentionally terminal in the current product; create/restore the desired operational Bot configuration rather than mutating archived history.

## 11. Final verification commands

```bash
uv run pytest -q \
  tests/test_users.py \
  tests/test_authorization.py \
  tests/test_user_admin.py \
  tests/test_audit.py \
  tests/test_retention.py \
  tests/test_redaction_boundaries.py \
  tests/test_legacy_cleanup.py

uv run pytest -q
uv run python -m compileall -q apps/api
uv lock --check
git diff --check
```

Also run the dependency/secret scans from the Phase 6 verification plan and classify intentional history/migration model matches rather than deleting data blindly.

## 12. Production smoke checklist

Automated tests use isolated SQLite and mocked provider/external-RAG HTTP boundaries. Before production cutover, validate with the actual deployed versions:

- Owner/Admin/Operator route permissions
- one real provider inbound/reply per enabled provider
- one real external-RAG retrieval with references
- escalation -> Operator Take -> human reply -> Return to Bot
- Draft regression -> publish -> new message on new Live version
- controlled external dependency failure -> one incident/alert -> recovery
- backup/restore rehearsal for CIFS and external RAG

Record the CIFS Git revision, external RAG product/version, provider application/bot identifiers (not secrets), and smoke-test date in the operational handoff.
