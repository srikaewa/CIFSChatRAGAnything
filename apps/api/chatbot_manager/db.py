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
    _migrate_sqlite_rule_escalate(active_engine)
    _migrate_sqlite_assistant_settings_escalation(active_engine)
    _migrate_sqlite_system_prompt(active_engine)
    _migrate_sqlite_rule_conditions(active_engine)
    _migrate_sqlite_bot_links(active_engine)

    from .bots.service import ensure_default_bot

    with Session(active_engine) as session:
        ensure_default_bot(session)


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


def _migrate_sqlite_rule_escalate(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    with active_engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(rule)")).all()
        }
        if "escalate" not in existing:
            connection.execute(text("ALTER TABLE rule ADD COLUMN escalate BOOLEAN NOT NULL DEFAULT 0"))
        if "escalate_message" not in existing:
            connection.execute(text("ALTER TABLE rule ADD COLUMN escalate_message TEXT NOT NULL DEFAULT ''"))


def _migrate_sqlite_assistant_settings_escalation(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    with active_engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(assistantsettings)")).all()
        }
        if "admin_notify_channel" not in existing:
            connection.execute(text("ALTER TABLE assistantsettings ADD COLUMN admin_notify_channel TEXT NOT NULL DEFAULT 'telegram'"))
        if "admin_notify_chat_id" not in existing:
            connection.execute(text("ALTER TABLE assistantsettings ADD COLUMN admin_notify_chat_id TEXT NOT NULL DEFAULT ''"))


def _migrate_sqlite_rule_conditions(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    with active_engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(rule)")).all()
        }
        if "condition_logic" not in existing:
            connection.execute(text("ALTER TABLE rule ADD COLUMN condition_logic TEXT NOT NULL DEFAULT 'and'"))
        if "conditions" not in existing:
            connection.execute(text("ALTER TABLE rule ADD COLUMN conditions TEXT NOT NULL DEFAULT '[]'"))


def _migrate_sqlite_system_prompt(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    old_prompts = [
        "Answer as a helpful business assistant. Use the knowledge base when needed.",
        "Answer the question using only the provided context. Do not add information from your own training. If the context does not contain the answer, say you do not have that information.",
    ]
    new_prompt = "Answer the question using the provided context. If the context contains relevant information, use it to answer. If the context is missing or insufficient, say that the information is not available in the knowledge base rather than making up an answer."

    with active_engine.begin() as connection:
        for old in old_prompts:
            connection.execute(
                text("UPDATE assistantsettings SET system_prompt = :new WHERE system_prompt = :old"),
                {"new": new_prompt, "old": old},
            )


def _migrate_sqlite_bot_links(active_engine: Engine) -> None:
    if not active_engine.url.drivername.startswith("sqlite"):
        return

    with active_engine.begin() as connection:
        channel_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(channel)")).all()
        }
        if "bot_id" not in channel_columns:
            connection.execute(text("ALTER TABLE channel ADD COLUMN bot_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channel_bot_id ON channel (bot_id)"))

        event_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(chatevent)")).all()
        }
        if "bot_id" not in event_columns:
            connection.execute(text("ALTER TABLE chatevent ADD COLUMN bot_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_chatevent_bot_id ON chatevent (bot_id)"))


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
