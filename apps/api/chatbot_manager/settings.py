from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_secret_key: str = Field(default="change-me-very-secret-jwt-key-minimum-32-chars")
    app_encryption_key: str = "local-dev-encryption-key-32-chars"
    admin_email: str = "admin@example.local"
    admin_password: str = "admin1234!"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
