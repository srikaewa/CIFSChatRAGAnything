# Chatbot Manager Design

Date: 2026-07-08

## Goal

Build a first working Chatbot Manager for a nontechnical admin to control, modify, test, and run a chatbot on LINE and Facebook Messenger.

The first version prioritizes a working local integration over a large production platform. It must let an admin:

- Configure LINE and Messenger channel credentials.
- Create simple keyword rules and replies.
- Upload knowledge documents for RAG-Anything.
- Edit the assistant prompt and fallback behavior.
- Test the chatbot before using real channels.
- Receive LINE/Messenger webhook events and send replies.
- Review logs showing why each answer was chosen.

## Scope

In scope:

- FastAPI backend.
- Local SQLite database.
- Server-rendered or lightweight web admin dashboard.
- LINE webhook verification, event parsing, and reply API calls.
- Facebook Messenger webhook verification, event parsing, and Send API calls.
- Rule-first chatbot engine with RAG-Anything fallback.
- RAG-Anything document ingestion and hybrid retrieval.
- Admin pages for channels, rules, knowledge, assistant settings, test chat, and logs.
- Environment-based secrets for local development.

Out of scope for v1:

- Multi-tenant organizations.
- Role-based access control beyond one local admin account.
- Payment, billing, or subscriptions.
- Human support inbox with assignments.
- Visual node-based flow builder.
- Production scaling with Redis workers, Postgres, object storage, or vector DB services.
- Full analytics dashboards beyond event/reply logs.

## Architecture

Use a lean full-stack app:

- `apps/api`: FastAPI app, database models, chatbot engine, channel adapters, RAG service.
- `apps/web` or backend templates: admin dashboard. If a frontend bundler is added, keep it minimal and run from one local command.
- `data`: local app state, uploaded documents, RAG-Anything working directory, generated parser output.

Core backend modules:

- `settings`: load `.env` values for app secret, channel tokens, LLM provider, RAG-Anything paths, model names.
- `db`: SQLite engine and migrations or schema bootstrap.
- `channels`: provider-specific adapters for LINE and Messenger.
- `chatbot`: deterministic message handling pipeline.
- `rag`: RAG-Anything wrapper for indexing and querying.
- `admin`: dashboard routes and API endpoints.
- `logs`: event, decision, reply, and error logging.

## Data Model

Tables:

- `channels`
  - `id`, `provider`, `enabled`, `display_name`, `status`, encrypted credential fields, timestamps.
- `rules`
  - `id`, `enabled`, `priority`, `match_type`, `pattern`, `reply_text`, timestamps.
- `assistant_settings`
  - `id`, `system_prompt`, `fallback_reply`, `rag_enabled`, `llm_model`, `vision_model`, timestamps.
- `knowledge_documents`
  - `id`, `filename`, `path`, `status`, `error`, `indexed_at`, timestamps.
- `chat_events`
  - `id`, `provider`, `external_user_id`, `incoming_text`, `decision_source`, `reply_text`, `raw_event`, timestamps.

Secrets should stay in `.env` for v1. The database may store non-secret channel metadata and status. If credential editing is included in the UI, values must be encrypted with `APP_ENCRYPTION_KEY`.

## Chatbot Message Flow

For every incoming text message:

1. Normalize the input by trimming whitespace and preparing a lowercase value for matching.
2. Check built-in commands such as `help`, `start`, and admin-defined postback values.
3. Match enabled rules by priority:
   - `exact`: normalized input equals pattern.
   - `contains`: normalized input contains pattern.
4. If no rule matches and RAG is enabled, query RAG-Anything:
   - Use `rag.aquery(question, mode="hybrid")`.
   - Add the admin system prompt around the answer generation.
   - Treat empty or low-confidence answers as no answer.
5. If RAG has no usable answer, send fallback reply.
6. Log the decision source as `command`, `rule`, `rag`, or `fallback`.

This keeps business-critical replies predictable while still allowing document-based answers when no rule covers the question.

## RAG-Anything Integration

Use RAG-Anything as the chatbot knowledge backend.

Configuration comes from existing environment shape:

- `RAG_BACKEND=rag_anything`
- `RAG_WORKING_DIR=./data/rag`
- `RAG_PARSER=mineru`
- `RAG_PARSE_METHOD=auto`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_DEFAULT_MODEL`
- `LLM_VISION_MODEL`

Indexing flow:

1. Admin uploads a document.
2. Backend stores it under `data/uploads`.
3. Create or reuse a `RAGAnything` instance with:
   - `RAGAnythingConfig(working_dir=RAG_WORKING_DIR, parser=RAG_PARSER, parse_method=RAG_PARSE_METHOD)`.
   - OpenAI-compatible completion function.
   - OpenAI-compatible embedding function.
   - Vision model function when available.
4. Call `process_document_complete(file_path=..., output_dir=..., parse_method=...)`.
5. Store status as `indexed` or `failed`.

Query flow:

1. Chatbot receives unmatched user question.
2. Call `aquery(question, mode="hybrid")`.
3. Return concise answer to channel.
4. Log RAG usage and error details.

RAG-Anything supports multimodal document processing, including PDFs, Office documents, images, tables, and equations. Office file parsing may require LibreOffice on the host. MinerU installation and model downloads are environment prerequisites.

## Channel Integration

LINE:

- Endpoint: `POST /webhooks/line`.
- Validate signature with channel secret.
- Parse message events and reply tokens.
- Send replies through LINE Messaging API using channel access token.
- Ignore unsupported event types but log them.

Messenger:

- Endpoint: `GET /webhooks/messenger` for verification challenge.
- Endpoint: `POST /webhooks/messenger` for events.
- Validate verify token and app secret proof where practical.
- Parse text message events.
- Send replies through Messenger Send API using page access token.
- Ignore unsupported event types but log them.

Both adapters should convert provider payloads into one internal shape:

```python
IncomingMessage(
    provider="line" | "messenger",
    external_user_id="...",
    text="...",
    reply_context={...},
    raw_event={...},
)
```

## Admin Dashboard

Audience: business owner or marketing staff. UI should use plain labels and avoid raw JSON on primary screens.

Pages:

- Dashboard
  - Channel status, number of active rules, knowledge index status, recent bot decisions.
- Channels
  - LINE and Messenger setup status, webhook URLs, credential fields, test buttons.
- Rules
  - Table with enabled switch, priority, match type, pattern, reply text.
  - Create/edit/delete rule forms.
- Knowledge
  - Upload documents, see indexing status, retry failed jobs.
- Assistant
  - System prompt, fallback reply, RAG enabled switch, model fields.
- Test Chat
  - Message input and transcript.
  - Show answer source: rule, RAG, fallback.
- Logs
  - Recent incoming messages, decision source, reply text, provider, timestamp.
  - Detail view can show raw payload for technical troubleshooting.

The first screen should be the working manager dashboard, not a marketing page.

## Error Handling

- Channel signature failures return 401 and create a security log entry.
- Missing credentials disable that channel and show setup guidance in dashboard.
- RAG indexing failures keep the document row with `failed` status and visible error.
- RAG query failures fall back to configured fallback reply and log error.
- LLM API failures should not break webhook response handling.
- Unsupported message types produce a simple fallback or no-op, depending on provider norms.

## Security

- Load secrets from `.env`.
- Never print channel tokens or LLM API keys in logs.
- Validate provider signatures before processing webhook events.
- Use CSRF protection if dashboard uses cookie-based forms.
- Add local admin password before exposing outside localhost.
- Store uploaded files in controlled directories and reject path traversal.
- Restrict upload file types to formats RAG-Anything can handle.

## Testing

Backend tests:

- Rule matching order and match types.
- Chatbot decision pipeline: command, rule, RAG, fallback.
- RAG wrapper mocked success/failure.
- LINE signature validation and event parsing.
- Messenger verification and event parsing.
- Admin CRUD for rules and assistant settings.

Manual smoke tests:

- Start local server.
- Add a rule and verify test chat uses it.
- Upload a sample document and verify RAG answer.
- Send sample LINE webhook payload and verify reply path with mocked HTTP.
- Send sample Messenger payload and verify reply path with mocked HTTP.

## Milestones

1. Project scaffold and local run command.
2. Database schema and settings.
3. Chatbot engine with rule and mocked RAG fallback.
4. RAG-Anything wrapper and knowledge upload/index flow.
5. LINE and Messenger adapters.
6. Admin dashboard pages.
7. Logs and test chat.
8. Verification tests and local smoke run.

## Chosen V1 Decisions

- Frontend implementation style: backend-rendered templates with small progressive JavaScript where needed. This keeps the app easy to run and avoids a second dev server for v1.
- Admin auth depth: one local admin password stored as a hash in environment or initialized database settings. The dashboard should require login even when running on localhost.
- RAG indexing: use FastAPI background tasks for v1 so document uploads return quickly while indexing status updates in the Knowledge page.
