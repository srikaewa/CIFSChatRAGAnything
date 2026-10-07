from fastapi.testclient import TestClient


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_primary_navigation_uses_final_product_surface(client: TestClient) -> None:
    login(client)
    html = client.get("/").text

    assert 'href="/bots"' in html
    assert 'href="/conversations"' in html
    assert 'href="/knowledge-services"' in html
    assert 'href="/analytics"' in html
    for legacy_href in (
        "/assistant",
        "/rules",
        "/channels",
        "/test-chat",
        "/logs",
        "/knowledge",
        "/knowledge-graph",
    ):
        assert f'href="{legacy_href}"' not in html


def test_command_center_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_with_default_owner(client: TestClient) -> None:
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


def test_logout_clears_session(client: TestClient) -> None:
    login(client)
    page = client.get("/bots")
    marker = 'name="csrf_token" value="'
    csrf = page.text.split(marker, 1)[1].split('"', 1)[0]

    response = client.post(
        "/logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
