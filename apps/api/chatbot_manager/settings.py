from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_APP_SECRET_KEY = "change-me-very-secret-jwt-key-minimum-32-chars"
DEFAULT_ADMIN_PASSWORD = "admin1234!"
DEFAULT_ENCRYPTION_KEY = "local-dev-encryption-key-32-chars"
UNSAFE_ENCRYPTION_KEYS = frozenset({"", DEFAULT_ENCRYPTION_KEY, "change-me", "replace-me"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_secret_key: str = Field(default=DEFAULT_APP_SECRET_KEY)
    app_encryption_key: str = DEFAULT_ENCRYPTION_KEY
    admin_email: str = "admin@example.local"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    admin_session_max_age_seconds: int = 28_800
    cookie_secure: bool = True

    database_url: str = "sqlite:///./data/chatbot.sqlite3"

    ops_poll_seconds: int = 60
    warning_persist_minutes: int = 15
    human_wait_warning_minutes: int = 10
    human_wait_critical_minutes: int = 30
    rag_latency_warning_ms: int = 3000
    rag_latency_critical_ms: int = 8000
    alert_telegram_bot_token: str = ""
    alert_telegram_chat_id: str = ""

    conversation_retention_days: int = 365
    incident_retention_days: int = 730
    audit_retention_days: int = 1095


def validate_deployment_settings(settings: Settings) -> None:
    if settings.app_env.strip().lower() in {"local", "test"}:
        return

    unsafe: list[str] = []
    if settings.app_secret_key == DEFAULT_APP_SECRET_KEY:
        unsafe.append("APP_SECRET_KEY")
    if settings.admin_password == DEFAULT_ADMIN_PASSWORD:
        unsafe.append("ADMIN_PASSWORD")
    if settings.app_encryption_key.strip().lower() in UNSAFE_ENCRYPTION_KEYS:
        unsafe.append("APP_ENCRYPTION_KEY")
    if not settings.cookie_secure:
        unsafe.append("COOKIE_SECURE")
    if unsafe:
        raise RuntimeError(f"Unsafe deployment settings: {', '.join(unsafe)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
