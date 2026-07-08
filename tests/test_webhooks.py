import base64
import hashlib
import hmac

import respx
from fastapi.testclient import TestClient
from httpx import Response


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


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


@respx.mock
def test_line_webhook_processes_text_rule_and_logs(client: TestClient) -> None:
    login(client)
    client.post(
        "/rules",
        data={"pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    body = (
        b'{"events":[{"type":"message","replyToken":"reply-token","source":{"userId":"user-1"},'
        b'"message":{"type":"text","text":"price please"}}]}'
    )

    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "")})

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content

    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text
