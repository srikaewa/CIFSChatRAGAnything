import re

from fastapi.testclient import TestClient

from chatbot_manager.security import make_csrf_token, verify_csrf_token


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def rendered_csrf_token(
    client: TestClient,
    path: str = "/bots/1/channels",
) -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def test_csrf_tokens_are_bound_to_the_authenticated_email() -> None:
    token = make_csrf_token("admin@example.local")

    assert verify_csrf_token(token, "admin@example.local") is True
    assert verify_csrf_token(token, "other@example.local") is False
    assert verify_csrf_token(f"{token}tampered", "admin@example.local") is False


def test_authenticated_post_without_csrf_token_is_rejected(
    client: TestClient,
) -> None:
    login(client)

    response = client.post(
        "/bots/1/channels/line",
        data={
            "enabled": "on",
            "channel_secret": "secret",
            "channel_access_token": "token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_authenticated_post_with_foreign_csrf_token_is_rejected(
    client: TestClient,
) -> None:
    login(client)

    response = client.post(
        "/bots/1/channels/line",
        data={
            "csrf_token": make_csrf_token("other@example.local"),
            "enabled": "on",
            "channel_secret": "secret",
            "channel_access_token": "token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_rendered_csrf_token_allows_authenticated_mutation(
    client: TestClient,
) -> None:
    login(client)
    token = rendered_csrf_token(client)

    response = client.post(
        "/bots/1/channels/line",
        data={
            "csrf_token": token,
            "enabled": "on",
            "channel_secret": "secret",
            "channel_access_token": "token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_final_admin_post_forms_render_csrf_tokens(client: TestClient) -> None:
    login(client)

    for path in ("/bots/1/channels", "/knowledge-services", "/users"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'name="csrf_token"' in response.text
