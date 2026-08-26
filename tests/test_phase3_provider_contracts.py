import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from chatbot_manager.channel_config import resolve_channel
from chatbot_manager.db import get_engine
from chatbot_manager.models import Channel
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
    ("enabled", "credentials", "saved_status", "expected"),
    [
        (False, {}, "not_configured", "disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "failed",
        ),
    ],
)
def test_line_resolved_channel_state(client: TestClient, enabled, credentials, saved_status, expected) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()
        resolved = resolve_channel(session, get_settings(), "line")

    assert resolved.state == expected


@pytest.mark.parametrize(
    ("enabled", "credentials", "saved_status", "label"),
    [
        (False, {}, "not_configured", "Disabled"),
        (True, {"channel_secret": "secret"}, "not_configured", "Incomplete"),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "configured",
            "Ready",
        ),
        (
            True,
            {"channel_secret": "secret", "channel_access_token": "token"},
            "failed",
            "Failed",
        ),
    ],
)
def test_channels_page_renders_line_state(client: TestClient, enabled, credentials, saved_status, label) -> None:
    with Session(get_engine()) as session:
        session.add(
            Channel(
                provider="line",
                enabled=enabled,
                display_name="LINE",
                status=saved_status,
                credential_json=json.dumps(credentials),
            )
        )
        session.commit()

    login(client)
    response = client.get("/channels")

    assert response.status_code == 200
    line_panel = response.text.split("<h2>LINE</h2>", 1)[1].split("</article>", 1)[0]
    assert f"Status: {label}" in line_panel
