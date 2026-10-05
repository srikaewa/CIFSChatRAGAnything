import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from chatbot_manager.admin.dependencies import require_admin, require_csrf, templates
from chatbot_manager.bots.versions import (
    PublishBlocked,
    PublishService,
    get_draft_config,
    restore_version_as_draft,
)
from chatbot_manager.db import get_session
from chatbot_manager.models import (
    Bot,
    BotConfigVersion,
    BotTestCase,
    BotTestResult,
    BotTestRun,
    utc_now,
)
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest, RuntimeResult
from chatbot_manager.testing.regression import ExpectedBehavior, RegressionService

from .bots import _get_bot, _workspace_context


router = APIRouter(prefix="/bots")


def _test_context(
    session: Session,
    bot: Bot,
    *,
    run_id: int | None = None,
) -> dict[str, object]:
    context = _workspace_context(session, bot, active_workspace_tab="test")
    cases = session.exec(
        select(BotTestCase)
        .where(BotTestCase.bot_id == bot.id)
        .order_by(BotTestCase.id)
    ).all()
    run = session.get(BotTestRun, run_id) if run_id is not None else None
    results = (
        session.exec(
            select(BotTestResult)
            .where(BotTestResult.test_run_id == run.id)
            .order_by(BotTestResult.id)
        ).all()
        if run is not None and run.id is not None
        else []
    )
    context.update({"cases": cases, "test_run": run, "test_results": results})
    return context


def _render_test_page(
    request: Request,
    session: Session,
    bot: Bot,
    admin_email: str,
    *,
    run_id: int | None = None,
    interactive_result: RuntimeResult | None = None,
    interactive_input: str = "",
    compare_results: tuple[RuntimeResult, RuntimeResult] | None = None,
    publish_error: str = "",
) -> Response:
    context = _test_context(session, bot, run_id=run_id)
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "interactive_result": interactive_result,
            "interactive_input": interactive_input,
            "compare_results": compare_results,
            "publish_error": publish_error,
        }
    )
    return templates.TemplateResponse(request, "bot_test.html", context)


@router.get("/{bot_id}/test", response_class=HTMLResponse)
def bot_test_page(
    bot_id: int,
    request: Request,
    run_id: int | None = None,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    return _render_test_page(
        request,
        session,
        _get_bot(session, bot_id),
        admin_email,
        run_id=run_id,
    )


@router.post("/{bot_id}/test/interactive", response_class=HTMLResponse)
async def interactive_test(
    bot_id: int,
    request: Request,
    input_message: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    draft = get_draft_config(session, bot_id)
    if draft is None or draft.id is None:
        raise HTTPException(status_code=409, detail="draft_version_missing")
    result = await BotRuntime(session).run(
        RuntimeRequest(
            bot_id=bot_id,
            conversation_id=0,
            message_id=0,
            text=input_message,
            provider="test",
            external_user_id="interactive",
            config_version_id=draft.id,
            test_mode=True,
        )
    )
    return _render_test_page(
        request,
        session,
        bot,
        admin_email,
        interactive_result=result,
        interactive_input=input_message,
    )


@router.post("/{bot_id}/test/cases")
def create_test_case(
    bot_id: int,
    name: str = Form(""),
    input_message: str = Form(...),
    expected_behavior_json: str = Form("{}"),
    tags_json: str = Form("[]"),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    try:
        ExpectedBehavior.from_json(expected_behavior_json)
        tags = json.loads(tags_json or "[]")
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid_test_case") from None
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise HTTPException(status_code=400, detail="invalid_test_case")
    text = input_message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input_message_required")
    session.add(
        BotTestCase(
            bot_id=bot.id,
            name=name.strip(),
            input_message=text,
            expected_behavior_json=expected_behavior_json or "{}",
            tags_json=json.dumps(tags),
            enabled=True,
            created_by=admin_email,
        )
    )
    session.commit()
    return RedirectResponse(f"/bots/{bot_id}/test?case_created=1", status_code=303)


@router.post("/{bot_id}/test/cases/{case_id}/toggle")
def toggle_test_case(
    bot_id: int,
    case_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _get_bot(session, bot_id)
    case = session.get(BotTestCase, case_id)
    if case is None or case.bot_id != bot_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    case.enabled = not case.enabled
    case.updated_at = utc_now()
    session.add(case)
    session.commit()
    return RedirectResponse(f"/bots/{bot_id}/test", status_code=303)


@router.post("/{bot_id}/test/run")
async def run_test_suite(
    bot_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _get_bot(session, bot_id)
    draft = get_draft_config(session, bot_id)
    if draft is None or draft.id is None:
        raise HTTPException(status_code=409, detail="draft_version_missing")
    run = await RegressionService(session).run_suite(bot_id, draft.id, admin_email)
    return RedirectResponse(f"/bots/{bot_id}/test?run_id={run.id}", status_code=303)


@router.post("/{bot_id}/test/compare", response_class=HTMLResponse)
async def compare_live_draft(
    bot_id: int,
    request: Request,
    input_message: str = Form(...),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    live_id = bot.live_config_version_id
    draft = get_draft_config(session, bot_id)
    if live_id is None or draft is None or draft.id is None:
        raise HTTPException(status_code=409, detail="live_and_draft_required")

    runtime = BotRuntime(session)
    common = {
        "bot_id": bot_id,
        "conversation_id": 0,
        "message_id": 0,
        "text": input_message,
        "provider": "test",
        "external_user_id": "compare",
        "test_mode": True,
    }
    live_result = await runtime.run(RuntimeRequest(**common, config_version_id=live_id))
    draft_result = await runtime.run(RuntimeRequest(**common, config_version_id=draft.id))
    return _render_test_page(
        request,
        session,
        bot,
        admin_email,
        compare_results=(live_result, draft_result),
        interactive_input=input_message,
    )


@router.post("/{bot_id}/test/publish", response_class=HTMLResponse)
async def publish_from_test_center(
    bot_id: int,
    request: Request,
    acknowledge_warnings: str | None = Form(None),
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    try:
        await PublishService(session).publish(
            bot_id,
            admin_email,
            acknowledge_warnings=acknowledge_warnings == "on",
        )
    except PublishBlocked as exc:
        return _render_test_page(
            request,
            session,
            bot,
            admin_email,
            publish_error=str(exc),
        )

    session.refresh(bot)
    if bot.lifecycle_status != "paused":
        bot.lifecycle_status = "active"
    bot.updated_at = utc_now()
    session.add(bot)
    session.commit()
    return RedirectResponse(f"/bots/{bot_id}/test?published=1", status_code=303)


@router.get("/{bot_id}/versions", response_class=HTMLResponse)
def bot_versions_page(
    bot_id: int,
    request: Request,
    admin_email: str = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    bot = _get_bot(session, bot_id)
    context = _workspace_context(session, bot, active_workspace_tab="versions")
    versions = session.exec(
        select(BotConfigVersion)
        .where(BotConfigVersion.bot_id == bot_id)
        .order_by(BotConfigVersion.version_number.desc())
    ).all()
    context.update(
        {
            "admin_email": admin_email,
            "active_page": "bots",
            "versions": versions,
        }
    )
    return templates.TemplateResponse(request, "bot_versions.html", context)


@router.post("/{bot_id}/versions/{version_id}/restore")
def restore_version(
    bot_id: int,
    version_id: int,
    admin_email: str = Depends(require_csrf),
    session: Session = Depends(get_session),
) -> Response:
    _get_bot(session, bot_id)
    restore_version_as_draft(session, bot_id, version_id, admin_email)
    return RedirectResponse(f"/bots/{bot_id}/versions?restored=1", status_code=303)
