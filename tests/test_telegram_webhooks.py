import json

import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from chatbot_manager.channels.telegram import TELEGRAM_API
from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel


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


def test_telegram_webhook_rejects_bad_secret(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="telegram",
                enabled=True,
                display_name="Telegram",
                credential_json=json.dumps({"bot_token": "token", "webhook_secret": "mysecret"}),
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/telegram",
        json={"update_id": 1, "message": {"text": "hello", "chat": {"id": 1}, "from": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 403
    assert "Invalid Telegram webhook secret" in response.text


def test_telegram_webhook_accepts_correct_secret(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="telegram",
                enabled=True,
                display_name="Telegram",
                credential_json=json.dumps({"bot_token": "token", "webhook_secret": "mysecret"}),
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "mysecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


def test_telegram_webhook_accepts_non_message_events(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="telegram",
                enabled=True,
                display_name="Telegram",
                credential_json=json.dumps({"bot_token": "token", "webhook_secret": "mysecret"}),
            )
        )
        session.commit()
    response = client.post(
        "/webhooks/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "mysecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 0}


def test_telegram_webhook_accepts_without_secret(client: TestClient) -> None:
    response = client.post(
        "/webhooks/telegram",
        json={"update_id": 1},
    )

    assert response.status_code == 403


@respx.mock
def test_telegram_webhook_processes_text_rule_and_logs(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/rules",
        data={"csrf_token": csrf_token, "pattern": "price", "match_type": "contains", "reply_text": "Price is 100.", "priority": "10"},
        follow_redirects=False,
    )
    route = respx.post(f"{TELEGRAM_API}token/sendMessage").mock(return_value=Response(200, json={"ok": True}))
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="telegram",
                enabled=True,
                display_name="Telegram",
                credential_json=json.dumps({"bot_token": "token", "webhook_secret": "mysecret"}),
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/telegram",
        json={
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 123, "is_bot": False, "first_name": "User"},
                "chat": {"id": 456, "type": "private"},
                "text": "price please",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "mysecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert route.called
    assert b"Price is 100." in route.calls[0].request.content

    logs = client.get("/logs")
    assert "price please" in logs.text
    assert "Price is 100." in logs.text


def test_telegram_channel_config_can_be_saved(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/channels/telegram",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "bot_token": "tg-bot-token-123",
            "webhook_secret": "tg-secret-456",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] in ("/channels?saved=1", "/channels")
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert channel.provider == "telegram"
        assert channel.enabled is True
        assert json.loads(channel.credential_json) == {
            "bot_token": "tg-bot-token-123",
            "webhook_secret": "tg-secret-456",
        }

    page = client.get("/channels")
    assert "tg-bot-token-123" not in page.text
    assert "tg-secret-456" not in page.text


def test_telegram_channel_config_blank_keeps_existing(client: TestClient) -> None:
    csrf_token = login(client)
    client.post(
        "/channels/telegram",
        data={"csrf_token": csrf_token, "enabled": "on", "bot_token": "tg-bot-token", "webhook_secret": "tg-secret"},
        follow_redirects=False,
    )

    response = client.post(
        "/channels/telegram",
        data={"csrf_token": csrf_token, "enabled": "on", "bot_token": "", "webhook_secret": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert json.loads(channel.credential_json) == {
            "bot_token": "tg-bot-token",
            "webhook_secret": "tg-secret",
        }


def test_channels_page_shows_telegram_webhook_url(client: TestClient) -> None:
    login(client)

    response = client.get("/channels")

    assert response.status_code == 200
    assert "/webhooks/telegram" in response.text
    assert "Telegram" in response.text
