import base64
import hashlib
import hmac

import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"



def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    assert marker in page.text
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def test_messenger_verification(client: TestClient) -> None:
    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "challenge-1"},
    )

    assert response.status_code == 403


def test_messenger_verification_uses_saved_channel_verify_token(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                credential_json='{"verify_token": "db-verify", "page_access_token": "", "app_secret": ""}',
            )
        )
        session.commit()

    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "db-verify", "hub.challenge": "challenge-1"},
    )

    assert response.status_code == 200
    assert response.text == "challenge-1"


def test_line_webhook_rejects_bad_signature(client: TestClient) -> None:
    response = client.post("/webhooks/line", content=b'{"events":[]}', headers={"x-line-signature": "bad"})

    assert response.status_code == 401


def test_line_webhook_accepts_empty_events(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
    body = b'{"events":[]}'

    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "db-secret")})

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


def test_messenger_webhook_accepts_empty_entries(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                credential_json='{"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"}',
            )
        )
        session.commit()
    body = b'{"entry":[]}'
    response = client.post(
        "/webhooks/messenger",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": messenger_signature(body, "app-secret"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


@respx.mock
def test_line_webhook_processes_text_rule_and_logs(client: TestClient) -> None:
    csrf_token = login(client)
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    body = (
        b'{"events":[{"type":"message","replyToken":"reply-token","source":{"userId":"user-1"},'
        b'"message":{"type":"text","text":"price please"}}]}'
    )

    response = client.post("/webhooks/line", content=body, headers={"x-line-signature": line_signature(body, "db-secret")})

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content

    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text


@respx.mock
def test_line_webhook_uses_saved_channel_credentials(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json='{"channel_secret": "db-secret", "channel_access_token": "db-token"}',
            )
        )
        session.commit()
    csrf_token = login(client)
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    body = (
        b'{"events":[{"type":"message","replyToken":"reply-token","source":{"userId":"user-1"},'
        b'"message":{"type":"text","text":"price please"}}]}'
    )

    response = client.post(
        "/webhooks/line",
        content=body,
        headers={"x-line-signature": line_signature(body, "db-secret")},
    )

    assert response.status_code == 200
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer db-token"
