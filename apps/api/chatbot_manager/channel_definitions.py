from dataclasses import dataclass


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
        required_fields=("verify_token", "page_access_token", "app_secret"),
    ),
    "telegram": ChannelDefinition(
        provider="telegram",
        display_name="Telegram",
        fields=("bot_token", "webhook_secret"),
        required_fields=("bot_token", "webhook_secret"),
    ),
}
