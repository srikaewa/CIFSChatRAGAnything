from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from chatbot_manager.models import (
    BotDecision,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
)


@dataclass(frozen=True)
class MetricValue:
    value: int | float
    drilldown_url: str


@dataclass(frozen=True)
class MetricSummary:
    operations: dict[str, MetricValue]
    quality: dict[str, MetricValue]


class MetricService:
    def __init__(self, session: Session, human_wait_warning_minutes: int = 10) -> None:
        self._session = session
        self._human_wait_warning_minutes = human_wait_warning_minutes

    def summary(
        self,
        start: datetime,
        end: datetime,
        bot_id: int | None = None,
    ) -> MetricSummary:
        decisions = self._decisions(start, end, bot_id)
        conversations = self._conversations(start, end, bot_id)
        decision_count = len(decisions)

        fallback_count = sum(row.decision_type == "fallback" for row in decisions)
        escalation_count = sum(row.decision_type == "escalation" for row in decisions)
        rag_attempts = [row for row in decisions if self._is_rag_attempt(row)]
        rag_failures = sum(
            bool(row.error_code) and row.error_code.startswith("knowledge_")
            for row in rag_attempts
        )
        resolved_without_human = self._resolved_without_human(conversations, end)

        average_latency = (
            sum(row.total_latency_ms for row in decisions) / decision_count
            if decision_count
            else 0.0
        )
        wait_cutoff = end - timedelta(minutes=self._human_wait_warning_minutes)
        human_wait_count = sum(
            row.status == "needs_human" and row.last_message_at <= wait_cutoff
            for row in conversations
        )

        operations = {
            "message_count": MetricValue(
                self._message_count(start, end, bot_id),
                self._drilldown(start, end, bot_id),
            ),
            "avg_response_ms": MetricValue(
                average_latency,
                self._drilldown(start, end, bot_id),
            ),
            "human_wait_count": MetricValue(
                human_wait_count,
                self._drilldown(start, end, bot_id, status="needs_human"),
            ),
        }
        quality = {
            "bot_resolution_rate": MetricValue(
                self._rate(resolved_without_human, len(conversations)),
                self._drilldown(start, end, bot_id),
            ),
            "fallback_rate": MetricValue(
                self._rate(fallback_count, decision_count),
                self._drilldown(start, end, bot_id, decision="fallback"),
            ),
            "escalation_rate": MetricValue(
                self._rate(escalation_count, decision_count),
                self._drilldown(start, end, bot_id, decision="escalation"),
            ),
            "rag_failure_rate": MetricValue(
                self._rate(rag_failures, len(rag_attempts)),
                self._drilldown(start, end, bot_id, decision="knowledge_error"),
            ),
        }
        return MetricSummary(operations=operations, quality=quality)

    def _decisions(
        self,
        start: datetime,
        end: datetime,
        bot_id: int | None,
    ) -> list[BotDecision]:
        statement = select(BotDecision).where(
            BotDecision.created_at >= start,
            BotDecision.created_at <= end,
        )
        if bot_id is not None:
            statement = statement.where(BotDecision.bot_id == bot_id)
        return list(self._session.exec(statement).all())

    def _conversations(
        self,
        start: datetime,
        end: datetime,
        bot_id: int | None,
    ) -> list[Conversation]:
        statement = select(Conversation).where(
            Conversation.started_at >= start,
            Conversation.started_at <= end,
        )
        if bot_id is not None:
            statement = statement.where(Conversation.bot_id == bot_id)
        return list(self._session.exec(statement).all())

    def _message_count(
        self,
        start: datetime,
        end: datetime,
        bot_id: int | None,
    ) -> int:
        statement = (
            select(func.count(ConversationMessage.id))
            .join(
                Conversation,
                Conversation.id == ConversationMessage.conversation_id,
            )
            .where(
                ConversationMessage.created_at >= start,
                ConversationMessage.created_at <= end,
            )
        )
        if bot_id is not None:
            statement = statement.where(Conversation.bot_id == bot_id)
        return int(self._session.exec(statement).one())

    def _resolved_without_human(
        self,
        conversations: list[Conversation],
        end: datetime,
    ) -> int:
        eligible_ids = [row.id for row in conversations if row.id is not None]
        if not eligible_ids:
            return 0
        takeover_ids = set(
            self._session.exec(
                select(ConversationHandoffEvent.conversation_id).where(
                    ConversationHandoffEvent.conversation_id.in_(eligible_ids),
                    ConversationHandoffEvent.event_type.in_(("taken", "assigned")),
                    ConversationHandoffEvent.created_at <= end,
                )
            ).all()
        )
        return sum(
            row.status in {"closed", "bot_active"} and row.id not in takeover_ids
            for row in conversations
        )

    @staticmethod
    def _is_rag_attempt(decision: BotDecision) -> bool:
        if decision.decision_type == "rag":
            return True
        return (
            decision.decision_type in {"fallback", "escalation"}
            and decision.error_code.startswith("knowledge_")
            and decision.error_code != "knowledge_service_not_configured"
        )

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _drilldown(
        start: datetime,
        end: datetime,
        bot_id: int | None,
        **filters: str,
    ) -> str:
        parts: list[str] = []
        if bot_id is not None:
            parts.append(f"bot_id={bot_id}")
        parts.extend(f"{key}={value}" for key, value in filters.items())
        parts.extend((f"from={start.isoformat()}", f"to={end.isoformat()}"))
        return "/conversations?" + "&".join(parts)
