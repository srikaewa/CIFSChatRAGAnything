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
