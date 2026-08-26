import base64
import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.admin.routes import index_document_task
from chatbot_manager.channel_config import save_channel
from chatbot_manager.db import get_engine
from chatbot_manager.models import AssistantSettings, Channel, ChatEvent, KnowledgeDocument
from chatbot_manager.settings import Settings, validate_deployment_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    return page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def messenger_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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
def test_nonlocal_deployment_rejects_placeholder_encryption_keys(encryption_key: str) -> None:
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


def test_authenticity_secrets_are_required_for_channel_readiness(client: TestClient) -> None:
    with Session(get_engine()) as session:
        messenger = save_channel(
            session,
            "messenger",
            enabled=True,
            incoming_credentials={"verify_token": "verify", "page_access_token": "page-token"},
        )
        telegram = save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "bot-token"},
        )

        assert messenger.status == "not_configured"
        assert telegram.status == "not_configured"


def test_telegram_setup_refuses_missing_webhook_secret(client: TestClient) -> None:
    with Session(get_engine()) as session:
        save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "bot-token"},
        )

    csrf_token = login(client)
    response = client.post(
        "/channels/telegram/setup-webhook",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/channels?error=Telegram+webhook+secret+is+not+configured"


def test_disabled_provider_webhooks_do_not_process_events(client: TestClient) -> None:
    line_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "message": {"type": "text", "text": "hello"},
                    "source": {"userId": "line-user"},
                    "replyToken": "line-reply",
                }
            ]
        }
    ).encode("utf-8")
    messenger_body = json.dumps(
        {
            "entry": [
                {
                    "messaging": [
                        {"sender": {"id": "messenger-user"}, "message": {"text": "hello"}}
                    ]
                }
            ]
        }
    ).encode("utf-8")
    telegram_body = json.dumps(
        {
            "update_id": 1,
            "message": {
                "text": "hello",
                "chat": {"id": 123},
                "from": {"id": 456},
            },
        }
    ).encode("utf-8")
    channels = [
        (
            "line",
            {"channel_secret": "line-secret", "channel_access_token": "line-token"},
            line_body,
            {"x-line-signature": line_signature(line_body, "line-secret")},
        ),
        (
            "messenger",
            {"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"},
            messenger_body,
            {"x-hub-signature-256": messenger_signature(messenger_body, "app-secret")},
        ),
        (
            "telegram",
            {"bot_token": "bot-token", "webhook_secret": "telegram-secret"},
            telegram_body,
            {"x-telegram-bot-api-secret-token": "telegram-secret"},
        ),
    ]

    with Session(get_engine()) as session:
        for provider, credentials, _, _ in channels:
            session.add(
                Channel(
                    provider=provider,
                    enabled=False,
                    display_name=provider.title(),
                    status="configured",
                    credential_json=json.dumps(credentials),
                )
            )
        session.commit()

    for provider, _, body, headers in channels:
        response = client.post(
            f"/webhooks/{provider}",
            content=body,
            headers={"content-type": "application/json", **headers},
        )
        assert response.status_code == 200
        assert response.json() == {"processed": 0}

    with Session(get_engine()) as session:
        assert session.exec(select(ChatEvent)).all() == []


def test_disabled_messenger_channel_refuses_verification(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="messenger",
                enabled=False,
                display_name="Messenger",
                status="configured",
                credential_json=json.dumps(
                    {"verify_token": "verify", "page_access_token": "page-token", "app_secret": "app-secret"}
                ),
            )
        )
        session.commit()

    response = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "challenge"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_index_failure_persists_only_safe_error(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    class FailingRagService:
        async def index_document(self, path: Path, rag_doc_id: str) -> None:
            raise RuntimeError("index-secret-should-not-leak")

    source = tmp_path / "stored.txt"
    source.write_text("content", encoding="utf-8")
    with Session(get_engine()) as session:
        session.add(AssistantSettings(rag_enabled=True))
        session.add(KnowledgeDocument(filename="menu.txt", path=str(source), rag_doc_id="knowledge-1"))
        session.commit()

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FailingRagService())
    await index_document_task(1, str(source))

    with Session(get_engine()) as session:
        document = session.get(KnowledgeDocument, 1)
        assert document is not None
        assert document.status == "failed"
        assert document.error == "Knowledge indexing failed. Retry indexing."
        assert "index-secret-should-not-leak" not in document.error
    assert "index-secret-should-not-leak" not in caplog.text


def test_test_chat_failure_uses_safe_error_code_and_message(client: TestClient, monkeypatch, caplog) -> None:
    class FailingRagService:
        async def answer(self, question: str, system_prompt: str) -> str:
            raise RuntimeError("test-chat-secret-should-not-leak")

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FailingRagService())
    with Session(get_engine()) as session:
        session.add(AssistantSettings(rag_enabled=True, fallback_reply="Safe fallback."))
        session.commit()
    csrf_token = login(client)

    response = client.post(
        "/test-chat",
        data={"csrf_token": csrf_token, "message": "unknown question"},
    )

    assert response.status_code == 200
    assert "Safe fallback." in response.text
    assert "Unable to generate a response. The fallback reply was used." in response.text
    assert "test-chat-secret-should-not-leak" not in response.text
    with Session(get_engine()) as session:
        event = session.exec(select(ChatEvent)).one()
        assert event.error == "response_generation_failed"
        assert "test-chat-secret-should-not-leak" not in event.error
    assert "test-chat-secret-should-not-leak" not in caplog.text


def test_each_admin_post_form_has_its_own_csrf_token(client: TestClient) -> None:
    login(client)

    for path in ("/", "/channels", "/rules", "/assistant", "/test-chat", "/knowledge"):
        response = client.get(path)
        assert response.status_code == 200
        post_forms = re.findall(
            r'<form\b(?=[^>]*\bmethod=["\']post["\'])[^>]*>.*?</form>',
            response.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        assert post_forms, f"No POST forms found on {path}"
        for form in post_forms:
            assert re.search(
                r'<input\b(?=[^>]*\bname=["\']csrf_token["\'])(?=[^>]*\bvalue=["\'][^"\']+["\'])[^>]*>',
                form,
                flags=re.IGNORECASE,
            ), f"POST form without a non-empty CSRF token on {path}"
