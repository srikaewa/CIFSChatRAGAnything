from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, ChannelConnection, Conversation
from chatbot_manager.settings import Settings, validate_deployment_settings


def test_nonlocal_deployment_rejects_example_encryption_key() -> None:
    example_values = {
        key: value
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="production-session-secret",
        admin_password="production-admin-password",
        app_encryption_key=example_values["APP_ENCRYPTION_KEY"],
        cookie_secure=True,
    )

    with pytest.raises(RuntimeError) as error:
        validate_deployment_settings(settings)

    assert "APP_ENCRYPTION_KEY" in str(error.value)
    assert settings.app_encryption_key not in str(error.value)


@pytest.mark.parametrize("encryption_key", ["", "change-me", "replace-me"])
def test_nonlocal_deployment_rejects_placeholder_encryption_keys(
    encryption_key: str,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="production-session-secret",
        admin_password="production-admin-password",
        app_encryption_key=encryption_key,
        cookie_secure=True,
    )

    with pytest.raises(RuntimeError) as error:
        validate_deployment_settings(settings)

    assert "APP_ENCRYPTION_KEY" in str(error.value)


def test_disabled_connection_webhook_does_not_process_events(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        credential = store_credential(
            session,
            "channel:telegram",
            {"bot_token": "token", "webhook_secret": "secret"},
        )
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="telegram",
            display_name="Telegram",
            webhook_key="disabled-key",
            credential_id=credential.id,
            enabled=False,
            status="disabled",
        )
        session.add(connection)
        session.commit()

    response = client.post(
        "/webhooks/telegram/disabled-key",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"processed": 0}
    with Session(get_engine()) as session:
        assert session.exec(select(Conversation)).all() == []


def test_disabled_messenger_connection_refuses_verification(
    client: TestClient,
) -> None:
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        credential = store_credential(
            session,
            "channel:messenger",
            {
                "verify_token": "verify",
                "page_access_token": "page-token",
                "app_secret": "app-secret",
            },
        )
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="messenger",
            display_name="Messenger",
            webhook_key="disabled-messenger",
            credential_id=credential.id,
            enabled=False,
            status="disabled",
        )
        session.add(connection)
        session.commit()

    response = client.get(
        "/webhooks/messenger/disabled-messenger",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify",
            "hub.challenge": "challenge",
        },
    )

    assert response.status_code == 403


def test_test_settings_ignore_host_and_dotenv_provider_credentials(
    client: TestClient,
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "LINE_CHANNEL_SECRET=dotenv-line-secret\n"
        "LINE_CHANNEL_ACCESS_TOKEN=dotenv-line-token\n"
        "MESSENGER_VERIFY_TOKEN=dotenv-verify-token\n"
        "MESSENGER_PAGE_ACCESS_TOKEN=dotenv-page-token\n"
        "MESSENGER_APP_SECRET=dotenv-app-secret\n"
        "TELEGRAM_BOT_TOKEN=dotenv-bot-token\n"
        "LLM_API_KEY=dotenv-llm-key\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv)
    for removed in (
        "line_channel_secret",
        "line_channel_access_token",
        "messenger_verify_token",
        "messenger_page_access_token",
        "messenger_app_secret",
        "telegram_bot_token",
        "llm_api_key",
    ):
        assert not hasattr(settings, removed)
