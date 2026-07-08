import pytest
import respx
from httpx import Response

from chatbot_manager.channels.messenger import MessengerAdapter


def test_messenger_verify_success() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")

    assert adapter.verify(mode="subscribe", token="verify", challenge="abc") == "abc"


def test_messenger_verify_failure() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")

    assert adapter.verify(mode="subscribe", token="wrong", challenge="abc") is None


def test_messenger_parse_text_event() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")
    payload = {
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "user-1"},
                        "message": {"text": "hello"},
                    }
                ]
            }
        ]
    }

    messages = adapter.parse_events(payload)

    assert len(messages) == 1
    assert messages[0].provider == "messenger"
    assert messages[0].external_user_id == "user-1"
    assert messages[0].text == "hello"
    assert messages[0].reply_context["recipient_id"] == "user-1"


def test_messenger_ignores_non_text_events() -> None:
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")
    payload = {
        "entry": [
            {
                "messaging": [
                    {"sender": {"id": "user-1"}, "message": {"attachments": []}},
                    {"sender": {"id": "user-2"}, "delivery": {"mids": []}},
                ]
            }
        ]
    }

    assert adapter.parse_events(payload) == []


@pytest.mark.asyncio
@respx.mock
async def test_messenger_send_reply() -> None:
    route = respx.post("https://graph.facebook.com/v20.0/me/messages").mock(
        return_value=Response(200, json={"recipient_id": "user-1", "message_id": "m1"})
    )
    adapter = MessengerAdapter(verify_token="verify", page_access_token="page-token", app_secret="")

    await adapter.send_reply({"recipient_id": "user-1"}, "Hello")

    assert route.called
    request = route.calls[0].request
    assert "access_token=page-token" in str(request.url)
    assert b"Hello" in request.content
