import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from chatbot_manager.channel_config import channel_credentials
from chatbot_manager.db import get_engine
from chatbot_manager.models import AssistantSettings, Channel
from chatbot_manager.rag.service import rag_service_from_assistant
from chatbot_manager.security import decrypt_secret, encrypt_secret
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
    assert marker in page.text
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def test_secret_encryption_round_trips_and_reads_legacy_plaintext() -> None:
    key = "test-encryption-key"
    encrypted = encrypt_secret("provider-token", key)

    assert encrypted.startswith("enc:v1:")
    assert "provider-token" not in encrypted
    assert decrypt_secret(encrypted, key) == "provider-token"
    assert decrypt_secret("legacy-token", key) == "legacy-token"
    assert encrypt_secret("", key) == ""


def test_channel_secrets_are_encrypted_at_rest_and_decrypted_for_use(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "line-secret-123",
            "channel_access_token": "line-token-456",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert "line-secret-123" not in channel.credential_json
        assert "line-token-456" not in channel.credential_json
        stored = json.loads(channel.credential_json)
        assert stored["channel_secret"].startswith("enc:v1:")
        assert stored["channel_access_token"].startswith("enc:v1:")
        assert channel_credentials(session, get_settings(), "line") == {
            "channel_secret": "line-secret-123",
            "channel_access_token": "line-token-456",
        }


def test_legacy_channel_secrets_are_rewritten_encrypted_on_next_save(client: TestClient) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=True,
                display_name="LINE",
                credential_json=json.dumps(
                    {"channel_secret": "legacy-secret", "channel_access_token": "legacy-token"}
                ),
            )
        )
        session.commit()

    csrf_token = login(client)
    response = client.post(
        "/channels/line",
        data={"csrf_token": csrf_token, "enabled": "on", "channel_secret": "", "channel_access_token": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        channel = session.get(Channel, 1)
        assert channel is not None
        assert "legacy-secret" not in channel.credential_json
        assert "legacy-token" not in channel.credential_json
        assert channel_credentials(session, get_settings(), "line") == {
            "channel_secret": "legacy-secret",
            "channel_access_token": "legacy-token",
        }


def test_assistant_api_key_is_encrypted_at_rest_and_decrypted_for_rag(client: TestClient) -> None:
    csrf_token = login(client)

    response = client.post(
        "/assistant",
        data={
            "csrf_token": csrf_token,
            "system_prompt": "Use docs.",
            "fallback_reply": "Ask staff.",
            "rag_enabled": "on",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "llm-secret-789",
            "llm_model": "gpt-4o-mini",
            "vision_model": "gpt-4o-mini",
            "embedding_model": "text-embedding-3-small",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        assistant = session.get(AssistantSettings, 1)
        assert assistant is not None
        assert assistant.llm_api_key.startswith("enc:v1:")
        assert "llm-secret-789" not in assistant.llm_api_key
        assert decrypt_secret(assistant.llm_api_key, get_settings().app_encryption_key) == "llm-secret-789"
        assert rag_service_from_assistant(assistant).llm_api_key == "llm-secret-789"

    page = client.get("/assistant")
    assert "llm-secret-789" not in page.text
    assert "llm...789" in page.text
