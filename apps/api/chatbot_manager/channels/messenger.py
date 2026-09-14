import hashlib
import hmac
from typing import Any

import httpx

from .line import IncomingMessage


class MessengerAdapter:
    send_url = "https://graph.facebook.com/v20.0/me/messages"

    def __init__(self, verify_token: str, page_access_token: str, app_secret: str) -> None:
        self.verify_token = verify_token
        self.page_access_token = page_access_token
        self.app_secret = app_secret

    def verify(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if self.verify_token and mode == "subscribe" and token == self.verify_token and challenge is not None:
            return challenge
        return None

    def validate_signature(self, body: bytes, signature: str) -> bool:
        if not self.app_secret or not signature.startswith("sha256="):
            return False
        supplied = signature.removeprefix("sha256=")
        if not supplied:
            return False
        expected = hmac.new(self.app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                message = event.get("message", {})
                text = message.get("text")
                external_message_id = message.get("mid", "")
                sender_id = event.get("sender", {}).get("id", "")
                if not text or not sender_id or not external_message_id:
                    continue
                timestamp = event.get("timestamp")
                messages.append(
                    IncomingMessage(
                        provider="messenger",
                        external_message_id=str(external_message_id),
                        external_user_id=sender_id,
                        text=text,
                        timestamp_ms=timestamp if isinstance(timestamp, int) else None,
                        reply_context={"recipient_id": sender_id},
                        attachments=[],
                        raw_event=event,
                    )
                )
        return messages

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        payload = {
            "recipient": {"id": reply_context["recipient_id"]},
            "message": {"text": text},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.send_url,
                params={"access_token": self.page_access_token},
                json=payload,
            )
            response.raise_for_status()
