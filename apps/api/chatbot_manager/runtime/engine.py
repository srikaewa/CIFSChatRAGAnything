from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlmodel import Session, select

from chatbot_manager.bots.service import get_live_config
from chatbot_manager.knowledge.client import (
    KnowledgeQuery,
    KnowledgeServiceClient,
    KnowledgeServiceError,
)
from chatbot_manager.knowledge.service import build_knowledge_client
from chatbot_manager.models import BotConfigRule, BotConfigVersion, BotDecision

from .conversations import ConversationService


@dataclass(frozen=True)
class RuntimeRequest:
    bot_id: int
    conversation_id: int
    message_id: int
    text: str
    provider: str
    external_user_id: str
    config_version_id: int | None = None
    test_mode: bool = False


@dataclass(frozen=True)
class RuntimeResult:
    decision_type: str
    reply_text: str
    escalate: bool
    error_code: str
    reference_count: int
    references: list[dict[str, object]]
    config_version_id: int
    knowledge_service_id: int | None
    total_latency_ms: int


KnowledgeClientFactory = Callable[[Session, int], KnowledgeServiceClient]


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _rule_matches(rule: Any, normalized_text: str) -> bool:
    def condition(pattern: str, match_type: str) -> bool:
        normalized_pattern = _normalize(pattern)
        if not normalized_pattern:
            return False
        normalized_match_type = _normalize(match_type)
        if normalized_match_type == "exact":
            return normalized_text == normalized_pattern
        if normalized_match_type == "contains":
            return normalized_pattern in normalized_text
        if normalized_match_type == "regex":
            try:
                return re.search(normalized_pattern, normalized_text) is not None
            except re.error:
                return False
        if normalized_match_type == "starts_with":
            return normalized_text.startswith(normalized_pattern)
        if normalized_match_type == "ends_with":
            return normalized_text.endswith(normalized_pattern)
        if normalized_match_type == "all_words":
            return all(word in normalized_text for word in normalized_pattern.split())
        if normalized_match_type == "any_word":
            return any(word in normalized_text for word in normalized_pattern.split())
        return False

    results = [
        condition(
            getattr(rule, "pattern", ""),
            getattr(rule, "match_type", "contains"),
        )
    ]
    extra_raw = getattr(rule, "conditions", "[]")
    if extra_raw:
        try:
            extra = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
            for item in extra:
                results.append(
                    condition(
                        item.get("pattern", ""),
                        item.get("match_type", "contains"),
                    )
                )
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if not any(results):
        return False
    logic = getattr(rule, "condition_logic", "and")
    return all(results) if logic == "and" else any(results)


def build_bot_instruction(config: BotConfigVersion) -> str:
    parts = [
        config.system_prompt.strip(),
        f"Tone: {config.tone}.",
        f"Language policy: {config.language}.",
        f"Response style: {config.response_style}.",
        config.custom_instructions.strip(),
        "Do not invent facts that are not supported by the retrieved knowledge.",
    ]
    return "\n".join(part for part in parts if part)


class BotRuntime:
    def __init__(
        self,
        session: Session,
        knowledge_client_factory: KnowledgeClientFactory = build_knowledge_client,
    ) -> None:
        self._session = session
        self._knowledge_client_factory = knowledge_client_factory
        self._conversations = ConversationService(session)

    def _config_for(self, request: RuntimeRequest) -> BotConfigVersion:
        if request.config_version_id is None:
            config = get_live_config(self._session, request.bot_id)
        else:
            config = self._session.get(BotConfigVersion, request.config_version_id)
            if config is None or config.bot_id != request.bot_id:
                raise LookupError("Bot configuration not available")
        if config.id is None:
            raise LookupError("Bot configuration is not persisted")
        return config

    def _rules_for(self, config_id: int) -> list[BotConfigRule]:
        return list(
            self._session.exec(
                select(BotConfigRule)
                .where(BotConfigRule.config_version_id == config_id)
                .where(BotConfigRule.enabled == True)  # noqa: E712
                .order_by(BotConfigRule.priority, BotConfigRule.id)
            ).all()
        )

    def _finish(
        self,
        *,
        request: RuntimeRequest,
        config: BotConfigVersion,
        started: float,
        decision_type: str,
        reply_text: str,
        escalate: bool = False,
        error_code: str = "",
        references: list[dict[str, object]] | None = None,
        rule_id: int | None = None,
        retrieval_latency_ms: int | None = None,
    ) -> RuntimeResult:
        refs = references or []
        total_latency_ms = int((perf_counter() - started) * 1000)
        if not request.test_mode:
            decision = BotDecision(
                message_id=request.message_id,
                bot_id=request.bot_id,
                config_version_id=config.id,
                decision_type=decision_type,
                rule_id=rule_id,
                knowledge_service_id=config.knowledge_service_id,
                reference_count=len(refs),
                retrieval_latency_ms=retrieval_latency_ms,
                total_latency_ms=total_latency_ms,
                error_code=error_code,
                metadata_json="{}",
            )
            self._session.add(decision)
            self._session.commit()
        return RuntimeResult(
            decision_type=decision_type,
            reply_text=reply_text,
            escalate=escalate,
            error_code=error_code,
            reference_count=len(refs),
            references=refs,
            config_version_id=config.id,
            knowledge_service_id=config.knowledge_service_id,
            total_latency_ms=total_latency_ms,
        )

    async def run(self, request: RuntimeRequest) -> RuntimeResult:
        started = perf_counter()
        config = self._config_for(request)
        normalized_text = _normalize(request.text)

        if not normalized_text:
            return self._finish(
                request=request,
                config=config,
                started=started,
                decision_type="fallback",
                reply_text=config.fallback_reply,
            )

        for rule in self._rules_for(config.id):
            if not _rule_matches(rule, normalized_text):
                continue
            action = rule.action.strip().upper()
            if action == "CONTINUE_TO_RAG":
                continue
            if action == "ESCALATE":
                return self._finish(
                    request=request,
                    config=config,
                    started=started,
                    decision_type="escalation",
                    reply_text=rule.escalate_message or rule.reply_text,
                    escalate=True,
                    rule_id=rule.id,
                )
            if action == "BLOCK":
                return self._finish(
                    request=request,
                    config=config,
                    started=started,
                    decision_type="blocked",
                    reply_text=rule.reply_text,
                    rule_id=rule.id,
                )
            if action == "RESPOND":
                return self._finish(
                    request=request,
                    config=config,
                    started=started,
                    decision_type="rule",
                    reply_text=rule.reply_text,
                    rule_id=rule.id,
                )

        if config.knowledge_service_id is None:
            return self._knowledge_fallback(
                request=request,
                config=config,
                started=started,
                error_code="knowledge_service_not_configured",
            )

        try:
            knowledge = self._knowledge_client_factory(
                self._session,
                config.knowledge_service_id,
            )
            answer = await knowledge.query(
                KnowledgeQuery(
                    question=request.text.strip(),
                    user_prompt=build_bot_instruction(config),
                    conversation_history=self._conversations.history(request.conversation_id),
                )
            )
        except KnowledgeServiceError as exc:
            return self._knowledge_fallback(
                request=request,
                config=config,
                started=started,
                error_code=exc.code,
            )
        except (LookupError, ValueError):
            return self._knowledge_fallback(
                request=request,
                config=config,
                started=started,
                error_code="knowledge_service_unavailable",
            )

        if not answer.text.strip():
            return self._knowledge_fallback(
                request=request,
                config=config,
                started=started,
                error_code="knowledge_response_empty",
            )

        return self._finish(
            request=request,
            config=config,
            started=started,
            decision_type="rag",
            reply_text=answer.text.strip(),
            references=answer.references,
            retrieval_latency_ms=answer.latency_ms,
        )

    def _knowledge_fallback(
        self,
        *,
        request: RuntimeRequest,
        config: BotConfigVersion,
        started: float,
        error_code: str,
    ) -> RuntimeResult:
        should_escalate = config.fallback_policy.strip().lower() == "escalate"
        return self._finish(
            request=request,
            config=config,
            started=started,
            decision_type="escalation" if should_escalate else "fallback",
            reply_text=config.fallback_reply,
            escalate=should_escalate,
            error_code=error_code,
        )
