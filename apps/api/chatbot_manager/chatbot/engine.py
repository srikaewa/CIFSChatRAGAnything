from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Iterable


@dataclass(frozen=True)
class ChatbotInput:
    text: str
    provider: str
    external_user_id: str


@dataclass(frozen=True)
class Decision:
    source: str
    reply_text: str


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
                return Decision(source="rule", reply_text=rule.reply_text)

        if getattr(settings, "rag_enabled", False):
            try:
                rag_reply = await self._answer_with_rag(incoming.text.strip(), getattr(settings, "system_prompt", ""))
            except Exception:
                rag_reply = ""
            if rag_reply.strip():
                return Decision(source="rag", reply_text=rag_reply.strip())

        return Decision(source="fallback", reply_text=fallback_reply)

    async def _answer_with_rag(self, question: str, system_prompt: str) -> str:
        answer = self._rag_service.answer(question, system_prompt)
        if isawaitable(answer):
            answer = await answer
        return answer or ""


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _rule_matches(rule: Any, normalized_text: str) -> bool:
    normalized_pattern = _normalize(getattr(rule, "pattern", ""))
    if not normalized_pattern:
        return False

    match_type = _normalize(getattr(rule, "match_type", "contains"))
    if match_type == "exact":
        return normalized_text == normalized_pattern
    if match_type == "contains":
        return normalized_pattern in normalized_text
    return False
