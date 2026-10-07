import json

import pytest
from sqlmodel import Session, select

from chatbot_manager.bots.versions import clone_live_to_draft
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    BotConfigRule,
    BotDecision,
    BotTestCase,
    BotTestResult,
    BotTestRun,
    Conversation,
    ConversationMessage,
)
from chatbot_manager.runtime.engine import RuntimeResult
from chatbot_manager.testing.regression import (
    ExpectedBehavior,
    RegressionService,
    evaluate_result,
)


def runtime_result(**overrides) -> RuntimeResult:
    values = {
        "decision_type": "rag",
        "reply_text": "Grounded reply",
        "escalate": False,
        "error_code": "",
        "reference_count": 1,
        "references": [{"file_name": "manual.pdf"}],
        "config_version_id": 2,
        "knowledge_service_id": 1,
        "total_latency_ms": 100,
    }
    values.update(overrides)
    return RuntimeResult(**values)


def test_behavior_check_passes_without_exact_text_match() -> None:
    expected = ExpectedBehavior(
        decision_type="rag",
        must_have_references=True,
        must_escalate=False,
        required_terms=("documents",),
    )
    result = evaluate_result(
        expected,
        runtime_result(
            decision_type="rag",
            reply_text="Required documents are listed here.",
            reference_count=2,
            escalate=False,
        ),
    )
    assert result.outcome == "pass"


def test_latency_threshold_is_warning_not_fail() -> None:
    expected = ExpectedBehavior(decision_type="rag", latency_warning_ms=1000)
    result = evaluate_result(
        expected,
        runtime_result(decision_type="rag", total_latency_ms=1500),
    )
    assert result.outcome == "warning"


def test_wrong_decision_type_is_fail() -> None:
    expected = ExpectedBehavior(decision_type="escalation")
    result = evaluate_result(expected, runtime_result(decision_type="rag"))
    assert result.outcome == "fail"


def test_behavior_checks_rag_escalation_fallback_and_required_terms() -> None:
    expected = ExpectedBehavior(
        must_call_rag=False,
        must_have_references=False,
        must_escalate=True,
        must_fallback=False,
        required_terms=("staff", "continue"),
    )
    result = evaluate_result(
        expected,
        runtime_result(
            decision_type="escalation",
            reply_text="CIFS staff will continue this conversation.",
            escalate=True,
            reference_count=0,
        ),
    )
    assert result.outcome == "pass"


@pytest.mark.asyncio
async def test_regression_suite_persists_result_snapshots_against_requested_config(client) -> None:
    with Session(get_engine()) as session:
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        assert bot.id is not None
        draft = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft.id is not None

        session.add(
            BotConfigRule(
                config_version_id=draft.id,
                name="documents",
                enabled=True,
                priority=1,
                match_type="contains",
                pattern="documents",
                action="RESPOND",
                reply_text="Required documents are listed here.",
            )
        )
        case = BotTestCase(
            bot_id=bot.id,
            name="documents rule",
            input_message="Which documents do I need?",
            expected_behavior_json=json.dumps(
                {
                    "decision_type": "rule",
                    "must_call_rag": False,
                    "must_have_references": False,
                    "must_escalate": False,
                    "must_fallback": False,
                    "required_terms": ["documents"],
                }
            ),
            tags_json='["core"]',
            enabled=True,
            created_by="admin@example.local",
        )
        session.add(case)
        session.commit()
        session.refresh(case)

        run = await RegressionService(session).run_suite(
            bot.id,
            draft.id,
            "admin@example.local",
        )

        assert run.status == "pass"
        results = session.exec(
            select(BotTestResult).where(BotTestResult.test_run_id == run.id)
        ).all()
        assert len(results) == 1
        result = results[0]
        assert result.test_case_id == case.id
        assert result.config_version_id == draft.id
        assert result.input_message == "Which documents do I need?"
        assert result.actual_response == "Required documents are listed here."
        assert result.decision_type == "rule"
        assert result.outcome == "pass"
        assert json.loads(result.expected_behavior_json)["decision_type"] == "rule"
        assert json.loads(result.tags_json) == ["core"]

        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []
        decisions = session.exec(
            select(BotDecision).where(BotDecision.config_version_id == draft.id)
        ).all()
        assert decisions == []

        stored_run = session.get(BotTestRun, run.id)
        assert stored_run is not None
        assert stored_run.config_version_id == draft.id
        assert stored_run.actor == "admin@example.local"
        assert stored_run.completed_at is not None
