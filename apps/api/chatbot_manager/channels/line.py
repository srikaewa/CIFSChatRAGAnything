import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True)
class IncomingMessage:
    provider: str
    external_user_id: str
    text: str
    reply_context: dict[str, Any]
    raw_event: dict[str, Any]
    external_message_id: str = ""
    timestamp_ms: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


class LineAdapter:
    reply_url = "https://api.line.me/v2/bot/message/reply"
    push_url = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_secret: str, channel_access_token: str) -> None:
        self.channel_secret = channel_secret
        self.channel_access_token = channel_access_token

    def validate_signature(self, body: bytes, signature: str) -> bool:
        if not self.channel_secret or not signature:
            return False
        digest = hmac.new(self.channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for event in payload.get("events", []):
            message = event.get("message", {})
            if event.get("type") != "message" or message.get("type") != "text":
                continue
            text = message.get("text", "")
            external_message_id = message.get("id", "")
            if not text or not external_message_id:
                continue
            timestamp = event.get("timestamp")
            user_id = event.get("source", {}).get("userId", "")
            messages.append(
                IncomingMessage(
                    provider="line",
                    external_message_id=str(external_message_id),
                    external_user_id=user_id,
                    text=text,
                    timestamp_ms=timestamp if isinstance(timestamp, int) else None,
                    reply_context={
                        "reply_token": event.get("replyToken", ""),
                        "user_id": user_id,
                    },
                    attachments=[],
                    raw_event=event,
                )
            )
        return messages

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.channel_access_token}"}

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        payload = {
            "replyToken": reply_context["reply_token"],
            "messages": [{"type": "text", "text": text}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.reply_url, json=payload, headers=self._headers())
            response.raise_for_status()

    async def send_push(self, user_id: str, text: str) -> None:
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": text}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.push_url, json=payload, headers=self._headers())
            response.raise_for_status()
