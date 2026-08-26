# Chatbot Manager

Local admin manager for LINE, Facebook Messenger, and Telegram chatbots with rule-first replies and RAG-Anything fallback.

## Run

```bash
rtk uv sync
rtk uv run uvicorn chatbot_manager.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

Default login:

- Email: `admin@example.local`
- Password: `admin1234!`

## Message Order

1. Built-in commands: `help`, `start`
2. Admin rules by priority
3. RAG-Anything hybrid retrieval
4. Fallback reply

## Admin Pages

- Dashboard: channel and RAG status.
- Channels: webhook URLs for LINE, Messenger, and Telegram.
- Rules: keyword rules and replies.
- Knowledge: upload documents for RAG-Anything indexing.
- Graph: view RAG-Anything/LightRAG entities and relationships from the indexed knowledge base.
- Assistant: system prompt, fallback reply, LLM base URL, API key, chat model, vision model, and embedding model.
- Test Chat: simulate a user message before going live.
- Logs: recent messages, answer source, and replies.

## Environment

Copy `.env.example` to `.env` and fill channel and LLM values.

Required for real channels:

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `MESSENGER_VERIFY_TOKEN`
- `MESSENGER_PAGE_ACCESS_TOKEN`
- `MESSENGER_APP_SECRET`
- `TELEGRAM_BOT_TOKEN`

Required for RAG answers:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_DEFAULT_MODEL`
- `LLM_VISION_MODEL`
- `RAG_WORKING_DIR`
- MinerU/RAG-Anything parser dependencies installed on host

## Webhooks & Tunnels

### Endpoints

| Channel | Method | Path |
|---|---|---|
| LINE | `POST` | `/webhooks/line` |
| Messenger | `GET` | `/webhooks/messenger` (verification) |
| Messenger | `POST` | `/webhooks/messenger` (events) |
| LINE | `POST` | `/webhooks/line` |
| Messenger | `GET` | `/webhooks/messenger` (verification) |
| Messenger | `POST` | `/webhooks/messenger` (events) |

LINE, Messenger, and Telegram use `API_PUBLIC_URL` from config. If testing from external developer tools, expose the local server through a tunnel and set `API_PUBLIC_URL` to the public URL.

Telegram can also use Tailscale Funnel for automatic public HTTPS exposure. Click "Setup Webhook (Tailscale)" on the Channels page to enable it.
