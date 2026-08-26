from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_APP_SECRET_KEY = "change-me-very-secret-jwt-key-minimum-32-chars"
DEFAULT_ADMIN_PASSWORD = "admin1234!"
DEFAULT_ENCRYPTION_KEY = "local-dev-encryption-key-32-chars"


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
    dashboard_url: str = "http://localhost:8000"
    api_public_url: str = "http://localhost:8000"

    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    messenger_verify_token: str = ""
    messenger_page_access_token: str = ""
    messenger_app_secret: str = ""
    telegram_bot_token: str = ""
    
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_default_model: str = "gpt-4o-mini"
    llm_vision_model: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    rag_backend: str = "rag_anything"
    rag_working_dir: Path = Path("./data/rag")
    rag_parser: str = "mineru"
    rag_parse_method: str = "auto"
    upload_dir: Path = Path("./data/uploads")

def validate_deployment_settings(settings: Settings) -> None:
    if settings.app_env.strip().lower() in {"local", "test"}:
        return

    unsafe: list[str] = []
    if settings.app_secret_key == DEFAULT_APP_SECRET_KEY:
        unsafe.append("APP_SECRET_KEY")
    if settings.admin_password == DEFAULT_ADMIN_PASSWORD:
        unsafe.append("ADMIN_PASSWORD")
    if settings.app_encryption_key == DEFAULT_ENCRYPTION_KEY:
        unsafe.append("APP_ENCRYPTION_KEY")
    if not settings.cookie_secure:
        unsafe.append("COOKIE_SECURE")
    if unsafe:
        raise RuntimeError(f"Unsafe deployment settings: {', '.join(unsafe)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
