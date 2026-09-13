from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import Bot, BotConfigRule, BotConfigVersion


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
