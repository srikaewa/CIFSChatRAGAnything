from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from chatbot_manager.channel_definitions import CHANNEL_DEFINITIONS
from chatbot_manager.channel_connections import resolve_connection_credentials
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotTestCase,
    ChannelConnection,
    KnowledgeService,
)


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    status: str
    message: str


@dataclass(frozen=True)
class ReadinessResult:
    checks: tuple[ReadinessCheck, ...]

    @property
    def can_publish(self) -> bool:
        return all(check.status != "fail" for check in self.checks)


class ReadinessService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def evaluate(self, bot_id: int, config_version_id: int) -> ReadinessResult:
        bot = self._session.get(Bot, bot_id)
        if bot is None:
            raise LookupError(f"Bot {bot_id} not found")
        config = self._session.get(BotConfigVersion, config_version_id)
        if config is None or config.bot_id != bot_id:
            raise LookupError("Bot configuration not available")

        checks = (
            self._bot_profile(bot),
            self._behavior(config),
            self._knowledge(config),
            self._channels(bot_id),
            self._regression_presence(bot_id),
        )
        return ReadinessResult(checks=checks)

    def _bot_profile(self, bot: Bot) -> ReadinessCheck:
        if not bot.name.strip():
            return ReadinessCheck("bot_profile", "fail", "Bot name is required.")
        return ReadinessCheck("bot_profile", "pass", "Bot profile is complete.")

    def _behavior(self, config: BotConfigVersion) -> ReadinessCheck:
        if not config.system_prompt.strip():
            return ReadinessCheck("behavior", "fail", "System prompt is required.")
        policy = config.fallback_policy.strip().lower()
        if policy not in {"reply", "escalate"}:
            return ReadinessCheck("behavior", "fail", "Fallback policy is invalid.")
        if not config.fallback_reply.strip():
            return ReadinessCheck("behavior", "fail", "Fallback reply is required.")
        return ReadinessCheck("behavior", "pass", "Behavior is complete.")

    def _rag_expected(self, config: BotConfigVersion) -> bool:
        if config.knowledge_service_id is not None:
            return True
        if config.id is None:
            return False
        rules = self._session.exec(
            select(BotConfigRule)
            .where(BotConfigRule.config_version_id == config.id)
            .where(BotConfigRule.enabled == True)  # noqa: E712
        ).all()
        return any(rule.action.strip().upper() == "CONTINUE_TO_RAG" for rule in rules)

    def _knowledge(self, config: BotConfigVersion) -> ReadinessCheck:
        if not self._rag_expected(config):
            return ReadinessCheck(
                "knowledge_service",
                "pass",
                "No configured rule requires the Knowledge Service path.",
            )
        if config.knowledge_service_id is None:
            return ReadinessCheck(
                "knowledge_service",
                "fail",
                "A Knowledge Service is required for the configured RAG path.",
            )
        service = self._session.get(KnowledgeService, config.knowledge_service_id)
        if service is None or not service.enabled:
            return ReadinessCheck(
                "knowledge_service",
                "fail",
                "Selected Knowledge Service is unavailable.",
            )
        if service.health_status.strip().lower() != "healthy":
            return ReadinessCheck(
                "knowledge_service",
                "fail",
                "Selected Knowledge Service is not healthy.",
            )
        return ReadinessCheck(
            "knowledge_service",
            "pass",
            "Selected Knowledge Service is healthy.",
        )

    def _channels(self, bot_id: int) -> ReadinessCheck:
        enabled = self._session.exec(
            select(ChannelConnection)
            .where(ChannelConnection.bot_id == bot_id)
            .where(ChannelConnection.enabled == True)  # noqa: E712
        ).all()
        if not enabled:
            return ReadinessCheck(
                "channel_readiness",
                "warning",
                "No enabled channel is configured.",
            )

        for connection in enabled:
            definition = CHANNEL_DEFINITIONS.get(connection.provider)
            if definition is None:
                return ReadinessCheck(
                    "channel_readiness",
                    "fail",
                    f"Enabled channel {connection.display_name} uses an unsupported provider.",
                )
            if connection.status.strip().lower() == "failed":
                return ReadinessCheck(
                    "channel_readiness",
                    "fail",
                    f"Enabled channel {connection.display_name} is failed.",
                )
            try:
                credentials = resolve_connection_credentials(self._session, connection)
            except (LookupError, ValueError):
                credentials = {}
            missing = [
                field
                for field in definition.required_fields
                if not credentials.get(field, "").strip()
            ]
            if missing:
                return ReadinessCheck(
                    "channel_readiness",
                    "fail",
                    f"Enabled channel {connection.display_name} is missing required credentials.",
                )

        return ReadinessCheck(
            "channel_readiness",
            "pass",
            "Enabled channels are configured.",
        )

    def _regression_presence(self, bot_id: int) -> ReadinessCheck:
        enabled = self._session.exec(
            select(BotTestCase)
            .where(BotTestCase.bot_id == bot_id)
            .where(BotTestCase.enabled == True)  # noqa: E712
        ).first()
        if enabled is None:
            return ReadinessCheck(
                "regression_presence",
                "warning",
                "No enabled saved regression tests exist yet.",
            )
        return ReadinessCheck(
            "regression_presence",
            "pass",
            "Saved regression tests are available.",
        )
