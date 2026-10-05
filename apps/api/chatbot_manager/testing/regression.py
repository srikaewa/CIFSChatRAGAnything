from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session, select

from chatbot_manager.models import (
    BotConfigVersion,
    BotTestCase,
    BotTestResult,
    BotTestRun,
    utc_now,
)
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest, RuntimeResult


@dataclass(frozen=True)
class ExpectedBehavior:
    decision_type: str | None = None
    must_call_rag: bool | None = None
    must_have_references: bool | None = None
    must_escalate: bool | None = None
    must_fallback: bool | None = None
    required_terms: tuple[str, ...] = ()
    latency_warning_ms: int | None = None

    @classmethod
    def from_json(cls, value: str) -> "ExpectedBehavior":
        try:
            raw = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_expected_behavior") from exc
        if not isinstance(raw, dict):
            raise ValueError("invalid_expected_behavior")
        required_terms = raw.get("required_terms", [])
        if not isinstance(required_terms, list) or not all(
            isinstance(term, str) for term in required_terms
        ):
            raise ValueError("invalid_expected_behavior")
        return cls(
            decision_type=_optional_str(raw.get("decision_type")),
            must_call_rag=_optional_bool(raw.get("must_call_rag")),
            must_have_references=_optional_bool(raw.get("must_have_references")),
            must_escalate=_optional_bool(raw.get("must_escalate")),
            must_fallback=_optional_bool(raw.get("must_fallback")),
            required_terms=tuple(required_terms),
            latency_warning_ms=_optional_int(raw.get("latency_warning_ms")),
        )


@dataclass(frozen=True)
class EvaluationResult:
    outcome: str
    details: tuple[str, ...]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_expected_behavior")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("invalid_expected_behavior")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid_expected_behavior")
    return value


def evaluate_result(
    expected: ExpectedBehavior,
    actual: RuntimeResult,
) -> EvaluationResult:
    failures: list[str] = []
    warnings: list[str] = []

    if expected.decision_type is not None and actual.decision_type != expected.decision_type:
        failures.append(
            f"decision_type expected {expected.decision_type} got {actual.decision_type}"
        )

    called_rag = actual.decision_type == "rag"
    if expected.must_call_rag is not None and called_rag != expected.must_call_rag:
        failures.append(f"must_call_rag expected {expected.must_call_rag}")

    has_references = actual.reference_count > 0
    if (
        expected.must_have_references is not None
        and has_references != expected.must_have_references
    ):
        failures.append(
            f"must_have_references expected {expected.must_have_references}"
        )

    if expected.must_escalate is not None and actual.escalate != expected.must_escalate:
        failures.append(f"must_escalate expected {expected.must_escalate}")

    is_fallback = actual.decision_type == "fallback"
    if expected.must_fallback is not None and is_fallback != expected.must_fallback:
        failures.append(f"must_fallback expected {expected.must_fallback}")

    reply = actual.reply_text.casefold()
    for term in expected.required_terms:
        if term.casefold() not in reply:
            failures.append(f"required_term missing: {term}")

    if (
        expected.latency_warning_ms is not None
        and actual.total_latency_ms > expected.latency_warning_ms
    ):
        warnings.append(
            f"latency {actual.total_latency_ms}ms exceeds {expected.latency_warning_ms}ms"
        )

    if failures:
        return EvaluationResult("fail", tuple(failures + warnings))
    if warnings:
        return EvaluationResult("warning", tuple(warnings))
    return EvaluationResult("pass", ())


RuntimeFactory = Callable[[Session], BotRuntime]


class RegressionService:
    def __init__(
        self,
        session: Session,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self._session = session
        self._runtime_factory = runtime_factory or BotRuntime

    async def run_suite(
        self,
        bot_id: int,
        config_version_id: int,
        actor: str,
    ) -> BotTestRun:
        config = self._session.get(BotConfigVersion, config_version_id)
        if config is None or config.bot_id != bot_id:
            raise LookupError("Bot configuration not available")

        run = BotTestRun(
            bot_id=bot_id,
            config_version_id=config_version_id,
            status="running",
            actor=actor,
        )
        self._session.add(run)
        self._session.commit()
        self._session.refresh(run)
        if run.id is None:
            raise LookupError("Regression run could not be persisted")

        cases = self._session.exec(
            select(BotTestCase)
            .where(BotTestCase.bot_id == bot_id)
            .where(BotTestCase.enabled == True)  # noqa: E712
            .order_by(BotTestCase.id)
        ).all()

        outcomes: list[str] = []
        runtime = self._runtime_factory(self._session)
        for case in cases:
            if case.id is None:
                continue
            expected = ExpectedBehavior.from_json(case.expected_behavior_json)
            actual = await runtime.run(
                RuntimeRequest(
                    bot_id=bot_id,
                    conversation_id=0,
                    message_id=0,
                    text=case.input_message,
                    provider="test",
                    external_user_id="regression",
                    config_version_id=config_version_id,
                    test_mode=True,
                )
            )
            evaluation = evaluate_result(expected, actual)
            outcomes.append(evaluation.outcome)
            self._session.add(
                BotTestResult(
                    test_run_id=run.id,
                    test_case_id=case.id,
                    config_version_id=actual.config_version_id,
                    input_message=case.input_message,
                    expected_behavior_json=case.expected_behavior_json,
                    tags_json=case.tags_json,
                    actual_response=actual.reply_text,
                    decision_type=actual.decision_type,
                    outcome=evaluation.outcome,
                    total_latency_ms=actual.total_latency_ms,
                    reference_count=actual.reference_count,
                    escalate=actual.escalate,
                    error_code=actual.error_code,
                    evaluation_details_json=json.dumps(list(evaluation.details)),
                )
            )
            self._session.commit()

        if "fail" in outcomes:
            run.status = "fail"
        elif "warning" in outcomes:
            run.status = "warning"
        else:
            run.status = "pass"
        run.completed_at = utc_now()
        self._session.add(run)
        self._session.commit()
        self._session.refresh(run)
        return run
