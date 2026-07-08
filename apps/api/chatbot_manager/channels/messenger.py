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
        if mode == "subscribe" and token == self.verify_token and challenge is not None:
            return challenge
        return None

    def parse_events(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                text = event.get("message", {}).get("text")
                sender_id = event.get("sender", {}).get("id", "")
                if not text or not sender_id:
                    continue
                messages.append(
                    IncomingMessage(
                        provider="messenger",
                        external_user_id=sender_id,
                        text=text,
                        reply_context={"recipient_id": sender_id},
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
