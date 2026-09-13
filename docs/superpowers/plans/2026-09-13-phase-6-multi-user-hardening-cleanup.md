# Phase 6 Multi-User Hardening and Legacy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single environment-admin login with Owner/Admin/Operator users and Bot-scoped authorization, add auditable administrative actions and retention/archive controls, complete secret/redaction hardening, and remove obsolete single-assistant/local-RAG code only after verified migration.

**Architecture:** Persist users and Bot assignments in SQLModel, authenticate signed sessions to a database-backed `CurrentUser`, and enforce roles/Bot scope in server-side route dependencies and application services. Add a centralized audit service that sanitizes secrets before persistence. Complete migration to the shared `Credential` abstraction, then use dependency searches plus full regression tests to prove legacy Assistant/Rule/KnowledgeDocument/in-process RAG paths can be retired without losing historical conversation data.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, Jinja2, pwdlib/Argon2, cryptography/Fernet, pytest, pytest-asyncio, httpx/respx.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Roles are exactly `owner`, `admin`, and `operator` in the first release.
- Owner has global access. Admin has Bot configuration/publish access and defaults to all Bots. Operator sees/acts only on Bots explicitly granted through `UserBotAccess`.
- Authorization is enforced server-side for direct URLs and POST actions; UI hiding is not a security boundary.
- Existing local admin credentials bootstrap the first Owner only when the user table is empty. Plaintext passwords are never persisted.
- Existing signed admin sessions may be invalidated by the migration; requiring a new login is acceptable and safer than supporting dual session formats indefinitely.
- Existing secrets are replace-only in UI. Secret values must not be written to logs, traces, errors, audit snapshots, or rendered HTML.
- Audit history and conversation history remain separate; audit rows reference conversation/message IDs instead of duplicating user message content.
- Prefer archive/disable over destructive deletion for Bots and referenced Knowledge Services.
- Historical `Conversation`/`ConversationMessage`/`BotDecision` data must survive legacy cleanup.
- Delete legacy code only after repository dependency search and full tests prove no active consumer remains.
- Update `HANDOFF.md` before declaring the redesign complete.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `User`, `UserBotAccess`, `AuditEvent`; add archive/retention fields where required.
- Create `apps/api/chatbot_manager/auth/__init__.py`.
- Create `apps/api/chatbot_manager/auth/service.py`: password hashing, owner bootstrap, authentication, current-user lookup.
- Create `apps/api/chatbot_manager/auth/authorization.py`: `CurrentUser`, role/Bot-scope checks.
- Modify `apps/api/chatbot_manager/security.py`: signed session/CSRF token payloads keyed to persisted user ID.
- Modify `apps/api/chatbot_manager/admin/dependencies.py`: database-backed authentication and authorization dependencies.
- Create `apps/api/chatbot_manager/audit.py`: sanitized administrative audit persistence.
- Create `apps/api/chatbot_manager/admin/users.py`.
- Modify Bot/Knowledge/Conversation/Incident admin routers to require roles/Bot scope and emit audit events.
- Create `apps/api/chatbot_manager/templates/users.html` and `audit.html`; modify `base.html` for role-aware navigation.
- Create `apps/api/chatbot_manager/retention.py`: archive/retention policy service.
- Modify `apps/api/chatbot_manager/channel_connections.py`: ensure every active channel secret uses shared `Credential` storage and remove fallback dependency on legacy `Channel` after verification.
- Modify `apps/api/chatbot_manager/settings.py` / `.env.example`: retention/session settings required by this phase.
- Remove obsolete legacy routes/templates/models/services only in the final cleanup task.
- Update `pyproject.toml`/`uv.lock` only after dependency search proves `raganything` or other legacy-only packages are unused.
- Create tests: `test_users.py`, `test_authorization.py`, `test_user_admin.py`, `test_audit.py`, `test_retention.py`, `test_legacy_cleanup.py`.
- Modify existing auth/session/admin/runtime tests for database-backed users.

---

### Task 1: Add User, Bot Access, and Audit Models

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `tests/test_users.py`
- Create: `tests/test_audit.py`

**Interfaces:**
- Produces:

```python
class User(SQLModel, table=True):
    id: int | None
    email: str
    password_hash: str
    role: str
    active: bool

class UserBotAccess(SQLModel, table=True):
    id: int | None
    user_id: int
    bot_id: int

class AuditEvent(SQLModel, table=True):
    id: int | None
    actor_user_id: int | None
    action: str
    object_type: str
    object_id: str
    bot_id: int | None
    summary: str
    before_json: str
    after_json: str
    request_metadata_json: str
```

- [ ] **Step 1: Write failing model tests**

Create `tests/test_users.py`:

```python
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import Bot, User, UserBotAccess


def test_user_and_bot_access_round_trip() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Admission", lifecycle_status="active")
        user = User(
            email="operator@example.local",
            password_hash="hash",
            role="operator",
            active=True,
        )
        session.add(bot)
        session.add(user)
        session.commit()
        session.refresh(bot)
        session.refresh(user)
        session.add(UserBotAccess(user_id=user.id, bot_id=bot.id))
        session.commit()
        access = session.exec(select(UserBotAccess)).one()

    assert access.user_id == user.id
    assert access.bot_id == bot.id
```

Create `tests/test_audit.py` initial model test:

```python
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import AuditEvent


def test_audit_event_round_trip(client) -> None:
    with Session(get_engine()) as session:
        event = AuditEvent(
            actor_user_id=None,
            action="bot.pause",
            object_type="bot",
            object_id="1",
            bot_id=1,
            summary="Paused Bot 1",
            before_json='{"status":"active"}',
            after_json='{"status":"paused"}',
            request_metadata_json="{}",
        )
        session.add(event)
        session.commit()
        stored = session.exec(select(AuditEvent)).one()
    assert stored.action == "bot.pause"
    assert stored.bot_id == 1
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_users.py tests/test_audit.py
```

Expected: imports fail because the new models do not exist.

- [ ] **Step 3: Add the models**

Add to `models.py`:

```python
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default="operator", index=True)
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserBotAccess(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    bot_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_user_id: Optional[int] = Field(default=None, index=True)
    action: str = Field(index=True)
    object_type: str = Field(index=True)
    object_id: str = Field(index=True)
    bot_id: Optional[int] = Field(default=None, index=True)
    summary: str
    before_json: str = "{}"
    after_json: str = "{}"
    request_metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

Enforce unique `(user_id, bot_id)` in the access service and test it there; do not retrofit a risky SQLite composite constraint into old installations in this task.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_users.py tests/test_audit.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/models.py tests/test_users.py tests/test_audit.py
git commit -m "feat: add users bot access and audit models"
```

---

### Task 2: Bootstrap Owner and Replace Environment-Only Authentication

**Files:**
- Create: `apps/api/chatbot_manager/auth/__init__.py`
- Create: `apps/api/chatbot_manager/auth/service.py`
- Modify: `apps/api/chatbot_manager/db.py`
- Modify: `apps/api/chatbot_manager/security.py`
- Modify: `tests/test_users.py`
- Modify: `tests/test_session_security.py`

**Interfaces:**
- Produces:

```python
password_hasher: PasswordHash
bootstrap_owner(session: Session) -> User
authenticate_user(session: Session, email: str, password: str) -> User | None
load_active_user(session: Session, user_id: int) -> User | None
make_session_token(user_id: int) -> str
read_session_user_id(token: str | None, max_age_seconds: int | None = None) -> int | None
```

- [ ] **Step 1: Write failing owner bootstrap/authentication tests**

Append to `tests/test_users.py`:

```python
from chatbot_manager.auth.service import authenticate_user, bootstrap_owner
from chatbot_manager.settings import get_settings


def test_bootstrap_owner_hashes_current_admin_password_once(client) -> None:
    with Session(get_engine()) as session:
        first = bootstrap_owner(session)
        second = bootstrap_owner(session)
        assert first.id == second.id
        assert first.email == get_settings().admin_email
        assert first.role == "owner"
        assert first.password_hash != get_settings().admin_password
        assert authenticate_user(session, first.email, get_settings().admin_password).id == first.id


def test_inactive_user_cannot_authenticate(client) -> None:
    with Session(get_engine()) as session:
        owner = bootstrap_owner(session)
        owner.active = False
        session.add(owner)
        session.commit()
        assert authenticate_user(session, owner.email, get_settings().admin_password) is None
```

Update session-security tests to assert a token round-trips `user_id` rather than email:

```python
def test_session_tokens_expire_and_reject_tampering() -> None:
    token = make_session_token(7)
    assert read_session_user_id(token, max_age_seconds=60) == 7
    assert read_session_user_id(token, max_age_seconds=-1) is None
    assert read_session_user_id(f"{token}tampered", max_age_seconds=60) is None
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_users.py tests/test_session_security.py
```

- [ ] **Step 3: Implement Argon2-backed auth service**

Use the already-installed `pwdlib[argon2]`:

```python
from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def bootstrap_owner(session: Session) -> User:
    existing = session.exec(select(User).order_by(User.id)).first()
    if existing is not None:
        return existing
    settings = get_settings()
    owner = User(
        email=settings.admin_email.strip().lower(),
        password_hash=password_hasher.hash(settings.admin_password),
        role="owner",
        active=True,
    )
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    user = session.exec(select(User).where(User.email == email.strip().lower())).first()
    if user is None or not user.active:
        return None
    if not password_hasher.verify(password, user.password_hash):
        return None
    return user
```

Call `bootstrap_owner()` from `init_db()` after schema creation and Bot migration.

- [ ] **Step 4: Change signed session payload to user ID**

Replace email session serialization with:

```python
def make_session_token(user_id: int) -> str:
    serializer = URLSafeTimedSerializer(get_settings().app_secret_key, salt="admin-session-v2")
    return serializer.dumps({"user_id": user_id})


def read_session_user_id(token: str | None, max_age_seconds: int | None = None) -> int | None:
    if not token:
        return None
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.app_secret_key, salt="admin-session-v2")
    try:
        data = serializer.loads(token, max_age=max_age_seconds or settings.admin_session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    user_id = data.get("user_id")
    return int(user_id) if isinstance(user_id, int) else None
```

Use a new salt so old environment-admin sessions are deliberately invalidated.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_users.py tests/test_session_security.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/auth apps/api/chatbot_manager/db.py apps/api/chatbot_manager/security.py tests/test_users.py tests/test_session_security.py
git commit -m "feat: add database backed user authentication"
```

---

### Task 3: Enforce Role and Bot-Scope Authorization Server-Side

**Files:**
- Create: `apps/api/chatbot_manager/auth/authorization.py`
- Modify: `apps/api/chatbot_manager/admin/dependencies.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `apps/api/chatbot_manager/admin/bots.py`
- Modify: `apps/api/chatbot_manager/admin/knowledge_services.py`
- Modify: `apps/api/chatbot_manager/admin/conversations.py`
- Modify: `apps/api/chatbot_manager/admin/test_center.py`
- Modify: `apps/api/chatbot_manager/admin/incidents.py`
- Modify: `apps/api/chatbot_manager/admin/analytics.py`
- Create: `tests/test_authorization.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    role: str
    allowed_bot_ids: frozenset[int]

require_current_user(request, session) -> CurrentUser
require_role(*allowed_roles: str)
require_bot_access(bot_id: int, current_user: CurrentUser, session: Session) -> Bot
```

- [ ] **Step 1: Write failing direct-route authorization tests**

Create `tests/test_authorization.py` with explicit login helpers that create users through the auth service. Required assertions:

```python
def test_owner_can_open_all_bots(client) -> None:
    owner = login_as(client, role="owner")
    bot = create_bot("Owner-visible")
    response = client.get(f"/bots/{bot.id}")
    assert response.status_code == 200


def test_operator_cannot_open_unassigned_bot_by_direct_url(client) -> None:
    operator = login_as(client, role="operator")
    bot = create_bot("Restricted")
    response = client.get(f"/bots/{bot.id}")
    assert response.status_code == 403


def test_operator_can_open_assigned_bot_conversation(client) -> None:
    operator = login_as(client, role="operator")
    bot = create_bot("Assigned")
    grant_bot_access(operator.id, bot.id)
    conversation = create_conversation(bot.id)
    response = client.get(f"/conversations/{conversation.id}")
    assert response.status_code == 200


def test_operator_cannot_publish_or_edit_bot_config(client) -> None:
    operator = login_as(client, role="operator")
    bot = create_bot("Assigned")
    grant_bot_access(operator.id, bot.id)
    response = client.post(f"/bots/{bot.id}/test/publish", data={"csrf_token": csrf(client)})
    assert response.status_code == 403


def test_admin_can_manage_bots_but_not_users(client) -> None:
    login_as(client, role="admin")
    assert client.get("/bots").status_code == 200
    assert client.get("/users").status_code == 403
```

Implement helper setup fully inside the test module; do not rely on hidden UI checks.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_authorization.py
```

- [ ] **Step 3: Implement `CurrentUser` and dependencies**

`require_current_user()` reads `user_id` from the signed session, loads an active User, and gathers explicit access rows for Operators. Owner/Admin use `allowed_bot_ids=frozenset()` as a sentinel because their role grants global Bot visibility.

Implement pure authorization helpers:

```python
def can_manage_bot(user: CurrentUser, bot_id: int) -> bool:
    if user.role in {"owner", "admin"}:
        return True
    return user.role == "operator" and bot_id in user.allowed_bot_ids


def require_bot_access(bot_id: int, current_user: CurrentUser, session: Session) -> Bot:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if not can_manage_bot(current_user, bot_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return bot
```

`require_role()` returns a FastAPI dependency closure that rejects roles not in the supplied set.

- [ ] **Step 4: Apply authorization to routes and services**

At minimum:

```text
Owner only: users, system settings, hard administrative archive/remove actions
Owner/Admin: create/edit/publish/pause Bots, Knowledge Services, channels, analytics, incidents/audit
Operator: assigned-Bot Inbox, take/reply/return/close/note, assigned-Bot analytics read-only
```

Every route resolving a conversation derives its Bot ID from the database and calls the same access predicate. Do not trust `bot_id` query parameters alone.

- [ ] **Step 5: Update login/CSRF dependencies to database users**

Login POST authenticates with `authenticate_user()`, stores the `user_id` session token, and renders email/role from `CurrentUser`. CSRF may continue to sign a stable user identity string such as `str(current_user.id)`; update generation/verification consistently.

- [ ] **Step 6: Verify GREEN plus existing auth/security tests**

```bash
rtk uv run pytest -q \
  tests/test_authorization.py \
  tests/test_admin_routes.py \
  tests/test_csrf_security.py \
  tests/test_session_security.py \
  tests/test_conversations_admin.py \
  tests/test_test_center_admin.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/chatbot_manager/auth apps/api/chatbot_manager/admin apps/api/chatbot_manager/security.py tests/test_authorization.py tests/test_admin_routes.py tests/test_csrf_security.py tests/test_conversations_admin.py tests/test_test_center_admin.py
git commit -m "feat: enforce role and bot scoped authorization"
```

---

### Task 4: Add Owner User Management and Bot Assignment UI

**Files:**
- Create: `apps/api/chatbot_manager/admin/users.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/users.html`
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Create: `tests/test_user_admin.py`

**Interfaces:**
- Consumes: User auth and authorization helpers.
- Produces Owner-only user creation, role/active update, password reset, and Operator Bot assignment.

- [ ] **Step 1: Write failing user-management tests**

```python
def test_owner_creates_operator_with_hashed_password(client) -> None:
    login_owner(client)
    token = csrf(client)
    response = client.post("/users", data={
        "csrf_token": token,
        "email": "operator@example.local",
        "password": "strong-password-123",
        "role": "operator",
    }, follow_redirects=False)
    assert response.status_code == 303
    with Session(get_engine()) as session:
        user = session.exec(select(User).where(User.email == "operator@example.local")).one()
        assert user.password_hash != "strong-password-123"
        assert user.role == "operator"


def test_owner_assigns_operator_to_selected_bots(client) -> None:
    login_owner(client)
    operator = create_user("operator@example.local", "operator")
    bot_a = create_bot("A")
    bot_b = create_bot("B")
    response = client.post(
        f"/users/{operator.id}/bots",
        data={"csrf_token": csrf(client), "bot_ids": [str(bot_a.id), str(bot_b.id)]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert assigned_bot_ids(operator.id) == {bot_a.id, bot_b.id}


def test_owner_cannot_deactivate_last_active_owner(client) -> None:
    owner = login_owner(client)
    response = client.post(
        f"/users/{owner.id}/update",
        data={"csrf_token": csrf(client), "role": "owner", "active": ""},
        follow_redirects=False,
    )
    assert response.status_code == 400
```

Provide concrete helper implementations in the test module for DB setup/query.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_user_admin.py
```

- [ ] **Step 3: Implement Owner-only routes**

Routes:

```text
GET  /users
POST /users
POST /users/{id}/update
POST /users/{id}/password
POST /users/{id}/bots
```

Validate role against `{"owner", "admin", "operator"}`. Normalize email to lowercase. Reject duplicate email. Password reset hashes the new password and never displays it after POST. Only Operators need explicit Bot access rows; clear stale rows when role changes away from Operator.

- [ ] **Step 4: Implement user template/navigation**

Owner sees `Users` under Administration. Admin/Operator do not receive the link, but direct URL is still protected by Task 3. The table shows email, role, active state, and assigned Bots; never password hashes.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_user_admin.py tests/test_authorization.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin/users.py apps/api/chatbot_manager/admin/__init__.py apps/api/chatbot_manager/templates/users.html apps/api/chatbot_manager/templates/base.html tests/test_user_admin.py
git commit -m "feat: add owner user administration"
```

---

### Task 5: Add Sanitized Audit Service and Audit UI

**Files:**
- Create: `apps/api/chatbot_manager/audit.py`
- Create: `apps/api/chatbot_manager/admin/audit.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/audit.html`
- Modify critical mutation routes to call the service.
- Expand `tests/test_audit.py`

**Interfaces:**
- Produces:

```python
SENSITIVE_KEYS = frozenset({
    "password", "password_hash", "api_key", "access_token", "channel_access_token",
    "channel_secret", "app_secret", "bot_token", "webhook_secret", "authorization",
    "x-api-key",
})

def sanitize_audit_value(value: object) -> object

def record_audit(
    session: Session,
    actor: CurrentUser | None,
    action: str,
    object_type: str,
    object_id: str,
    summary: str,
    bot_id: int | None = None,
    before: object = None,
    after: object = None,
    request_metadata: dict[str, object] | None = None,
) -> AuditEvent
```

- [ ] **Step 1: Write failing redaction/audit tests**

Append to `tests/test_audit.py`:

```python
def test_audit_redacts_sensitive_nested_keys(client) -> None:
    value = {
        "name": "RAG",
        "credential": {"api_key": "super-secret", "endpoint": "https://rag.test"},
        "headers": {"Authorization": "Bearer hidden"},
    }
    sanitized = sanitize_audit_value(value)
    assert sanitized["credential"]["api_key"] == "[REDACTED]"
    assert sanitized["headers"]["Authorization"] == "[REDACTED]"
    assert "super-secret" not in json.dumps(sanitized)
    assert "Bearer hidden" not in json.dumps(sanitized)


def test_publish_pause_credential_user_and_assignment_actions_create_audit_rows(client) -> None:
    login_owner(client)
    perform_representative_admin_mutations(client)
    with Session(get_engine()) as session:
        actions = {row.action for row in session.exec(select(AuditEvent)).all()}
    assert {"bot.publish", "bot.pause", "knowledge.credential_replace", "user.create", "conversation.assign"} <= actions
```

Implement `perform_representative_admin_mutations()` with actual route calls/fakes for external operations; do not insert AuditEvent directly in this integration test.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_audit.py
```

- [ ] **Step 3: Implement recursive sanitizer and recorder**

Case-fold dictionary keys before checking `SENSITIVE_KEYS`; recurse through dictionaries/lists/tuples. For opaque strings, do not attempt broad regex content inspection that could corrupt normal summaries; callers must pass structured before/after snapshots that exclude raw secret-bearing exception text.

- [ ] **Step 4: Instrument critical actions**

At minimum record:

```text
bot.publish
bot.pause / bot.resume / bot.archive
knowledge.create / knowledge.update / knowledge.credential_replace / knowledge.disable
user.create / user.update / user.password_reset / user.bot_access
conversation.assign / conversation.take / conversation.return_to_bot / conversation.close
incident.acknowledge
```

Audit conversation actions using conversation ID and state transition summary only; do not copy full message text.

- [ ] **Step 5: Add Owner/Admin audit page**

`GET /audit` filters by action, actor, Bot, and date. Render actor email, action, target, summary, timestamp, and sanitized before/after expanders. Operators receive 403.

- [ ] **Step 6: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_audit.py tests/test_authorization.py tests/test_user_admin.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/chatbot_manager/audit.py apps/api/chatbot_manager/admin apps/api/chatbot_manager/templates/audit.html tests/test_audit.py
git commit -m "feat: add sanitized administrative audit trail"
```

---

### Task 6: Add Archive and Retention Controls

**Files:**
- Create: `apps/api/chatbot_manager/retention.py`
- Modify: `apps/api/chatbot_manager/settings.py`
- Modify: `.env.example`
- Modify: `apps/api/chatbot_manager/admin/bots.py`
- Modify: `apps/api/chatbot_manager/admin/knowledge_services.py`
- Create: `tests/test_retention.py`

**Interfaces:**
- Produces `archive_bot()`, `disable_knowledge_service()`, and bounded retention cleanup methods that never remove active production dependencies.

- [ ] **Step 1: Write failing archive/dependency tests**

```python
def test_active_bot_must_pause_before_archive(client) -> None:
    with Session(get_engine()) as session:
        bot = create_active_bot(session)
        with pytest.raises(RetentionError, match="bot_must_be_paused"):
            RetentionService(session).archive_bot(bot.id, actor_user_id=1)


def test_referenced_knowledge_service_cannot_be_removed(client) -> None:
    with Session(get_engine()) as session:
        service, bot = create_live_bot_bound_to_service(session)
        with pytest.raises(RetentionError, match="knowledge_service_in_use"):
            RetentionService(session).remove_knowledge_service(service.id)


def test_conversation_retention_never_deletes_open_conversations(client) -> None:
    with Session(get_engine()) as session:
        old_closed = create_conversation(session, status="closed", age_days=400)
        old_open = create_conversation(session, status="bot_active", age_days=400)
        result = RetentionService(session).purge_closed_conversations(older_than_days=365)
        assert result.deleted_conversations == 1
        assert session.get(Conversation, old_closed.id) is None
        assert session.get(Conversation, old_open.id) is not None
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_retention.py
```

- [ ] **Step 3: Add configurable retention settings**

```python
conversation_retention_days: int = 365
incident_retention_days: int = 730
audit_retention_days: int = 1095
```

Document these settings in `.env.example`. Do not schedule destructive purges automatically in this phase; provide explicit service/admin maintenance action first so operators can validate policy.

- [ ] **Step 4: Implement archive/dependency protection**

`archive_bot()` requires paused state, sets `lifecycle_status="archived"`, disables associated channel connections, and preserves versions/conversations/tests/audit rows. `remove_knowledge_service()` rejects any reference from Live or Draft config history; normal UI offers `Disable` rather than hard delete when historical references exist.

- [ ] **Step 5: Implement explicit retention purge methods**

Only closed conversations older than threshold are eligible. Delete child messages/decisions/handoff events in one transaction before parent conversation. Incident/audit purge uses separate thresholds. Each purge returns counts and creates one audit event with counts only.

- [ ] **Step 6: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_retention.py tests/test_audit.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/chatbot_manager/retention.py apps/api/chatbot_manager/settings.py .env.example apps/api/chatbot_manager/admin/bots.py apps/api/chatbot_manager/admin/knowledge_services.py tests/test_retention.py
git commit -m "feat: add archive and retention controls"
```

---

### Task 7: Complete Credential and Redaction Hardening

**Files:**
- Modify: `apps/api/chatbot_manager/channel_connections.py`
- Modify: `apps/api/chatbot_manager/channel_config.py` only for legacy migration compatibility.
- Modify: `apps/api/chatbot_manager/admin/knowledge_services.py`
- Modify: `apps/api/chatbot_manager/runtime/delivery.py`
- Modify: `apps/api/chatbot_manager/knowledge/client.py`
- Modify: `tests/test_secret_encryption.py`
- Modify: `tests/test_failure_handling.py`
- Create: `tests/test_redaction_boundaries.py`

**Interfaces:**
- Consumes: common `Credential` and audit sanitizer.
- Produces no plaintext secret storage on active ChannelConnection/KnowledgeService paths.

- [ ] **Step 1: Write failing secret-boundary tests**

`tests/test_redaction_boundaries.py` must exercise real route/service error paths with fake exceptions containing known sentinel secrets:

```python
def test_channel_delivery_exception_never_persists_or_renders_secret(client, monkeypatch) -> None:
    secret = "channel-secret-sentinel"
    configure_channel_with_secret(secret)
    make_provider_send_raise(monkeypatch, f"failure containing {secret}")
    trigger_operator_reply(client)
    assert secret not in rendered_conversation_page(client)
    assert secret not in serialized_recent_audit_events()


def test_knowledge_client_exception_never_exposes_api_key(client, monkeypatch) -> None:
    secret = "rag-key-sentinel"
    configure_knowledge_service(secret)
    make_http_transport_fail(monkeypatch, f"request failed with {secret}")
    response = trigger_test_connection(client)
    assert secret not in response.text
    assert secret not in serialized_recent_audit_events()
```

Implement the helper functions in the test module using existing service factories and DB queries; no placeholder bodies.

- [ ] **Step 2: Verify RED or confirm existing behavior where already hardened**

```bash
rtk uv run pytest -q tests/test_redaction_boundaries.py tests/test_secret_encryption.py tests/test_failure_handling.py
```

At least the new cross-layer tests must initially fail until all paths are instrumented; if one case already passes, keep it as a regression test and continue with the failing boundary.

- [ ] **Step 3: Remove active-path legacy credential fallback**

After `ChannelConnection` migration is proven complete, runtime/admin channel operations read credentials only from `Credential`. Keep legacy `channel_config.py` only as a migration reader until Task 8 removes it.

- [ ] **Step 4: Standardize safe external-error mapping**

All provider/Knowledge client exceptions map to stable codes. Log only IDs/status/exception class; never `str(exc)` when an upstream exception can embed headers, URLs, or tokens.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_redaction_boundaries.py tests/test_secret_encryption.py tests/test_failure_handling.py tests/test_webhook_security.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/channel_connections.py apps/api/chatbot_manager/channel_config.py apps/api/chatbot_manager/admin/knowledge_services.py apps/api/chatbot_manager/runtime/delivery.py apps/api/chatbot_manager/knowledge/client.py tests/test_secret_encryption.py tests/test_failure_handling.py tests/test_redaction_boundaries.py
git commit -m "security: harden credential and redaction boundaries"
```

---

### Task 8: Remove Obsolete Single-Assistant and Local-RAG Product Paths

**Files:**
- Modify/remove only after dependency proof:
  - `apps/api/chatbot_manager/admin/routes.py`
  - legacy templates `assistant.html`, `rules.html`, `test_chat.html`, `logs.html`, `knowledge.html`, `knowledge_graph.html`
  - `apps/api/chatbot_manager/rag/service.py` and `rag/__init__.py`
  - legacy `Channel`, `Rule`, `AssistantSettings`, `KnowledgeDocument`, `ChatEvent` model classes only when migration/history consumers are gone.
  - `apps/api/chatbot_manager/channel_config.py` if no active consumer remains.
  - `pyproject.toml`, `uv.lock` for unused dependencies.
- Modify existing tests to the final product surface.
- Create `tests/test_legacy_cleanup.py`.

**Interfaces:**
- Consumes: all replacement paths from Phases 1–6.
- Produces final bot-centric codebase with no active in-process RAG/document manager or single-assistant runtime dependency.

- [ ] **Step 1: Capture dependency proof before deletion**

Run repository searches and save the exact output in the task notes/HANDOFF:

```bash
rtk rg -n "AssistantSettings|KnowledgeDocument|rag_service_from_assistant|RagAnythingService|Knowledge Graph|/knowledge-graph|/knowledge\b|/assistant\b|/rules\b|/test-chat\b|/logs\b" apps tests README.md docs/operations.md
rtk rg -n "from raganything|import raganything|RAGAnything|python-telegram-bot|telegram\.ext|telegram\.Bot" apps tests pyproject.toml
```

Classify every match as active replacement path, migration-only code, historical documentation, or obsolete code. Do not delete a model/table merely because its UI is gone if data migration into the new durable model has not been verified.

- [ ] **Step 2: Add failing final-surface tests**

Create `tests/test_legacy_cleanup.py`:

```python
def test_removed_legacy_admin_pages_are_not_routes(client) -> None:
    login_owner(client)
    for path in ("/assistant", "/rules", "/test-chat", "/logs", "/knowledge", "/knowledge-graph"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404


def test_primary_product_has_no_local_knowledge_upload_form(client) -> None:
    login_owner(client)
    for path in ("/", "/bots", "/knowledge-services"):
        html = client.get(path).text
        assert 'type="file"' not in html
        assert "Reindex" not in html


def test_production_runtime_does_not_import_in_process_rag_service() -> None:
    source = Path("apps/api/chatbot_manager").rglob("*.py")
    active_files = [path for path in source if "rag/service.py" not in path.as_posix()]
    text = "\n".join(path.read_text(encoding="utf-8") for path in active_files)
    assert "rag_service_from_assistant" not in text
    assert "RagAnythingService" not in text
```

- [ ] **Step 3: Verify RED before cleanup**

```bash
rtk uv run pytest -q tests/test_legacy_cleanup.py
```

Expected: legacy routes/imports still exist.

- [ ] **Step 4: Remove legacy admin routes/templates and in-process RAG code**

Delete only the obsolete route functions and their templates. Keep login/logout in focused auth/admin modules. Remove local upload/reindex/delete/graph APIs. Remove in-process `rag/service.py` after all imports use `knowledge/client.py`.

Before removing legacy table classes, verify all required historical data is present in replacements:

```text
AssistantSettings -> published BotConfigVersion
Rule             -> BotConfigRule
Channel          -> ChannelConnection + Credential
ChatEvent        -> historical Conversation/Message/Decision migration or explicit retained archive policy
KnowledgeDocument-> external RAG owns document lifecycle; no production dependency remains
```

If historical `ChatEvent` rows were not transformed in an earlier phase, run a one-time idempotent migration into historical `Conversation`/`ConversationMessage` records before dropping the class. Do not discard them.

- [ ] **Step 5: Remove unused dependencies only after source search**

If no active import remains:

```bash
rtk uv remove raganything
```

Run the second search again. Remove `python-telegram-bot` only if no source/test/tooling dependency imports it; current provider adapter uses `httpx`, so the search result decides rather than assumption.

- [ ] **Step 6: Verify final cleanup tests**

```bash
rtk uv run pytest -q tests/test_legacy_cleanup.py tests/test_admin_routes.py tests/test_webhooks.py tests/test_bot_runtime.py
rtk uv lock --check
```

- [ ] **Step 7: Commit cleanup**

Stage only the paths actually proven obsolete:

```bash
git status --short
git add apps/api/chatbot_manager tests pyproject.toml uv.lock README.md docs/operations.md
git diff --cached --check
git commit -m "refactor: retire legacy single assistant rag paths"
```

Before committing, inspect `git diff --cached --name-only` and unstage any unrelated tool/config path.

---

### Task 9: Final Redesign Verification, Manual Role Check, and Handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: all six implementation phases.
- Produces the final verified product/documentation handoff.

- [ ] **Step 1: Update operating documentation**

Document only the final product surface:

```text
Command Center
Bots / Setup / Behavior / Knowledge / Channels / Test / Versions
Conversations / Human Handoff
Knowledge Services + external RAG WebUI
Analytics
Incidents
Users / Audit
Owner/Admin/Operator roles
backup responsibility split between CIFS and external RAG
retention/archive behavior
```

Remove instructions that tell admins to upload/reindex local knowledge in CIFS.

- [ ] **Step 2: Run focused security/role verification**

```bash
rtk uv run pytest -q \
  tests/test_users.py \
  tests/test_authorization.py \
  tests/test_user_admin.py \
  tests/test_audit.py \
  tests/test_retention.py \
  tests/test_redaction_boundaries.py \
  tests/test_legacy_cleanup.py
```

- [ ] **Step 3: Run full fresh verification**

```bash
rtk uv run pytest -q
rtk python -m compileall -q apps/api
rtk uv lock --check
git diff --check
```

Record exact test count, failures/warnings, and command exit status. Do not reuse older counts.

- [ ] **Step 4: Manual browser/authorization checkpoint**

Verify with three actual test users:

```text
Owner: Users/System/Audit + all Bots/Inbox/Knowledge/Analytics available
Admin: Bot create/edit/test/publish + Knowledge/Analytics/Incidents available; Users denied
Operator assigned Bot A: Bot A Inbox/reply/analytics available; Bot B direct URLs return 403; config/publish denied
```

Then verify one end-to-end Bot flow:

```text
provider message -> Bot Runtime -> external RAG -> provider reply
escalate -> Unified Inbox -> Operator Take -> Bot silent -> human reply -> Return to Bot
Draft edit -> regression -> publish -> new message uses new Live version
simulated dependency failure -> one incident -> alert policy -> recovery
```

- [ ] **Step 5: Final dependency and secret scan**

```bash
rtk rg -n "AssistantSettings|KnowledgeDocument|RagAnythingService|rag_service_from_assistant" apps tests
rtk rg -n "raganything" apps tests pyproject.toml
rtk rg -n "api_key|access_token|channel_secret|app_secret|bot_token|webhook_secret" apps/api/chatbot_manager
```

Expected: legacy-type searches have no active matches except intentional migration/history fixtures documented in HANDOFF; secret-name matches are structured fields/redaction lists/configuration code, not hard-coded secret values or unsafe logging.

- [ ] **Step 6: Update `HANDOFF.md` before completion**

Record:

```text
all six phases and key commits
final current architecture and primary routes
external RAG deployment/version used in validation
fresh focused/full test results
manual Owner/Admin/Operator results
manual end-to-end provider/RAG/handoff/publish/incident results
retention/archive defaults
remaining non-goals and any deferred real-provider smoke checks
current Git status/divergence
exact next operational action, if any
```

This HANDOFF update is mandatory before declaring the redesign complete.

- [ ] **Step 7: Commit final docs/handoff**

```bash
git add README.md docs/operations.md HANDOFF.md
git diff --cached --check
git commit -m "docs: finalize chatbot operations console handoff"
```

Do not push unless the user explicitly requests a push or previously approved push for this exact integration step.
