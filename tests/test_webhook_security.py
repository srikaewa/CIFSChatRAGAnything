import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from chatbot_manager.channels.line import LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel


def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def save_channel(provider: str, credentials: dict[str, str]) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider=provider,
                enabled=True,
                display_name=provider.title(),
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()


def test_provider_validators_reject_blank_authenticity_secrets() -> None:
    body = b'{}'

    assert LineAdapter("", "token").validate_signature(body, "") is False
    assert MessengerAdapter("", "token", "").verify("subscribe", "", "challenge") is None
    assert TelegramAdapter("token", "").validate_webhook(body, "") is False


def test_messenger_signature_validation() -> None:
    body = b'{"entry":[]}'
    adapter = MessengerAdapter("verify", "page-token", "app-secret")

    assert adapter.validate_signature(body, messenger_signature(body, "app-secret")) is True
    assert adapter.validate_signature(body, messenger_signature(body, "wrong-secret")) is False
    assert adapter.validate_signature(body, "") is False


def test_unconfigured_webhooks_fail_closed(client: TestClient) -> None:
    line_response = client.post("/webhooks/line", content=b'{"events":[]}', headers={"x-line-signature": ""})
    messenger_response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "challenge"},
    )
    telegram_response = client.post("/webhooks/telegram", json={"update_id": 1})

    assert line_response.status_code == 401
    assert messenger_response.status_code == 403
    assert telegram_response.status_code == 403


def test_messenger_webhook_rejects_missing_signature(client: TestClient) -> None:
    save_channel(
        "messenger",
        {"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"},
    )

    response = client.post("/webhooks/messenger", content=b'{"entry":[]}', headers={"content-type": "application/json"})

    assert response.status_code == 401


def test_messenger_webhook_accepts_valid_signature(client: TestClient) -> None:
    save_channel(
        "messenger",
        {"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"},
    )
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
