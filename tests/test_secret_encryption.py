from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.channel_connections import resolve_connection_credentials
from chatbot_manager.db import get_engine
from chatbot_manager.models import ChannelConnection, Credential
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


def test_bot_channel_secrets_use_common_encrypted_credential_only(
    client: TestClient,
) -> None:
    csrf_token = login(client)

    response = client.post(
        "/bots/1/channels/line",
        data={
            "csrf_token": csrf_token,
            "enabled": "on",
            "channel_secret": "active-line-secret",
            "channel_access_token": "active-line-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        connection = session.exec(
            select(ChannelConnection)
            .where(ChannelConnection.bot_id == 1)
            .where(ChannelConnection.provider == "line")
        ).one()
        assert connection.credential_id is not None
        credential = session.get(Credential, connection.credential_id)
        assert credential is not None
        assert "active-line-secret" not in credential.encrypted_payload
        assert "active-line-token" not in credential.encrypted_payload
        assert "active-line-secret" not in connection.metadata_json
        assert "active-line-token" not in connection.metadata_json
        assert resolve_connection_credentials(session, connection) == {
            "channel_secret": "active-line-secret",
            "channel_access_token": "active-line-token",
        }
        assert decrypt_secret(
            credential.encrypted_payload,
            get_settings().app_encryption_key,
        )
