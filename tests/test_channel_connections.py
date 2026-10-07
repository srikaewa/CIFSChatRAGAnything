import json

from sqlmodel import Session, select

from chatbot_manager.channel_connections import (
    migrate_legacy_channels,
    resolve_connection_credentials,
)
from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, Channel, ChannelConnection, Credential


def add_legacy_channel(
    session: Session,
    provider: str,
    credentials: dict[str, str],
    *,
    enabled: bool = True,
    status: str = "ready",
) -> Channel:
    channel = Channel(
        provider=provider,
        enabled=enabled,
        display_name=provider.title(),
        status=status,
        credential_json=json.dumps(credentials),
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def test_legacy_channels_migrate_to_default_bot_connections(client) -> None:
    with Session(get_engine()) as session:
        default_bot = session.exec(
            select(Bot).where(Bot.name == "Default Bot")
        ).one()
        add_legacy_channel(
            session,
            "line",
            {
                "channel_secret": "line-secret",
                "channel_access_token": "line-token",
            },
        )

        migrate_legacy_channels(session)
        migrate_legacy_channels(session)

        connections = session.exec(select(ChannelConnection)).all()
        assert len(connections) == 1
        connection = connections[0]
        assert connection.bot_id == default_bot.id
        assert connection.provider == "line"
        assert connection.enabled is True
        assert connection.status == "ready"
        assert connection.webhook_key
        assert resolve_connection_credentials(session, connection) == {
            "channel_secret": "line-secret",
            "channel_access_token": "line-token",
        }
        credential = session.get(Credential, connection.credential_id)
        assert credential is not None
        assert credential.credential_type == "channel:line"
        assert "line-secret" not in credential.encrypted_payload


def test_two_line_connections_can_belong_to_different_bots(client) -> None:
    with Session(get_engine()) as session:
        first_bot = session.exec(
            select(Bot).where(Bot.name == "Default Bot")
        ).one()
        second_bot = Bot(name="Second Bot", description="Another chatbot")
        session.add(second_bot)
        session.commit()
        session.refresh(second_bot)

        first = ChannelConnection(
            bot_id=first_bot.id,
            provider="line",
            display_name="LINE Main",
            webhook_key="line-main-key",
        )
        second = ChannelConnection(
            bot_id=second_bot.id,
            provider="line",
            display_name="LINE Secondary",
            webhook_key="line-secondary-key",
        )
        session.add(first)
        session.add(second)
        session.commit()

        rows = session.exec(
            select(ChannelConnection).where(ChannelConnection.provider == "line")
        ).all()
        assert {row.bot_id for row in rows} == {first_bot.id, second_bot.id}
        assert {row.webhook_key for row in rows} == {
            "line-main-key",
            "line-secondary-key",
        }
