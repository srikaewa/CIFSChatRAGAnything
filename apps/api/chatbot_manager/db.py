from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .settings import Settings, get_settings


_engine: Engine | None = None


def make_engine(settings: Settings | None = None) -> Engine:
    loaded = settings or get_settings()
    connect_args: dict[str, bool] = {}
    engine_kwargs = {}

    if loaded.database_url.startswith("sqlite://"):
        connect_args = {"check_same_thread": False}

    if loaded.database_url in {"sqlite://", "sqlite:///:memory:"}:
        engine_kwargs["poolclass"] = StaticPool
    elif loaded.database_url.startswith("sqlite:///"):
        db_path = loaded.database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    return create_engine(
        loaded.database_url,
        connect_args=connect_args,
        **engine_kwargs,
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db() -> None:
    from . import models  # noqa: F401

    active_engine = get_engine()
    SQLModel.metadata.create_all(active_engine)
    _migrate_sqlite_assistant_settings(active_engine)
    _migrate_sqlite_knowledge_documents(active_engine)


def _migrate_sqlite_assistant_settings(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    required_columns = {
        "llm_base_url": "TEXT NOT NULL DEFAULT 'https://api.openai.com/v1'",
        "llm_api_key": "TEXT NOT NULL DEFAULT ''",
        "embedding_model": "TEXT NOT NULL DEFAULT 'text-embedding-3-small'",
    }
    with active_engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(assistantsettings)")).all()
        }
        for name, definition in required_columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE assistantsettings ADD COLUMN {name} {definition}"))


def _migrate_sqlite_knowledge_documents(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    with active_engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(knowledgedocument)")).all()
        }
        if "rag_doc_id" not in existing:
            connection.execute(text("ALTER TABLE knowledgedocument ADD COLUMN rag_doc_id TEXT NOT NULL DEFAULT ''"))


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
