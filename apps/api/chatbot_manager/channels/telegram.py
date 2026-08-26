import hmac
from typing import Any

import httpx

from .line import IncomingMessage


TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramAdapter:
    def __init__(self, bot_token: str, webhook_secret: str = "") -> None:
        self.bot_token = bot_token
        self.webhook_secret = webhook_secret
        self.api_base = f"{TELEGRAM_API}{bot_token}"

    def validate_webhook(self, request_body: bytes, header_secret: str) -> bool:
        if not self.webhook_secret or not header_secret:
            return False
        return hmac.compare_digest(header_secret, self.webhook_secret)

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        update = payload
        if "message" not in update:
            return messages
        msg = update["message"]
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id", "")
        from_id = msg.get("from", {}).get("id", "")
        if not text or not chat_id:
            return messages
        messages.append(
            IncomingMessage(
                provider="telegram",
                external_user_id=str(from_id),
                text=text,
                reply_context={"chat_id": chat_id},
                raw_event=update,
            )
        )
        return messages

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        payload = {
            "chat_id": reply_context["chat_id"],
            "text": text,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.api_base}/sendMessage", json=payload)
            response.raise_for_status()

    async def set_webhook(self, url: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if self.webhook_secret:
            payload["secret_token"] = self.webhook_secret
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.api_base}/setWebhook", json=payload)
            response.raise_for_status()
            return response.json()

    async def delete_webhook(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.api_base}/deleteWebhook")
            response.raise_for_status()
            return response.json()

    async def get_webhook_info(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.api_base}/getWebhookInfo")
            response.raise_for_status()
            return response.json()
