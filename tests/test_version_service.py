import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.bots.versions import (
    RuleInput,
    VersionStateError,
    clone_live_to_draft,
    get_draft_config,
    publish_draft,
    replace_draft_rules,
    restore_version_as_draft,
    update_draft_config,
)
from chatbot_manager.models import Bot, BotConfigRule, BotConfigVersion


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def seed_active_bot(session: Session) -> tuple[Bot, BotConfigVersion, BotConfigRule]:
    bot = Bot(name="Versioned Bot", lifecycle_status="active")
    session.add(bot)
    session.commit()
    session.refresh(bot)
    assert bot.id is not None

    live = BotConfigVersion(
        bot_id=bot.id,
        version_number=1,
        status="published",
        system_prompt="Live prompt",
        fallback_reply="Live fallback",
        fallback_policy="reply",
        created_by="seed",
    )
    session.add(live)
    session.commit()
    session.refresh(live)
    assert live.id is not None

    rule = BotConfigRule(
        config_version_id=live.id,
        name="price",
        priority=10,
        pattern="price",
        action="RESPOND",
        reply_text="100",
    )
    session.add(rule)
    bot.live_config_version_id = live.id
    session.add(bot)
    session.commit()
    session.refresh(rule)
    return bot, live, rule


def test_editing_live_creates_single_new_draft() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, live, live_rule = seed_active_bot(session)
        assert bot.id is not None
        assert live.id is not None

        draft1 = clone_live_to_draft(session, bot.id, "admin@example.local")
        draft2 = clone_live_to_draft(session, bot.id, "admin@example.local")
        copied_rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == draft1.id)
        ).all()

        assert draft1.id == draft2.id
        assert draft1.id != live.id
        assert draft1.version_number == live.version_number + 1
        assert draft1.status == "draft"
        assert len(copied_rules) == 1
        assert copied_rules[0].pattern == live_rule.pattern
        assert copied_rules[0].id != live_rule.id


def test_get_draft_config_returns_none_until_draft_exists() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, _, _ = seed_active_bot(session)
        assert bot.id is not None

        assert get_draft_config(session, bot.id) is None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert get_draft_config(session, bot.id).id == draft.id


def test_update_draft_config_changes_only_allowed_fields() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, live, _ = seed_active_bot(session)
        assert bot.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")

        updated = update_draft_config(
            session,
            bot.id,
            {"system_prompt": "Draft prompt", "tone": "friendly"},
        )
        session.refresh(live)

        assert updated.id == draft.id
        assert updated.system_prompt == "Draft prompt"
        assert updated.tone == "friendly"
        assert live.system_prompt == "Live prompt"

        with pytest.raises(VersionStateError, match="config_field_not_editable"):
            update_draft_config(session, bot.id, {"status": "published"})


def test_published_version_is_immutable_through_draft_update_service() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, live, _ = seed_active_bot(session)
        assert bot.id is not None
        assert live.id is not None
        bot.draft_config_version_id = live.id
        session.add(bot)
        session.commit()

        with pytest.raises(VersionStateError, match="published_version_immutable"):
            update_draft_config(session, bot.id, {"system_prompt": "Must not write"})


def test_replace_draft_rules_does_not_modify_live_rules() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, live, _ = seed_active_bot(session)
        assert bot.id is not None
        assert live.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")

        replaced = replace_draft_rules(
            session,
            bot.id,
            [
                RuleInput(
                    name="hours",
                    enabled=True,
                    priority=5,
                    match_type="contains",
                    pattern="hours",
                    condition_logic="and",
                    conditions="[]",
                    action="RESPOND",
                    reply_text="09:00-17:00",
                    escalate_message="",
                )
            ],
        )
        live_rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == live.id)
        ).all()
        draft_rules = session.exec(
            select(BotConfigRule).where(BotConfigRule.config_version_id == draft.id)
        ).all()

        assert len(replaced) == 1
        assert len(draft_rules) == 1
        assert draft_rules[0].pattern == "hours"
        assert len(live_rules) == 1
        assert live_rules[0].pattern == "price"


def test_restore_old_version_creates_new_draft_not_live_switch() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, v1, _ = seed_active_bot(session)
        assert bot.id is not None
        assert v1.id is not None

        v2 = BotConfigVersion(
            bot_id=bot.id,
            version_number=2,
            status="published",
            system_prompt="Version 2",
            created_by="seed",
        )
        v3 = BotConfigVersion(
            bot_id=bot.id,
            version_number=3,
            status="published",
            system_prompt="Version 3",
            created_by="seed",
        )
        session.add(v2)
        session.add(v3)
        session.commit()
        session.refresh(v3)
        assert v3.id is not None
        bot.live_config_version_id = v3.id
        session.add(bot)
        session.commit()
        current_live = v3.id

        restored = restore_version_as_draft(
            session,
            bot.id,
            source_version_id=v1.id,
            actor="admin@example.local",
        )
        session.refresh(bot)

        assert bot.live_config_version_id == current_live
        assert bot.draft_config_version_id == restored.id
        assert restored.status == "draft"
        assert restored.version_number == 4
        assert restored.system_prompt == v1.system_prompt


def test_publish_draft_switches_live_pointer_and_freezes_version() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot, old_live, _ = seed_active_bot(session)
        assert bot.id is not None
        assert old_live.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        update_draft_config(session, bot.id, {"system_prompt": "New live prompt"})

        published = publish_draft(session, bot.id, "admin@example.local")
        session.refresh(bot)
        session.refresh(old_live)

        assert published.id == draft.id
        assert published.status == "published"
        assert published.published_at is not None
        assert published.created_by == "admin@example.local"
        assert bot.live_config_version_id == published.id
        assert bot.draft_config_version_id is None
        assert old_live.status == "published"
        assert old_live.id != published.id
