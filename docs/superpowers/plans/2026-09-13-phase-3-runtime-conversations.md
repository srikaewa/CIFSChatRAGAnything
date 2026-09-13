# Phase 3 Runtime and Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch production messaging to a Bot-scoped runtime backed by external Knowledge Services, add durable conversation/decision history, and deliver a Unified Inbox with safe human takeover.

**Architecture:** Introduce a new `ChannelConnection` model because the legacy `Channel.provider` uniqueness cannot support multiple provider accounts. Migrate current channel configuration into Bot-owned connections, normalize inbound messages with provider message IDs, route all messages through `BotRuntime`, persist conversations/messages/decisions, and make human takeover a service-layer state machine. Keep legacy `Channel`/`ChatEvent` tables only as migration/history compatibility until Phase 6.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, httpx, Jinja2, pytest, pytest-asyncio, respx.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Every inbound message resolves to one `ChannelConnection`, one Bot, and one Live `BotConfigVersion` before Bot processing.
- `human_active` is checked before rules or external RAG; the Bot must remain silent until `return_to_bot`.
- Inbound idempotency uses provider `external_message_id`; retries must not generate duplicate RAG calls or outbound replies.
- External RAG creates the final answer; CIFS builds behavior instructions and bounded conversation history only.
- No fabricated confidence percentage.
- Operator outbound delivery records `sending`, `delivered`, or `failed` accurately.
- Assignment uses an atomic conditional transition so two operators cannot take the same conversation.
- Initial conversation inactivity window is 24 hours.
- Legacy fixed webhook URLs remain compatibility aliases for the migrated Default Bot during this phase; new connections receive Bot-specific webhook URLs.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `ChannelConnection`, `Conversation`, `ConversationMessage`, `BotDecision`, `ConversationHandoffEvent`.
- Modify `apps/api/chatbot_manager/db.py`: channel migration/bootstrap and indexes.
- Create `apps/api/chatbot_manager/channel_connections.py`: connection CRUD/credential resolution/migration helpers.
- Modify provider adapters under `apps/api/chatbot_manager/channels/`: normalized `IncomingMessage` includes `external_message_id`, timestamp, attachment metadata.
- Create `apps/api/chatbot_manager/runtime/__init__.py`.
- Create `apps/api/chatbot_manager/runtime/engine.py`: `BotRuntime`, request/result types, prompt builder, rule evaluation integration.
- Create `apps/api/chatbot_manager/runtime/conversations.py`: conversation lookup/session window/history/idempotency.
- Create `apps/api/chatbot_manager/runtime/handoff.py`: legal transitions/atomic take/assign/return/close.
- Create `apps/api/chatbot_manager/runtime/delivery.py`: provider outbound abstraction.
- Rewrite only the message-processing core in `apps/api/chatbot_manager/webhooks.py`; preserve provider signature checks.
- Create `apps/api/chatbot_manager/admin/conversations.py`.
- Modify `apps/api/chatbot_manager/admin/__init__.py`.
- Create `apps/api/chatbot_manager/templates/conversations.html`.
- Create `apps/api/chatbot_manager/templates/conversation_detail.html`.
- Modify `apps/api/chatbot_manager/templates/base.html`.
- Modify `apps/api/chatbot_manager/static/styles.css`.
- Create `tests/test_channel_connections.py`.
- Modify `tests/test_line_channel.py`, `test_messenger_channel.py`, `test_telegram_channel.py` for normalized IDs.
- Create `tests/test_conversation_service.py`.
- Create `tests/test_bot_runtime.py`.
- Create `tests/test_handoff_service.py`.
- Create `tests/test_conversations_admin.py`.
- Modify webhook/failure/security tests for the new runtime.

---

### Task 1: Add Multi-Bot ChannelConnection and Migrate Current Channels

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `apps/api/chatbot_manager/channel_connections.py`
- Modify: `apps/api/chatbot_manager/db.py`
- Create: `tests/test_channel_connections.py`

**Interfaces:**
- Consumes: Phase 1 Bot, Phase 2 Credential helpers.
- Produces:
  - `ChannelConnection`
  - `migrate_legacy_channels(session) -> None`
  - `get_channel_connection(session, connection_id) -> ChannelConnection`
  - `resolve_connection_credentials(session, connection) -> dict[str, str]`
  - `legacy_default_connection(session, provider) -> ChannelConnection | None`

- [ ] **Step 1: Write failing model/migration tests**

```python
from sqlmodel import Session, select

from chatbot_manager.channel_config import save_channel
from chatbot_manager.channel_connections import migrate_legacy_channels, resolve_connection_credentials
from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, ChannelConnection, Credential


def test_legacy_channels_migrate_to_default_bot_connections(client) -> None:
    with Session(get_engine()) as session:
        default_bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        save_channel(session, "line", True, {
            "channel_secret": "line-secret",
            "channel_access_token": "line-token",
        })
        migrate_legacy_channels(session)
        migrate_legacy_channels(session)
        connections = session.exec(select(ChannelConnection)).all()
        assert len(connections) == 1
        connection = connections[0]
        assert connection.bot_id == default_bot.id
        assert connection.provider == "line"
        assert connection.webhook_key
        assert resolve_connection_credentials(session, connection) == {
            "channel_secret": "line-secret",
            "channel_access_token": "line-token",
        }
        assert session.get(Credential, connection.credential_id) is not None
```

Add a test proving two LINE `ChannelConnection` rows can exist for different Bots and webhook keys; this is the reason not to reuse the legacy unique-provider table.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_channel_connections.py
```

- [ ] **Step 3: Add `ChannelConnection`**

```python
class ChannelConnection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    provider: str = Field(index=True)
    display_name: str
    external_account_id: str = Field(default="", index=True)
    webhook_key: str = Field(index=True, unique=True)
    credential_id: Optional[int] = Field(default=None, index=True)
    enabled: bool = False
    status: str = Field(default="not_configured", index=True)
    last_health_check: Optional[datetime] = None
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

Use a random `uuid4().hex` webhook key. Enforce `provider + external_account_id` uniqueness in the service when `external_account_id` is non-empty; do not attempt a fragile SQLite partial constraint migration in this phase.

- [ ] **Step 4: Implement idempotent migration**

For each legacy `Channel`, find or create one connection for `legacy.bot_id`/provider, decrypt legacy credential JSON via existing `decode_credentials()`, store it in a Phase 2 `Credential` as `credential_type=f"channel:{provider}"`, and mark migration metadata:

```json
{"legacy_channel_id": 1}
```

If a connection with that legacy ID already exists, do nothing. Call migration from `init_db()` after Default Bot bootstrap.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_channel_connections.py tests/test_secret_encryption.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/channel_connections.py apps/api/chatbot_manager/db.py tests/test_channel_connections.py
git commit -m "feat: add bot-owned channel connections"
```

---

### Task 2: Normalize Provider Message IDs for Idempotency

**Files:**
- Modify: `apps/api/chatbot_manager/channels/line.py`
- Modify: `apps/api/chatbot_manager/channels/messenger.py`
- Modify: `apps/api/chatbot_manager/channels/telegram.py`
- Modify: `tests/test_line_channel.py`
- Modify: `tests/test_messenger_channel.py`
- Modify: `tests/test_telegram_channel.py`

**Interfaces:**
- Consumes: existing provider parsers.
- Produces normalized:

```python
@dataclass(frozen=True)
class IncomingMessage:
    provider: str
    external_message_id: str
    external_user_id: str
    text: str
    timestamp_ms: int | None
    reply_context: dict[str, Any]
    attachments: list[dict[str, Any]]
    raw_event: dict[str, Any]
```

- [ ] **Step 1: Add failing provider parsing assertions**

For LINE, assert `external_message_id` comes from `event["message"]["id"]`; Messenger from `event["message"]["mid"]`; Telegram from `f"{update_id}:{message_id}"` so provider retries map to the same ID.

Example LINE assertion:

```python
payload = {"events": [{
    "type": "message",
    "timestamp": 1700000000000,
    "replyToken": "r1",
    "source": {"userId": "u1"},
    "message": {"id": "m1", "type": "text", "text": "hello"},
}]}
message = adapter.parse_events(payload)[0]
assert message.external_message_id == "m1"
assert message.timestamp_ms == 1700000000000
assert message.attachments == []
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_line_channel.py tests/test_messenger_channel.py tests/test_telegram_channel.py
```

- [ ] **Step 3: Extend adapters**

Populate stable IDs/timestamps. For text-only events, use `attachments=[]`; do not add media sending. If a provider payload lacks a stable message ID, parser skips that message and logs only a stable error code at the webhook boundary rather than inventing a random ID that would defeat idempotency.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_line_channel.py tests/test_messenger_channel.py tests/test_telegram_channel.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/channels tests/test_line_channel.py tests/test_messenger_channel.py tests/test_telegram_channel.py
git commit -m "feat: normalize provider message identity"
```

---

### Task 3: Add Conversation, Message, Decision, and Handoff Persistence

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `apps/api/chatbot_manager/runtime/__init__.py`
- Create: `apps/api/chatbot_manager/runtime/conversations.py`
- Create: `tests/test_conversation_service.py`

**Interfaces:**
- Produces:
  - `Conversation`
  - `ConversationMessage`
  - `BotDecision`
  - `ConversationHandoffEvent`
  - `ConversationService.get_or_create(bot_id, channel_connection_id, external_user_id) -> Conversation`
  - `ConversationService.record_inbound(conversation, external_message_id, text, metadata) -> ConversationMessage`
  - `ConversationService.history(conversation_id, limit=12) -> list[dict[str, str]]`
  - `ConversationService.is_duplicate(conversation_id, external_message_id) -> bool`

- [ ] **Step 1: Write failing service tests**

```python
from datetime import timedelta
from sqlmodel import Session, select

from chatbot_manager.models import Conversation, ConversationMessage, utc_now
from chatbot_manager.runtime.conversations import ConversationService


def test_same_user_reuses_open_conversation_within_24_hours(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session, inactivity_hours=24)
        first = service.get_or_create(bot_id=1, channel_connection_id=1, external_user_id="u1")
        second = service.get_or_create(bot_id=1, channel_connection_id=1, external_user_id="u1")
        assert first.id == second.id


def test_duplicate_external_message_is_not_inserted_twice(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        first = service.record_inbound(conversation, "m-1", "hello", {})
        second = service.record_inbound(conversation, "m-1", "hello", {})
        assert first.id == second.id
        rows = session.exec(select(ConversationMessage)).all()
        assert len(rows) == 1


def test_history_excludes_internal_notes_and_system_events(client) -> None:
    with Session(get_engine()) as session:
        service = ConversationService(session)
        conversation = service.get_or_create(1, 1, "u1")
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="user", content="question"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="operator", content="human answer"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="internal_note", content="private note"))
        session.add(ConversationMessage(conversation_id=conversation.id, sender_type="system", content="taken by operator"))
        session.commit()
        history = service.history(conversation.id)

    assert history == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "human answer"},
    ]
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_conversation_service.py
```

- [ ] **Step 3: Add models**

Use the fields from the design spec. Add an index-friendly `external_message_id` to `ConversationMessage`; enforce uniqueness in service logic by `(conversation_id, external_message_id, sender_type='user')` because SQLite table migration constraints must remain backward-compatible.

`BotDecision.metadata_json` and `ConversationMessage.metadata_json` are JSON strings in SQLite. `Conversation.assigned_operator_id` remains nullable integer even before Phase 6 user rows exist.

- [ ] **Step 4: Implement `ConversationService`**

Required behavior:

```python
class ConversationService:
    def __init__(self, session: Session, inactivity_hours: int = 24) -> None:
        self._session = session
        self._inactivity_hours = inactivity_hours

    def get_or_create(self, bot_id: int, channel_connection_id: int, external_user_id: str) -> Conversation:
        # reuse newest non-closed conversation updated inside inactivity window;
        # otherwise create bot_active conversation.

    def record_inbound(self, conversation: Conversation, external_message_id: str, text: str, metadata: dict[str, object]) -> ConversationMessage:
        # return existing user message when duplicate ID already exists.

    def history(self, conversation_id: int, limit: int = 12) -> list[dict[str, str]]:
        # return bounded oldest->newest user/bot/operator messages only.
```

Update `last_message_at` whenever a new inbound/outbound message is inserted.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_conversation_service.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/runtime tests/test_conversation_service.py
git commit -m "feat: add durable conversations and decision history"
```

---

### Task 4: Build BotRuntime with External Knowledge and Deterministic Rules

**Files:**
- Create: `apps/api/chatbot_manager/runtime/engine.py`
- Modify: `apps/api/chatbot_manager/runtime/__init__.py`
- Create: `tests/test_bot_runtime.py`

**Interfaces:**
- Consumes: `BotConfigVersion`, `BotConfigRule`, `KnowledgeServiceClient`, `ConversationService`.
- Produces exact runtime contract:

```python
@dataclass(frozen=True)
class RuntimeRequest:
    bot_id: int
    conversation_id: int
    message_id: int
    text: str
    provider: str
    external_user_id: str
    config_version_id: int | None = None
    test_mode: bool = False

@dataclass(frozen=True)
class RuntimeResult:
    decision_type: str
    reply_text: str
    escalate: bool
    error_code: str
    reference_count: int
    references: list[dict[str, object]]
    config_version_id: int
    knowledge_service_id: int | None
    total_latency_ms: int

class BotRuntime:
    async def run(self, request: RuntimeRequest) -> RuntimeResult:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing runtime tests**

Required tests:

```python
@pytest.mark.asyncio
async def test_runtime_rule_response_skips_knowledge(client) -> None:
    # live BotConfigRule action RESPOND; fake knowledge client raises if called.

@pytest.mark.asyncio
async def test_runtime_escalation_rule_skips_knowledge(client) -> None:
    # action ESCALATE -> result.escalate True.

@pytest.mark.asyncio
async def test_runtime_rag_uses_bot_instruction_and_history(client) -> None:
    # no matching rule; fake client captures KnowledgeQuery.
    # assert prompt includes identity/tone/language/response style/custom instructions.
    # assert conversation history was supplied and returned references are preserved.

@pytest.mark.asyncio
async def test_runtime_knowledge_failure_uses_configured_fallback(client) -> None:
    # fake client raises stable knowledge timeout; result is fallback or escalation per config policy.
```

Write full fixtures using in-memory/current test DB and a fake client with a `queries` list; do not depend on real LightRAG.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_runtime.py
```

- [ ] **Step 3: Implement rule evaluation and instruction builder**

Reuse the existing `_normalize()` / matching semantics by moving or sharing them without changing behavior. Map actions:

```text
RESPOND        -> reply_text
ESCALATE       -> escalate_message or reply_text + escalate=True
BLOCK          -> configured reply_text, no RAG
CONTINUE_TO_RAG-> continue evaluation / RAG
```

Build instructions deterministically:

```python
def build_bot_instruction(config: BotConfigVersion) -> str:
    parts = [
        config.system_prompt.strip(),
        f"Tone: {config.tone}.",
        f"Language policy: {config.language}.",
        f"Response style: {config.response_style}.",
        config.custom_instructions.strip(),
        "Do not invent facts that are not supported by the retrieved knowledge.",
    ]
    return "\n".join(part for part in parts if part)
```

When RAG is used, call one `KnowledgeServiceClient.query()` and never pass the answer through a second LLM.

- [ ] **Step 4: Persist BotDecision from runtime**

Record decision type, config version, rule ID when present, knowledge service ID, reference count, latency, stable error code, and sanitized metadata. Do not persist full retrieved context in `BotDecision`.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_bot_runtime.py tests/test_chatbot_engine.py
```

The old engine tests remain green because the legacy engine still exists until Phase 6, but production switch happens in Task 6.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/runtime/engine.py apps/api/chatbot_manager/runtime/__init__.py tests/test_bot_runtime.py
git commit -m "feat: add bot-scoped runtime engine"
```

---

### Task 5: Implement Human Handoff State Machine and Provider Delivery Service

**Files:**
- Create: `apps/api/chatbot_manager/runtime/handoff.py`
- Create: `apps/api/chatbot_manager/runtime/delivery.py`
- Create: `tests/test_handoff_service.py`

**Interfaces:**
- Produces:
  - `HandoffService.escalate()`
  - `HandoffService.take()`
  - `HandoffService.assign()`
  - `HandoffService.return_to_bot()`
  - `HandoffService.close()`
  - `DeliveryService.send(connection, reply_context, text) -> DeliveryResult`

- [ ] **Step 1: Write failing state tests**

```python
def test_take_transitions_needs_human_to_human_active(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="needs_human")
        result = HandoffService(session).take(conversation.id, operator_id=10)
        assert result.status == "human_active"
        assert result.assigned_operator_id == 10


def test_second_take_loses_atomic_claim(client) -> None:
    with Session(get_engine()) as session:
        conversation = make_conversation(session, status="needs_human")
        HandoffService(session).take(conversation.id, 10)
        with pytest.raises(ConversationStateError):
            HandoffService(session).take(conversation.id, 11)
```

Also cover legal/illegal transitions and creation of `ConversationHandoffEvent` rows.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_handoff_service.py
```

- [ ] **Step 3: Implement atomic take**

Use SQLAlchemy update with status predicate:

```python
statement = (
    update(Conversation)
    .where(Conversation.id == conversation_id, Conversation.status == "needs_human")
    .values(status="human_active", assigned_operator_id=operator_id)
)
result = session.exec(statement)
if result.rowcount != 1:
    session.rollback()
    raise ConversationStateError("conversation_not_available")
```

Then persist one `taken` handoff event.

- [ ] **Step 4: Implement `DeliveryService`**

Given a `ChannelConnection`, resolve encrypted credentials and construct the existing provider adapter. `send()` returns:

```python
@dataclass(frozen=True)
class DeliveryResult:
    status: str        # delivered | failed
    error_code: str = ""
```

Map provider failures to stable codes such as `provider_delivery_failed`; log provider/connection IDs and exception type only.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_handoff_service.py tests/test_failure_handling.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/runtime/handoff.py apps/api/chatbot_manager/runtime/delivery.py tests/test_handoff_service.py
git commit -m "feat: add human handoff and delivery services"
```

---

### Task 6: Switch Webhooks to BotRuntime with Idempotency and Compatibility Aliases

**Files:**
- Modify: `apps/api/chatbot_manager/webhooks.py`
- Modify: `tests/test_webhooks.py`
- Modify: `tests/test_telegram_webhooks.py`
- Modify: `tests/test_webhook_security.py`
- Modify: `tests/test_failure_handling.py`

**Interfaces:**
- Consumes: ChannelConnection, ConversationService, BotRuntime, HandoffService, DeliveryService.
- Produces new keyed endpoints plus legacy aliases:

```text
GET/POST /webhooks/{provider}/{webhook_key}
POST     /webhooks/line                  # Default Bot compatibility
GET/POST /webhooks/messenger             # Default Bot compatibility
POST     /webhooks/telegram              # Default Bot compatibility
```

- [ ] **Step 1: Add failing integration tests**

Add tests proving:

```text
valid keyed LINE webhook resolves correct Bot
bad provider signature creates no Conversation/Message
same external_message_id POSTed twice -> one user message, one RAG/runtime invocation, one outbound reply
human_active inbound -> message persisted, zero runtime/RAG call, zero Bot reply
escalating runtime result -> conversation becomes needs_human
```

Use fake runtime/delivery injected by monkeypatch and inspect DB rows.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_webhooks.py tests/test_telegram_webhooks.py tests/test_webhook_security.py tests/test_failure_handling.py
```

- [ ] **Step 3: Refactor webhook orchestration**

Keep signature verification before message persistence. After successful parse:

```text
resolve ChannelConnection
get/create conversation
check duplicate external_message_id
record inbound
if conversation.human_active: stop after persistence
snapshot live config ID
BotRuntime.run()
record Bot reply message / Decision
if escalate: transition needs_human
else DeliveryService.send()
record outbound delivery status
```

A duplicate inbound returns as processed/idempotent without re-running runtime or delivery.

- [ ] **Step 4: Verify compatibility alias behavior**

Legacy provider URLs must resolve the migrated Default Bot connection while it is unambiguous. If more than one enabled connection exists for that provider, the legacy alias returns 409/503 with stable `ambiguous_legacy_webhook` rather than routing to an arbitrary Bot.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_webhooks.py tests/test_telegram_webhooks.py tests/test_webhook_security.py tests/test_failure_handling.py tests/test_bot_runtime.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/webhooks.py tests/test_webhooks.py tests/test_telegram_webhooks.py tests/test_webhook_security.py tests/test_failure_handling.py
git commit -m "feat: route provider webhooks through bot runtime"
```

---

### Task 7: Add Unified Inbox and Human Reply Workflow

**Files:**
- Create: `apps/api/chatbot_manager/admin/conversations.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/conversations.html`
- Create: `apps/api/chatbot_manager/templates/conversation_detail.html`
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `apps/api/chatbot_manager/static/styles.css`
- Create: `tests/test_conversations_admin.py`

**Interfaces:**
- Consumes: Conversation/Handoff/Delivery services.
- Produces filtered global Inbox plus Bot-scoped view and actions `take`, `assign` (admin-compatible identity in Phase 3), `reply`, `return-to-bot`, `close`, `internal-note`.

- [ ] **Step 1: Write failing Inbox tests**

Required tests and concrete assertions:

- `test_conversations_page_filters_needs_human`: seed one `needs_human` and one `bot_active` conversation, request `/conversations?status=needs_human`, and assert only the waiting conversation/user appears.
- `test_take_marks_conversation_human_active`: POST `/conversations/{id}/take`, then reload the row and assert `status == "human_active"` and an assigned operator marker is present.
- `test_operator_reply_uses_original_channel_and_records_delivery`: monkeypatch `DeliveryService.send()` to capture the `ChannelConnection`; POST a reply and assert the original connection ID is used and an operator `ConversationMessage` has `delivery_status == "delivered"`.
- `test_return_to_bot_does_not_generate_replay_reply`: place a conversation in `human_active`, monkeypatch `BotRuntime.run()` to count calls, POST return-to-bot, and assert status becomes `bot_active` with zero runtime calls.
- `test_internal_note_is_not_sent_or_added_to_bot_history`: POST an internal note with `DeliveryService.send()` configured to fail if called; assert no send occurs and `ConversationService.history()` excludes the note.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_conversations_admin.py
```

- [ ] **Step 3: Implement routes**

Initial routes:

```text
GET  /conversations?bot_id=&channel=&status=&assigned=&q=
GET  /conversations/{id}
POST /conversations/{id}/take
POST /conversations/{id}/reply
POST /conversations/{id}/return-to-bot
POST /conversations/{id}/close
POST /conversations/{id}/note
```

Until Phase 6 creates users, use the authenticated admin email as actor metadata and a deterministic temporary operator ID `0` for assignment; Phase 6 migrates assignment ownership to real `User.id`. Do not expose multi-user claims before real users exist.

- [ ] **Step 4: Implement templates**

Global Inbox shows Bot, provider, user label/ID, last message excerpt, status, wait time, assigned actor, and filters. Detail view shows chronological user/Bot/operator messages, system handoff events, internal notes with distinct styling, delivery state, Decision Trace summary, and allowed actions based on status.

Add `Conversations` to global navigation. `Bot Workspace / Conversations` links to `/conversations?bot_id=<id>` rather than duplicating UI.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_conversations_admin.py tests/test_bot_admin.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin/conversations.py apps/api/chatbot_manager/admin/__init__.py apps/api/chatbot_manager/templates apps/api/chatbot_manager/static/styles.css tests/test_conversations_admin.py
git commit -m "feat: add unified conversation inbox"
```

---

### Task 8: Phase 3 Verification and Manual Provider/Handoff Checkpoint

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Focused verification**

```bash
rtk uv run pytest -q \
  tests/test_channel_connections.py \
  tests/test_line_channel.py \
  tests/test_messenger_channel.py \
  tests/test_telegram_channel.py \
  tests/test_conversation_service.py \
  tests/test_bot_runtime.py \
  tests/test_handoff_service.py \
  tests/test_webhooks.py \
  tests/test_telegram_webhooks.py \
  tests/test_webhook_security.py \
  tests/test_conversations_admin.py \
  tests/test_failure_handling.py
```

- [ ] **Step 2: Full verification**

```bash
rtk uv run pytest -q
rtk python -m compileall -q apps/api
rtk uv lock --check
git diff --check
```

- [ ] **Step 3: Manual checkpoint**

Using one authorized provider and external RAG connection where available:

```text
send normal user question -> correct Bot -> external RAG -> reply
re-deliver identical provider message -> no duplicate reply
trigger explicit escalation -> Inbox shows needs_human
Take -> status human_active
send user message while human_active -> Inbox receives it; Bot sends nothing
reply from Inbox -> user receives provider message
Return to Bot -> no automatic replay
send next user message -> Bot resumes and has relevant operator history
```

- [ ] **Step 4: Update handoff and commit**

Record provider(s) tested, external-RAG test mode, idempotency result, handoff result, full suite result, known constraints, and next plan:

`docs/superpowers/plans/2026-09-13-phase-4-draft-test-publish.md`

```bash
git add HANDOFF.md
git commit -m "docs: hand off runtime conversations phase"
```
