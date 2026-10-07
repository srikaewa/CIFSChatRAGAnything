import json
from uuid import uuid4

from sqlmodel import Session, select

from chatbot_manager.credentials import read_credential, store_credential
from chatbot_manager.models import Bot, Channel, ChannelConnection
from chatbot_manager.security import decrypt_secret
from chatbot_manager.settings import get_settings


def get_channel_connection(session: Session, connection_id: int) -> ChannelConnection:
    connection = session.get(ChannelConnection, connection_id)
    if connection is None:
        raise LookupError("Channel connection not found")
    return connection


def resolve_connection_credentials(
    session: Session,
    connection: ChannelConnection,
) -> dict[str, str]:
    return read_credential(session, connection.credential_id)


def _metadata(connection: ChannelConnection) -> dict[str, object]:
    try:
        loaded = json.loads(connection.metadata_json)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _decode_legacy_credentials(channel: Channel) -> dict[str, str]:
    try:
        raw = json.loads(channel.credential_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    key = get_settings().app_encryption_key
    return {
        str(field): decrypt_secret(str(value), key)
        for field, value in raw.items()
        if value is not None
    }


def _connection_for_legacy_id(
    session: Session,
    legacy_channel_id: int,
) -> ChannelConnection | None:
    for connection in session.exec(select(ChannelConnection)).all():
        if _metadata(connection).get("legacy_channel_id") == legacy_channel_id:
            return connection
    return None


def migrate_legacy_channels(session: Session) -> None:
    default_bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).first()
    if default_bot is None or default_bot.id is None:
        return

    for legacy in session.exec(select(Channel)).all():
        if legacy.id is None or _connection_for_legacy_id(session, legacy.id) is not None:
            continue

        credentials = _decode_legacy_credentials(legacy)
        credential_id = None
        if credentials:
            credential = store_credential(
                session,
                f"channel:{legacy.provider}",
                credentials,
            )
            credential_id = credential.id

        connection = ChannelConnection(
            bot_id=legacy.bot_id or default_bot.id,
            provider=legacy.provider,
            display_name=legacy.display_name,
            webhook_key=uuid4().hex,
            credential_id=credential_id,
            enabled=legacy.enabled,
            status=legacy.status,
            metadata_json=json.dumps({"legacy_channel_id": legacy.id}, sort_keys=True),
        )
        session.add(connection)
        session.commit()
