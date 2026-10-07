from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import Session, select

from chatbot_manager.audit import record_audit
from chatbot_manager.auth.authorization import CurrentUser
from chatbot_manager.models import (
    AuditEvent,
    Bot,
    BotConfigVersion,
    BotDecision,
    ChannelConnection,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
    Incident,
    KnowledgeService,
    User,
    utc_now,
)


class RetentionError(RuntimeError):
    """Stable archive/retention error safe for route mapping."""


@dataclass(frozen=True)
class RetentionResult:
    deleted_conversations: int = 0
    deleted_messages: int = 0
    deleted_decisions: int = 0
    deleted_handoff_events: int = 0
    deleted_incidents: int = 0
    deleted_audit_events: int = 0


class RetentionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _actor(self, user_id: int | None) -> CurrentUser | None:
        if user_id is None:
            return None
        user = self._session.get(User, user_id)
        if user is None or user.id is None:
            return None
        return CurrentUser(
            id=user.id,
            email=user.email,
            role=user.role,
            allowed_bot_ids=frozenset(),
        )

    @staticmethod
    def _cutoff(older_than_days: int):
        if older_than_days <= 0:
            raise RetentionError("retention_days_must_be_positive")
        return utc_now() - timedelta(days=older_than_days)

    def archive_bot(self, bot_id: int, *, actor_user_id: int | None = None) -> Bot:
        bot = self._session.get(Bot, bot_id)
        if bot is None:
            raise RetentionError("bot_not_found")
        if bot.lifecycle_status != "paused":
            raise RetentionError("bot_must_be_paused")

        bot.lifecycle_status = "archived"
        bot.updated_at = utc_now()
        self._session.add(bot)

        channels = self._session.exec(
            select(ChannelConnection).where(ChannelConnection.bot_id == bot_id)
        ).all()
        for channel in channels:
            channel.enabled = False
            channel.status = "disabled"
            channel.updated_at = utc_now()
            self._session.add(channel)

        record_audit(
            self._session,
            self._actor(actor_user_id),
            "bot.archive",
            "bot",
            str(bot_id),
            f"Archived Bot {bot.name}",
            bot_id=bot_id,
            before={"status": "paused"},
            after={
                "status": "archived",
                "disabled_channel_count": len(channels),
            },
        )
        self._session.refresh(bot)
        return bot

    def disable_knowledge_service(
        self,
        service_id: int,
        *,
        actor_user_id: int | None = None,
    ) -> KnowledgeService:
        service = self._session.get(KnowledgeService, service_id)
        if service is None:
            raise RetentionError("knowledge_service_not_found")
        was_enabled = service.enabled
        service.enabled = False
        service.updated_at = utc_now()
        self._session.add(service)
        if was_enabled:
            record_audit(
                self._session,
                self._actor(actor_user_id),
                "knowledge.disable",
                "knowledge_service",
                str(service_id),
                f"Disabled Knowledge Service {service.name}",
                before={"enabled": True},
                after={"enabled": False},
            )
        else:
            self._session.commit()
        self._session.refresh(service)
        return service

    def remove_knowledge_service(
        self,
        service_id: int,
        *,
        actor_user_id: int | None = None,
    ) -> None:
        service = self._session.get(KnowledgeService, service_id)
        if service is None:
            raise RetentionError("knowledge_service_not_found")
        if self._session.exec(
            select(BotConfigVersion.id).where(
                BotConfigVersion.knowledge_service_id == service_id
            )
        ).first() is not None:
            raise RetentionError("knowledge_service_in_use")

        service_name = service.name
        self._session.delete(service)
        record_audit(
            self._session,
            self._actor(actor_user_id),
            "knowledge.remove",
            "knowledge_service",
            str(service_id),
            f"Removed Knowledge Service {service_name}",
            before={"name": service_name},
        )

    def purge_closed_conversations(
        self,
        *,
        older_than_days: int,
        actor_user_id: int | None = None,
    ) -> RetentionResult:
        cutoff = self._cutoff(older_than_days)
        conversation_ids = list(
            self._session.exec(
                select(Conversation.id).where(
                    Conversation.status == "closed",
                    Conversation.closed_at.is_not(None),
                    Conversation.closed_at < cutoff,
                )
            ).all()
        )
        message_ids: list[int] = []
        if conversation_ids:
            message_ids = list(
                self._session.exec(
                    select(ConversationMessage.id).where(
                        ConversationMessage.conversation_id.in_(conversation_ids)
                    )
                ).all()
            )

        deleted_decisions = 0
        if message_ids:
            deleted_decisions = len(
                self._session.exec(
                    select(BotDecision.id).where(BotDecision.message_id.in_(message_ids))
                ).all()
            )
            self._session.exec(
                delete(BotDecision).where(BotDecision.message_id.in_(message_ids))
            )

        deleted_messages = len(message_ids)
        deleted_handoff_events = 0
        if conversation_ids:
            deleted_handoff_events = len(
                self._session.exec(
                    select(ConversationHandoffEvent.id).where(
                        ConversationHandoffEvent.conversation_id.in_(conversation_ids)
                    )
                ).all()
            )
            self._session.exec(
                delete(ConversationMessage).where(
                    ConversationMessage.conversation_id.in_(conversation_ids)
                )
            )
            self._session.exec(
                delete(ConversationHandoffEvent).where(
                    ConversationHandoffEvent.conversation_id.in_(conversation_ids)
                )
            )
            self._session.exec(
                delete(Conversation).where(Conversation.id.in_(conversation_ids))
            )

        result = RetentionResult(
            deleted_conversations=len(conversation_ids),
            deleted_messages=deleted_messages,
            deleted_decisions=deleted_decisions,
            deleted_handoff_events=deleted_handoff_events,
        )
        record_audit(
            self._session,
            self._actor(actor_user_id),
            "retention.conversations",
            "retention",
            "conversations",
            "Purged expired closed conversations",
            after={
                "older_than_days": older_than_days,
                "deleted_conversations": result.deleted_conversations,
                "deleted_messages": result.deleted_messages,
                "deleted_decisions": result.deleted_decisions,
                "deleted_handoff_events": result.deleted_handoff_events,
            },
        )
        return result

    def purge_incidents(
        self,
        *,
        older_than_days: int,
        actor_user_id: int | None = None,
    ) -> RetentionResult:
        cutoff = self._cutoff(older_than_days)
        ids = list(
            self._session.exec(
                select(Incident.id).where(
                    Incident.status == "resolved",
                    Incident.resolved_at.is_not(None),
                    Incident.resolved_at < cutoff,
                )
            ).all()
        )
        if ids:
            self._session.exec(delete(Incident).where(Incident.id.in_(ids)))
        result = RetentionResult(deleted_incidents=len(ids))
        record_audit(
            self._session,
            self._actor(actor_user_id),
            "retention.incidents",
            "retention",
            "incidents",
            "Purged expired resolved incidents",
            after={
                "older_than_days": older_than_days,
                "deleted_incidents": result.deleted_incidents,
            },
        )
        return result

    def purge_audit(
        self,
        *,
        older_than_days: int,
        actor_user_id: int | None = None,
    ) -> RetentionResult:
        cutoff = self._cutoff(older_than_days)
        ids = list(
            self._session.exec(
                select(AuditEvent.id).where(AuditEvent.created_at < cutoff)
            ).all()
        )
        if ids:
            self._session.exec(delete(AuditEvent).where(AuditEvent.id.in_(ids)))
        result = RetentionResult(deleted_audit_events=len(ids))
        record_audit(
            self._session,
            self._actor(actor_user_id),
            "retention.audit",
            "retention",
            "audit",
            "Purged expired audit events",
            after={
                "older_than_days": older_than_days,
                "deleted_audit_events": result.deleted_audit_events,
            },
        )
        return result
