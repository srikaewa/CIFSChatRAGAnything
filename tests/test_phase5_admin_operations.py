from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from chatbot_manager.admin.telegram_ops import discover_tailscale_host, run_funnel, service_port, tailscale_command
from chatbot_manager.channel_config import channel_credentials, save_channel, update_channel_credentials
from chatbot_manager.db import get_engine
from chatbot_manager.settings import get_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    return page.text.split(marker, 1)[1].split('"', 1)[0]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost", 80),
        ("https://example.test", 443),
        ("http://localhost:8765/path", 8765),
    ],
)
def test_service_port(url: str, expected: int) -> None:
    assert service_port(url) == expected


@pytest.mark.parametrize("url", ["", "localhost:8000", "ftp://example.test", "https:///missing-host"])
def test_service_port_rejects_invalid_url(url: str) -> None:
    with pytest.raises(ValueError):
        service_port(url)


def test_windows_funnel_command_has_no_sudo() -> None:
    assert tailscale_command("on", 8000, "Windows") == ["tailscale", "funnel", "--bg", "8000"]


def test_posix_funnel_command_has_no_interactive_sudo_assumption() -> None:
    assert tailscale_command("off", 8000, "Linux") == ["tailscale", "funnel", "off", "8000"]


def test_discover_tailscale_host_rejects_invalid_self_shape() -> None:
    class Result:
        returncode = 0
        stdout = '{"Self": null}'
        stderr = ''

    def fake_run(*args, **kwargs):
        return Result()

    assert discover_tailscale_host(run=fake_run) == (None, "tailscale_status_invalid")


def test_run_funnel_maps_access_denied_without_exposing_stderr() -> None:
    calls = []

    class Result:
        returncode = 1
        stdout = ''
        stderr = 'Access denied: sensitive host detail'

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    assert run_funnel("on", 8765, run=fake_run, platform_name="Windows") == "tailscale_access_denied"
    assert calls == [["tailscale", "funnel", "--bg", "8765"]]



def test_normal_channel_save_preserves_telegram_webhook_url(client: TestClient) -> None:
    with Session(get_engine()) as session:
        save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "bot-token", "webhook_secret": "hook-secret"},
        )
        update_channel_credentials(
            session,
            "telegram",
            {"webhook_url": "https://bot.example.ts.net/webhooks/telegram"},
        )
        save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "", "webhook_secret": ""},
        )
        credentials = channel_credentials(session, get_settings(), "telegram")

    assert credentials["bot_token"] == "bot-token"
    assert credentials["webhook_secret"] == "hook-secret"
    assert credentials["webhook_url"] == "https://bot.example.ts.net/webhooks/telegram"


def test_telegram_setup_failure_rolls_back_funnel_and_returns_safe_error(client: TestClient, monkeypatch) -> None:
    csrf_token = login(client)
    with Session(get_engine()) as session:
        save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "super-secret-bot-token", "webhook_secret": "hook-secret"},
        )

    funnel_actions: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "chatbot_manager.admin.routes.discover_tailscale_host",
        lambda: ("bot.example.ts.net", None),
    )

    def fake_run_funnel(action: str, port: int) -> str | None:
        funnel_actions.append((action, port))
        return None

    async def failing_set_webhook(self, url: str):
        raise RuntimeError("request failed at https://api.telegram.org/botsuper-secret-bot-token/setWebhook")

    monkeypatch.setattr("chatbot_manager.admin.routes.run_funnel", fake_run_funnel)
    monkeypatch.setattr("chatbot_manager.admin.routes.TelegramAdapter.set_webhook", failing_set_webhook)

    safe_client = TestClient(client.app, raise_server_exceptions=False)
    safe_client.cookies.update(client.cookies)
    response = safe_client.post(
        "/channels/telegram/setup-webhook",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/channels?error=telegram_set_webhook_failed"
    assert "super-secret-bot-token" not in response.text
    assert funnel_actions == [("on", 8000), ("off", 8000)]


def test_telegram_disable_failure_returns_safe_error(client: TestClient, monkeypatch) -> None:
    csrf_token = login(client)
    with Session(get_engine()) as session:
        save_channel(
            session,
            "telegram",
            enabled=True,
            incoming_credentials={"bot_token": "super-secret-bot-token", "webhook_secret": "hook-secret"},
        )

    async def failing_delete_webhook(self):
        raise RuntimeError("request failed at https://api.telegram.org/botsuper-secret-bot-token/deleteWebhook")

    monkeypatch.setattr("chatbot_manager.admin.routes.TelegramAdapter.delete_webhook", failing_delete_webhook)
    safe_client = TestClient(client.app, raise_server_exceptions=False)
    safe_client.cookies.update(client.cookies)
    response = safe_client.post(
        "/channels/telegram/disable-webhook",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/channels?error=telegram_delete_webhook_failed"
    assert "super-secret-bot-token" not in response.text

def test_channels_page_has_accessible_feedback_and_secret_fields(client: TestClient) -> None:
    login(client)
    html = client.get("/channels?saved=1").text
    assert 'role="status"' in html
    assert 'aria-label="Primary navigation"' in html
    assert 'aria-current="page"' in html
    assert 'type="password"' in html
    assert 'autocomplete="new-password"' in html
    assert 'type="button"' in html


def test_channels_page_error_uses_alert_role(client: TestClient) -> None:
    login(client)
    html = client.get("/channels?error=channel_save_failed").text
    assert 'role="alert"' in html
    assert "Could not save channel settings" in html


def test_base_css_keeps_visible_focus_and_narrow_layout() -> None:
    css = Path("apps/api/chatbot_manager/static/styles.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "@media (max-width: 760px)" in css


def test_readme_lists_each_webhook_once_and_links_operations() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    assert text.count("`/webhooks/line`") == 1
    assert text.count("`/webhooks/messenger`") == 2
    assert text.count("`/webhooks/telegram`") == 1
    assert "docs/operations.md" in text


def test_operations_guide_covers_windows_rag_backup_and_recovery() -> None:
    text = Path("docs/operations.md").read_text(encoding="utf-8")
    for phrase in ["WinError 206", "--basetemp", "Tailscale", "RAG", "Backup", "Restore"]:
        assert phrase in text
