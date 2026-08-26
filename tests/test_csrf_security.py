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


def rendered_csrf_token(client: TestClient, path: str = "/rules") -> str:
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


def test_authenticated_post_without_csrf_token_is_rejected(client: TestClient) -> None:
    login(client)

    response = client.post(
        "/rules",
        data={"pattern": "price", "match_type": "contains", "reply_text": "Price is 100."},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_authenticated_post_with_foreign_csrf_token_is_rejected(client: TestClient) -> None:
    login(client)

    response = client.post(
        "/rules",
        data={
            "csrf_token": make_csrf_token("other@example.local"),
            "pattern": "price",
            "match_type": "contains",
            "reply_text": "Price is 100.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_rendered_csrf_token_allows_authenticated_mutation(client: TestClient) -> None:
    login(client)
    token = rendered_csrf_token(client)

    response = client.post(
        "/rules",
        data={
            "csrf_token": token,
            "pattern": "price",
            "match_type": "contains",
            "reply_text": "Price is 100.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_all_admin_post_forms_render_csrf_tokens(client: TestClient) -> None:
    login(client)

    for path in ("/", "/channels", "/rules", "/assistant", "/test-chat", "/knowledge"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'name="csrf_token"' in response.text
