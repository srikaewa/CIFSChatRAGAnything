# CIFS Chatbot Manager

CIFS Chatbot Manager is a multi-bot operations console for LINE, Facebook Messenger, and Telegram. CIFS owns Bot configuration, channel credentials, runtime decisions, conversations and human handoff, testing/publishing, analytics, incidents, users, audit, archive, and retention. External LightRAG-compatible Knowledge Services own document ingestion, indexing, knowledge graphs, retrieval storage, and their own WebUI/backup lifecycle.

## Quick start

```bash
uv sync
uv run uvicorn chatbot_manager.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Copy `.env.example` to `.env` before non-local use. Production startup rejects the shipped development session/password/encryption defaults and requires secure cookies.

Default local login:

- Email: `admin@example.local`
- Password: `admin1234!`

## Final product surface

- **Command Center** (`/`): action-first operational health and incident summary.
- **Bots** (`/bots`): Bot workspaces with Overview, Setup, Behavior, Knowledge, Channels, Test, Analytics, Conversations, and Versions.
- **Conversations** (`/conversations`): unified inbox, human handoff, operator replies, notes, close/return-to-bot actions, and decision traces.
- **Knowledge Services** (`/knowledge-services`): register external LightRAG-compatible services, encrypted API credentials, connection checks, retrieval checks, and links to the external RAG manager.
- **Analytics** (`/analytics`): operational and quality metrics with drill-down links.
- **Incidents** (`/incidents`): deduplicated operational incidents and acknowledgement/history.
- **Audit** (`/audit`): sanitized administrative audit history for Owner/Admin.
- **Users** (`/users`): Owner-only user, role, password, and Operator Bot-assignment administration.

Knowledge documents are **not uploaded, indexed, reindexed, deleted, or graphed inside CIFS**. Perform those operations in the external RAG WebUI.

## Roles

| Role | Access |
|---|---|
| Owner | All Bots/conversations/Knowledge/Analytics/Incidents/Audit plus Users and system administration |
| Admin | Bot create/edit/test/publish, conversations, Knowledge Services, Analytics, Incidents, and Audit; Users is denied |
| Operator | Assigned Bots and their conversations/Inbox/replies/analytics only; configuration and publish actions are denied |

Authorization is enforced server-side. Hiding navigation is not the security boundary.

## Bot lifecycle and publishing

Bot configuration uses immutable versions. Draft edits do not affect Live until the publish gate succeeds. The Test workspace runs the Draft through the same BotRuntime contract used by production, supports saved regression cases, and compares Live vs Draft.

Typical flow:

1. Configure Behavior/Rules, Knowledge binding, and Channels.
2. Run interactive/regression tests.
3. Publish the Draft.
4. Provider messages use the new Live version.
5. Pause a Bot before archiving it. Archive disables its channel connections but preserves configuration, conversations, tests, and audit history.

## Channels and webhooks

Configure credentials per Bot under **Bots → Channels**. New deployments should register the Bot-specific keyed webhook path with the provider:

| Provider | Method | Path |
|---|---|---|
| LINE | `POST` | `/webhooks/line/{webhook_key}` |
| Messenger | `GET` | `/webhooks/messenger/{webhook_key}` |
| Messenger | `POST` | `/webhooks/messenger/{webhook_key}` |
| Telegram | `POST` | `/webhooks/telegram/{webhook_key}` |

CIFS stores channel secrets in the encrypted common Credential model. Public HTTPS routing, reverse proxy/Tailscale Funnel configuration, and provider-console webhook registration are deployment responsibilities; CIFS does not provision the tunnel.

Unkeyed provider webhook aliases remain only for legacy migration compatibility and should not be used for new multi-bot deployments.

## External Knowledge Services

Register an external service from **Knowledge Services**, then bind it in the Bot Knowledge tab. CIFS stores the service URL, optional WebUI URL, encrypted API credential, health state, and Bot binding. At runtime, CIFS sends the Bot instruction, conversation history, and question to the registered service and records only safe decision/reference/latency metadata.

Document lifecycle, parser/model configuration, graph management, external RAG upgrades, and external RAG backups are owned by the external RAG deployment.

## Operations, archive, and retention

The background operations scheduler checks database/runtime health, external Knowledge Services, configured channels, human-wait SLA, and RAG retrieval latency. It creates deduplicated incidents; critical alerts are eligible immediately, warnings after the persistence threshold, and recovery can resolve prior incidents.

Default policy values:

- Operations poll: 60 seconds
- Warning persistence: 15 minutes
- Human-wait warning / critical: 10 / 30 minutes
- RAG latency warning / critical: 3000 / 8000 ms
- Closed conversation retention: 365 days
- Resolved incident retention: 730 days
- Audit retention: 1095 days

Retention purges are explicit maintenance actions, not an automatic destructive schedule.

## Backup responsibility

CIFS backup:

- SQLite database configured by `DATABASE_URL`
- secure `.env` / deployment secrets through your secrets-backup process
- application deployment configuration needed to recreate public routing

External RAG backup:

- document sources
- index/vector/graph data
- external RAG service configuration and model/provider settings

Back up and restore these systems independently but record which versions were paired for production validation.

## Verification

```bash
uv run pytest -q
uv run python -m compileall -q apps/api
uv lock --check
git diff --check
```

For deployment, also perform real provider webhook/reply smoke tests and a real external-RAG retrieval check using the exact provider/RAG versions deployed.

See `docs/operations.md` for the operating checklist, recovery boundaries, retention details, and final verification procedure.
