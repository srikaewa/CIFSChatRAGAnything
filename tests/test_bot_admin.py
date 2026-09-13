from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, BotConfigVersion, KnowledgeService


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf_from_page(html: str) -> str:
    marker = 'name="csrf_token" value="'
    assert marker in html
    return html.split(marker, 1)[1].split('"', 1)[0]


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


def test_changing_bot_knowledge_creates_or_updates_draft_only(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        service = KnowledgeService(
            name="RAG B",
            service_type="lightrag",
            api_base_url="https://rag-b.test",
            webui_url="https://rag-b.test/webui",
            health_status="healthy",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id

        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        bot_id = bot.id
        live_id = bot.live_config_version_id

    assert service_id is not None
    assert bot_id is not None
    page = client.get(f"/bots/{bot_id}/knowledge")
    assert page.status_code == 200
    csrf = csrf_from_page(page.text)

    response = client.post(
        f"/bots/{bot_id}/knowledge",
        data={"csrf_token": csrf, "knowledge_service_id": str(service_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/bots/{bot_id}/knowledge?saved=1"

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id is not None
        draft = session.get(BotConfigVersion, bot.draft_config_version_id)
        assert draft is not None
        assert draft.status == "draft"
        assert draft.knowledge_service_id == service_id

        live = session.get(BotConfigVersion, live_id)
        assert live is not None
        assert live.knowledge_service_id is None


def test_bot_knowledge_page_shows_live_draft_status_and_external_manager(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        service = KnowledgeService(
            name="Healthy RAG",
            service_type="lightrag",
            api_base_url="https://rag-health.test",
            webui_url="https://rag-health.test/webui",
            health_status="healthy",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        bot_id = bot.id

    assert service_id is not None
    assert bot_id is not None
    csrf = csrf_from_page(client.get(f"/bots/{bot_id}/knowledge").text)
    client.post(
        f"/bots/{bot_id}/knowledge",
        data={"csrf_token": csrf, "knowledge_service_id": str(service_id)},
        follow_redirects=False,
    )

    page = client.get(f"/bots/{bot_id}/knowledge")
    assert page.status_code == 200
    assert "Live binding" in page.text
    assert "Draft binding" in page.text
    assert "Healthy RAG" in page.text
    assert "Healthy" in page.text
    assert "Test Retrieval" in page.text
    assert 'href="https://rag-health.test/webui"' in page.text
    assert 'rel="noopener noreferrer"' in page.text


def test_bot_knowledge_rejects_disabled_service(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        service = KnowledgeService(
            name="Disabled RAG",
            api_base_url="https://rag-disabled.test",
            enabled=False,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        bot_id = bot.id

    assert service_id is not None
    assert bot_id is not None
    csrf = csrf_from_page(client.get(f"/bots/{bot_id}/knowledge").text)
    response = client.post(
        f"/bots/{bot_id}/knowledge",
        data={"csrf_token": csrf, "knowledge_service_id": str(service_id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=knowledge_service_unavailable" in response.headers["location"]
    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.draft_config_version_id is None
