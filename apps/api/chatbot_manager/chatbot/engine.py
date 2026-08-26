from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Iterable

logger = logging.getLogger(__name__)
RESPONSE_GENERATION_FAILED = "response_generation_failed"
RESPONSE_GENERATION_FAILED_MESSAGE = "Unable to generate a response. The fallback reply was used."


@dataclass(frozen=True)
class ChatbotInput:
    text: str
    provider: str
    external_user_id: str


@dataclass(frozen=True)
class Decision:
    source: str
    reply_text: str
    escalate: bool = False
    rule_reply: str = ""
    error: str = ""


class ChatbotEngine:
    def __init__(self, rag_service: Any) -> None:
        self._rag_service = rag_service

    async def answer(self, incoming: ChatbotInput, rules: Iterable[Any], settings: Any) -> Decision:
        normalized_text = _normalize(incoming.text)
        fallback_reply = getattr(settings, "fallback_reply", "")

        if not normalized_text:
            return Decision(source="fallback", reply_text=fallback_reply)

        if normalized_text in {"help", "/help"}:
            return Decision(
                source="command",
                reply_text="Available commands: help, start. Ask a question and I will answer from configured rules or the knowledge base.",
            )

        if normalized_text in {"start", "/start"}:
            return Decision(
                source="command",
                reply_text="Hello. Ask a question and I will help with the available information.",
            )

        for rule in sorted((rule for rule in rules if getattr(rule, "enabled", False)), key=lambda rule: rule.priority):
            if _rule_matches(rule, normalized_text):
                escalate = getattr(rule, "escalate", False)
                reply = rule.escalate_message if escalate and rule.escalate_message else rule.reply_text
                return Decision(source="rule", reply_text=reply, escalate=escalate, rule_reply=rule.reply_text if escalate else "")

        if getattr(settings, "rag_enabled", False):
            try:
                rag_reply = await self._answer_with_rag(incoming.text.strip(), getattr(settings, "system_prompt", ""))
            except Exception as exc:
                logger.error(
                    "RAG response generation failed provider=%s error_type=%s",
                    incoming.provider,
                    type(exc).__name__,
                )
                rag_reply = ""
                error = RESPONSE_GENERATION_FAILED
            else:
                error = RESPONSE_GENERATION_FAILED if not rag_reply.strip() else ""
            if rag_reply.strip():
                return Decision(source="rag", reply_text=rag_reply.strip())
            if error:
                return Decision(source="fallback", reply_text=fallback_reply, error=error)

        return Decision(source="fallback", reply_text=fallback_reply)

    async def _answer_with_rag(self, question: str, system_prompt: str) -> str:
        answer = self._rag_service.answer(question, system_prompt)
        if isawaitable(answer):
            answer = await answer
        return answer or ""


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _rule_matches(rule: Any, normalized_text: str) -> bool:
    def _condition(pattern: str, match_type: str) -> bool:
        np = _normalize(pattern)
        if not np:
            return False
        mt = _normalize(match_type)
        if mt == "exact":
            return normalized_text == np
        if mt == "contains":
            return np in normalized_text
        if mt == "regex":
            try:
                return re.search(np, normalized_text) is not None
            except re.error:
                return False
        if mt == "starts_with":
            return normalized_text.startswith(np)
        if mt == "ends_with":
            return normalized_text.endswith(np)
        if mt == "all_words":
            words = np.split()
            return all(word in normalized_text for word in words)
        if mt == "any_word":
            words = np.split()
            return any(word in normalized_text for word in words)
        return False

    results = []
    primary = _condition(getattr(rule, "pattern", ""), getattr(rule, "match_type", "contains"))
    results.append(primary)

    extra_raw = getattr(rule, "conditions", "[]")
    if extra_raw:
        try:
            extra = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
            for cond in extra:
                results.append(_condition(cond.get("pattern", ""), cond.get("match_type", "contains")))
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if not results or not any(results):
        return False
    logic = getattr(rule, "condition_logic", "and")
    return all(results) if logic == "and" else any(results)
