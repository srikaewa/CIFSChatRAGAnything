# Chatbot Manager

Local admin manager for LINE and Facebook Messenger chatbots with rule-first replies and RAG-Anything fallback.

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
- Channels: webhook URLs for LINE and Messenger.
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

Required for RAG answers:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_DEFAULT_MODEL`
- `LLM_VISION_MODEL`
- `RAG_WORKING_DIR`
- MinerU/RAG-Anything parser dependencies installed on host

## Webhooks

- LINE: `POST /webhooks/line`
- Messenger verification: `GET /webhooks/messenger`
- Messenger events: `POST /webhooks/messenger`

If testing from external LINE or Facebook developer tools, expose the local server through a tunnel and set `API_PUBLIC_URL` to the public URL.
