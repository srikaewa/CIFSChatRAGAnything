from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from sqlmodel import Session, select

from chatbot_manager.bots.service import get_live_config
from chatbot_manager.chatbot.engine import _normalize, _rule_matches
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
            code = str(exc)
            if not code.startswith("knowledge_"):
                code = "knowledge_service_error"
            return self._knowledge_fallback(
                request=request,
                config=config,
                started=started,
                error_code=code,
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
