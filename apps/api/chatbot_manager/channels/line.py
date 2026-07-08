import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class IncomingMessage:
    provider: str
    external_user_id: str
    text: str
    reply_context: dict[str, Any]
    raw_event: dict[str, Any]


class LineAdapter:
    reply_url = "https://api.line.me/v2/bot/message/reply"

    def __init__(self, channel_secret: str, channel_access_token: str) -> None:
        self.channel_secret = channel_secret
        self.channel_access_token = channel_access_token

    def validate_signature(self, body: bytes, signature: str) -> bool:
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
            if not text:
                continue
            messages.append(
                IncomingMessage(
                    provider="line",
                    external_user_id=event.get("source", {}).get("userId", ""),
                    text=text,
                    reply_context={"reply_token": event.get("replyToken", "")},
                    raw_event=event,
                )
            )
        return messages

    async def send_reply(self, reply_context: dict[str, Any], text: str) -> None:
        headers = {"Authorization": f"Bearer {self.channel_access_token}"}
        payload = {
            "replyToken": reply_context["reply_token"],
            "messages": [{"type": "text", "text": text}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.reply_url, json=payload, headers=headers)
            response.raise_for_status()
