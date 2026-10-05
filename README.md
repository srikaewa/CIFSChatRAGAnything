# Chatbot Manager

Bot-centric operations console for LINE, Facebook Messenger, and Telegram chatbots. CIFS manages bot behavior, channels, operations, and external Knowledge Service bindings; RAG-Anything/LightRAG owns documents, indexing, knowledge graphs, retrieval, and grounded answer generation.

## Quick start

```bash
uv sync
uv run uvicorn chatbot_manager.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Copy `.env.example` to `.env` before configuring real providers. Local development defaults are for local-only use; production/non-local startup requires secure credentials.

Default local login:

- Email: `admin@example.local`
- Password: `admin1234!`

## Message order

1. Built-in commands such as `help` and `start`.
2. Enabled admin rules by priority.
3. Escalation decision when configured.
4. RAG-Anything retrieval when enabled and available.
5. Fallback reply.

## Admin pages

- Dashboard: command-center view for current operational status.
- Bots: open a Bot Workspace for Overview, Behavior, Knowledge binding, Channels, Test Center, Conversations, and Versions. Draft edits stay separate from Live until publish.
- Conversations: unified inbox, human handoff, operator replies, decision traces, and Add to Test Suite.
- Knowledge Services: register external RAG-Anything/LightRAG endpoints, test connectivity/retrieval, and manage encrypted service credentials.
- Test Center: run the Draft through the production BotRuntime in test mode, save regression cases, compare Live vs Draft, and publish only after readiness/regression gates pass.
- Versions: inspect immutable published versions and restore an older version as a new Draft without switching Live immediately.
- Knowledge files, indexing, reindexing, deletion, and knowledge-graph management are performed in the external RAG WebUI; CIFS stores only service bindings and credentials.
- Legacy `/assistant`, `/rules`, `/channels`, `/test-chat`, and `/logs` routes remain directly reachable for migration/debugging but are no longer primary navigation.

## Environment

Important settings are documented in `.env.example`. Real channels additionally require their provider credentials. Telegram webhook secret is configured through the Channels page together with the bot token. RAG answers require compatible LLM settings and the optional RAG/parser dependency stack.

For Windows short-path setup, RAG dependencies, Tailscale Funnel, backups, restore, and troubleshooting, see `docs/operations.md`.

## Webhooks and tunnels

| Channel | Method | Path | Purpose |
|---|---|---|---|
| LINE | `POST` | `/webhooks/line` | Events |
| Messenger | `GET` | `/webhooks/messenger` | Verification |
| Messenger | `POST` | `/webhooks/messenger` | Events |
| Telegram | `POST` | `/webhooks/telegram` | Events |

The application builds provider webhook URLs from `API_PUBLIC_URL`. External provider consoles therefore need a public HTTPS route to the local application.

Telegram can optionally use Tailscale Funnel from the Channels page. The application invokes the local `tailscale` CLI directly and does not assume `sudo`; the local machine must already be authenticated and authorized to use Funnel.

## Tests

Use an explicit writable temp directory on Windows:

```powershell
$root = (Get-Location).Path
python -m pytest -q --basetemp "$env:TEMP\cifs-pytest"
python -m compileall -q apps/api
git diff --check
```

See `docs/operations.md` for the short-path environment used when the optional full RAG stack hits Windows path-length limits.
