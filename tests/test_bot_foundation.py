from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager import db
from chatbot_manager.bots.service import ensure_default_bot, ensure_draft_config, get_live_config
from chatbot_manager.models import (
    AssistantSettings,
    Bot,
    BotConfigRule,
    BotConfigVersion,
    Channel,
    ChatEvent,
    Rule,
)
from chatbot_manager.settings import get_settings


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_bot_config_models_round_trip() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Default Bot", description="Migrated bot", lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)

        version = BotConfigVersion(
            bot_id=bot.id,
            version_number=1,
            status="published",
            system_prompt="Use available knowledge.",
            fallback_reply="Ask staff.",
            tone="professional",
            language="auto",
            response_style="concise",
            fallback_policy="reply",
            escalation_policy="{}",
            created_by="migration",
        )
        session.add(version)
        session.commit()
        session.refresh(version)

        session.add(
            BotConfigRule(
                config_version_id=version.id,
                name="price",
                priority=10,
                match_type="contains",
                pattern="price",
                condition_logic="and",
                conditions="[]",
                action="RESPOND",
                reply_text="Price is 100.",
            )
        )
        session.commit()

        stored_bot = session.exec(select(Bot)).one()
        stored_version = session.exec(select(BotConfigVersion)).one()
        stored_rule = session.exec(select(BotConfigRule)).one()

    assert stored_bot.lifecycle_status == "active"
    assert stored_version.knowledge_service_id is None
    assert stored_version.status == "published"
    assert stored_rule.action == "RESPOND"


def test_default_bot_migrates_legacy_configuration_once() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AssistantSettings(system_prompt="Legacy prompt", fallback_reply="Legacy fallback"))
        session.add(
            Rule(
                priority=5,
                pattern="human",
                match_type="contains",
                reply_text="Internal",
                escalate=True,
                escalate_message="Staff soon",
            )
        )
        session.add(Channel(provider="line", display_name="LINE", enabled=True))
        session.add(ChatEvent(provider="line", incoming_text="hello", decision_source="fallback"))
        session.commit()

        first = ensure_default_bot(session)
        second = ensure_default_bot(session)
        live = get_live_config(session, first.id)
        rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == live.id)
        ).all()
        channel = session.exec(select(Channel)).one()
        event = session.exec(select(ChatEvent)).one()

    assert first.id == second.id
    assert live.version_number == 1
    assert live.status == "published"
    assert live.system_prompt == "Legacy prompt"
    assert live.fallback_reply == "Legacy fallback"
    assert len(rules) == 1
    assert rules[0].action == "ESCALATE"
    assert rules[0].escalate_message == "Staff soon"
    assert channel.bot_id == first.id
    assert event.bot_id == first.id


def test_ensure_draft_config_clones_live_config_and_rules_once() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AssistantSettings(system_prompt="Live prompt"))
        session.add(Rule(pattern="price", reply_text="100"))
        session.commit()
        bot = ensure_default_bot(session)
        draft1 = ensure_draft_config(session, bot.id, "admin@example.local")
        draft2 = ensure_draft_config(session, bot.id, "admin@example.local")
        copied = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == draft1.id)
        ).all()

    assert draft1.id == draft2.id
    assert draft1.status == "draft"
    assert draft1.version_number == 2
    assert len(copied) == 1


def test_sqlite_bot_link_migration_adds_missing_columns() -> None:
    engine = memory_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE channel (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE chatevent (id INTEGER PRIMARY KEY)"))

    db._migrate_sqlite_bot_links(engine)

    with engine.connect() as connection:
        channel_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(channel)")).all()}
        event_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(chatevent)")).all()}

    assert "bot_id" in channel_columns
    assert "bot_id" in event_columns


def test_init_db_bootstraps_default_bot(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()
    try:
        db.init_db()
        with Session(db.get_engine()) as session:
            bots = session.exec(select(Bot)).all()
            assert len(bots) == 1
            assert bots[0].name == "Default Bot"
            assert bots[0].live_config_version_id is not None
    finally:
        db.reset_engine()
        get_settings.cache_clear()
