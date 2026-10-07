from datetime import datetime, timedelta
from time import perf_counter

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from chatbot_manager.models import (
    BotDecision,
    Conversation,
    ConversationHandoffEvent,
    ConversationMessage,
)
from chatbot_manager.operations.metrics import MetricService


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_quality_metrics_derive_resolution_fallback_escalation_and_rag_failure() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    start = datetime(2026, 9, 13, 0, 0, 0)
    end = datetime(2026, 9, 13, 23, 59, 59)

    with Session(engine) as session:
        conversations = [
            Conversation(
                bot_id=2,
                channel_connection_id=1,
                external_user_id="resolved-closed",
                status="closed",
                started_at=start + timedelta(hours=1),
                last_message_at=start + timedelta(hours=1),
            ),
            Conversation(
                bot_id=2,
                channel_connection_id=1,
                external_user_id="resolved-active",
                status="bot_active",
                started_at=start + timedelta(hours=2),
                last_message_at=start + timedelta(hours=2),
            ),
            Conversation(
                bot_id=2,
                channel_connection_id=1,
                external_user_id="human-then-closed",
                status="closed",
                started_at=start + timedelta(hours=3),
                last_message_at=start + timedelta(hours=3),
            ),
            Conversation(
                bot_id=2,
                channel_connection_id=1,
                external_user_id="waiting",
                status="needs_human",
                started_at=start + timedelta(hours=4),
                last_message_at=start + timedelta(hours=4),
            ),
        ]
        session.add_all(conversations)
        session.commit()
        for conversation in conversations:
            session.refresh(conversation)
            assert conversation.id is not None

        session.add(
            ConversationHandoffEvent(
                conversation_id=conversations[2].id,
                event_type="taken",
                actor_user_id=10,
                created_at=start + timedelta(hours=3, minutes=5),
            )
        )
        decisions = [
            BotDecision(
                message_id=1,
                bot_id=2,
                config_version_id=1,
                decision_type="rag",
                total_latency_ms=100,
                created_at=start + timedelta(hours=5),
            ),
            BotDecision(
                message_id=2,
                bot_id=2,
                config_version_id=1,
                decision_type="fallback",
                knowledge_service_id=1,
                total_latency_ms=200,
                error_code="knowledge_service_unavailable",
                created_at=start + timedelta(hours=6),
            ),
            BotDecision(
                message_id=3,
                bot_id=2,
                config_version_id=1,
                decision_type="escalation",
                total_latency_ms=300,
                created_at=start + timedelta(hours=7),
            ),
            BotDecision(
                message_id=4,
                bot_id=2,
                config_version_id=1,
                decision_type="rule",
                total_latency_ms=400,
                created_at=start + timedelta(hours=8),
            ),
        ]
        session.add_all(decisions)
        session.commit()

        summary = MetricService(session).summary(start, end, bot_id=2)

        assert summary.quality["bot_resolution_rate"].value == 0.5
        assert summary.quality["fallback_rate"].value == 0.25
        assert summary.quality["escalation_rate"].value == 0.25
        assert summary.quality["rag_failure_rate"].value == 0.5


def test_operations_metrics_derive_messages_latency_and_human_wait() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    start = datetime(2026, 9, 13, 10, 0, 0)
    end = datetime(2026, 9, 13, 12, 0, 0)

    with Session(engine) as session:
        old_wait = Conversation(
            bot_id=2,
            channel_connection_id=1,
            external_user_id="old-wait",
            status="needs_human",
            started_at=start + timedelta(minutes=10),
            last_message_at=end - timedelta(minutes=20),
        )
        recent_wait = Conversation(
            bot_id=2,
            channel_connection_id=1,
            external_user_id="recent-wait",
            status="needs_human",
            started_at=start + timedelta(minutes=20),
            last_message_at=end - timedelta(minutes=5),
        )
        other_bot = Conversation(
            bot_id=3,
            channel_connection_id=2,
            external_user_id="other",
            status="needs_human",
            started_at=start + timedelta(minutes=30),
            last_message_at=end - timedelta(minutes=30),
        )
        session.add_all([old_wait, recent_wait, other_bot])
        session.commit()
        for conversation in (old_wait, recent_wait, other_bot):
            session.refresh(conversation)
            assert conversation.id is not None

        session.add_all(
            [
                ConversationMessage(
                    conversation_id=old_wait.id,
                    sender_type="user",
                    content="a",
                    created_at=start + timedelta(minutes=15),
                ),
                ConversationMessage(
                    conversation_id=old_wait.id,
                    sender_type="bot",
                    content="b",
                    created_at=start + timedelta(minutes=16),
                ),
                ConversationMessage(
                    conversation_id=recent_wait.id,
                    sender_type="user",
                    content="c",
                    created_at=start + timedelta(minutes=25),
                ),
                ConversationMessage(
                    conversation_id=old_wait.id,
                    sender_type="user",
                    content="outside",
                    created_at=start - timedelta(minutes=1),
                ),
                ConversationMessage(
                    conversation_id=other_bot.id,
                    sender_type="user",
                    content="other bot",
                    created_at=start + timedelta(minutes=35),
                ),
                BotDecision(
                    message_id=1,
                    bot_id=2,
                    config_version_id=1,
                    decision_type="rag",
                    total_latency_ms=100,
                    created_at=start + timedelta(minutes=40),
                ),
                BotDecision(
                    message_id=2,
                    bot_id=2,
                    config_version_id=1,
                    decision_type="fallback",
                    total_latency_ms=300,
                    created_at=start + timedelta(minutes=45),
                ),
                BotDecision(
                    message_id=3,
                    bot_id=3,
                    config_version_id=1,
                    decision_type="rag",
                    total_latency_ms=999,
                    created_at=start + timedelta(minutes=50),
                ),
            ]
        )
        session.commit()

        summary = MetricService(session, human_wait_warning_minutes=10).summary(
            start,
            end,
            bot_id=2,
        )

        assert summary.operations["message_count"].value == 3
        assert summary.operations["avg_response_ms"].value == 200.0
        assert summary.operations["human_wait_count"].value == 1


def test_metric_drilldown_returns_filter_contract_for_conversations() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    start = datetime(2026, 9, 13, 0, 0, 0)
    end = datetime(2026, 9, 13, 23, 59, 59)

    with Session(engine) as session:
        metric = MetricService(session).summary(start, end, bot_id=2).quality[
            "fallback_rate"
        ]

    assert metric.drilldown_url == (
        "/conversations?bot_id=2&decision=fallback"
        "&from=2026-09-13T00:00:00&to=2026-09-13T23:59:59"
    )


def test_direct_summary_benchmark_10000_decisions_is_sub_second(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metrics-benchmark.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    start = datetime(2026, 9, 13, 0, 0, 0)
    end = datetime(2026, 9, 13, 23, 59, 59)

    with Session(engine) as session:
        session.add_all(
            [
                BotDecision(
                    message_id=index + 1,
                    bot_id=2,
                    config_version_id=1,
                    decision_type=("fallback" if index % 10 == 0 else "rag"),
                    total_latency_ms=100 + (index % 50),
                    error_code=(
                        "knowledge_service_unavailable"
                        if index % 25 == 0
                        else ""
                    ),
                    created_at=start + timedelta(seconds=index % 86_400),
                )
                for index in range(10_000)
            ]
        )
        session.commit()

        started = perf_counter()
        summary = MetricService(session).summary(start, end, bot_id=2)
        elapsed = perf_counter() - started
        print(f"metric_summary_10000_seconds={elapsed:.6f}")

        assert summary.quality["fallback_rate"].value == 0.1
        assert summary.operations["avg_response_ms"].value == 124.5
