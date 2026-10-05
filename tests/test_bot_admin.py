from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, BotConfigRule, BotConfigVersion, KnowledgeService


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


def test_create_bot_starts_draft_setup(client: TestClient) -> None:
    login(client)
    page = client.get("/bots")
    csrf = csrf_from_page(page.text)

    response = client.post(
        "/bots",
        data={
            "csrf_token": csrf,
            "name": "Admissions Bot",
            "description": "Admissions support",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Admissions Bot")).one()
        assert bot.lifecycle_status == "draft"
        assert bot.live_config_version_id is None
        assert bot.draft_config_version_id is not None
        draft = session.get(BotConfigVersion, bot.draft_config_version_id)
        assert draft is not None
        assert draft.status == "draft"
        assert draft.version_number == 1
        assert response.headers["location"] == f"/bots/{bot.id}/setup"


def test_behavior_save_changes_draft_not_live(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
        live_id = bot.live_config_version_id
        live = session.get(BotConfigVersion, live_id)
        assert live is not None
        original_prompt = live.system_prompt

    page = client.get(f"/bots/{bot_id}/behavior")
    assert page.status_code == 200
    csrf = csrf_from_page(page.text)
    response = client.post(
        f"/bots/{bot_id}/behavior",
        data={
            "csrf_token": csrf,
            "system_prompt": "Draft-only behavior",
            "tone": "friendly",
            "language": "Thai",
            "response_style": "concise",
            "fallback_reply": "Please contact staff.",
            "fallback_policy": "reply",
            "custom_instructions": "Use official CIFS information.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id is not None
        live = session.get(BotConfigVersion, live_id)
        draft = session.get(BotConfigVersion, bot.draft_config_version_id)
        assert live is not None and draft is not None
        assert live.system_prompt == original_prompt
        assert draft.system_prompt == "Draft-only behavior"
        assert draft.tone == "friendly"


def test_rule_create_is_scoped_to_draft_version(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
        live_id = bot.live_config_version_id

    page = client.get(f"/bots/{bot_id}/rules")
    assert page.status_code == 200
    csrf = csrf_from_page(page.text)
    response = client.post(
        f"/bots/{bot_id}/rules",
        data={
            "csrf_token": csrf,
            "name": "admissions",
            "priority": "5",
            "match_type": "contains",
            "pattern": "admissions",
            "action": "RESPOND",
            "reply_text": "Admissions information",
            "escalate_message": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.draft_config_version_id is not None
        rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.pattern == "admissions")
        ).all()
        assert len(rules) == 1
        assert rules[0].config_version_id == bot.draft_config_version_id
        assert rules[0].config_version_id != live_id


def test_setup_wizard_shows_profile_knowledge_behavior_channels_test_steps(client: TestClient) -> None:
    login(client)
    page = client.get("/bots/1/setup")
    assert page.status_code == 200
    labels = ["Profile", "Knowledge", "Behavior", "Channels", "Test"]
    positions = [page.text.index(f">{label}<") for label in labels]
    assert positions == sorted(positions)
    for href in (
        "/bots/1",
        "/bots/1/knowledge",
        "/bots/1/behavior",
        "/channels",
        "/bots/1/test",
    ):
        assert f'href="{href}"' in page.text


def test_pause_active_bot_does_not_modify_config_version(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
        live_id = bot.live_config_version_id
        draft_id = bot.draft_config_version_id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/pause",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.lifecycle_status == "paused"
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id == draft_id


def test_ready_transition_uses_draft_readiness_without_publishing(client: TestClient) -> None:
    login(client)
    csrf = csrf_from_page(client.get("/bots").text)
    client.post(
        "/bots",
        data={"csrf_token": csrf, "name": "Ready Bot", "description": "Ready transition"},
        follow_redirects=False,
    )
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Ready Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
        draft_id = bot.draft_config_version_id

    behavior = client.get(f"/bots/{bot_id}/behavior")
    csrf = csrf_from_page(behavior.text)
    client.post(
        f"/bots/{bot_id}/behavior",
        data={
            "csrf_token": csrf,
            "system_prompt": "Help users with CIFS information.",
            "tone": "professional",
            "language": "auto",
            "response_style": "concise",
            "fallback_reply": "Please contact staff.",
            "fallback_policy": "reply",
            "custom_instructions": "Use approved information only.",
        },
        follow_redirects=False,
    )

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/ready",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.lifecycle_status == "ready"
        assert bot.live_config_version_id is None
        assert bot.draft_config_version_id == draft_id


def test_activate_ready_bot_requires_existing_live_config(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot.lifecycle_status = "ready"
        session.add(bot)
        session.commit()
        bot_id = bot.id
        live_id = bot.live_config_version_id
        draft_id = bot.draft_config_version_id

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/activate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.lifecycle_status == "active"
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id == draft_id


def test_resume_paused_bot_checks_live_not_unpublished_draft(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        bot_id = bot.id
        live_id = bot.live_config_version_id

    behavior = client.get(f"/bots/{bot_id}/behavior")
    csrf = csrf_from_page(behavior.text)
    client.post(
        f"/bots/{bot_id}/behavior",
        data={
            "csrf_token": csrf,
            "system_prompt": "",
            "tone": "professional",
            "language": "auto",
            "response_style": "concise",
            "fallback_reply": "Please contact staff.",
            "fallback_policy": "reply",
            "custom_instructions": "",
        },
        follow_redirects=False,
    )
    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        bot.lifecycle_status = "paused"
        draft_id = bot.draft_config_version_id
        assert draft_id is not None
        session.add(bot)
        session.commit()

    csrf = csrf_from_page(client.get(f"/bots/{bot_id}").text)
    response = client.post(
        f"/bots/{bot_id}/resume",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        bot = session.get(Bot, bot_id)
        assert bot is not None
        assert bot.lifecycle_status == "active"
        assert bot.live_config_version_id == live_id
        assert bot.draft_config_version_id == draft_id


def test_workspace_pages_share_bot_navigation(client: TestClient) -> None:
    login(client)
    for path in ("/bots/1", "/bots/1/knowledge", "/bots/1/behavior", "/bots/1/rules"):
        page = client.get(path)
        assert page.status_code == 200
        assert 'aria-label="Bot workspace navigation"' in page.text
        for href in (
            "/bots/1",
            "/bots/1/behavior",
            "/bots/1/knowledge",
            "/channels",
            "/bots/1/test",
            "/conversations?bot_id=1",
            "/bots/1/versions",
        ):
            assert f'href="{href}"' in page.text
