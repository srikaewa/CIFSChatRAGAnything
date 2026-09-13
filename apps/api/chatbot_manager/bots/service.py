from sqlmodel import Session, select

from chatbot_manager.models import (
    AssistantSettings,
    Bot,
    BotConfigRule,
    BotConfigVersion,
    Channel,
    ChatEvent,
    Rule,
    utc_now,
)


DEFAULT_BOT_NAME = "Default Bot"


def _attach_legacy_rows(session: Session, bot_id: int) -> None:
    changed = False
    for channel in session.exec(select(Channel)).all():
        if channel.bot_id is None:
            channel.bot_id = bot_id
            session.add(channel)
            changed = True
    for event in session.exec(select(ChatEvent)).all():
        if event.bot_id is None:
            event.bot_id = bot_id
            session.add(event)
            changed = True
    if changed:
        session.commit()


def ensure_default_bot(session: Session) -> Bot:
    existing = session.exec(select(Bot).where(Bot.name == DEFAULT_BOT_NAME)).first()
    if existing is not None:
        if existing.id is None:
            raise LookupError("Default Bot has no persisted id")
        _attach_legacy_rows(session, existing.id)
        return existing

    legacy = session.get(AssistantSettings, 1) or AssistantSettings()
    bot = Bot(
        name=DEFAULT_BOT_NAME,
        description="Migrated from the original single-assistant configuration",
        lifecycle_status="active",
    )
    session.add(bot)
    session.commit()
    session.refresh(bot)
    if bot.id is None:
        raise LookupError("Default Bot could not be persisted")

    version = BotConfigVersion(
        bot_id=bot.id,
        version_number=1,
        status="published",
        system_prompt=legacy.system_prompt,
        fallback_reply=legacy.fallback_reply,
        tone="professional",
        language="auto",
        response_style="concise",
        fallback_policy="reply",
        escalation_policy="{}",
        created_by="migration",
        published_at=utc_now(),
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    if version.id is None:
        raise LookupError("Default Bot live config could not be persisted")

    for legacy_rule in session.exec(select(Rule).order_by(Rule.priority)).all():
        session.add(
            BotConfigRule(
                config_version_id=version.id,
                name=legacy_rule.pattern,
                enabled=legacy_rule.enabled,
                priority=legacy_rule.priority,
                match_type=legacy_rule.match_type,
                pattern=legacy_rule.pattern,
                condition_logic=legacy_rule.condition_logic,
                conditions=legacy_rule.conditions,
                action="ESCALATE" if legacy_rule.escalate else "RESPOND",
                reply_text=legacy_rule.reply_text,
                escalate_message=legacy_rule.escalate_message,
            )
        )

    bot.live_config_version_id = version.id
    bot.updated_at = utc_now()
    session.add(bot)
    _attach_legacy_rows(session, bot.id)
    session.commit()
    session.refresh(bot)
    return bot


def get_live_config(session: Session, bot_id: int) -> BotConfigVersion:
    bot = session.get(Bot, bot_id)
    if bot is None or bot.live_config_version_id is None:
        raise LookupError(f"Bot {bot_id} has no live configuration")
    live = session.get(BotConfigVersion, bot.live_config_version_id)
    if live is None:
        raise LookupError(f"Bot {bot_id} live configuration is missing")
    return live


def ensure_draft_config(session: Session, bot_id: int, actor: str) -> BotConfigVersion:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise LookupError(f"Bot {bot_id} not found")

    if bot.draft_config_version_id is not None:
        existing = session.get(BotConfigVersion, bot.draft_config_version_id)
        if existing is not None and existing.status == "draft":
            return existing

    live = get_live_config(session, bot_id)
    versions = session.exec(
        select(BotConfigVersion).where(BotConfigVersion.bot_id == bot_id)
    ).all()
    next_version = max((version.version_number for version in versions), default=0) + 1

    draft = BotConfigVersion(
        bot_id=bot_id,
        version_number=next_version,
        status="draft",
        system_prompt=live.system_prompt,
        tone=live.tone,
        language=live.language,
        response_style=live.response_style,
        fallback_reply=live.fallback_reply,
        fallback_policy=live.fallback_policy,
        escalation_policy=live.escalation_policy,
        custom_instructions=live.custom_instructions,
        knowledge_service_id=live.knowledge_service_id,
        created_by=actor,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    if draft.id is None:
        raise LookupError("Draft configuration could not be persisted")

    live_rules = session.exec(
        select(BotConfigRule)
        .where(BotConfigRule.config_version_id == live.id)
        .order_by(BotConfigRule.priority)
    ).all()
    for rule in live_rules:
        session.add(
            BotConfigRule(
                config_version_id=draft.id,
                name=rule.name,
                enabled=rule.enabled,
                priority=rule.priority,
                match_type=rule.match_type,
                pattern=rule.pattern,
                condition_logic=rule.condition_logic,
                conditions=rule.conditions,
                action=rule.action,
                reply_text=rule.reply_text,
                escalate_message=rule.escalate_message,
            )
        )

    bot.draft_config_version_id = draft.id
    bot.updated_at = utc_now()
    session.add(bot)
    session.commit()
    session.refresh(draft)
    return draft
