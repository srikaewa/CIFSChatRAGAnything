import json
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from chatbot_manager.models import Channel, utc_now
from chatbot_manager.security import decrypt_secret, encrypt_secret, mask_secret
from chatbot_manager.settings import Settings, get_settings


@dataclass(frozen=True)
class ChannelDefinition:
    provider: str
    display_name: str
    fields: tuple[str, ...]
    required_fields: tuple[str, ...]


CHANNEL_DEFINITIONS: dict[str, ChannelDefinition] = {
    "line": ChannelDefinition(
        provider="line",
        display_name="LINE",
        fields=("channel_secret", "channel_access_token"),
        required_fields=("channel_secret", "channel_access_token"),
    ),
    "messenger": ChannelDefinition(
        provider="messenger",
        display_name="Messenger",
        fields=("verify_token", "page_access_token", "app_secret"),
        required_fields=("verify_token", "page_access_token"),
    ),
    "telegram": ChannelDefinition(
        provider="telegram",
        display_name="Telegram",
        fields=("bot_token", "webhook_secret"),
        required_fields=("bot_token",),
    ),
}


def env_credentials(settings: Settings, provider: str) -> dict[str, str]:
    if provider == "line":
        return {
            "channel_secret": settings.line_channel_secret,
            "channel_access_token": settings.line_channel_access_token,
        }
    if provider == "messenger":
        return {
            "verify_token": settings.messenger_verify_token,
            "page_access_token": settings.messenger_page_access_token,
            "app_secret": settings.messenger_app_secret,
        }
    if provider == "telegram":
        return {
            "bot_token": settings.telegram_bot_token,
            "webhook_secret": "",
        }
    raise KeyError(provider)


def get_channel(session: Session, provider: str) -> Channel | None:
    return session.exec(select(Channel).where(Channel.provider == provider)).first()


def decode_credentials(channel: Channel | None, encryption_key: str | None = None) -> dict[str, str]:
    if channel is None:
        return {}
    try:
        raw = json.loads(channel.credential_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    key = encryption_key or get_settings().app_encryption_key
    return {
        str(field): decrypt_secret(str(value), key)
        for field, value in raw.items()
        if value is not None
    }


def encode_credentials(credentials: dict[str, str], encryption_key: str) -> dict[str, str]:
    return {field: encrypt_secret(value, encryption_key) for field, value in credentials.items()}


def channel_credentials(session: Session, settings: Settings, provider: str) -> dict[str, str]:
    credentials = env_credentials(settings, provider)
    for key, value in decode_credentials(get_channel(session, provider), settings.app_encryption_key).items():
        if value:
            credentials[key] = value
    return credentials


def save_channel(
    session: Session,
    provider: str,
    enabled: bool,
    incoming_credentials: dict[str, str],
) -> Channel:
    definition = CHANNEL_DEFINITIONS[provider]
    channel = get_channel(session, provider)
    existing = decode_credentials(channel)
    credentials: dict[str, str] = {}
    for field in definition.fields:
        incoming = incoming_credentials.get(field, "").strip()
        credentials[field] = incoming if incoming else existing.get(field, "")

    if channel is None:
        channel = Channel(provider=provider, display_name=definition.display_name)
    channel.enabled = enabled
    channel.display_name = definition.display_name
    channel.credential_json = json.dumps(encode_credentials(credentials, get_settings().app_encryption_key))
    channel.status = "configured" if is_configured(credentials, definition.required_fields) else "not_configured"
    channel.updated_at = utc_now()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def update_channel_credentials(session: Session, provider: str, updates: dict[str, str]) -> Channel:
    definition = CHANNEL_DEFINITIONS[provider]
    channel = get_channel(session, provider)
    credentials = decode_credentials(channel)
    credentials.update({key: value for key, value in updates.items() if value is not None})
    if channel is None:
        channel = Channel(provider=provider, display_name=definition.display_name)
    channel.display_name = definition.display_name
    channel.credential_json = json.dumps(encode_credentials(credentials, get_settings().app_encryption_key))
    channel.status = "configured" if is_configured(credentials, definition.required_fields) else "not_configured"
    channel.updated_at = utc_now()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def is_configured(credentials: dict[str, str], required_fields: tuple[str, ...]) -> bool:
    return all(bool(credentials.get(field, "").strip()) for field in required_fields)


def channel_cards(session: Session, settings: Settings) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for definition in CHANNEL_DEFINITIONS.values():
        saved = get_channel(session, definition.provider)
        credentials = channel_credentials(session, settings, definition.provider)
        cards.append(
            {
                "provider": definition.provider,
                "display_name": definition.display_name,
                "enabled": saved.enabled if saved is not None else True,
                "configured": is_configured(credentials, definition.required_fields),
                "credentials": credentials,
                "masked": {field: mask_secret(credentials.get(field, "")) for field in definition.fields},
            }
        )
    return cards
