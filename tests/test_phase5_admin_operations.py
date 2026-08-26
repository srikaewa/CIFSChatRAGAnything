from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chatbot_manager.admin.telegram_ops import discover_tailscale_host, run_funnel, service_port, tailscale_command


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
