# Phase 2 External Knowledge Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CIFS product-level ownership of knowledge documents with a secure external Knowledge Service registry and a tested LightRAG client, while keeping legacy local-RAG code available only as a temporary migration fallback.

**Architecture:** Store Knowledge Service connection metadata in SQLModel and secrets in a separate encrypted `Credential` record. Add a small async `KnowledgeServiceClient` abstraction over LightRAG HTTP endpoints, expose registry/test UI, and allow a Bot Draft to bind to a service without changing its Live configuration. Remove local Upload/Reindex/Graph links from normal navigation but do not physically delete legacy code until Phase 6.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, httpx, cryptography/Fernet, Jinja2, pytest, respx.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- External RAG owns documents, parsing, indexing, graph/vector storage, document lifecycle, and final grounded answer generation.
- CIFS does not proxy file upload/reindex/delete/graph-management operations in the new product flow.
- Credential encryption begins in this phase; API keys are masked and replace-only in UI.
- Do not log request authorization headers, API keys, raw upstream exception bodies, or secret-bearing URLs.
- `BotConfigVersion.knowledge_service_id` is the binding; never add knowledge ownership directly to `Bot`.
- Production still uses the Phase 1 live/legacy runtime until Phase 3; changing a Knowledge binding affects Draft only.
- Re-check the actual deployed LightRAG API before implementation. The plan contract assumes `GET /health`, `POST /query`, optional `X-API-Key`, and the design minimum LightRAG `>=1.5.5`; if the pinned server differs, amend only the adapter mapping/tests, not the CIFS domain interface.
- Use explicit timeouts and do not follow redirects automatically for service calls.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `Credential`, `KnowledgeService`.
- Create `apps/api/chatbot_manager/credentials.py`: encrypted payload create/replace/decrypt helpers.
- Create `apps/api/chatbot_manager/knowledge/__init__.py`.
- Create `apps/api/chatbot_manager/knowledge/client.py`: domain request/response types, protocol, LightRAG HTTP implementation, fake client.
- Create `apps/api/chatbot_manager/knowledge/service.py`: DB-to-client factory and connection validation.
- Create `apps/api/chatbot_manager/admin/knowledge_services.py`: registry CRUD/test/retrieval routes.
- Modify `apps/api/chatbot_manager/admin/bots.py`: Bot Knowledge tab and Draft binding endpoint.
- Modify `apps/api/chatbot_manager/admin/__init__.py`: include registry router.
- Modify `apps/api/chatbot_manager/templates/base.html`: replace normal Knowledge/Graph links with Knowledge Services.
- Create `apps/api/chatbot_manager/templates/knowledge_services.html`.
- Create `apps/api/chatbot_manager/templates/bot_knowledge.html`.
- Modify `apps/api/chatbot_manager/static/styles.css` only for registry/status UI.
- Create `tests/test_credentials.py`.
- Create `tests/test_knowledge_client.py`.
- Create `tests/test_knowledge_services_admin.py`.
- Modify `tests/test_bot_admin.py`.
- Modify legacy knowledge tests only to reflect removal from navigation; keep direct legacy-route coverage until Phase 6.

---

### Task 1: Add Credential and Knowledge Service Persistence

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `apps/api/chatbot_manager/credentials.py`
- Create: `tests/test_credentials.py`

**Interfaces:**
- Consumes: `encrypt_secret()`, `decrypt_secret()`, `mask_secret()` in `security.py`.
- Produces:
  - `Credential`
  - `KnowledgeService`
  - `store_credential(session, credential_type, payload) -> Credential`
  - `replace_credential(session, credential_id, payload) -> Credential`
  - `read_credential(session, credential_id) -> dict[str, str]`
  - `masked_credential(session, credential_id) -> dict[str, str]`

- [ ] **Step 1: Write failing persistence/encryption tests**

`tests/test_credentials.py`:

```python
import json
from sqlmodel import Session

from chatbot_manager.credentials import masked_credential, read_credential, replace_credential, store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import KnowledgeService


def test_credential_payload_is_encrypted_and_replace_only(client) -> None:
    with Session(get_engine()) as session:
        credential = store_credential(session, "knowledge_service", {"api_key": "rag-secret-123"})
        encrypted = credential.encrypted_payload
        assert "rag-secret-123" not in encrypted
        assert encrypted.startswith("enc:v1:")
        assert read_credential(session, credential.id) == {"api_key": "rag-secret-123"}
        assert masked_credential(session, credential.id)["api_key"] == "rag...123"

        replaced = replace_credential(session, credential.id, {"api_key": "new-secret-456"})
        assert replaced.id == credential.id
        assert read_credential(session, credential.id) == {"api_key": "new-secret-456"}


def test_knowledge_service_references_credential(client) -> None:
    with Session(get_engine()) as session:
        credential = store_credential(session, "knowledge_service", {"api_key": "secret"})
        service = KnowledgeService(
            name="CIFS General RAG",
            service_type="lightrag",
            api_base_url="https://rag.example.test",
            webui_url="https://rag.example.test/webui",
            credential_id=credential.id,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
    assert service.enabled is True
    assert service.health_status == "unknown"
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_credentials.py
```

- [ ] **Step 3: Add models**

Add to `models.py`:

```python
class Credential(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    credential_type: str = Field(index=True)
    encrypted_payload: str
    created_at: datetime = Field(default_factory=utc_now)
    rotated_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None


class KnowledgeService(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    service_type: str = Field(default="lightrag", index=True)
    api_base_url: str
    webui_url: str = ""
    credential_id: Optional[int] = Field(default=None, index=True)
    enabled: bool = True
    health_status: str = Field(default="unknown", index=True)
    last_health_check: Optional[datetime] = None
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

- [ ] **Step 4: Implement encrypted credential service**

`credentials.py` serializes the entire payload to canonical JSON before encryption so individual secrets never appear in DB columns:

```python
def store_credential(session: Session, credential_type: str, payload: dict[str, str]) -> Credential:
    plaintext = json.dumps(payload, sort_keys=True)
    item = Credential(
        credential_type=credential_type,
        encrypted_payload=encrypt_secret(plaintext, get_settings().app_encryption_key),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def read_credential(session: Session, credential_id: int | None) -> dict[str, str]:
    if credential_id is None:
        return {}
    item = session.get(Credential, credential_id)
    if item is None:
        raise LookupError("Credential not found")
    raw = decrypt_secret(item.encrypted_payload, get_settings().app_encryption_key)
    item.last_used_at = utc_now()
    session.add(item)
    session.commit()
    return {str(k): str(v) for k, v in json.loads(raw).items()}
```

`replace_credential()` overwrites `encrypted_payload`, sets `rotated_at`, and never returns plaintext. `masked_credential()` decrypts only in memory, applies `mask_secret()` per field, and returns masked strings.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_credentials.py tests/test_secret_encryption.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/credentials.py tests/test_credentials.py
git commit -m "feat: add encrypted knowledge service credentials"
```

---

### Task 2: Implement the External Knowledge Client Contract

**Files:**
- Create: `apps/api/chatbot_manager/knowledge/__init__.py`
- Create: `apps/api/chatbot_manager/knowledge/client.py`
- Create: `apps/api/chatbot_manager/knowledge/service.py`
- Create: `tests/test_knowledge_client.py`

**Interfaces:**
- Consumes: `KnowledgeService`, `Credential`, `read_credential()`.
- Produces exact shared types:

```python
@dataclass(frozen=True)
class KnowledgeQuery:
    question: str
    user_prompt: str
    conversation_history: list[dict[str, str]]
    include_references: bool = True
    response_type: str = "Multiple Paragraphs"

@dataclass(frozen=True)
class KnowledgeAnswer:
    text: str
    references: list[dict[str, object]]
    latency_ms: int

@dataclass(frozen=True)
class KnowledgeHealth:
    status: str              # healthy | unavailable | unauthorized | invalid
    latency_ms: int | None
    detail_code: str = ""

class KnowledgeServiceClient(Protocol):
    async def health(self) -> KnowledgeHealth:
        raise NotImplementedError

    async def query(self, request: KnowledgeQuery) -> KnowledgeAnswer:
        raise NotImplementedError

    async def test_connection(self) -> KnowledgeHealth:
        raise NotImplementedError

    def capabilities(self) -> frozenset[str]:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing HTTP mapping tests**

Use `respx`:

```python
import respx
from httpx import Response
import pytest

from chatbot_manager.knowledge.client import KnowledgeQuery, LightRAGClient


@pytest.mark.asyncio
@respx.mock
async def test_lightrag_query_sends_bot_prompt_history_and_api_key() -> None:
    route = respx.post("https://rag.example.test/query").mock(
        return_value=Response(200, json={
            "response": "Grounded answer",
            "references": [{"file_name": "guide.pdf"}],
        })
    )
    client = LightRAGClient("https://rag.example.test", "rag-key", timeout_seconds=5.0)
    answer = await client.query(KnowledgeQuery(
        question="What documents?",
        user_prompt="Answer professionally in Thai.",
        conversation_history=[{"role": "user", "content": "Earlier question"}],
    ))

    sent = route.calls[0].request
    assert sent.headers["x-api-key"] == "rag-key"
    payload = json.loads(sent.content)
    assert payload["query"] == "What documents?"
    assert payload["user_prompt"] == "Answer professionally in Thai."
    assert payload["conversation_history"][0]["content"] == "Earlier question"
    assert payload["include_references"] is True
    assert answer.text == "Grounded answer"
    assert answer.references[0]["file_name"] == "guide.pdf"
```

Also test:

```python
@pytest.mark.asyncio
@respx.mock
async def test_health_maps_unauthorized_without_leaking_body() -> None:
    respx.get("https://rag.example.test/health").mock(Response(401, text="secret upstream detail"))
    client = LightRAGClient("https://rag.example.test", "rag-key", timeout_seconds=5.0)
    health = await client.health()
    assert health.status == "unauthorized"
    assert health.detail_code == "knowledge_service_unauthorized"
```

And validate `http/https` only, no automatic redirects, timeout/connection failures map to `unavailable`, and malformed 200 query payload raises a stable `KnowledgeServiceError("knowledge_response_invalid")` without embedding raw payload text.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_knowledge_client.py
```

- [ ] **Step 3: Implement `LightRAGClient`**

Use one private request helper with explicit `httpx.Timeout` and `follow_redirects=False`:

```python
class LightRAGClient:
    def __init__(self, api_base_url: str, api_key: str = "", timeout_seconds: float = 10.0) -> None:
        parsed = urlsplit(api_base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("invalid_knowledge_service_url")
        self._base = api_base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key} if self._api_key else {}

    async def query(self, request: KnowledgeQuery) -> KnowledgeAnswer:
        started = perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            response = await client.post(
                f"{self._base}/query",
                headers=self._headers(),
                json={
                    "query": request.question,
                    "user_prompt": request.user_prompt,
                    "conversation_history": request.conversation_history,
                    "include_references": request.include_references,
                    "response_type": request.response_type,
                },
            )
        _raise_for_safe_status(response)
        payload = response.json()
        text = payload.get("response")
        if not isinstance(text, str):
            raise KnowledgeServiceError("knowledge_response_invalid")
        refs = payload.get("references", [])
        return KnowledgeAnswer(text=text, references=refs if isinstance(refs, list) else [], latency_ms=int((perf_counter() - started) * 1000))
```

`test_connection()` delegates to `health()`. `capabilities()` initially returns `frozenset({"query", "references", "conversation_history", "user_prompt", "webui"})`.

- [ ] **Step 4: Implement DB factory**

`knowledge/service.py`:

```python
def build_knowledge_client(session: Session, knowledge_service_id: int) -> KnowledgeServiceClient:
    service = session.get(KnowledgeService, knowledge_service_id)
    if service is None or not service.enabled:
        raise LookupError("Knowledge Service not available")
    secrets = read_credential(session, service.credential_id)
    if service.service_type != "lightrag":
        raise ValueError("unsupported_knowledge_service_type")
    return LightRAGClient(service.api_base_url, secrets.get("api_key", ""))
```

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_knowledge_client.py tests/test_credentials.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/knowledge tests/test_knowledge_client.py
git commit -m "feat: add external LightRAG client"
```

---

### Task 3: Add Knowledge Service Registry and Safe Connection Testing

**Files:**
- Create: `apps/api/chatbot_manager/admin/knowledge_services.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/knowledge_services.html`
- Modify: `apps/api/chatbot_manager/static/styles.css`
- Create: `tests/test_knowledge_services_admin.py`

**Interfaces:**
- Consumes: Task 1 persistence and Task 2 client factory.
- Produces: `/knowledge-services` registry, create/update, `POST /knowledge-services/{id}/test`, and `POST /knowledge-services/{id}/test-retrieval`.

- [ ] **Step 1: Write failing admin tests**

```python
def test_knowledge_service_registry_masks_api_key(client: TestClient, monkeypatch) -> None:
    csrf = login_and_csrf(client)
    response = client.post("/knowledge-services", data={
        "csrf_token": csrf,
        "name": "CIFS General RAG",
        "api_base_url": "https://rag.example.test",
        "webui_url": "https://rag.example.test/webui",
        "api_key": "rag-secret-123",
    }, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/knowledge-services")
    assert "CIFS General RAG" in page.text
    assert "rag-secret-123" not in page.text
    assert "rag...123" in page.text


def test_blank_api_key_update_keeps_existing_secret(client: TestClient) -> None:
    csrf = login_and_csrf(client)
    client.post(
        "/knowledge-services",
        data={
            "csrf_token": csrf,
            "name": "CIFS General RAG",
            "api_base_url": "https://rag.example.test",
            "webui_url": "https://rag.example.test/webui",
            "api_key": "original-rag-key",
        },
        follow_redirects=False,
    )
    with Session(get_engine()) as session:
        service = session.exec(
            select(KnowledgeService).where(KnowledgeService.name == "CIFS General RAG")
        ).one()
        service_id = service.id
        before = read_credential(session, service.credential_id)

    response = client.post(
        f"/knowledge-services/{service_id}/update",
        data={
            "csrf_token": csrf,
            "name": "CIFS General RAG",
            "api_base_url": "https://rag.example.test",
            "webui_url": "https://rag.example.test/webui",
            "api_key": "",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert read_credential(session, service.credential_id) == before
```

Add a test that monkeypatches `build_knowledge_client()` with a fake returning `KnowledgeHealth("healthy", 25)` and verifies the service row updates `health_status="healthy"` and `last_health_check`.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_knowledge_services_admin.py
```

- [ ] **Step 3: Implement registry routes**

Routes must:

```text
GET  /knowledge-services                  list services + masked key
POST /knowledge-services                  create service/credential
POST /knowledge-services/{id}/update      edit URLs/name/enabled; blank key keeps existing
POST /knowledge-services/{id}/test        call health/test_connection and persist status/timestamp
POST /knowledge-services/{id}/test-retrieval
                                             submit a harmless admin-entered query and show response/references without storing it as production conversation
```

Map external exceptions to stable UI codes such as `knowledge_service_unavailable`, `knowledge_service_unauthorized`, `knowledge_response_invalid`. Never include `str(exc)` in redirect query strings or rendered pages.

- [ ] **Step 4: Implement the registry template**

Render service name/type/status/endpoint/WebUI link/masked credential and actions. API key input uses:

```html
<input name="api_key" type="password" autocomplete="new-password" placeholder="Leave blank to keep current key">
```

`Open RAG Manager` uses the stored WebUI URL with `target="_blank" rel="noopener noreferrer"`.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_knowledge_services_admin.py tests/test_secret_encryption.py tests/test_failure_handling.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin apps/api/chatbot_manager/templates/knowledge_services.html apps/api/chatbot_manager/static/styles.css tests/test_knowledge_services_admin.py
git commit -m "feat: add knowledge service registry"
```

---

### Task 4: Bind Bot Drafts to Knowledge Services

**Files:**
- Modify: `apps/api/chatbot_manager/admin/bots.py`
- Create: `apps/api/chatbot_manager/templates/bot_knowledge.html`
- Modify: `tests/test_bot_admin.py`

**Interfaces:**
- Consumes: `ensure_draft_config()` from Phase 1 and `KnowledgeService` registry.
- Produces: `GET/POST /bots/{bot_id}/knowledge`; POST changes Draft only.

- [ ] **Step 1: Write failing Draft-binding tests**

```python
def test_changing_bot_knowledge_creates_or_updates_draft_only(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        service = KnowledgeService(name="RAG B", service_type="lightrag", api_base_url="https://rag-b.test")
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        live_id = bot.live_config_version_id

    csrf = csrf_from_page(client.get(f"/bots/{bot.id}/knowledge").text)
    response = client.post(f"/bots/{bot.id}/knowledge", data={"csrf_token": csrf, "knowledge_service_id": str(service_id)}, follow_redirects=False)
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot.id)
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id is not None
        draft = session.get(BotConfigVersion, bot.draft_config_version_id)
        assert draft.knowledge_service_id == service_id
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_admin.py
```

- [ ] **Step 3: Implement Knowledge tab route/template**

GET shows Live binding, Draft binding (if present), selected service health/status, `Test Retrieval`, and external manager link. POST calls `ensure_draft_config()`, validates the selected enabled `KnowledgeService`, sets `draft.knowledge_service_id`, commits, and redirects back.

Do not change `bot.live_config_version_id` in this phase.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_bot_foundation.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/admin/bots.py apps/api/chatbot_manager/templates/bot_knowledge.html tests/test_bot_admin.py
git commit -m "feat: bind bot drafts to knowledge services"
```

---

### Task 5: Retire Local Knowledge Management from Normal Navigation

**Files:**
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `tests/test_admin_routes.py`
- Modify: `tests/test_phase4_knowledge_graph.py` only where it asserts global navigation, not its direct legacy route behavior.
- Modify: `README.md`

**Interfaces:**
- Consumes: registry UI from Task 3.
- Produces: normal admin navigation points to external-service management instead of local document/graph management; legacy routes remain directly reachable until Phase 6.

- [ ] **Step 1: Add failing navigation contract test**

```python
def test_primary_navigation_uses_knowledge_services_not_local_knowledge(client: TestClient) -> None:
    login(client)
    html = client.get("/").text
    assert 'href="/knowledge-services"' in html
    assert 'href="/knowledge"' not in html
    assert 'href="/knowledge-graph"' not in html
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_admin_routes.py tests/test_phase4_knowledge_graph.py
```

- [ ] **Step 3: Update base navigation and README boundary**

Replace the normal `Knowledge` and `Graph` nav entries with `Knowledge Services`. Update README product description so it states that knowledge files are managed in external RAG-Anything/LightRAG WebUI and CIFS stores only service bindings/credentials.

Keep `/knowledge`, `/knowledge-graph`, and their APIs implemented but unlinked; add a short route comment `# Legacy migration path; remove after Phase 6 dependency verification.` rather than changing their behavior in this task.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_admin_routes.py tests/test_phase4_knowledge_graph.py tests/test_knowledge_services_admin.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/templates/base.html apps/api/chatbot_manager/admin/routes.py tests/test_admin_routes.py tests/test_phase4_knowledge_graph.py README.md
git commit -m "refactor: move knowledge management to external service"
```

---

### Task 6: Phase 2 Verification and Authorized External-RAG Checkpoint

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Re-verify deployed LightRAG contract before real smoke**

Against the actual authorized deployment/version, verify the configured query/health paths and authentication method without printing the API key. Record version and any adapter mapping difference in `HANDOFF.md`; if `/health` or `/query` differs, update `LightRAGClient` tests and implementation in the same reviewed task before continuing.

- [ ] **Step 2: Run focused tests**

```bash
rtk uv run pytest -q \
  tests/test_credentials.py \
  tests/test_knowledge_client.py \
  tests/test_knowledge_services_admin.py \
  tests/test_bot_admin.py \
  tests/test_secret_encryption.py \
  tests/test_failure_handling.py
```

- [ ] **Step 3: Run full verification**

```bash
rtk uv run pytest -q
rtk python -m compileall -q apps/api
rtk uv lock --check
git diff --check
```

- [ ] **Step 4: Manual checkpoint**

In the admin UI:

```text
Create or edit one authorized Knowledge Service
Confirm stored API key is masked and cannot be revealed
Test Connection -> Healthy
Test Retrieval -> grounded response + references when upstream returns them
Open RAG Manager -> external WebUI
Default Bot / Knowledge -> bind the service to Draft
Confirm Live version binding did not change
Confirm local Upload/Graph links are absent from primary navigation
```

- [ ] **Step 5: Update handoff and commit**

Record external service version/API path, focused/full test results, manual results, and next plan:

`docs/superpowers/plans/2026-09-13-phase-3-runtime-conversations.md`

```bash
git add HANDOFF.md
git commit -m "docs: hand off external knowledge phase"
```
