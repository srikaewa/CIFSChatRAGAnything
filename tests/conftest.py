from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    from chatbot_manager.db import reset_engine
    from chatbot_manager.main import create_app
    from chatbot_manager.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_engine()
    get_settings.cache_clear()
