import pytest
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.knowledge.client import (
    FakeKnowledgeServiceClient,
    KnowledgeAnswer,
    KnowledgeServiceError,
)
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotConfigVersion,
    BotDecision,
    KnowledgeService,
)
from chatbot_manager.runtime.conversations import ConversationService
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest


class RaisingKnowledgeClient(FakeKnowledgeServiceClient):
    async def query(self, request):
        raise AssertionError("Knowledge Service should not be called")


class FailingKnowledgeClient(FakeKnowledgeServiceClient):
    async def query(self, request):
        raise KnowledgeServiceError("knowledge_service_unavailable")


def _prepare_runtime_state(session: Session):
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    config = session.get(BotConfigVersion, bot.live_config_version_id)
    assert config is not None

    service = KnowledgeService(
        name="Runtime RAG",
        service_type="lightrag",
        api_base_url="https://rag.example.test",
    )
    session.add(service)
    session.commit()
    session.refresh(service)

    config.system_prompt = "You are the CIFS specialist."
    config.tone = "friendly"
    config.language = "Thai"
    config.response_style = "brief with sources"
    config.custom_instructions = "Use official manuals when available."
    config.fallback_reply = "Please contact CIFS staff."
    config.fallback_policy = "reply"
    config.knowledge_service_id = service.id
    session.add(config)
    session.commit()
    session.refresh(config)

    conversations = ConversationService(session)
    conversation = conversations.get_or_create(bot.id, 1, "u-runtime")
    session.add_all([])
    prior_user = conversations.record_inbound(conversation, "prior-1", "Earlier question", {})
    from chatbot_manager.models import ConversationMessage

    session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            sender_type="bot",
            content="Earlier answer",
        )
    )
    session.commit()
    current = conversations.record_inbound(conversation, "current-1", "What documents are needed?", {})
    return bot, config, service, conversation, current, prior_user


@pytest.mark.asyncio
async def test_runtime_rule_response_skips_knowledge(client) -> None:
    with Session(get_engine()) as session:
        bot, config, _service, conversation, message, _prior = _prepare_runtime_state(session)
        session.add(
            BotConfigRule(
                config_version_id=config.id,
                priority=1,
                match_type="contains",
                pattern="documents",
                action="RESPOND",
                reply_text="Use the application checklist.",
            )
        )
        session.commit()
        knowledge = RaisingKnowledgeClient()
        runtime = BotRuntime(session, knowledge_client_factory=lambda _session, _id: knowledge)

        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="line",
                external_user_id="u-runtime",
            )
        )

        assert result.decision_type == "rule"
        assert result.reply_text == "Use the application checklist."
        assert result.escalate is False
        decisions = session.exec(select(BotDecision)).all()
        assert len(decisions) == 1
        assert decisions[0].decision_type == "rule"
        assert decisions[0].rule_id is not None


@pytest.mark.asyncio
async def test_runtime_escalation_rule_skips_knowledge(client) -> None:
    with Session(get_engine()) as session:
        bot, config, _service, conversation, message, _prior = _prepare_runtime_state(session)
        session.add(
            BotConfigRule(
                config_version_id=config.id,
                priority=1,
                match_type="contains",
                pattern="documents",
                action="ESCALATE",
                reply_text="Internal escalation context",
                escalate_message="A staff member will continue this conversation.",
            )
        )
        session.commit()
        knowledge = RaisingKnowledgeClient()
        runtime = BotRuntime(session, knowledge_client_factory=lambda _session, _id: knowledge)

        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="telegram",
                external_user_id="u-runtime",
            )
        )

        assert result.decision_type == "escalation"
        assert result.escalate is True
        assert result.reply_text == "A staff member will continue this conversation."


@pytest.mark.asyncio
async def test_runtime_rag_uses_bot_instruction_and_history(client) -> None:
    with Session(get_engine()) as session:
        bot, config, service, conversation, message, _prior = _prepare_runtime_state(session)
        knowledge = FakeKnowledgeServiceClient(
            answer=KnowledgeAnswer(
                text="Grounded answer",
                references=[{"file_name": "manual.pdf"}, {"file_name": "policy.pdf"}],
                latency_ms=17,
            )
        )
        runtime = BotRuntime(session, knowledge_client_factory=lambda _session, _id: knowledge)

        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="messenger",
                external_user_id="u-runtime",
            )
        )

        assert result.decision_type == "rag"
        assert result.reply_text == "Grounded answer"
        assert result.reference_count == 2
        assert result.references[0]["file_name"] == "manual.pdf"
        assert result.knowledge_service_id == service.id
        assert len(knowledge.queries) == 1
        query = knowledge.queries[0]
        assert query.question == "What documents are needed?"
        assert "You are the CIFS specialist." in query.user_prompt
        assert "Tone: friendly." in query.user_prompt
        assert "Language policy: Thai." in query.user_prompt
        assert "Response style: brief with sources." in query.user_prompt
        assert "Use official manuals when available." in query.user_prompt
        assert {"role": "user", "content": "Earlier question"} in query.conversation_history
        assert {"role": "assistant", "content": "Earlier answer"} in query.conversation_history

        decision = session.exec(select(BotDecision)).one()
        assert decision.decision_type == "rag"
        assert decision.knowledge_service_id == service.id
        assert decision.reference_count == 2
        assert decision.retrieval_latency_ms == 17


@pytest.mark.asyncio
async def test_runtime_knowledge_failure_uses_configured_fallback(client) -> None:
    with Session(get_engine()) as session:
        bot, config, service, conversation, message, _prior = _prepare_runtime_state(session)
        knowledge = FailingKnowledgeClient()
        runtime = BotRuntime(session, knowledge_client_factory=lambda _session, _id: knowledge)

        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="line",
                external_user_id="u-runtime",
            )
        )

        assert result.decision_type == "fallback"
        assert result.reply_text == "Please contact CIFS staff."
        assert result.escalate is False
        assert result.error_code == "knowledge_service_unavailable"
        assert result.knowledge_service_id == service.id
        decision = session.exec(select(BotDecision)).one()
        assert decision.error_code == "knowledge_service_unavailable"


@pytest.mark.asyncio
async def test_runtime_continue_to_rag_rule_does_not_short_circuit(client) -> None:
    with Session(get_engine()) as session:
        bot, config, _service, conversation, message, _prior = _prepare_runtime_state(session)
        session.add(
            BotConfigRule(
                config_version_id=config.id,
                priority=1,
                match_type="contains",
                pattern="documents",
                action="CONTINUE_TO_RAG",
                reply_text="ignored",
            )
        )
        session.commit()
        knowledge = FakeKnowledgeServiceClient(
            answer=KnowledgeAnswer(text="RAG after rule", references=[], latency_ms=3)
        )
        runtime = BotRuntime(session, knowledge_client_factory=lambda _session, _id: knowledge)

        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="line",
                external_user_id="u-runtime",
            )
        )

        assert result.decision_type == "rag"
        assert result.reply_text == "RAG after rule"
        assert len(knowledge.queries) == 1
