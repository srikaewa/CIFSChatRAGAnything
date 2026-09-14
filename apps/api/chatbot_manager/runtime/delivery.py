import logging
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from chatbot_manager.channel_connections import resolve_connection_credentials
from chatbot_manager.channels.line import LineAdapter
from chatbot_manager.channels.messenger import MessengerAdapter
from chatbot_manager.channels.telegram import TelegramAdapter
from chatbot_manager.models import ChannelConnection


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    error_code: str = ""


class DeliveryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _adapter(self, connection: ChannelConnection):
        credentials = resolve_connection_credentials(self._session, connection)
        if connection.provider == "line":
            return LineAdapter(
                channel_secret=credentials.get("channel_secret", ""),
                channel_access_token=credentials.get("channel_access_token", ""),
            )
        if connection.provider == "messenger":
            return MessengerAdapter(
                verify_token=credentials.get("verify_token", ""),
                page_access_token=credentials.get("page_access_token", ""),
                app_secret=credentials.get("app_secret", ""),
            )
        if connection.provider == "telegram":
            return TelegramAdapter(
                bot_token=credentials.get("bot_token", ""),
                webhook_secret=credentials.get("webhook_secret", ""),
            )
        raise ValueError("unsupported_channel_provider")

    async def send(
        self,
        connection: ChannelConnection,
        reply_context: dict[str, Any],
        text: str,
    ) -> DeliveryResult:
        try:
            adapter = self._adapter(connection)
            await adapter.send_reply(reply_context, text)
        except Exception as exc:
            logger.error(
                "Provider delivery failed provider=%s connection_id=%s error_type=%s",
                connection.provider,
                connection.id,
                type(exc).__name__,
            )
            return DeliveryResult(
                status="failed",
                error_code="provider_delivery_failed",
            )
        return DeliveryResult(status="delivered")
