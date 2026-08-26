import pytest
from fastapi.testclient import TestClient

from chatbot_manager.security import make_session_token, read_session_token
from chatbot_manager.settings import Settings, get_settings, validate_deployment_settings


def test_session_tokens_expire_and_reject_tampering() -> None:
    token = make_session_token("admin@example.local")

    assert read_session_token(token, max_age_seconds=60) == "admin@example.local"
    assert read_session_token(token, max_age_seconds=-1) is None
    assert read_session_token(f"{token}tampered", max_age_seconds=60) is None


def test_production_settings_default_to_secure_cookies() -> None:
    settings = Settings(app_env="production")

    assert settings.cookie_secure is True


def test_nonlocal_deployment_rejects_known_default_credentials() -> None:
    settings = Settings(app_env="production", cookie_secure=True)

    with pytest.raises(RuntimeError) as error:
        validate_deployment_settings(settings)

    message = str(error.value)
    assert "APP_SECRET_KEY" in message
    assert "ADMIN_PASSWORD" in message
    assert settings.app_secret_key not in message
    assert settings.admin_password not in message


def test_local_and_test_deployments_allow_development_defaults() -> None:
    validate_deployment_settings(Settings(app_env="local", cookie_secure=False))
    validate_deployment_settings(Settings(app_env="test", cookie_secure=False))


def test_login_cookie_has_expiry_and_security_attributes(client: TestClient) -> None:
    settings = get_settings()
    settings.cookie_secure = True
    settings.admin_session_max_age_seconds = 123

    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )

    cookie = response.headers["set-cookie"].lower()
    assert "admin_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie
    assert "max-age=123" in cookie
