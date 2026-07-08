from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import AssistantSettings, Channel, ChatEvent, KnowledgeDocument, Rule


def test_rule_model_round_trip() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Rule(match_type="contains", pattern="price", reply_text="Our price list is here."))
        session.commit()
        rule = session.exec(select(Rule)).one()

    assert rule.enabled is True
    assert rule.priority == 100
    assert rule.pattern == "price"


def test_assistant_defaults() -> None:
    settings = AssistantSettings()
    assert settings.id == 1
    assert settings.rag_enabled is True
    assert "don't have enough information" in settings.fallback_reply.lower()
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_api_key == ""
    assert settings.embedding_model == "text-embedding-3-small"


def test_channel_document_and_event_models_construct() -> None:
    channel = Channel(provider="line", display_name="LINE")
    document = KnowledgeDocument(filename="menu.pdf", path="data/uploads/menu.pdf")
    event = ChatEvent(provider="messenger", incoming_text="hello", decision_source="fallback")

    assert channel.enabled is False
    assert document.status == "pending"
    assert document.rag_doc_id == ""
    assert event.reply_text == ""


def test_chat_event_timestamps_round_trip_as_naive_utc() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(ChatEvent(provider="line", incoming_text="hello", decision_source="fallback"))
        session.commit()
        event = session.exec(select(ChatEvent)).one()

    assert event.created_at.tzinfo is None
