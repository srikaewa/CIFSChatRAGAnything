import pytest
import respx
from httpx import Response

from chatbot_manager.channels.telegram import TelegramAdapter, TELEGRAM_API


def test_validate_webhook_no_secret() -> None:
    adapter = TelegramAdapter(bot_token="token", webhook_secret="")
    assert adapter.validate_webhook(b"{}", "") is False
    assert adapter.validate_webhook(b"{}", "anything") is False


def test_validate_webhook_with_secret() -> None:
    adapter = TelegramAdapter(bot_token="token", webhook_secret="mysecret")
    assert adapter.validate_webhook(b"{}", "mysecret") is True
    assert adapter.validate_webhook(b"{}", "wrong") is False
    assert adapter.validate_webhook(b"{}", "") is False


def test_parse_text_event() -> None:
    adapter = TelegramAdapter(bot_token="token")
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 123, "is_bot": False, "first_name": "User"},
            "chat": {"id": 456, "type": "private"},
            "text": "hello",
        },
    }

    messages = adapter.parse_events(payload)

    assert len(messages) == 1
    assert messages[0].provider == "telegram"
    assert messages[0].text == "hello"
    assert messages[0].external_user_id == "123"
    assert messages[0].reply_context["chat_id"] == 456


def test_parse_ignores_non_message_updates() -> None:
    adapter = TelegramAdapter(bot_token="token")
    payload = {"update_id": 1}
    assert adapter.parse_events(payload) == []

    payload = {"update_id": 2, "callback_query": {"id": "cq1"}}
    assert adapter.parse_events(payload) == []


def test_parse_ignores_empty_text() -> None:
    adapter = TelegramAdapter(bot_token="token")
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "",
        },
    }
    assert adapter.parse_events(payload) == []


@pytest.mark.asyncio
@respx.mock
async def test_send_reply() -> None:
    route = respx.post(f"{TELEGRAM_API}token/sendMessage").mock(return_value=Response(200, json={"ok": True}))
    adapter = TelegramAdapter(bot_token="token")

    await adapter.send_reply({"chat_id": 456}, "Hello")

    assert route.called
    request = route.calls[0].request
    assert b"Hello" in request.content
    assert b"456" in request.content


@pytest.mark.asyncio
@respx.mock
async def test_set_webhook() -> None:
    route = respx.post(f"{TELEGRAM_API}token/setWebhook").mock(return_value=Response(200, json={"ok": True}))
    adapter = TelegramAdapter(bot_token="token", webhook_secret="mysecret")

    result = await adapter.set_webhook("https://example.com/webhooks/telegram")

    assert route.called
    assert result == {"ok": True}
    body = route.calls[0].request.content
    assert b"https://example.com/webhooks/telegram" in body
    assert b"mysecret" in body


@pytest.mark.asyncio
@respx.mock
async def test_set_webhook_without_secret() -> None:
    route = respx.post(f"{TELEGRAM_API}token/setWebhook").mock(return_value=Response(200, json={"ok": True}))
    adapter = TelegramAdapter(bot_token="token", webhook_secret="")

    await adapter.set_webhook("https://example.com/webhooks/telegram")

    body = route.calls[0].request.content
    assert b"mysecret" not in body


@pytest.mark.asyncio
@respx.mock
async def test_delete_webhook() -> None:
    route = respx.post(f"{TELEGRAM_API}token/deleteWebhook").mock(return_value=Response(200, json={"ok": True}))
    adapter = TelegramAdapter(bot_token="token")

    result = await adapter.delete_webhook()

    assert route.called
    assert result == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_get_webhook_info() -> None:
    route = respx.post(f"{TELEGRAM_API}token/getWebhookInfo").mock(
        return_value=Response(200, json={"ok": True, "result": {"url": "https://example.com/hook"}})
    )
    adapter = TelegramAdapter(bot_token="token")

    result = await adapter.get_webhook_info()

    assert route.called
    assert result["result"]["url"] == "https://example.com/hook"
