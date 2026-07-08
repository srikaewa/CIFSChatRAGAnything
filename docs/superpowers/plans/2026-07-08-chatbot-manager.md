# Chatbot Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI Chatbot Manager where a business admin can configure LINE/Messenger, edit rules, upload RAG-Anything knowledge, test chat behavior, and inspect logs.

**Architecture:** Use one FastAPI app with Jinja templates, SQLite via SQLModel, provider adapters for LINE and Messenger, and a rule-first chatbot engine with RAG-Anything fallback. Keep external APIs behind small interfaces so tests can use fakes and webhook behavior can be verified without real credentials.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel, Jinja2, httpx, pytest, RAG-Anything (`raganything`), SQLite, vanilla HTML/CSS/JavaScript.

---

## Execution Notes

- Current workspace has an invalid `.git` directory. Commit steps are included because this plan is designed for normal repo execution, but they will fail until Git is initialized or repaired.
- All shell commands use `rtk` per project instruction.
- Run each task independently. Do not start Task N+1 until tests in Task N pass.
- RAG-Anything is wrapped behind `RagService` so local tests do not need MinerU downloads, LLM keys, or document parser models.

## File Structure

Create these files:

- `pyproject.toml`: project metadata, runtime dependencies, test dependencies.
- `README.md`: local setup and run commands.
- `.gitignore`: Python, SQLite, uploaded files, RAG working directory, visual companion files.
- `apps/api/chatbot_manager/__init__.py`: package marker.
- `apps/api/chatbot_manager/main.py`: FastAPI app factory and route registration.
- `apps/api/chatbot_manager/settings.py`: environment settings.
- `apps/api/chatbot_manager/db.py`: SQLModel engine/session/bootstrap.
- `apps/api/chatbot_manager/models.py`: database tables.
- `apps/api/chatbot_manager/security.py`: password hashing, login sessions, secret masking.
- `apps/api/chatbot_manager/chatbot/__init__.py`: chatbot package marker.
- `apps/api/chatbot_manager/chatbot/engine.py`: message decision pipeline.
- `apps/api/chatbot_manager/rag/__init__.py`: RAG package marker.
- `apps/api/chatbot_manager/rag/service.py`: RAG-Anything adapter and test fake.
- `apps/api/chatbot_manager/channels/__init__.py`: channel package marker.
- `apps/api/chatbot_manager/channels/line.py`: LINE signature validation, event parsing, reply sending.
- `apps/api/chatbot_manager/channels/messenger.py`: Messenger verification, event parsing, reply sending.
- `apps/api/chatbot_manager/admin/__init__.py`: admin package marker.
- `apps/api/chatbot_manager/admin/routes.py`: dashboard, CRUD forms, test chat, upload routes.
- `apps/api/chatbot_manager/templates/base.html`: shared admin layout.
- `apps/api/chatbot_manager/templates/login.html`: local admin login.
- `apps/api/chatbot_manager/templates/dashboard.html`: summary page.
- `apps/api/chatbot_manager/templates/channels.html`: channel setup/status page.
- `apps/api/chatbot_manager/templates/rules.html`: rules table and form.
- `apps/api/chatbot_manager/templates/knowledge.html`: document upload and index status.
- `apps/api/chatbot_manager/templates/assistant.html`: prompt/model/fallback settings.
- `apps/api/chatbot_manager/templates/test_chat.html`: simulated chat page.
- `apps/api/chatbot_manager/templates/logs.html`: event log page.
- `apps/api/chatbot_manager/static/styles.css`: admin UI styling.
- `tests/conftest.py`: test app and temporary SQLite setup.
- `tests/test_chatbot_engine.py`: command/rule/RAG/fallback pipeline tests.
- `tests/test_rag_service.py`: RAG wrapper tests with monkeypatching.
- `tests/test_line_channel.py`: LINE signature, parsing, reply tests.
- `tests/test_messenger_channel.py`: Messenger verify, parsing, reply tests.
- `tests/test_admin_routes.py`: auth, rule CRUD, assistant settings, test chat route tests.
- `tests/test_webhooks.py`: provider webhook route tests using mocked adapters.

## Task 1: Project Scaffold, Settings, Database

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `apps/api/chatbot_manager/__init__.py`
- Create: `apps/api/chatbot_manager/settings.py`
- Create: `apps/api/chatbot_manager/db.py`
- Create: `apps/api/chatbot_manager/main.py`
- Create: `tests/conftest.py`
- Test: `tests/test_app_boot.py`

- [ ] **Step 1: Write failing boot test**

Create `tests/test_app_boot.py`:

```python
from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Add project dependencies**

Create `pyproject.toml`:

```toml
[project]
name = "cifs-chatbot-manager"
version = "0.1.0"
description = "Local admin manager for LINE and Facebook Messenger chatbots with RAG-Anything."
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "sqlmodel>=0.0.22",
  "jinja2>=3.1.4",
  "python-multipart>=0.0.9",
  "httpx>=0.27.0",
  "itsdangerous>=2.2.0",
  "pwdlib[argon2]>=0.2.1",
  "pydantic-settings>=2.4.0",
  "raganything>=1.0.0"
]

[dependency-groups]
dev = [
  "pytest>=8.3.0",
  "pytest-asyncio>=0.24.0",
  "respx>=0.21.1"
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["apps/api"]
```

- [ ] **Step 3: Add ignore rules**

Create `.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.pyc
*.sqlite3
data/uploads/
data/rag/
.superpowers/
.env
```

- [ ] **Step 4: Add settings and database bootstrap**

Create `apps/api/chatbot_manager/__init__.py`:

```python
__all__ = ["create_app"]
```

Create `apps/api/chatbot_manager/settings.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_secret_key: str = Field(default="change-me-very-secret-jwt-key-minimum-32-chars")
    app_encryption_key: str = "local-dev-encryption-key-32-chars"
    admin_email: str = "admin@example.local"
    admin_password: str = "admin1234!"

    database_url: str = "sqlite:///./data/chatbot.sqlite3"
    dashboard_url: str = "http://localhost:8000"
    api_public_url: str = "http://localhost:8000"

    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    messenger_verify_token: str = ""
    messenger_page_access_token: str = ""
    messenger_app_secret: str = ""

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_default_model: str = "gpt-4o-mini"
    llm_vision_model: str = "gpt-4o-mini"

    rag_backend: str = "rag_anything"
    rag_working_dir: Path = Path("./data/rag")
    rag_parser: str = "mineru"
    rag_parse_method: str = "auto"
    upload_dir: Path = Path("./data/uploads")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `apps/api/chatbot_manager/db.py`:

```python
from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from .settings import Settings, get_settings


def make_engine(settings: Settings | None = None):
    loaded = settings or get_settings()
    if loaded.database_url.startswith("sqlite:///"):
        db_path = loaded.database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(loaded.database_url, connect_args={"check_same_thread": False})


engine = make_engine()


def init_db() -> None:
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
```

Create `apps/api/chatbot_manager/main.py`:

```python
from fastapi import FastAPI

from .db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot Manager")

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 5: Add test fixture**

Create `tests/conftest.py`:

```python
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from chatbot_manager.main import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
```

- [ ] **Step 6: Run boot test and verify failure before install is resolved**

Run:

```bash
rtk uv run pytest tests/test_app_boot.py -v
```

Expected first result before dependencies are synced: environment may report missing packages. After `uv sync`, expected result is PASS.

- [ ] **Step 7: Sync dependencies and rerun**

Run:

```bash
rtk uv sync
rtk uv run pytest tests/test_app_boot.py -v
```

Expected: `1 passed`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore README.md apps tests
git commit -m "feat: scaffold chatbot manager app"
```

## Task 2: Database Models

**Files:**
- Create: `apps/api/chatbot_manager/models.py`
- Modify: `apps/api/chatbot_manager/db.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/test_models.py`:

```python
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import AssistantSettings, Channel, ChatEvent, KnowledgeDocument, Rule


def test_rule_model_round_trip() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Rule(match_type="contains", pattern="price", reply_text="Our price list is here."))
        session.commit()
        rule = session.exec(select(Rule)).one()

    assert rule.enabled is True
    assert rule.priority == 100
    assert rule.pattern == "price"


def test_assistant_defaults() -> None:
    settings = AssistantSettings()
    assert settings.rag_enabled is True
    assert "don't have enough information" in settings.fallback_reply.lower()


def test_channel_document_and_event_models_construct() -> None:
    channel = Channel(provider="line", display_name="LINE")
    document = KnowledgeDocument(filename="menu.pdf", path="data/uploads/menu.pdf")
    event = ChatEvent(provider="messenger", incoming_text="hello", decision_source="fallback")

    assert channel.enabled is False
    assert document.status == "pending"
    assert event.reply_text == ""
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
rtk uv run pytest tests/test_models.py -v
```

Expected: FAIL because `chatbot_manager.models` does not exist.

- [ ] **Step 3: Implement models**

Create `apps/api/chatbot_manager/models.py`:

```python
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True, unique=True)
    enabled: bool = False
    display_name: str
    status: str = "not_configured"
    credential_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Rule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    enabled: bool = True
    priority: int = Field(default=100, index=True)
    match_type: str = "contains"
    pattern: str = Field(index=True)
    reply_text: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AssistantSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    system_prompt: str = "Answer as a helpful business assistant. Use the knowledge base when needed."
    fallback_reply: str = "I don't have enough information yet. Please contact staff."
    rag_enabled: bool = True
    llm_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    path: str
    status: str = "pending"
    error: str = ""
    indexed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    external_user_id: str = ""
    incoming_text: str
    decision_source: str
    reply_text: str = ""
    raw_event: str = "{}"
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 4: Ensure bootstrap imports models**

Confirm `apps/api/chatbot_manager/db.py` contains:

```python
def init_db() -> None:
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
```

- [ ] **Step 5: Run model tests**

Run:

```bash
rtk uv run pytest tests/test_models.py -v
```

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/db.py tests/test_models.py
git commit -m "feat: add chatbot manager data models"
```

## Task 3: Chatbot Engine

**Files:**
- Create: `apps/api/chatbot_manager/chatbot/__init__.py`
- Create: `apps/api/chatbot_manager/chatbot/engine.py`
- Test: `tests/test_chatbot_engine.py`

- [ ] **Step 1: Write failing chatbot engine tests**

Create `tests/test_chatbot_engine.py`:

```python
import pytest

from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput, Decision
from chatbot_manager.models import AssistantSettings, Rule
from chatbot_manager.rag.service import FakeRagService


@pytest.mark.asyncio
async def test_help_command_wins_before_rules() -> None:
    engine = ChatbotEngine(rag_service=FakeRagService(answer="RAG answer"))
    decision = await engine.answer(
        ChatbotInput(text="help", provider="line", external_user_id="u1"),
        rules=[Rule(pattern="help", reply_text="Rule help")],
        settings=AssistantSettings(),
    )
    assert decision == Decision(source="command", reply_text="Available commands: help, start.")


@pytest.mark.asyncio
async def test_exact_rule_wins_before_rag() -> None:
    engine = ChatbotEngine(rag_service=FakeRagService(answer="RAG answer"))
    decision = await engine.answer(
        ChatbotInput(text="PRICE", provider="messenger", external_user_id="u2"),
        rules=[Rule(match_type="exact", pattern="price", reply_text="Price is 100.")],
        settings=AssistantSettings(),
    )
    assert decision.source == "rule"
    assert decision.reply_text == "Price is 100."


@pytest.mark.asyncio
async def test_contains_rule_uses_priority_order() -> None:
    engine = ChatbotEngine(rag_service=FakeRagService(answer="RAG answer"))
    rules = [
        Rule(priority=50, match_type="contains", pattern="shipping", reply_text="Fast shipping."),
        Rule(priority=10, match_type="contains", pattern="ship", reply_text="General shipping."),
    ]
    decision = await engine.answer(
        ChatbotInput(text="Do you ship?", provider="line", external_user_id="u3"),
        rules=rules,
        settings=AssistantSettings(),
    )
    assert decision.reply_text == "General shipping."


@pytest.mark.asyncio
async def test_rag_used_when_no_rule_matches() -> None:
    engine = ChatbotEngine(rag_service=FakeRagService(answer="The warranty is 1 year."))
    decision = await engine.answer(
        ChatbotInput(text="What is warranty?", provider="line", external_user_id="u4"),
        rules=[],
        settings=AssistantSettings(rag_enabled=True),
    )
    assert decision == Decision(source="rag", reply_text="The warranty is 1 year.")


@pytest.mark.asyncio
async def test_fallback_used_when_rag_empty() -> None:
    engine = ChatbotEngine(rag_service=FakeRagService(answer=""))
    decision = await engine.answer(
        ChatbotInput(text="unknown", provider="line", external_user_id="u5"),
        rules=[],
        settings=AssistantSettings(rag_enabled=True, fallback_reply="Ask staff."),
    )
    assert decision == Decision(source="fallback", reply_text="Ask staff.")
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
rtk uv run pytest tests/test_chatbot_engine.py -v
```

Expected: FAIL because chatbot and RAG packages do not exist.

- [ ] **Step 3: Add RAG fake used by engine tests**

Create `apps/api/chatbot_manager/rag/__init__.py`:

```python
from .service import FakeRagService, RagService

__all__ = ["FakeRagService", "RagService"]
```

Create initial `apps/api/chatbot_manager/rag/service.py`:

```python
from dataclasses import dataclass


class RagService:
    async def answer(self, question: str, system_prompt: str) -> str:
        return ""


@dataclass
class FakeRagService(RagService):
    answer_text: str = ""

    def __init__(self, answer: str = "") -> None:
        self.answer_text = answer

    async def answer(self, question: str, system_prompt: str) -> str:
        return self.answer_text
```

- [ ] **Step 4: Implement chatbot engine**

Create `apps/api/chatbot_manager/chatbot/__init__.py`:

```python
from .engine import ChatbotEngine, ChatbotInput, Decision

__all__ = ["ChatbotEngine", "ChatbotInput", "Decision"]
```

Create `apps/api/chatbot_manager/chatbot/engine.py`:

```python
from dataclasses import dataclass

from chatbot_manager.models import AssistantSettings, Rule
from chatbot_manager.rag.service import RagService


@dataclass(frozen=True)
class ChatbotInput:
    text: str
    provider: str
    external_user_id: str


@dataclass(frozen=True)
class Decision:
    source: str
    reply_text: str


class ChatbotEngine:
    def __init__(self, rag_service: RagService) -> None:
        self.rag_service = rag_service

    async def answer(
        self,
        incoming: ChatbotInput,
        rules: list[Rule],
        settings: AssistantSettings,
    ) -> Decision:
        normalized = self._normalize(incoming.text)
        command = self._command_reply(normalized)
        if command:
            return Decision(source="command", reply_text=command)

        rule = self._match_rule(normalized, rules)
        if rule:
            return Decision(source="rule", reply_text=rule.reply_text)

        if settings.rag_enabled:
            rag_answer = (await self.rag_service.answer(incoming.text, settings.system_prompt)).strip()
            if rag_answer:
                return Decision(source="rag", reply_text=rag_answer)

        return Decision(source="fallback", reply_text=settings.fallback_reply)

    def _normalize(self, text: str) -> str:
        return " ".join(text.strip().lower().split())

    def _command_reply(self, normalized: str) -> str:
        if normalized in {"help", "/help"}:
            return "Available commands: help, start."
        if normalized in {"start", "/start"}:
            return "Hello. How can I help?"
        return ""

    def _match_rule(self, normalized: str, rules: list[Rule]) -> Rule | None:
        enabled_rules = [rule for rule in rules if rule.enabled]
        for rule in sorted(enabled_rules, key=lambda item: item.priority):
            pattern = self._normalize(rule.pattern)
            if rule.match_type == "exact" and normalized == pattern:
                return rule
            if rule.match_type == "contains" and pattern in normalized:
                return rule
        return None
```

- [ ] **Step 5: Run chatbot engine tests**

Run:

```bash
rtk uv run pytest tests/test_chatbot_engine.py -v
```

Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/chatbot apps/api/chatbot_manager/rag tests/test_chatbot_engine.py
git commit -m "feat: add rule-first chatbot engine"
```

## Task 4: RAG-Anything Service

**Files:**
- Modify: `apps/api/chatbot_manager/rag/service.py`
- Test: `tests/test_rag_service.py`

- [ ] **Step 1: Write failing RAG service tests**

Create `tests/test_rag_service.py`:

```python
from pathlib import Path

import pytest

from chatbot_manager.rag.service import RagAnythingService
from chatbot_manager.settings import Settings


class DummyRag:
    def __init__(self) -> None:
        self.indexed: list[str] = []
        self.queries: list[tuple[str, str]] = []

    async def process_document_complete(self, file_path: str, output_dir: str, parse_method: str) -> None:
        self.indexed.append(file_path)

    async def aquery(self, question: str, mode: str = "hybrid") -> str:
        self.queries.append((question, mode))
        return "Answer from documents."


@pytest.mark.asyncio
async def test_index_document_calls_raganything(tmp_path: Path) -> None:
    settings = Settings(rag_working_dir=tmp_path / "rag", upload_dir=tmp_path / "uploads")
    dummy = DummyRag()
    service = RagAnythingService(settings=settings, rag_client=dummy)
    file_path = tmp_path / "menu.pdf"
    file_path.write_text("menu", encoding="utf-8")

    await service.index_document(file_path)

    assert dummy.indexed == [str(file_path)]


@pytest.mark.asyncio
async def test_answer_uses_hybrid_query(tmp_path: Path) -> None:
    settings = Settings(rag_working_dir=tmp_path / "rag", upload_dir=tmp_path / "uploads")
    dummy = DummyRag()
    service = RagAnythingService(settings=settings, rag_client=dummy)

    answer = await service.answer("What is on the menu?", "Answer from docs.")

    assert answer == "Answer from documents."
    assert dummy.queries == [("What is on the menu?", "hybrid")]
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
rtk uv run pytest tests/test_rag_service.py -v
```

Expected: FAIL because `RagAnythingService` does not exist.

- [ ] **Step 3: Implement RAG-Anything wrapper**

Replace `apps/api/chatbot_manager/rag/service.py` with:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chatbot_manager.settings import Settings, get_settings


class RagClientProtocol(Protocol):
    async def process_document_complete(self, file_path: str, output_dir: str, parse_method: str) -> None:
        pass

    async def aquery(self, question: str, mode: str = "hybrid") -> str:
        pass


class RagService:
    async def answer(self, question: str, system_prompt: str) -> str:
        return ""


@dataclass
class FakeRagService(RagService):
    answer_text: str = ""

    def __init__(self, answer: str = "") -> None:
        self.answer_text = answer

    async def answer(self, question: str, system_prompt: str) -> str:
        return self.answer_text


class RagAnythingService(RagService):
    def __init__(self, settings: Settings | None = None, rag_client: RagClientProtocol | None = None) -> None:
        self.settings = settings or get_settings()
        self._rag_client = rag_client

    def client(self) -> RagClientProtocol:
        if self._rag_client is None:
            self._rag_client = self._build_client()
        return self._rag_client

    async def index_document(self, file_path: Path) -> None:
        self.settings.rag_working_dir.mkdir(parents=True, exist_ok=True)
        await self.client().process_document_complete(
            file_path=str(file_path),
            output_dir=str(self.settings.rag_working_dir),
            parse_method=self.settings.rag_parse_method,
        )

    async def answer(self, question: str, system_prompt: str) -> str:
        result = await self.client().aquery(question, mode="hybrid")
        return str(result).strip()

    def _build_client(self) -> RagClientProtocol:
        try:
            from raganything import RAGAnything, RAGAnythingConfig
        except ImportError as exc:
            raise RuntimeError("RAG-Anything is not installed. Run `uv sync`.") from exc

        config = RAGAnythingConfig(
            working_dir=str(self.settings.rag_working_dir),
            parser=self.settings.rag_parser,
            parse_method=self.settings.rag_parse_method,
        )
        return RAGAnything(config=config)
```

- [ ] **Step 4: Run RAG service tests**

Run:

```bash
rtk uv run pytest tests/test_rag_service.py tests/test_chatbot_engine.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/rag/service.py tests/test_rag_service.py
git commit -m "feat: wrap RAG-Anything service"
```

## Task 5: LINE Channel Adapter

**Files:**
- Create: `apps/api/chatbot_manager/channels/__init__.py`
- Create: `apps/api/chatbot_manager/channels/line.py`
- Test: `tests/test_line_channel.py`

- [ ] **Step 1: Write failing LINE tests**

Create `tests/test_line_channel.py`:

```python
import base64
import hashlib
import hmac

import pytest
import respx
from httpx import Response

from chatbot_manager.channels.line import LineAdapter


def signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_line_signature_validation() -> None:
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")
    body = b'{"events":[]}'
    assert adapter.validate_signature(body, signature(body, "secret")) is True
    assert adapter.validate_signature(body, "bad") is False


def test_line_parse_text_event() -> None:
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")
    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "reply-token",
                "source": {"userId": "user-1"},
                "message": {"type": "text", "text": "hello"},
            }
        ]
    }
    messages = adapter.parse_events(payload)
    assert len(messages) == 1
    assert messages[0].text == "hello"
    assert messages[0].external_user_id == "user-1"
    assert messages[0].reply_context["reply_token"] == "reply-token"


@pytest.mark.asyncio
@respx.mock
async def test_line_send_reply() -> None:
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")

    await adapter.send_reply({"reply_token": "reply-token"}, "Hello")

    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer token"
    assert b"Hello" in request.content
```

- [ ] **Step 2: Run failing LINE tests**

Run:

```bash
rtk uv run pytest tests/test_line_channel.py -v
```

Expected: FAIL because `channels.line` does not exist.

- [ ] **Step 3: Implement LINE adapter**

Create `apps/api/chatbot_manager/channels/__init__.py`:

```python
from .line import IncomingMessage, LineAdapter

__all__ = ["IncomingMessage", "LineAdapter"]
```

Create `apps/api/chatbot_manager/channels/line.py`:

```python
import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class IncomingMessage:
    provider: str
    external_user_id: str
    text: str
    reply_context: dict[str, Any]
    raw_event: dict[str, Any]


class LineAdapter:
    reply_url = "https://api.line.me/v2/bot/message/reply"

    def __init__(self, channel_secret: str, channel_access_token: str) -> None:
        self.channel_secret = channel_secret
        self.channel_access_token = channel_access_token

    def validate_signature(self, body: bytes, signature: str) -> bool:
        digest = hmac.new(self.channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for event in payload.get("events", []):
            message = event.get("message", {})
            if event.get("type") != "message" or message.get("type") != "text":
                continue
            messages.append(
                IncomingMessage(
                    provider="line",
                    external_user_id=event.get("source", {}).get("userId", ""),
                    text=message.get("text", ""),
                    reply_context={"reply_token": event.get("replyToken", "")},
                    raw_event=event,
                )
            )
        return messages

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        headers = {"Authorization": f"Bearer {self.channel_access_token}"}
        payload = {
            "replyToken": reply_context["reply_token"],
            "messages": [{"type": "text", "text": text}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.reply_url, json=payload, headers=headers)
            response.raise_for_status()
```

- [ ] **Step 4: Run LINE tests**

Run:

```bash
rtk uv run pytest tests/test_line_channel.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/channels tests/test_line_channel.py
git commit -m "feat: add LINE channel adapter"
```

## Task 6: Messenger Channel Adapter

**Files:**
- Create: `apps/api/chatbot_manager/channels/messenger.py`
- Modify: `apps/api/chatbot_manager/channels/__init__.py`
- Test: `tests/test_messenger_channel.py`

- [ ] **Step 1: Write failing Messenger tests**

Create `tests/test_messenger_channel.py`:

```python
import pytest
import respx
from httpx import Response

from chatbot_manager.channels.messenger import MessengerAdapter


def test_messenger_verify_success() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")
    assert adapter.verify(mode="subscribe", token="verify", challenge="abc") == "abc"


def test_messenger_verify_failure() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")
    assert adapter.verify(mode="subscribe", token="wrong", challenge="abc") is None


def test_messenger_parse_text_event() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")
    payload = {
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "user-1"},
                        "message": {"text": "hello"},
                    }
                ]
            }
        ]
    }
    messages = adapter.parse_events(payload)
    assert len(messages) == 1
    assert messages[0].provider == "messenger"
    assert messages[0].external_user_id == "user-1"
    assert messages[0].text == "hello"


@pytest.mark.asyncio
@respx.mock
async def test_messenger_send_reply() -> None:
    route = respx.post("https://graph.facebook.com/v20.0/me/messages").mock(
        return_value=Response(200, json={"recipient_id": "user-1", "message_id": "m1"})
    )
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")

    await adapter.send_reply({"recipient_id": "user-1"}, "Hello")

    assert route.called
    request = route.calls[0].request
    assert "access_token=page-token" in str(request.url)
    assert b"Hello" in request.content
```

- [ ] **Step 2: Run failing Messenger tests**

Run:

```bash
rtk uv run pytest tests/test_messenger_channel.py -v
```

Expected: FAIL because `channels.messenger` does not exist.

- [ ] **Step 3: Implement Messenger adapter**

Create `apps/api/chatbot_manager/channels/messenger.py`:

```python
from typing import Any

import httpx

from .line import IncomingMessage


class MessengerAdapter:
    send_url = "https://graph.facebook.com/v20.0/me/messages"

    def __init__(self, verify_token: str, page_access_token: str, app_secret: str) -> None:
        self.verify_token = verify_token
        self.page_access_token = page_access_token
        self.app_secret = app_secret

    def verify(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if mode == "subscribe" and token == self.verify_token and challenge is not None:
            return challenge
        return None

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                text = event.get("message", {}).get("text")
                sender_id = event.get("sender", {}).get("id", "")
                if not text or not sender_id:
                    continue
                messages.append(
                    IncomingMessage(
                        provider="messenger",
                        external_user_id=sender_id,
                        text=text,
                        reply_context={"recipient_id": sender_id},
                        raw_event=event,
                    )
                )
        return messages

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        payload = {
            "recipient": {"id": reply_context["recipient_id"]},
            "message": {"text": text},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.send_url,
                params={"access_token": self.page_access_token},
                json=payload,
            )
            response.raise_for_status()
```

Update `apps/api/chatbot_manager/channels/__init__.py`:

```python
from .line import IncomingMessage, LineAdapter
from .messenger import MessengerAdapter

__all__ = ["IncomingMessage", "LineAdapter", "MessengerAdapter"]
```

- [ ] **Step 4: Run Messenger tests**

Run:

```bash
rtk uv run pytest tests/test_messenger_channel.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/channels tests/test_messenger_channel.py
git commit -m "feat: add Messenger channel adapter"
```

## Task 7: Admin Auth and Templates

**Files:**
- Create: `apps/api/chatbot_manager/security.py`
- Create: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/admin/routes.py`
- Create: `apps/api/chatbot_manager/templates/base.html`
- Create: `apps/api/chatbot_manager/templates/login.html`
- Create: `apps/api/chatbot_manager/static/styles.css`
- Modify: `apps/api/chatbot_manager/main.py`
- Test: `tests/test_admin_routes.py`

- [ ] **Step 1: Write failing auth route tests**

Create `tests/test_admin_routes.py`:

```python
from fastapi.testclient import TestClient


def test_dashboard_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_with_default_admin(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_rejects_bad_password(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "wrong"},
    )
    assert response.status_code == 401
    assert "Invalid login" in response.text
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py -v
```

Expected: FAIL because admin routes do not exist.

- [ ] **Step 3: Implement security helpers**

Create `apps/api/chatbot_manager/security.py`:

```python
from itsdangerous import BadSignature, URLSafeSerializer
from pwdlib import PasswordHash

from .settings import get_settings


password_hash = PasswordHash.recommended()


def verify_admin(email: str, password: str) -> bool:
    settings = get_settings()
    return email == settings.admin_email and password == settings.admin_password


def make_session_token(email: str) -> str:
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-session")
    return serializer.dumps({"email": email})


def read_session_token(token: str | None) -> str | None:
    if not token:
        return None
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-session")
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    return str(data.get("email", ""))


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-3:]}"
```

- [ ] **Step 4: Implement admin routes**

Create `apps/api/chatbot_manager/admin/__init__.py`:

```python
from .routes import router

__all__ = ["router"]
```

Create `apps/api/chatbot_manager/admin/routes.py`:

```python
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from chatbot_manager.security import make_session_token, read_session_token, verify_admin
from chatbot_manager.settings import get_settings

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
router = APIRouter()


def require_admin(request: Request) -> str:
    email = read_session_token(request.cookies.get("admin_session"))
    if not email:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return email


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)) -> Response:
    if not verify_admin(email, password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid login"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("admin_session", make_session_token(email), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    settings = get_settings()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "admin_email": admin_email,
            "settings": settings,
            "active_page": "dashboard",
        },
    )
```

- [ ] **Step 5: Add templates and styles**

Create `apps/api/chatbot_manager/templates/base.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title or "Chatbot Manager" }}</title>
    <link rel="stylesheet" href="/static/styles.css">
  </head>
  <body>
    <aside class="sidebar">
      <div class="brand">Chatbot Manager</div>
      <nav>
        <a href="/" class="{{ 'active' if active_page == 'dashboard' else '' }}">Dashboard</a>
        <a href="/channels" class="{{ 'active' if active_page == 'channels' else '' }}">Channels</a>
        <a href="/rules" class="{{ 'active' if active_page == 'rules' else '' }}">Rules</a>
        <a href="/knowledge" class="{{ 'active' if active_page == 'knowledge' else '' }}">Knowledge</a>
        <a href="/assistant" class="{{ 'active' if active_page == 'assistant' else '' }}">Assistant</a>
        <a href="/test-chat" class="{{ 'active' if active_page == 'test_chat' else '' }}">Test Chat</a>
        <a href="/logs" class="{{ 'active' if active_page == 'logs' else '' }}">Logs</a>
      </nav>
    </aside>
    <main class="main">
      <header class="topbar">
        <span>{{ admin_email or "" }}</span>
        <form method="post" action="/logout"><button type="submit">Log out</button></form>
      </header>
      {% block content %}{% endblock %}
    </main>
  </body>
</html>
```

Create `apps/api/chatbot_manager/templates/login.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Login - Chatbot Manager</title>
    <link rel="stylesheet" href="/static/styles.css">
  </head>
  <body class="login-body">
    <form class="login-panel" method="post" action="/login">
      <h1>Chatbot Manager</h1>
      <label>Email <input name="email" type="email" value="admin@example.local" required></label>
      <label>Password <input name="password" type="password" required></label>
      {% if error %}<p class="error">{{ error }}</p>{% endif %}
      <button type="submit">Log in</button>
    </form>
  </body>
</html>
```

Create `apps/api/chatbot_manager/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head">
  <h1>Dashboard</h1>
  <p>Manage LINE, Messenger, rules, knowledge, and test replies before going live.</p>
</section>
<section class="grid">
  <article class="panel"><h2>LINE</h2><p>{{ "Configured" if settings.line_channel_access_token else "Needs token" }}</p></article>
  <article class="panel"><h2>Messenger</h2><p>{{ "Configured" if settings.messenger_page_access_token else "Needs token" }}</p></article>
  <article class="panel"><h2>RAG</h2><p>{{ settings.rag_backend }}</p></article>
</section>
{% endblock %}
```

Create `apps/api/chatbot_manager/static/styles.css`:

```css
:root {
  color-scheme: light;
  --bg: oklch(0.97 0.006 210);
  --panel: oklch(0.995 0.004 210);
  --ink: oklch(0.22 0.025 235);
  --muted: oklch(0.48 0.02 235);
  --line: oklch(0.84 0.012 235);
  --accent: oklch(0.56 0.13 175);
  --accent-dark: oklch(0.38 0.11 175);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Aptos", "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}
a { color: inherit; }
button, input, select, textarea {
  font: inherit;
}
.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  width: 240px;
  padding: 24px 16px;
  background: oklch(0.25 0.04 235);
  color: oklch(0.96 0.006 210);
}
.brand {
  font-weight: 800;
  margin-bottom: 28px;
}
.sidebar nav {
  display: grid;
  gap: 6px;
}
.sidebar a {
  padding: 10px 12px;
  border-radius: 8px;
  text-decoration: none;
  color: oklch(0.88 0.012 220);
}
.sidebar a.active,
.sidebar a:hover {
  background: oklch(0.34 0.055 235);
  color: oklch(0.98 0.004 210);
}
.main {
  margin-left: 240px;
  min-height: 100vh;
  padding: 24px 32px 48px;
}
.topbar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 16px;
  margin-bottom: 32px;
  color: var(--muted);
}
.topbar button,
.login-panel button {
  border: 0;
  border-radius: 8px;
  padding: 10px 14px;
  background: var(--accent);
  color: oklch(0.99 0.004 175);
  cursor: pointer;
}
.page-head {
  max-width: 760px;
  margin-bottom: 24px;
}
.page-head h1 {
  font-size: 2rem;
  margin: 0 0 8px;
}
.page-head p {
  color: var(--muted);
  margin: 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}
.panel,
.login-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 20px;
}
.panel h2 {
  margin: 0 0 8px;
  font-size: 1rem;
}
.panel p {
  margin: 0;
  color: var(--muted);
}
.login-body {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}
.login-panel {
  width: min(420px, 100%);
  display: grid;
  gap: 14px;
}
.login-panel h1 {
  margin: 0 0 8px;
}
.login-panel label {
  display: grid;
  gap: 6px;
  color: var(--muted);
}
.login-panel input,
input,
select,
textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  background: oklch(1 0 0);
  color: var(--ink);
}
.error {
  color: oklch(0.52 0.17 28);
  margin: 0;
}
@media (max-width: 760px) {
  .sidebar {
    position: static;
    width: auto;
  }
  .main {
    margin-left: 0;
    padding: 20px;
  }
}
```

- [ ] **Step 6: Register admin router and static files**

Modify `apps/api/chatbot_manager/main.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .admin import router as admin_router
from .db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot Manager")

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(admin_router)
    return app


app = create_app()
```

- [ ] **Step 7: Run admin auth tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py tests/test_app_boot.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/chatbot_manager/security.py apps/api/chatbot_manager/admin apps/api/chatbot_manager/templates apps/api/chatbot_manager/static apps/api/chatbot_manager/main.py tests/test_admin_routes.py
git commit -m "feat: add admin dashboard login"
```

## Task 8: Admin Rules, Assistant Settings, Test Chat, Logs

**Files:**
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Create: `apps/api/chatbot_manager/templates/rules.html`
- Create: `apps/api/chatbot_manager/templates/assistant.html`
- Create: `apps/api/chatbot_manager/templates/test_chat.html`
- Create: `apps/api/chatbot_manager/templates/logs.html`
- Modify: `tests/test_admin_routes.py`

- [ ] **Step 1: Extend route tests**

Append to `tests/test_admin_routes.py`:

```python
def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_rule_create_and_list(client: TestClient) -> None:
    login(client)
    created = client.post(
        "/rules",
        data={"pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    page = client.get("/rules")
    assert "price" in page.text
    assert "Price is 100." in page.text


def test_assistant_settings_update(client: TestClient) -> None:
    login(client)
    response = client.post(
        "/assistant",
        data={
            "system_prompt": "Use shop docs.",
            "fallback_reply": "Ask staff.",
            "rag_enabled": "on",
            "llm_model": "gpt-4o-mini",
            "vision_model": "gpt-4o-mini",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/assistant")
    assert "Use shop docs." in page.text


def test_test_chat_uses_rule(client: TestClient) -> None:
    login(client)
    client.post(
        "/rules",
        data={"pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    response = client.post("/test-chat", data={"message": "what price"}, follow_redirects=False)
    assert response.status_code == 200
    assert "Price is 100." in response.text
    assert "rule" in response.text
```

- [ ] **Step 2: Run failing admin feature tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py -v
```

Expected: FAIL because `/rules`, `/assistant`, and `/test-chat` are missing.

- [ ] **Step 3: Add helper functions and routes**

Append these imports to `apps/api/chatbot_manager/admin/routes.py`:

```python
from sqlmodel import Session, select

from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule, utc_now
from chatbot_manager.rag.service import RagAnythingService
```

Append these route helpers and routes to `apps/api/chatbot_manager/admin/routes.py`:

```python
def assistant_settings(session: Session) -> AssistantSettings:
    settings = session.exec(select(AssistantSettings)).first()
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


@router.get("/rules", response_class=HTMLResponse)
def rules_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    rules = session.exec(select(Rule).order_by(Rule.priority)).all()
    return templates.TemplateResponse(
        "rules.html",
        {"request": request, "admin_email": admin_email, "active_page": "rules", "rules": rules},
    )


@router.post("/rules")
def create_rule(
    pattern: str = Form(...),
    match_type: str = Form(...),
    reply_text: str = Form(...),
    priority: int = Form(100),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    session.add(Rule(pattern=pattern, match_type=match_type, reply_text=reply_text, priority=priority))
    session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = assistant_settings(session)
    return templates.TemplateResponse(
        "assistant.html",
        {"request": request, "admin_email": admin_email, "active_page": "assistant", "settings": settings},
    )


@router.post("/assistant")
def update_assistant(
    system_prompt: str = Form(...),
    fallback_reply: str = Form(...),
    rag_enabled: str | None = Form(None),
    llm_model: str = Form(...),
    vision_model: str = Form(...),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = assistant_settings(session)
    settings.system_prompt = system_prompt
    settings.fallback_reply = fallback_reply
    settings.rag_enabled = rag_enabled == "on"
    settings.llm_model = llm_model
    settings.vision_model = vision_model
    settings.updated_at = utc_now()
    session.add(settings)
    session.commit()
    return RedirectResponse("/assistant", status_code=303)


@router.get("/test-chat", response_class=HTMLResponse)
def test_chat_page(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    return templates.TemplateResponse(
        "test_chat.html",
        {"request": request, "admin_email": admin_email, "active_page": "test_chat", "message": "", "reply": "", "source": ""},
    )


@router.post("/test-chat", response_class=HTMLResponse)
async def test_chat_submit(
    request: Request,
    message: str = Form(...),
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    rules = session.exec(select(Rule).order_by(Rule.priority)).all()
    settings = assistant_settings(session)
    engine = ChatbotEngine(rag_service=RagAnythingService())
    decision = await engine.answer(
        ChatbotInput(text=message, provider="test", external_user_id="admin"),
        rules=list(rules),
        settings=settings,
    )
    session.add(ChatEvent(provider="test", external_user_id="admin", incoming_text=message, decision_source=decision.source, reply_text=decision.reply_text))
    session.commit()
    return templates.TemplateResponse(
        "test_chat.html",
        {
            "request": request,
            "admin_email": admin_email,
            "active_page": "test_chat",
            "message": message,
            "reply": decision.reply_text,
            "source": decision.source,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
def logs_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    events = session.exec(select(ChatEvent).order_by(ChatEvent.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(
        "logs.html",
        {"request": request, "admin_email": admin_email, "active_page": "logs", "events": events},
    )
```

- [ ] **Step 4: Add templates**

Create `apps/api/chatbot_manager/templates/rules.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Rules</h1><p>Rules answer predictable questions before RAG runs.</p></section>
<form class="panel form-grid" method="post" action="/rules">
  <label>Keyword <input name="pattern" required></label>
  <label>Match <select name="match_type"><option value="contains">Contains</option><option value="exact">Exact</option></select></label>
  <label>Priority <input name="priority" type="number" value="100"></label>
  <label class="wide">Reply <textarea name="reply_text" required></textarea></label>
  <button type="submit">Add rule</button>
</form>
<table class="data-table">
  <thead><tr><th>Priority</th><th>Match</th><th>Keyword</th><th>Reply</th></tr></thead>
  <tbody>
    {% for rule in rules %}
    <tr><td>{{ rule.priority }}</td><td>{{ rule.match_type }}</td><td>{{ rule.pattern }}</td><td>{{ rule.reply_text }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

Create `apps/api/chatbot_manager/templates/assistant.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Assistant</h1><p>Set the voice, fallback reply, and model names.</p></section>
<form class="panel form-grid" method="post" action="/assistant">
  <label class="wide">System prompt <textarea name="system_prompt" rows="6">{{ settings.system_prompt }}</textarea></label>
  <label class="wide">Fallback reply <textarea name="fallback_reply" rows="3">{{ settings.fallback_reply }}</textarea></label>
  <label><input type="checkbox" name="rag_enabled" {{ "checked" if settings.rag_enabled else "" }}> Use RAG-Anything fallback</label>
  <label>LLM model <input name="llm_model" value="{{ settings.llm_model }}"></label>
  <label>Vision model <input name="vision_model" value="{{ settings.vision_model }}"></label>
  <button type="submit">Save assistant</button>
</form>
{% endblock %}
```

Create `apps/api/chatbot_manager/templates/test_chat.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Test Chat</h1><p>Try a user message before connecting live channels.</p></section>
<form class="panel form-grid" method="post" action="/test-chat">
  <label class="wide">User message <textarea name="message" rows="3">{{ message }}</textarea></label>
  <button type="submit">Send test</button>
</form>
{% if reply %}
<section class="panel result">
  <h2>Bot reply</h2>
  <p>{{ reply }}</p>
  <small>Source: {{ source }}</small>
</section>
{% endif %}
{% endblock %}
```

Create `apps/api/chatbot_manager/templates/logs.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Logs</h1><p>Recent messages and how the bot answered.</p></section>
<table class="data-table">
  <thead><tr><th>Time</th><th>Provider</th><th>Message</th><th>Source</th><th>Reply</th></tr></thead>
  <tbody>
    {% for event in events %}
    <tr>
      <td>{{ event.created_at }}</td>
      <td>{{ event.provider }}</td>
      <td>{{ event.incoming_text }}</td>
      <td>{{ event.decision_source }}</td>
      <td>{{ event.reply_text }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Extend CSS for forms and tables**

Append to `apps/api/chatbot_manager/static/styles.css`:

```css
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}
.form-grid label {
  display: grid;
  gap: 6px;
  color: var(--muted);
}
.form-grid .wide {
  grid-column: 1 / -1;
}
textarea {
  resize: vertical;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.data-table th,
.data-table td {
  text-align: left;
  padding: 12px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
.data-table th {
  color: var(--muted);
  font-weight: 700;
}
.result {
  margin-top: 20px;
}
.result h2 {
  margin: 0 0 8px;
}
.result small {
  color: var(--muted);
}
```

- [ ] **Step 6: Run admin feature tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py tests/test_chatbot_engine.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/chatbot_manager/admin/routes.py apps/api/chatbot_manager/templates apps/api/chatbot_manager/static/styles.css tests/test_admin_routes.py
git commit -m "feat: add admin rules assistant and test chat"
```

## Task 9: Knowledge Upload and Indexing

**Files:**
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Create: `apps/api/chatbot_manager/templates/knowledge.html`
- Modify: `tests/test_admin_routes.py`

- [ ] **Step 1: Add failing knowledge tests**

Append to `tests/test_admin_routes.py`:

```python
def test_knowledge_page_loads(client: TestClient) -> None:
    login(client)
    response = client.get("/knowledge")
    assert response.status_code == 200
    assert "Upload document" in response.text


def test_knowledge_upload_records_document(client: TestClient, monkeypatch) -> None:
    async def fake_index(document_id: int, path: str) -> None:
        return None

    monkeypatch.setattr("chatbot_manager.admin.routes.index_document_task", fake_index)
    login(client)
    response = client.post(
        "/knowledge",
        files={"file": ("menu.txt", b"Menu content", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/knowledge")
    assert "menu.txt" in page.text
```

- [ ] **Step 2: Run failing knowledge tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py::test_knowledge_page_loads tests/test_admin_routes.py::test_knowledge_upload_records_document -v
```

Expected: FAIL because `/knowledge` is missing.

- [ ] **Step 3: Add indexing helper and routes**

Append imports to `apps/api/chatbot_manager/admin/routes.py`:

```python
from fastapi import BackgroundTasks, UploadFile
from pathlib import PurePath
from chatbot_manager.models import KnowledgeDocument
```

Append helper and routes:

```python
async def index_document_task(document_id: int, path: str) -> None:
    from chatbot_manager.db import engine

    with Session(engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return
        try:
            await RagAnythingService().index_document(Path(path))
            document.status = "indexed"
            document.error = ""
            document.indexed_at = utc_now()
        except Exception as exc:
            document.status = "failed"
            document.error = str(exc)
        document.updated_at = utc_now()
        session.add(document)
        session.commit()


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    documents = session.exec(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).all()
    return templates.TemplateResponse(
        "knowledge.html",
        {"request": request, "admin_email": admin_email, "active_page": "knowledge", "documents": documents},
    )


@router.post("/knowledge")
async def upload_knowledge(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    filename = PurePath(file.filename or "upload.bin").name
    target = settings.upload_dir / filename
    content = await file.read()
    target.write_bytes(content)
    document = KnowledgeDocument(filename=filename, path=str(target), status="pending")
    session.add(document)
    session.commit()
    session.refresh(document)
    background_tasks.add_task(index_document_task, document.id, str(target))
    return RedirectResponse("/knowledge", status_code=303)
```

- [ ] **Step 4: Add knowledge template**

Create `apps/api/chatbot_manager/templates/knowledge.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Knowledge</h1><p>Upload documents for RAG-Anything indexing.</p></section>
<form class="panel form-grid" method="post" action="/knowledge" enctype="multipart/form-data">
  <label class="wide">Upload document <input type="file" name="file" required></label>
  <button type="submit">Upload and index</button>
</form>
<table class="data-table">
  <thead><tr><th>File</th><th>Status</th><th>Indexed</th><th>Error</th></tr></thead>
  <tbody>
    {% for document in documents %}
    <tr>
      <td>{{ document.filename }}</td>
      <td>{{ document.status }}</td>
      <td>{{ document.indexed_at or "" }}</td>
      <td>{{ document.error }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Run knowledge tests**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py -v
```

Expected: all tests pass. The upload test patches `index_document_task` so test execution never calls external RAG-Anything dependencies.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin/routes.py apps/api/chatbot_manager/templates/knowledge.html tests/test_admin_routes.py
git commit -m "feat: add knowledge upload indexing"
```

## Task 10: Webhook Routes and Event Logging

**Files:**
- Modify: `apps/api/chatbot_manager/main.py`
- Create: `apps/api/chatbot_manager/webhooks.py`
- Test: `tests/test_webhooks.py`

- [ ] **Step 1: Write failing webhook tests**

Create `tests/test_webhooks.py`:

```python
import base64
import hashlib
import hmac

from fastapi.testclient import TestClient


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_messenger_verification(client: TestClient) -> None:
    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "challenge-1"},
    )
    assert response.status_code == 200
    assert response.text == "challenge-1"


def test_line_webhook_rejects_bad_signature(client: TestClient) -> None:
    response = client.post("/webhooks/line", content=b'{"events":[]}', headers={"x-line-signature": "bad"})
    assert response.status_code == 401


def test_line_webhook_accepts_empty_events(client: TestClient) -> None:
    body = b'{"events":[]}'
    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "")})
    assert response.status_code == 200
    assert response.json() == {"processed": 0}


def test_messenger_webhook_accepts_empty_entries(client: TestClient) -> None:
    response = client.post("/webhooks/messenger", json={"entry": []})
    assert response.status_code == 200
    assert response.json() == {"processed": 0}
```

- [ ] **Step 2: Run failing webhook tests**

Run:

```bash
rtk uv run pytest tests/test_webhooks.py -v
```

Expected: FAIL because webhook routes do not exist.

- [ ] **Step 3: Implement webhook routes**

Create `apps/api/chatbot_manager/webhooks.py`:

```python
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from chatbot_manager.channels.line import LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.db import get_session
from chatbot_manager.models import AssistantSettings, ChatEvent, Rule
from chatbot_manager.rag.service import RagAnythingService
from chatbot_manager.settings import get_settings

router = APIRouter(prefix="/webhooks")


def get_assistant_settings(session: Session) -> AssistantSettings:
    settings = session.exec(select(AssistantSettings)).first()
    if settings is None:
        settings = AssistantSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


async def process_messages(messages, sender, session: Session) -> int:
    rules = list(session.exec(select(Rule).order_by(Rule.priority)).all())
    settings = get_assistant_settings(session)
    engine = ChatbotEngine(rag_service=RagAnythingService())
    processed = 0
    for message in messages:
        decision = await engine.answer(
            ChatbotInput(text=message.text, provider=message.provider, external_user_id=message.external_user_id),
            rules=rules,
            settings=settings,
        )
        await sender(message.reply_context, decision.reply_text)
        session.add(
            ChatEvent(
                provider=message.provider,
                external_user_id=message.external_user_id,
                incoming_text=message.text,
                decision_source=decision.source,
                reply_text=decision.reply_text,
                raw_event=json.dumps(message.raw_event),
            )
        )
        processed += 1
    session.commit()
    return processed


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    settings = get_settings()
    body = await request.body()
    adapter = LineAdapter(settings.line_channel_secret, settings.line_channel_access_token)
    if not adapter.validate_signature(body, x_line_signature):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")
    payload = await request.json()
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}


@router.get("/messenger", response_class=PlainTextResponse)
def messenger_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    settings = get_settings()
    adapter = MessengerAdapter(settings.messenger_verify_token, settings.messenger_page_access_token, settings.messenger_app_secret)
    challenge = adapter.verify(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Invalid Messenger verification")
    return PlainTextResponse(challenge)


@router.post("/messenger")
async def messenger_webhook(request: Request, session: Session = Depends(get_session)) -> dict[str, int]:
    settings = get_settings()
    adapter = MessengerAdapter(settings.messenger_verify_token, settings.messenger_page_access_token, settings.messenger_app_secret)
    payload = await request.json()
    messages = adapter.parse_events(payload)
    processed = await process_messages(messages, adapter.send_reply, session)
    return {"processed": processed}
```

- [ ] **Step 4: Register webhook router**

Modify `apps/api/chatbot_manager/main.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .admin import router as admin_router
from .db import init_db
from .webhooks import router as webhook_router


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot Manager")

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(webhook_router)
    app.include_router(admin_router)
    return app


app = create_app()
```

- [ ] **Step 5: Run webhook tests**

Run:

```bash
rtk uv run pytest tests/test_webhooks.py tests/test_line_channel.py tests/test_messenger_channel.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/webhooks.py apps/api/chatbot_manager/main.py tests/test_webhooks.py
git commit -m "feat: add channel webhook routes"
```

## Task 11: Channels Page, README, Smoke Verification

**Files:**
- Create: `apps/api/chatbot_manager/templates/channels.html`
- Modify: `apps/api/chatbot_manager/admin/routes.py`
- Modify: `README.md`
- Test: `tests/test_admin_routes.py`

- [ ] **Step 1: Add failing channels page test**

Append to `tests/test_admin_routes.py`:

```python
def test_channels_page_shows_webhook_urls(client: TestClient) -> None:
    login(client)
    response = client.get("/channels")
    assert response.status_code == 200
    assert "/webhooks/line" in response.text
    assert "/webhooks/messenger" in response.text
```

- [ ] **Step 2: Run failing channels test**

Run:

```bash
rtk uv run pytest tests/test_admin_routes.py::test_channels_page_shows_webhook_urls -v
```

Expected: FAIL because `/channels` is missing.

- [ ] **Step 3: Add channels route**

Append to `apps/api/chatbot_manager/admin/routes.py`:

```python
@router.get("/channels", response_class=HTMLResponse)
def channels_page(request: Request, admin_email: str = Depends(require_admin)) -> Response:
    settings = get_settings()
    return templates.TemplateResponse(
        "channels.html",
        {
            "request": request,
            "admin_email": admin_email,
            "active_page": "channels",
            "settings": settings,
            "line_webhook": f"{settings.api_public_url}/webhooks/line",
            "messenger_webhook": f"{settings.api_public_url}/webhooks/messenger",
        },
    )
```

- [ ] **Step 4: Add channels template**

Create `apps/api/chatbot_manager/templates/channels.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="page-head"><h1>Channels</h1><p>Copy these webhook URLs into LINE and Facebook developer settings.</p></section>
<section class="grid">
  <article class="panel">
    <h2>LINE</h2>
    <p>Webhook URL: <code>{{ line_webhook }}</code></p>
    <p>Status: {{ "Ready" if settings.line_channel_access_token else "Needs channel access token" }}</p>
  </article>
  <article class="panel">
    <h2>Messenger</h2>
    <p>Webhook URL: <code>{{ messenger_webhook }}</code></p>
    <p>Verify token: <code>{{ settings.messenger_verify_token }}</code></p>
    <p>Status: {{ "Ready" if settings.messenger_page_access_token else "Needs page access token" }}</p>
  </article>
</section>
{% endblock %}
```

- [ ] **Step 5: Update README**

Create `README.md`:

```markdown
# Chatbot Manager

Local admin manager for LINE and Facebook Messenger chatbots.

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
- `RAG_WORKING_DIR`
- MinerU/RAG-Anything parser dependencies installed on host
```

- [ ] **Step 6: Run full tests**

Run:

```bash
rtk uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Run local server smoke check**

Run:

```bash
rtk uv run uvicorn chatbot_manager.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
rtk curl -s http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 8: Commit**

```bash
git add apps/api/chatbot_manager/admin/routes.py apps/api/chatbot_manager/templates/channels.html README.md tests/test_admin_routes.py
git commit -m "feat: document local chatbot manager"
```

## Self-Review Checklist

- Spec coverage:
  - LINE webhook verification, parsing, reply sending: Tasks 5 and 10.
  - Messenger verification, parsing, reply sending: Tasks 6 and 10.
  - Rule-first chatbot engine: Task 3.
  - RAG-Anything fallback and document indexing: Tasks 4 and 9.
  - Admin dashboard, rules, assistant, test chat, logs, channels, knowledge: Tasks 7 through 11.
  - Local auth: Task 7.
  - SQLite models and logs: Tasks 2, 8, 10.
- Red-flag scan:
  - No forbidden empty-work tokens or unspecified implementation steps.
  - Known execution caveat is invalid Git state, captured in Execution Notes.
- Type consistency:
  - `ChatbotInput`, `Decision`, `RagService`, `RagAnythingService`, `IncomingMessage`, and model names are introduced before use.
  - Route paths match template navigation and tests.
