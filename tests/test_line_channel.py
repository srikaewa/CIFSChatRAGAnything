import base64
import hashlib
import hmac

import pytest
import respx
from httpx import Response

from chatbot_manager.channels.line import LineAdapter


def signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_line_signature_validation() -> None:
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")
    body = b'{"events":[]}'

    assert adapter.validate_signature(body, signature(body, "secret")) is True
    assert adapter.validate_signature(body, "bad") is False


def test_line_parse_text_event() -> None:
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")
    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "reply-token",
                "source": {"userId": "user-1"},
                "message": {"type": "text", "text": "hello"},
            }
        ]
    }

    messages = adapter.parse_events(payload)

    assert len(messages) == 1
    assert messages[0].provider == "line"
    assert messages[0].text == "hello"
    assert messages[0].external_user_id == "user-1"
    assert messages[0].reply_context["reply_token"] == "reply-token"


def test_line_ignores_non_text_events() -> None:
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")
    payload = {
        "events": [
            {"type": "follow", "source": {"userId": "user-1"}},
            {"type": "message", "message": {"type": "image"}, "source": {"userId": "user-2"}},
        ]
    }

    assert adapter.parse_events(payload) == []


@pytest.mark.asyncio
@respx.mock
async def test_line_send_reply() -> None:
    route = respx.post("https://api.line.me/v2/bot/message/reply").mock(return_value=Response(200, json={}))
    adapter = LineAdapter(channel_secret="secret", channel_access_token="token")

    await adapter.send_reply({"reply_token": "reply-token"}, "Hello")

    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer token"
    assert b"Hello" in request.content
