import base64
import hashlib
import hmac
import json

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from chatbot_manager.channel_config import resolve_channel
from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel
from chatbot_manager.settings import get_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    return page.text.split(marker, 1)[1].split('"', 1)[0]


@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "expected"),
    [
        (False, {}, "not_configured", "disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "failed",
        ),
    ],
)
def test_line_resolved_channel_state(client: TestClient, enabled, credentials, saved_status, expected) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()
        resolved = resolve_channel(session, get_settings(), "line")

    assert resolved.state == expected


@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "label"),
    [
        (False, {}, "not_configured", "Disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "Incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "Ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "Failed",
        ),
    ],
)
def test_channels_page_renders_line_state(client: TestClient, enabled, credentials, saved_status, label) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()

    login(client)
    response = client.get("/channels")

    assert response.status_code == 200
    line_panel = response.text.split("<h2>LINE</h2>", 1)[1].split("</article>", 1)[0]
    assert f"Status: {label}" in line_panel



def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.parametrize(
    ("provider", "credentials", "body", "headers"),
    [
        (
            "line",
            {"channel_secret": "line-secret"},
            b'{"events":[]}',
            {"x-line-signature": line_signature(b'{"events":[]}', "line-secret")},
        ),
        (
            "messenger",
            {"verify_token": "verify", "page_access_token": "page-token"},
            b'{"entry":[]}',
            {"x-hub-signature-256": "sha256=missing-app-secret"},
        ),
        (
            "telegram",
            {"bot_token": "bot-token"},
            b'{"update_id":1}',
            {"x-telegram-bot-api-secret-token": "missing-secret"},
        ),
    ],
)
def test_enabled_incomplete_provider_returns_503_before_processing(
    client: TestClient, provider, credentials, body, headers
) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider=provider,
                enabled=True,
                display_name=provider.title(),
                status="incomplete",
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()

    response = client.post(
        f"/webhooks/{provider}",
        content=body,
        headers={"content-type": "application/json", **headers},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == f"{provider} channel is not ready"


def test_failed_provider_returns_503_before_processing(client: TestClient) -> None:
    body = b'{"events":[]}'
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                status="failed",
                credential_json=json.dumps(
                    {"channel_secret": "line-secret", "channel_access_token": "line-token"}
                ),
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/line",
        content=body,
        headers={"x-line-signature": line_signature(body, "line-secret")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "line channel is not ready"


@pytest.mark.parametrize("provider", ["line", "messenger", "telegram"])
def test_authenticated_malformed_json_returns_400(client: TestClient, provider: str) -> None:
    body = b'{"broken":'
    credentials_by_provider = {
        "line": {"channel_secret": "line-secret", "channel_access_token": "line-token"},
        "messenger": {
            "verify_token": "verify",
            "page_access_token": "page-token",
            "app_secret": "app-secret",
        },
        "telegram": {"bot_token": "bot-token", "webhook_secret": "telegram-secret"},
    }
    headers_by_provider = {
        "line": {"x-line-signature": line_signature(body, "line-secret")},
        "messenger": {"x-hub-signature-256": messenger_signature(body, "app-secret")},
        "telegram": {"x-telegram-bot-api-secret-token": "telegram-secret"},
    }
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider=provider,
                enabled=True,
                display_name=provider.title(),
                status="ready",
                credential_json=json.dumps(credentials_by_provider[provider]),
            )
        )
        session.commit()

    safe_client = TestClient(client.app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/webhooks/{provider}",
        content=body,
        headers={"content-type": "application/json", **headers_by_provider[provider]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed JSON payload"


@respx.mock
def test_messenger_webhook_processes_text_rule_end_to_end(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/rules",
        data={
            "csrf_token": csrf_token,
            "pattern": "price",
            "match_type": "contains",
            "reply_text": "Price is 100.",
            "priority": "10",
        },
        follow_redirects=False,
    )
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=True,
                display_name="Messenger",
                status="ready",
                credential_json=json.dumps(
                    {
                        "verify_token": "verify",
                        "page_access_token": "page-token",
                        "app_secret": "app-secret",
                    }
                ),
            )
        )
        session.commit()

    route = respx.post("https://graph.facebook.com/v20.0/me/messages").mock(
        return_value=Response(200, json={"recipient_id": "user-1", "message_id": "m1"})
    )
    body = b'{"entry":[{"messaging":[{"sender":{"id":"user-1"},"message":{"text":"price please"}}]}]}'

    response = client.post(
        "/webhooks/messenger",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": messenger_signature(body, "app-secret"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content
    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text
