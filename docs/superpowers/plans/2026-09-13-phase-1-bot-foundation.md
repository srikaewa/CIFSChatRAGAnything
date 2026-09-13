# Phase 1 Bot Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the Bot/config-version foundation, migrate all current single-assistant data into a `Default Bot`, and expose a minimal Bot-centric workspace without changing current production chatbot behavior.

**Architecture:** Add new SQLModel tables alongside legacy `AssistantSettings`/`Rule` tables, use an idempotent bootstrap service to copy current configuration into an immutable published v1, and attach current channels/events to the Default Bot. Keep webhook/runtime reads on the legacy models in Phase 1 so migration can be verified independently before the runtime switch in Phase 3.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Preserve current provider/runtime behavior in Phase 1; no webhook decision-path switch yet.
- Do not delete or repurpose `AssistantSettings`, `Rule`, `Channel`, `ChatEvent`, `KnowledgeDocument`, or in-process RAG code in this phase.
- First migrated configuration is immutable/published and belongs to `Default Bot`.
- Knowledge binding field exists on `BotConfigVersion` but remains nullable until Phase 2.
- Keep migrations idempotent on both fresh SQLite databases and existing databases.
- Do not introduce Alembic solely for this phase; follow the repository's existing `init_db()` + bounded SQLite migration pattern.
- No Bot Draft editing UI is exposed yet; the initial workspace is status/read-only so administrators cannot create config that production ignores.
- Use TDD for every behavior change and preserve unrelated files.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `Bot`, `BotConfigVersion`, `BotConfigRule`; add nullable `bot_id` to legacy `Channel` and `ChatEvent` for migration context.
- Modify `apps/api/chatbot_manager/db.py`: add SQLite columns and invoke idempotent Bot bootstrap after schema creation.
- Create `apps/api/chatbot_manager/bots/__init__.py`: export Bot service functions.
- Create `apps/api/chatbot_manager/bots/service.py`: Default Bot bootstrap and config lookup helpers.
- Create `apps/api/chatbot_manager/admin/dependencies.py`: shared templates/auth/CSRF dependencies extracted from the large legacy router.
- Create `apps/api/chatbot_manager/admin/bots.py`: Bot list and workspace routes.
- Modify `apps/api/chatbot_manager/admin/routes.py`: import shared dependencies; no unrelated route rewrite.
- Modify `apps/api/chatbot_manager/admin/__init__.py`: compose legacy + Bot routers.
- Modify `apps/api/chatbot_manager/templates/base.html`: add `Bots` navigation while retaining legacy pages during migration.
- Create `apps/api/chatbot_manager/templates/bots.html`: Bot fleet list.
- Create `apps/api/chatbot_manager/templates/bot_overview.html`: read-only migrated Bot workspace/overview.
- Modify `apps/api/chatbot_manager/static/styles.css`: only styles needed by new Bot cards/status chips.
- Create `tests/test_bot_foundation.py`: schema/bootstrap/idempotency/migration tests.
- Create `tests/test_bot_admin.py`: Bot list/workspace/auth tests.
- Modify `tests/test_models.py`: construction/default assertions for new entities.

---

### Task 1: Define Bot and Version Models

**Files:**
- Modify: `apps/api/chatbot_manager/models.py:1-75`
- Modify: `tests/test_models.py`
- Create: `tests/test_bot_foundation.py`

**Interfaces:**
- Consumes: existing `utc_now()` and SQLModel conventions.
- Produces: `Bot`, `BotConfigVersion`, `BotConfigRule`, plus nullable `Channel.bot_id` and `ChatEvent.bot_id` used by Task 2 and later phases.

- [ ] **Step 1: Write failing model tests**

Create the first tests in `tests/test_bot_foundation.py`:

```python
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import Bot, BotConfigRule, BotConfigVersion


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_bot_config_models_round_trip() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Default Bot", description="Migrated bot", lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)

        version = BotConfigVersion(
            bot_id=bot.id,
            version_number=1,
            status="published",
            system_prompt="Use available knowledge.",
            fallback_reply="Ask staff.",
            tone="professional",
            language="auto",
            response_style="concise",
            fallback_policy="reply",
            escalation_policy="{}",
            created_by="migration",
        )
        session.add(version)
        session.commit()
        session.refresh(version)

        session.add(
            BotConfigRule(
                config_version_id=version.id,
                name="price",
                priority=10,
                match_type="contains",
                pattern="price",
                condition_logic="and",
                conditions="[]",
                action="RESPOND",
                reply_text="Price is 100.",
            )
        )
        session.commit()

        stored_bot = session.exec(select(Bot)).one()
        stored_version = session.exec(select(BotConfigVersion)).one()
        stored_rule = session.exec(select(BotConfigRule)).one()

    assert stored_bot.lifecycle_status == "active"
    assert stored_version.knowledge_service_id is None
    assert stored_version.status == "published"
    assert stored_rule.action == "RESPOND"
```

Add to `tests/test_models.py`:

```python
def test_legacy_channel_and_event_allow_bot_link() -> None:
    channel = Channel(provider="line", display_name="LINE", bot_id=7)
    event = ChatEvent(provider="line", incoming_text="hello", decision_source="rule", bot_id=7)
    assert channel.bot_id == 7
    assert event.bot_id == 7
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
rtk uv run pytest -q tests/test_bot_foundation.py tests/test_models.py
```

Expected: import/constructor failures because the new models/fields do not exist.

- [ ] **Step 3: Add the minimal models**

Add to `apps/api/chatbot_manager/models.py`:

```python
class Bot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    lifecycle_status: str = Field(default="draft", index=True)
    live_config_version_id: Optional[int] = Field(default=None, index=True)
    draft_config_version_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BotConfigVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    version_number: int = Field(index=True)
    status: str = Field(default="draft", index=True)
    system_prompt: str = ""
    tone: str = "professional"
    language: str = "auto"
    response_style: str = "concise"
    fallback_reply: str = ""
    fallback_policy: str = "reply"
    escalation_policy: str = "{}"
    custom_instructions: str = ""
    knowledge_service_id: Optional[int] = Field(default=None, index=True)
    created_by: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    published_at: Optional[datetime] = None


class BotConfigRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    config_version_id: int = Field(index=True)
    name: str = ""
    enabled: bool = True
    priority: int = Field(default=100, index=True)
    match_type: str = "contains"
    pattern: str = Field(index=True)
    condition_logic: str = "and"
    conditions: str = "[]"
    action: str = "RESPOND"
    reply_text: str = ""
    escalate_message: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

Add `bot_id: Optional[int] = Field(default=None, index=True)` to both legacy `Channel` and `ChatEvent`.

- [ ] **Step 4: Run the tests and verify GREEN**

```bash
rtk uv run pytest -q tests/test_bot_foundation.py tests/test_models.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/models.py tests/test_models.py tests/test_bot_foundation.py
git commit -m "feat: add bot configuration models"
```

---

### Task 2: Bootstrap and Migrate the Default Bot

**Files:**
- Create: `apps/api/chatbot_manager/bots/__init__.py`
- Create: `apps/api/chatbot_manager/bots/service.py`
- Modify: `apps/api/chatbot_manager/db.py:37-160`
- Modify: `tests/test_bot_foundation.py`

**Interfaces:**
- Consumes: models from Task 1 and legacy `AssistantSettings`, `Rule`, `Channel`, `ChatEvent`.
- Produces:
  - `DEFAULT_BOT_NAME = "Default Bot"`
  - `ensure_default_bot(session: Session) -> Bot`
  - `get_live_config(session: Session, bot_id: int) -> BotConfigVersion`
  - `ensure_draft_config(session: Session, bot_id: int, actor: str) -> BotConfigVersion` (used beginning Phase 2; it clones published scalar config + rules but is not exposed in Phase 1 UI).

- [ ] **Step 1: Add failing migration/idempotency tests**

Append to `tests/test_bot_foundation.py`:

```python
from chatbot_manager.bots.service import ensure_default_bot, get_live_config
from chatbot_manager.models import AssistantSettings, Channel, ChatEvent, Rule


def test_default_bot_migrates_legacy_configuration_once() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AssistantSettings(system_prompt="Legacy prompt", fallback_reply="Legacy fallback"))
        session.add(Rule(priority=5, pattern="human", match_type="contains", reply_text="Internal", escalate=True, escalate_message="Staff soon"))
        session.add(Channel(provider="line", display_name="LINE", enabled=True))
        session.add(ChatEvent(provider="line", incoming_text="hello", decision_source="fallback"))
        session.commit()

        first = ensure_default_bot(session)
        second = ensure_default_bot(session)
        live = get_live_config(session, first.id)
        rules = session.exec(select(BotConfigRule).where(BotConfigRule.config_version_id == live.id)).all()
        channel = session.exec(select(Channel)).one()
        event = session.exec(select(ChatEvent)).one()

    assert first.id == second.id
    assert live.version_number == 1
    assert live.status == "published"
    assert live.system_prompt == "Legacy prompt"
    assert live.fallback_reply == "Legacy fallback"
    assert len(rules) == 1
    assert rules[0].action == "ESCALATE"
    assert rules[0].escalate_message == "Staff soon"
    assert channel.bot_id == first.id
    assert event.bot_id == first.id
```

Add a clone test:

```python
def test_ensure_draft_config_clones_live_config_and_rules_once() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AssistantSettings(system_prompt="Live prompt"))
        session.add(Rule(pattern="price", reply_text="100"))
        session.commit()
        bot = ensure_default_bot(session)
        draft1 = ensure_draft_config(session, bot.id, "admin@example.local")
        draft2 = ensure_draft_config(session, bot.id, "admin@example.local")
        copied = session.exec(select(BotConfigRule).where(BotConfigRule.config_version_id == draft1.id)).all()

    assert draft1.id == draft2.id
    assert draft1.status == "draft"
    assert draft1.version_number == 2
    assert len(copied) == 1
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_foundation.py
```

Expected: missing `chatbot_manager.bots.service` / helper failures.

- [ ] **Step 3: Implement the service**

`apps/api/chatbot_manager/bots/__init__.py`:

```python
from .service import ensure_default_bot, ensure_draft_config, get_live_config

__all__ = ["ensure_default_bot", "ensure_draft_config", "get_live_config"]
```

Implement `apps/api/chatbot_manager/bots/service.py` with this behavior:

```python
DEFAULT_BOT_NAME = "Default Bot"


def ensure_default_bot(session: Session) -> Bot:
    existing = session.exec(select(Bot).where(Bot.name == DEFAULT_BOT_NAME)).first()
    if existing is not None:
        _attach_legacy_rows(session, existing.id)
        return existing

    legacy = session.get(AssistantSettings, 1) or AssistantSettings()
    bot = Bot(name=DEFAULT_BOT_NAME, description="Migrated from the original single-assistant configuration", lifecycle_status="active")
    session.add(bot)
    session.commit()
    session.refresh(bot)

    version = BotConfigVersion(
        bot_id=bot.id,
        version_number=1,
        status="published",
        system_prompt=legacy.system_prompt,
        fallback_reply=legacy.fallback_reply,
        tone="professional",
        language="auto",
        response_style="concise",
        fallback_policy="reply",
        escalation_policy="{}",
        created_by="migration",
        published_at=utc_now(),
    )
    session.add(version)
    session.commit()
    session.refresh(version)

    for legacy_rule in session.exec(select(Rule).order_by(Rule.priority)).all():
        session.add(BotConfigRule(
            config_version_id=version.id,
            name=legacy_rule.pattern,
            enabled=legacy_rule.enabled,
            priority=legacy_rule.priority,
            match_type=legacy_rule.match_type,
            pattern=legacy_rule.pattern,
            condition_logic=legacy_rule.condition_logic,
            conditions=legacy_rule.conditions,
            action="ESCALATE" if legacy_rule.escalate else "RESPOND",
            reply_text=legacy_rule.reply_text,
            escalate_message=legacy_rule.escalate_message,
        ))

    bot.live_config_version_id = version.id
    bot.updated_at = utc_now()
    session.add(bot)
    _attach_legacy_rows(session, bot.id)
    session.commit()
    return bot
```

`_attach_legacy_rows()` sets null `Channel.bot_id` and `ChatEvent.bot_id` to the Default Bot. `get_live_config()` rejects missing Bot/live config with `LookupError`. `ensure_draft_config()` clones the current live scalar fields and all `BotConfigRule` rows, assigns `version_number = max(existing)+1`, stores `draft_config_version_id`, and returns an existing draft unchanged when one already exists.

- [ ] **Step 4: Add SQLite ALTER migration and startup bootstrap**

In `apps/api/chatbot_manager/db.py`, add an idempotent `_migrate_sqlite_bot_links()` before bootstrap:

```python
def _migrate_sqlite_bot_links(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return
    with active_engine.begin() as connection:
        for table in ("channel", "chatevent"):
            columns = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})")).all()}
            if "bot_id" not in columns:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN bot_id INTEGER"))
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_bot_id ON {table} (bot_id)"))
```

At the end of `init_db()`:

```python
    _migrate_sqlite_bot_links(active_engine)
    from .bots.service import ensure_default_bot
    with Session(active_engine) as session:
        ensure_default_bot(session)
```

- [ ] **Step 5: Verify focused + migration behavior**

```bash
rtk uv run pytest -q tests/test_bot_foundation.py tests/test_models.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/bots apps/api/chatbot_manager/db.py tests/test_bot_foundation.py
git commit -m "feat: migrate current data into default bot"
```

---

### Task 3: Extract Shared Admin Dependencies and Add Bot Workspace Routes

**Files:**
- Create: `apps/api/chatbot_manager/admin/dependencies.py`
- Create: `apps/api/chatbot_manager/admin/bots.py`
- Modify: `apps/api/chatbot_manager/admin/routes.py:1-142`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `tests/test_bot_admin.py`

**Interfaces:**
- Consumes: `ensure_default_bot()`, `get_live_config()` from Task 2.
- Produces: authenticated `GET /bots` and `GET /bots/{bot_id}` routes without altering existing legacy URLs.

- [ ] **Step 1: Write failing route tests**

`tests/test_bot_admin.py`:

```python
from fastapi.testclient import TestClient


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_bots_requires_login(client: TestClient) -> None:
    response = client.get("/bots", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_bots_lists_migrated_default_bot(client: TestClient) -> None:
    login(client)
    response = client.get("/bots")
    assert response.status_code == 200
    assert "Default Bot" in response.text
    assert "Active" in response.text


def test_bot_overview_shows_live_version(client: TestClient) -> None:
    login(client)
    response = client.get("/bots/1")
    assert response.status_code == 200
    assert "Default Bot" in response.text
    assert "Live v1" in response.text
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_admin.py
```

Expected: 404 for Bot routes.

- [ ] **Step 3: Extract only shared dependencies**

Create `apps/api/chatbot_manager/admin/dependencies.py` containing the existing `templates`, `require_admin()`, and `require_csrf()` behavior. Keep function signatures compatible so current routes/tests do not change semantics:

```python
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.globals["csrf_token_for"] = make_csrf_token


def require_admin(request: Request) -> str:
    settings = get_settings()
    email = read_session_token(request.cookies.get("admin_session"), settings.admin_session_max_age_seconds)
    if not email:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return email


def require_csrf(csrf_token: str = Form(""), admin_email: str = Depends(require_admin)) -> str:
    if not verify_csrf_token(csrf_token, admin_email):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return admin_email
```

Update `admin/routes.py` imports to use these definitions; do not move unrelated routes.

- [ ] **Step 4: Implement Bot routes**

`admin/bots.py` should query Bots and live versions and render the new templates:

```python
router = APIRouter(prefix="/bots")


@router.get("", response_class=HTMLResponse)
def bots_page(request: Request, admin_email: str = Depends(require_admin), session: Session = Depends(get_session)) -> Response:
    bots = session.exec(select(Bot).order_by(Bot.name)).all()
    return templates.TemplateResponse(request, "bots.html", {
        "admin_email": admin_email,
        "active_page": "bots",
        "bots": bots,
    })


@router.get("/{bot_id}", response_class=HTMLResponse)
def bot_overview(bot_id: int, request: Request, admin_email: str = Depends(require_admin), session: Session = Depends(get_session)) -> Response:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    live = get_live_config(session, bot_id) if bot.live_config_version_id else None
    return templates.TemplateResponse(request, "bot_overview.html", {
        "admin_email": admin_email,
        "active_page": "bots",
        "bot": bot,
        "live": live,
    })
```

In `admin/__init__.py`, compose routers without changing `main.py`:

```python
from fastapi import APIRouter
from .routes import router as legacy_router
from .bots import router as bots_router

router = APIRouter()
router.include_router(legacy_router)
router.include_router(bots_router)

__all__ = ["router"]
```

- [ ] **Step 5: Verify GREEN and existing auth routes**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_admin_routes.py tests/test_csrf_security.py tests/test_session_security.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin tests/test_bot_admin.py
git commit -m "feat: add bot workspace routes"
```

---

### Task 4: Add Minimal Bot-Centric UI Without Removing Legacy Admin Pages

**Files:**
- Modify: `apps/api/chatbot_manager/templates/base.html:10-21`
- Create: `apps/api/chatbot_manager/templates/bots.html`
- Create: `apps/api/chatbot_manager/templates/bot_overview.html`
- Modify: `apps/api/chatbot_manager/static/styles.css`
- Modify: `tests/test_bot_admin.py`

**Interfaces:**
- Consumes: Bot routes from Task 3.
- Produces: read-only Bot fleet + workspace checkpoint while legacy Assistant/Rules/Channels remain available until later phases.

- [ ] **Step 1: Add failing UI assertions**

Append:

```python
def test_global_navigation_exposes_bots(client: TestClient) -> None:
    login(client)
    html = client.get("/").text
    assert 'href="/bots"' in html
    assert ">Bots<" in html


def test_bot_overview_is_read_only_in_phase_one(client: TestClient) -> None:
    login(client)
    html = client.get("/bots/1").text
    assert "Live v1" in html
    assert "System prompt" in html
    assert "Phase 1 migration view" in html
    assert "Save Draft" not in html
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_admin.py
```

- [ ] **Step 3: Implement templates**

`bots.html` must render each Bot name, lifecycle state, and workspace link. `bot_overview.html` must render name, description, lifecycle state, live version number/status, prompt/fallback summary, and a clear `Phase 1 migration view` note; it must not expose edit forms yet.

Use semantic markup such as:

```html
<section class="page-head">
  <h1>Bots</h1>
  <p>Manage chatbot workspaces. Phase 1 shows the migrated production Bot without changing live behavior.</p>
</section>

{% for bot in bots %}
<article class="panel bot-card">
  <div>
    <h2>{{ bot.name }}</h2>
    <span class="status-chip">{{ bot.lifecycle_status|title }}</span>
  </div>
  <a class="action-btn" href="/bots/{{ bot.id }}">Open Workspace</a>
</article>
{% endfor %}
```

Add only necessary `.bot-card`, `.status-chip`, and workspace header styles to `styles.css`; preserve existing responsive/focus behavior.

- [ ] **Step 4: Verify UI tests and old pages**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_admin_routes.py tests/test_phase5_admin_operations.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/templates apps/api/chatbot_manager/static/styles.css tests/test_bot_admin.py
git commit -m "feat: add default bot workspace UI"
```

---

### Task 5: Phase 1 Regression Verification and Manual Checkpoint

**Files:**
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: verified migration checkpoint and explicit next-phase state.

- [ ] **Step 1: Run focused migration/runtime compatibility tests**

```bash
rtk uv run pytest -q \
  tests/test_bot_foundation.py \
  tests/test_bot_admin.py \
  tests/test_webhooks.py \
  tests/test_telegram_webhooks.py \
  tests/test_chatbot_engine.py \
  tests/test_secret_encryption.py
```

Expected: PASS with existing webhook replies still using the legacy engine/settings path.

- [ ] **Step 2: Run full verification**

```bash
rtk uv run pytest -q
rtk python -m compileall -q apps/api
rtk uv lock --check
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Manual checkpoint**

Run the local app and verify:

```bash
DATABASE_URL=sqlite:///./data/chatbot.sqlite3 rtk uv run uvicorn chatbot_manager.main:app --app-dir apps/api --host 127.0.0.1 --port 8001
```

Verify in browser:

```text
/bots lists Default Bot
/bots/<id> shows Live v1 and migrated prompt/fallback
existing /channels, /rules, /assistant, /test-chat still load
one configured provider path still replies exactly as before
```

Do not proceed if existing production behavior changed unexpectedly.

- [ ] **Step 4: Update handoff**

Record in `HANDOFF.md`:

```text
Phase 1 completed tasks and commits
Default Bot ID / live version observed
migration verification results
full suite result
manual checkpoint result
data or compatibility caveats
next plan: docs/superpowers/plans/2026-09-13-phase-2-external-knowledge-service.md
```

- [ ] **Step 5: Commit the checkpoint**

```bash
git add HANDOFF.md
git commit -m "docs: hand off bot foundation phase"
```
