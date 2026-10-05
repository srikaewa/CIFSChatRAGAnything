from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from chatbot_manager.models import Bot, BotConfigRule, BotConfigVersion, utc_now


EDITABLE_CONFIG_FIELDS = {
    "system_prompt",
    "tone",
    "language",
    "response_style",
    "fallback_reply",
    "fallback_policy",
    "escalation_policy",
    "custom_instructions",
    "knowledge_service_id",
}


class VersionStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleInput:
    name: str = ""
    enabled: bool = True
    priority: int = 100
    match_type: str = "contains"
    pattern: str = ""
    condition_logic: str = "and"
    conditions: str = "[]"
    action: str = "RESPOND"
    reply_text: str = ""
    escalate_message: str = ""


def _get_bot(session: Session, bot_id: int) -> Bot:
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise LookupError(f"Bot {bot_id} not found")
    return bot


def _next_version_number(session: Session, bot_id: int) -> int:
    versions = session.exec(
        select(BotConfigVersion).where(BotConfigVersion.bot_id == bot_id)
    ).all()
    return max((version.version_number for version in versions), default=0) + 1


def _copy_rule(source: BotConfigRule, config_version_id: int) -> BotConfigRule:
    return BotConfigRule(
        config_version_id=config_version_id,
        name=source.name,
        enabled=source.enabled,
        priority=source.priority,
        match_type=source.match_type,
        pattern=source.pattern,
        condition_logic=source.condition_logic,
        conditions=source.conditions,
        action=source.action,
        reply_text=source.reply_text,
        escalate_message=source.escalate_message,
    )


def _rule_from_input(rule: RuleInput, config_version_id: int) -> BotConfigRule:
    return BotConfigRule(
        config_version_id=config_version_id,
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


def _clone_version(
    session: Session,
    bot: Bot,
    source: BotConfigVersion,
    actor: str,
) -> BotConfigVersion:
    if bot.id is None:
        raise LookupError("Bot has no persisted id")
    if source.id is None:
        raise LookupError("Source configuration has no persisted id")

    draft = BotConfigVersion(
        bot_id=bot.id,
        version_number=_next_version_number(session, bot.id),
        status="draft",
        system_prompt=source.system_prompt,
        tone=source.tone,
        language=source.language,
        response_style=source.response_style,
        fallback_reply=source.fallback_reply,
        fallback_policy=source.fallback_policy,
        escalation_policy=source.escalation_policy,
        custom_instructions=source.custom_instructions,
        knowledge_service_id=source.knowledge_service_id,
        created_by=actor,
    )
    session.add(draft)
    session.flush()
    if draft.id is None:
        raise LookupError("Draft configuration could not be persisted")

    source_rules = session.exec(
        select(BotConfigRule)
        .where(BotConfigRule.config_version_id == source.id)
        .order_by(BotConfigRule.priority)
    ).all()
    for rule in source_rules:
        session.add(_copy_rule(rule, draft.id))

    bot.draft_config_version_id = draft.id
    bot.updated_at = utc_now()
    session.add(bot)
    session.commit()
    session.refresh(draft)
    return draft


def clone_live_to_draft(session: Session, bot_id: int, actor: str) -> BotConfigVersion:
    bot = _get_bot(session, bot_id)
    if bot.draft_config_version_id is not None:
        existing = session.get(BotConfigVersion, bot.draft_config_version_id)
        if existing is not None and existing.status == "draft":
            return existing
        if existing is not None:
            raise VersionStateError("published_version_immutable")
        raise VersionStateError("draft_version_missing")

    if bot.live_config_version_id is None:
        raise VersionStateError("live_version_missing")
    live = session.get(BotConfigVersion, bot.live_config_version_id)
    if live is None:
        raise VersionStateError("live_version_missing")
    if live.status != "published":
        raise VersionStateError("live_version_not_published")
    return _clone_version(session, bot, live, actor)


def get_draft_config(session: Session, bot_id: int) -> BotConfigVersion | None:
    bot = _get_bot(session, bot_id)
    if bot.draft_config_version_id is None:
        return None
    draft = session.get(BotConfigVersion, bot.draft_config_version_id)
    if draft is None or draft.status != "draft":
        return None
    return draft


def _draft_for_edit(session: Session, bot_id: int) -> BotConfigVersion:
    bot = _get_bot(session, bot_id)
    if bot.draft_config_version_id is None:
        return clone_live_to_draft(session, bot_id, "system")
    draft = session.get(BotConfigVersion, bot.draft_config_version_id)
    if draft is None:
        raise VersionStateError("draft_version_missing")
    if draft.status != "draft":
        raise VersionStateError("published_version_immutable")
    return draft


def update_draft_config(
    session: Session,
    bot_id: int,
    changes: dict[str, object],
) -> BotConfigVersion:
    if any(field not in EDITABLE_CONFIG_FIELDS for field in changes):
        raise VersionStateError("config_field_not_editable")

    draft = _draft_for_edit(session, bot_id)
    for field, value in changes.items():
        setattr(draft, field, value)
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def replace_draft_rules(
    session: Session,
    bot_id: int,
    rules: list[RuleInput],
) -> list[BotConfigRule]:
    draft = _draft_for_edit(session, bot_id)
    if draft.id is None:
        raise LookupError("Draft configuration has no persisted id")

    existing = session.exec(
        select(BotConfigRule).where(BotConfigRule.config_version_id == draft.id)
    ).all()
    for rule in existing:
        session.delete(rule)
    session.flush()

    for rule in rules:
        session.add(_rule_from_input(rule, draft.id))
    session.commit()
    return session.exec(
        select(BotConfigRule)
        .where(BotConfigRule.config_version_id == draft.id)
        .order_by(BotConfigRule.priority)
    ).all()


def restore_version_as_draft(
    session: Session,
    bot_id: int,
    source_version_id: int,
    actor: str,
) -> BotConfigVersion:
    bot = _get_bot(session, bot_id)
    source = session.get(BotConfigVersion, source_version_id)
    if source is None:
        raise LookupError(f"Configuration version {source_version_id} not found")
    if source.bot_id != bot_id:
        raise VersionStateError("version_not_owned_by_bot")
    if source.status != "published":
        raise VersionStateError("restore_source_not_published")
    return _clone_version(session, bot, source, actor)


def publish_draft(session: Session, bot_id: int, actor: str) -> BotConfigVersion:
    bot = _get_bot(session, bot_id)
    if bot.draft_config_version_id is None:
        raise VersionStateError("draft_version_missing")
    draft = session.get(BotConfigVersion, bot.draft_config_version_id)
    if draft is None:
        raise VersionStateError("draft_version_missing")
    if draft.status != "draft":
        raise VersionStateError("published_version_immutable")

    draft.status = "published"
    draft.published_at = utc_now()
    draft.created_by = actor
    bot.live_config_version_id = draft.id
    bot.draft_config_version_id = None
    bot.updated_at = utc_now()
    session.add(draft)
    session.add(bot)
    session.commit()
    session.refresh(draft)
    return draft
