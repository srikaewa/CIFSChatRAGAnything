import pytest

from chatbot_manager.chatbot.engine import ChatbotEngine, ChatbotInput
from chatbot_manager.models import AssistantSettings, Rule
from chatbot_manager.rag.service import FakeRagService, RagService


@pytest.mark.asyncio
async def test_help_command_wins_before_rules() -> None:
    engine = ChatbotEngine(FakeRagService(answer="rag reply"))
    settings = AssistantSettings()
    rules = [Rule(priority=1, match_type="exact", pattern="help", reply_text="rule reply")]

    decision = await engine.answer(ChatbotInput(" HELP ", "line", "user-1"), rules, settings)

    assert decision.source == "command"
    assert "help" in decision.reply_text.lower()
    assert decision.reply_text != "rule reply"


@pytest.mark.asyncio
async def test_exact_rule_wins_before_rag() -> None:
    engine = ChatbotEngine(FakeRagService(answer="rag reply"))
    settings = AssistantSettings(rag_enabled=True)
    rules = [Rule(match_type="exact", pattern="opening hours", reply_text="Open 9 to 5.")]

    decision = await engine.answer(ChatbotInput(" opening   HOURS ", "messenger", "user-2"), rules, settings)

    assert decision.source == "rule"
    assert decision.reply_text == "Open 9 to 5."


@pytest.mark.asyncio
async def test_contains_rules_use_priority_order() -> None:
    engine = ChatbotEngine(FakeRagService(answer="rag reply"))
    settings = AssistantSettings()
    rules = [
        Rule(priority=20, match_type="contains", pattern="price", reply_text="Low priority price reply."),
        Rule(priority=5, match_type="contains", pattern="service", reply_text="High priority service reply."),
    ]

    decision = await engine.answer(ChatbotInput("Tell me the service price", "line", "user-3"), rules, settings)

    assert decision.source == "rule"
    assert decision.reply_text == "High priority service reply."


@pytest.mark.asyncio
async def test_rag_used_when_no_rule_matches() -> None:
    engine = ChatbotEngine(FakeRagService(answer="knowledge base reply"))
    settings = AssistantSettings(rag_enabled=True, system_prompt="Use KB.")
    rules = [Rule(match_type="contains", pattern="pricing", reply_text="Rule reply.")]

    decision = await engine.answer(ChatbotInput("What documents are needed?", "line", "user-4"), rules, settings)

    assert decision.source == "rag"
    assert decision.reply_text == "knowledge base reply"


@pytest.mark.asyncio
async def test_fallback_used_when_rag_empty() -> None:
    engine = ChatbotEngine(FakeRagService(answer=""))
    settings = AssistantSettings(rag_enabled=True, fallback_reply="Please contact staff.")

    decision = await engine.answer(ChatbotInput("unknown question", "line", "user-5"), [], settings)

    assert decision.source == "fallback"
    assert decision.reply_text == "Please contact staff."


@pytest.mark.asyncio
async def test_disabled_rules_are_ignored() -> None:
    engine = ChatbotEngine(FakeRagService(answer=""))
    settings = AssistantSettings(rag_enabled=False, fallback_reply="Fallback.")
    rules = [Rule(enabled=False, match_type="contains", pattern="unknown", reply_text="Disabled reply.")]

    decision = await engine.answer(ChatbotInput("unknown question", "line", "user-6"), rules, settings)

    assert decision.source == "fallback"
    assert decision.reply_text == "Fallback."


class RaisingRagService(RagService):
    async def answer(self, question: str, system_prompt: str) -> str:
        raise AssertionError("RAG should not be called for empty input")


@pytest.mark.asyncio
async def test_empty_input_uses_fallback_without_rag() -> None:
    engine = ChatbotEngine(RaisingRagService())
    settings = AssistantSettings(rag_enabled=True, fallback_reply="Please type a question.")

    decision = await engine.answer(ChatbotInput("   \n\t  ", "line", "user-7"), [], settings)

    assert decision.source == "fallback"
    assert decision.reply_text == "Please type a question."


@pytest.mark.asyncio
async def test_base_rag_service_requires_concrete_implementation() -> None:
    with pytest.raises(NotImplementedError):
        await RagService().answer("question", "prompt")


class FailingRagService(RagService):
    async def answer(self, question: str, system_prompt: str) -> str:
        raise RuntimeError("RAG backend down")


@pytest.mark.asyncio
async def test_rag_error_uses_fallback() -> None:
    engine = ChatbotEngine(FailingRagService())
    settings = AssistantSettings(rag_enabled=True, fallback_reply="Please contact staff.")

    decision = await engine.answer(ChatbotInput("unknown", "line", "user-8"), [], settings)

    assert decision.source == "fallback"
    assert decision.reply_text == "Please contact staff."
