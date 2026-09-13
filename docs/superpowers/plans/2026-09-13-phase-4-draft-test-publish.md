# Phase 4 Draft Test Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bot configuration safe to change by adding Draft editing, a guided Setup Wizard, interactive Test Center, saved regression tests, publish gates, immutable publication, and restore-as-Draft rollback.

**Architecture:** Use the Phase 1 version model and Phase 3 BotRuntime as the single execution path. Admin edits always target `bot.draft_config_version_id`; production resolves `bot.live_config_version_id`. Test execution explicitly supplies a Draft config and `test_mode=True`, which suppresses real provider delivery and real handoff assignment. Publication validates readiness/regression and performs one atomic live-pointer switch.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, Jinja2, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Saving Draft must never change production behavior.
- Published versions are immutable.
- Test Center and production use the same `BotRuntime`; do not create a second decision engine.
- Test mode may persist `BotTestRun`/`BotTestResult` but must not send provider replies, assign real human conversations, or modify production conversations.
- `FAIL` blocks publish. `WARNING` requires explicit acknowledgement. `PASS` publishes normally.
- Do not use exact generated text equality as the primary regression assertion.
- Restore creates a new Draft cloned from an old published version; it never switches live immediately.
- `Pause Bot` remains an operational lifecycle action distinct from version rollback.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `BotTestCase`, `BotTestRun`, `BotTestResult`.
- Create `apps/api/chatbot_manager/bots/versions.py`: Draft clone/update/publish/restore helpers.
- Modify `apps/api/chatbot_manager/bots/service.py` to delegate version lifecycle.
- Create `apps/api/chatbot_manager/testing/__init__.py`.
- Create `apps/api/chatbot_manager/testing/regression.py`: assertion/evaluation/run service.
- Create `apps/api/chatbot_manager/testing/readiness.py`: readiness checks.
- Modify `apps/api/chatbot_manager/runtime/engine.py`: explicit config override/test-mode side-effect contract if not already complete.
- Expand `apps/api/chatbot_manager/admin/bots.py`: Behavior, Rules, Knowledge, Channels, Versions, Setup Wizard lifecycle routes.
- Create `apps/api/chatbot_manager/admin/test_center.py` and include router.
- Create/update templates: `bot_overview.html`, `bot_behavior.html`, `bot_rules.html`, `bot_test.html`, `bot_versions.html`, `bot_setup.html`, workspace partial/header.
- Modify `apps/api/chatbot_manager/templates/base.html` and CSS for final Bot Workspace navigation.
- Create `tests/test_version_service.py`.
- Create `tests/test_readiness.py`.
- Create `tests/test_regression_service.py`.
- Create `tests/test_publish_flow.py`.
- Create `tests/test_test_center_admin.py`.
- Modify `tests/test_bot_admin.py`.

---

### Task 1: Finalize Version Lifecycle Service

**Files:**
- Create: `apps/api/chatbot_manager/bots/versions.py`
- Modify: `apps/api/chatbot_manager/bots/service.py`
- Create: `tests/test_version_service.py`

**Interfaces:**
- Consumes: `Bot`, `BotConfigVersion`, `BotConfigRule`.
- Produces:

```python
clone_live_to_draft(session, bot_id: int, actor: str) -> BotConfigVersion
get_draft_config(session, bot_id: int) -> BotConfigVersion | None
update_draft_config(session, bot_id: int, changes: dict[str, object]) -> BotConfigVersion
replace_draft_rules(session, bot_id: int, rules: list[RuleInput]) -> list[BotConfigRule]
restore_version_as_draft(session, bot_id: int, source_version_id: int, actor: str) -> BotConfigVersion
publish_draft(session, bot_id: int, actor: str) -> BotConfigVersion
```

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_editing_live_creates_single_new_draft(client) -> None:
    with Session(get_engine()) as session:
        bot = default_bot(session)
        live = get_live_config(session, bot.id)
        draft1 = clone_live_to_draft(session, bot.id, "admin@example.local")
        draft2 = clone_live_to_draft(session, bot.id, "admin@example.local")
        assert draft1.id == draft2.id
        assert draft1.id != live.id
        assert draft1.version_number == live.version_number + 1


def test_restore_old_version_creates_new_draft_not_live_switch(client) -> None:
    with Session(get_engine()) as session:
        bot = seeded_bot_with_versions(session, versions=3)
        current_live = bot.live_config_version_id
        restored = restore_version_as_draft(session, bot.id, source_version_id=1, actor="admin@example.local")
        session.refresh(bot)
        assert bot.live_config_version_id == current_live
        assert bot.draft_config_version_id == restored.id
        assert restored.status == "draft"
        assert restored.version_number == 4
```

Also test that attempts to update a published version through the service raise `VersionStateError("published_version_immutable")`.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_version_service.py
```

- [ ] **Step 3: Implement clone/update/restore**

Create a `RuleInput` dataclass matching BotConfigRule editable fields. Centralize copy logic in one private `_clone_version()` so live-to-draft and restore produce identical deep rule copies.

`update_draft_config()` only allows these fields:

```python
EDITABLE_CONFIG_FIELDS = {
    "system_prompt", "tone", "language", "response_style",
    "fallback_reply", "fallback_policy", "escalation_policy",
    "custom_instructions", "knowledge_service_id",
}
```

Reject every other key with `VersionStateError("config_field_not_editable")`.

- [ ] **Step 4: Implement publish pointer switch without readiness yet**

`publish_draft()` validates only lifecycle state in this task, marks the draft `published`, sets `published_at`, clears `draft_config_version_id`, and atomically updates `live_config_version_id` in one transaction. Task 4 wraps it with readiness/regression gate.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_version_service.py tests/test_bot_foundation.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/bots tests/test_version_service.py
git commit -m "feat: add bot configuration version lifecycle"
```

---

### Task 2: Add Saved Regression Test Persistence and Deterministic Evaluator

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `apps/api/chatbot_manager/testing/__init__.py`
- Create: `apps/api/chatbot_manager/testing/regression.py`
- Create: `tests/test_regression_service.py`

**Interfaces:**
- Produces `BotTestCase`, `BotTestRun`, `BotTestResult`, and:

```python
@dataclass(frozen=True)
class ExpectedBehavior:
    decision_type: str | None = None
    must_call_rag: bool | None = None
    must_have_references: bool | None = None
    must_escalate: bool | None = None
    must_fallback: bool | None = None
    required_terms: tuple[str, ...] = ()
    latency_warning_ms: int | None = None

class RegressionService:
    async def run_suite(self, bot_id: int, config_version_id: int, actor: str) -> BotTestRun:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing evaluator tests**

```python
def test_behavior_check_passes_without_exact_text_match() -> None:
    expected = ExpectedBehavior(
        decision_type="rag",
        must_have_references=True,
        must_escalate=False,
        required_terms=("documents",),
    )
    result = evaluate_result(expected, runtime_result(
        decision_type="rag",
        reply_text="Required documents are listed here.",
        reference_count=2,
        escalate=False,
    ))
    assert result.outcome == "pass"


def test_latency_threshold_is_warning_not_fail() -> None:
    expected = ExpectedBehavior(decision_type="rag", latency_warning_ms=1000)
    result = evaluate_result(expected, runtime_result(decision_type="rag", total_latency_ms=1500))
    assert result.outcome == "warning"


def test_wrong_decision_type_is_fail() -> None:
    expected = ExpectedBehavior(decision_type="escalation")
    result = evaluate_result(expected, runtime_result(decision_type="rag"))
    assert result.outcome == "fail"
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_regression_service.py
```

- [ ] **Step 3: Add persistence models**

Use JSON strings for `expected_behavior_json`, `tags_json`, and `evaluation_details_json`. Persist actual response/decision/outcome/latency on `BotTestResult` so history remains reviewable even if the test case changes later.

- [ ] **Step 4: Implement evaluator + suite runner**

`RegressionService.run_suite()` loads enabled Bot test cases, runs each through the real `BotRuntime` with:

```python
RuntimeRequest(
    bot_id=bot_id,
    conversation_id=test_conversation_id,
    message_id=test_message_id,
    text=test_case.input_message,
    provider="test",
    external_user_id="regression",
    config_version_id=config_version_id,
    test_mode=True,
)
```

The test context uses an isolated synthetic conversation and no provider delivery. Persist one `BotTestResult` per case, then set run status from aggregate outcomes.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_regression_service.py tests/test_bot_runtime.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/testing tests/test_regression_service.py
git commit -m "feat: add chatbot regression test service"
```

---

### Task 3: Add Readiness Checks

**Files:**
- Create: `apps/api/chatbot_manager/testing/readiness.py`
- Create: `tests/test_readiness.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    status: str       # pass | warning | fail
    message: str

@dataclass(frozen=True)
class ReadinessResult:
    checks: tuple[ReadinessCheck, ...]
    @property
    def can_publish(self) -> bool:
        return all(check.status != "fail" for check in self.checks)
```

- [ ] **Step 1: Write failing readiness tests**

Required assertions with explicit fixtures:

- `test_readiness_fails_without_knowledge_service_for_rag_bot`: seed a Draft whose fallback/rules require the RAG path and no `knowledge_service_id`; assert the `knowledge_service` check is `fail` and `can_publish` is false.
- `test_readiness_fails_when_selected_knowledge_service_unhealthy`: bind a Draft to an enabled service with `health_status="unavailable"`; assert the knowledge check is `fail`.
- `test_readiness_warns_when_bot_has_no_enabled_channel`: seed a complete Draft with healthy Knowledge Service but zero enabled `ChannelConnection` rows; assert the channel check is `warning` and there is no unrelated `fail`.
- `test_readiness_passes_complete_active_configuration`: seed profile, complete Draft, healthy Knowledge Service, and enabled configured channel; assert every required check is `pass` except explicitly optional warning checks and `can_publish` is true.

Implement the fixtures using `Bot`, Draft config, `KnowledgeService`, and `ChannelConnection` rows rather than mocking the readiness result.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_readiness.py
```

- [ ] **Step 3: Implement deterministic checks**

Initial checks:

```text
bot_profile          name present
behavior             system_prompt and fallback policy/reply coherent
knowledge_service    selected + enabled + health_status == healthy when RAG path is expected
channel_readiness    warning if none enabled; fail for an enabled but incomplete connection
regression_presence  warning when zero enabled saved test cases
```

Readiness does not call external services itself; health status comes from Phase 2/5 checks. Publish Task 4 will run an explicit fresh Knowledge test before pointer switch.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_readiness.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/testing/readiness.py tests/test_readiness.py
git commit -m "feat: add bot readiness checks"
```

---

### Task 4: Implement Publish Gate and Atomic Publication

**Files:**
- Modify: `apps/api/chatbot_manager/bots/versions.py`
- Create: `tests/test_publish_flow.py`

**Interfaces:**
- Consumes: ReadinessService, RegressionService, KnowledgeServiceClient.
- Produces `PublishService.publish(bot_id, actor, acknowledge_warnings=False) -> BotConfigVersion`.

- [ ] **Step 1: Write failing publish tests**

```python
@pytest.mark.asyncio
async def test_regression_fail_blocks_publish(client) -> None:
    # seed Draft + latest run containing fail, then assert PublishBlocked("regression_failed")

@pytest.mark.asyncio
async def test_warning_requires_acknowledgement(client) -> None:
    # warning-only gate rejects acknowledge_warnings=False and publishes when True.

@pytest.mark.asyncio
async def test_publish_switches_live_pointer_and_freezes_version(client) -> None:
    # pass readiness/regression/fresh knowledge check; publish Draft; assert old live retained, new live published, draft pointer cleared.
```

Write concrete rows/fakes for each case; never mock `publish_draft()` itself.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_publish_flow.py
```

- [ ] **Step 3: Implement `PublishService`**

Order is fixed:

```text
load Bot + Draft
run readiness checks
run fresh knowledge `test_connection()` when Draft has Knowledge Service
run regression suite against Draft
block on any FAIL
require `acknowledge_warnings=True` if any WARNING
call atomic `publish_draft()`
```

Do not reuse stale regression result as the gate; publish runs the suite again so the release record reflects the actual Draft being published.

- [ ] **Step 4: Verify concurrent/runtime snapshot property**

Add a test that captures Live v1 in a `RuntimeRequest`, publishes v2, then runs the request explicitly against captured v1 and proves it still uses v1 while a new default production request resolves v2.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_publish_flow.py tests/test_version_service.py tests/test_bot_runtime.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/bots/versions.py tests/test_publish_flow.py
git commit -m "feat: enforce bot publish gate"
```

---

### Task 5: Add Behavior/Rules Draft Editing and Bot Setup Wizard

**Files:**
- Modify: `apps/api/chatbot_manager/admin/bots.py`
- Create: `apps/api/chatbot_manager/templates/_bot_workspace_header.html`
- Create: `apps/api/chatbot_manager/templates/bot_behavior.html`
- Create: `apps/api/chatbot_manager/templates/bot_rules.html`
- Create: `apps/api/chatbot_manager/templates/bot_setup.html`
- Modify: `apps/api/chatbot_manager/templates/bot_overview.html`
- Modify: `tests/test_bot_admin.py`

**Interfaces:**
- Consumes: version lifecycle + Knowledge registry + channels.
- Produces Bot creation and Draft-only configuration workflow.

- [ ] **Step 1: Write failing UI/route tests**

Required tests and DB assertions:

- `test_create_bot_starts_draft_setup`: POST `/bots`; assert the new Bot is `draft`, has `draft_config_version_id`, and has no Live version.
- `test_behavior_save_changes_draft_not_live`: seed an Active Bot with Live version, POST Behavior changes, then assert Live prompt is unchanged and Draft contains the new value.
- `test_rule_create_is_scoped_to_draft_version`: POST a rule from the workspace and assert the created `BotConfigRule.config_version_id` equals the Bot's Draft version, not Live.
- `test_setup_wizard_shows_profile_knowledge_behavior_channels_test_steps`: GET the setup page and assert all five approved step labels/links render in order.
- `test_pause_active_bot_does_not_modify_config_version`: POST pause and assert lifecycle becomes `paused` while both Live/Draft version pointers remain unchanged.

Each test queries the database after the POST; do not treat rendered UI alone as proof of scoping.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_admin.py
```

- [ ] **Step 3: Implement Bot creation and workspace tabs**

`POST /bots` creates:

```text
Bot lifecycle_status=draft
BotConfigVersion v1 status=draft
bot.draft_config_version_id=v1
no live version yet
```

Behavior/rule POST routes call version service and never update a published row. The setup page links the five approved steps and uses readiness results to show completion.

- [ ] **Step 4: Implement lifecycle actions**

Initial legal lifecycle actions:

```text
draft -> ready      only after configuration readiness has no FAIL
ready -> active     after first publish
active -> paused    immediate operational action
paused -> active    if a live version exists and critical readiness checks pass
```

Pause must not clone/change configuration.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_version_service.py tests/test_readiness.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin/bots.py apps/api/chatbot_manager/templates tests/test_bot_admin.py
git commit -m "feat: add bot draft editing and setup wizard"
```

---

### Task 6: Add Interactive Test Center, Saved Cases, Live-vs-Draft Compare, and Versions UI

**Files:**
- Create: `apps/api/chatbot_manager/admin/test_center.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/bot_test.html`
- Create: `apps/api/chatbot_manager/templates/bot_versions.html`
- Modify: `apps/api/chatbot_manager/templates/conversation_detail.html`
- Create: `tests/test_test_center_admin.py`

**Interfaces:**
- Consumes: BotRuntime, RegressionService, PublishService, version restore.
- Produces interactive test, saved cases, compare, publish, restore, and `Add to Test Suite` workflow.

- [ ] **Step 1: Write failing admin tests**

Required tests and observable assertions:

- `test_interactive_test_uses_draft_and_no_provider_delivery`: give Live and Draft distinguishable prompts, monkeypatch provider delivery to fail if called, run Interactive Test, and assert Decision Trace names the Draft config with zero delivery calls.
- `test_saved_case_can_be_created_and_run`: POST a case with deterministic expected behavior, run the suite, and assert one persisted `BotTestRun`/`BotTestResult` with PASS/WARNING/FAIL outcome.
- `test_compare_runs_same_case_against_live_and_draft`: capture fake Knowledge queries and assert the same input is executed once with Live config ID and once with Draft config ID; render both responses/traces.
- `test_publish_fail_is_rendered_as_blocked`: configure one failing regression case, POST publish, assert no Live pointer switch and the page reports the failing case.
- `test_restore_version_creates_new_draft`: restore an older published version and assert a new higher-numbered Draft is created while current Live stays unchanged.
- `test_conversation_message_can_seed_test_case`: POST `Add to Test Suite` for a user message and assert a disabled Draft test case is created with that exact input and no expected behavior silently invented.

Use fake Knowledge clients and database assertions to prove no production conversation, provider-delivery, or human-assignment side effect.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_test_center_admin.py
```

- [ ] **Step 3: Implement Test Center routes**

Routes under `/bots/{bot_id}/test`:

```text
GET  page
POST /interactive
POST /cases
POST /cases/{case_id}/toggle
POST /run
POST /compare
POST /publish
```

Version routes:

```text
GET  /bots/{bot_id}/versions
POST /bots/{bot_id}/versions/{version_id}/restore
```

Conversation-to-test route:

```text
POST /conversations/{conversation_id}/messages/{message_id}/add-test-case
```

Only user messages can seed a test case. Copy the user input; Admin supplies/edits expected behavior before enabling the case.

- [ ] **Step 4: Implement UI**

Test page shows Draft version, interactive response, Decision Trace, saved cases with PASS/WARNING/FAIL, Run All, Compare Live/Draft, readiness summary, and Publish button state. Versions page shows Draft/Live/archived published history and `Restore as Draft`.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_test_center_admin.py tests/test_publish_flow.py tests/test_regression_service.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/admin/test_center.py apps/api/chatbot_manager/admin/__init__.py apps/api/chatbot_manager/templates tests/test_test_center_admin.py
git commit -m "feat: add bot test and publish center"
```

---

### Task 7: Move Primary Admin Configuration Navigation into Bot Workspaces

**Files:**
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `README.md`
- Modify: legacy route tests as required to assert direct compatibility rather than primary navigation.

**Interfaces:**
- Produces approved information architecture: global navigation no longer treats Assistant/Rules/Channels/Test Chat/Logs as peer configuration pages.

- [ ] **Step 1: Write failing navigation test**

```python
def test_primary_navigation_is_bot_centric_after_phase_four(client):
    login(client)
    html = client.get("/").text
    for href in ("/bots", "/conversations", "/knowledge-services"):
        assert f'href="{href}"' in html
    for legacy_href in ("/assistant", "/rules", "/channels", "/test-chat", "/logs"):
        assert f'href="{legacy_href}"' not in html
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_admin_routes.py
```

- [ ] **Step 3: Update navigation/docs**

Keep legacy routes reachable for migration/debugging but remove them from primary nav. Update README admin pages to Bot Workspace, Conversations, Knowledge Services, Test/Versions. Do not delete legacy routes yet.

- [ ] **Step 4: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_bot_admin.py tests/test_admin_routes.py tests/test_test_center_admin.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/chatbot_manager/templates/base.html README.md tests
git commit -m "refactor: make bot workspace primary admin surface"
```

---

### Task 8: Phase 4 Verification and Manual Publish Checkpoint

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Focused verification**

```bash
rtk uv run pytest -q \
  tests/test_version_service.py \
  tests/test_readiness.py \
  tests/test_regression_service.py \
  tests/test_publish_flow.py \
  tests/test_bot_admin.py \
  tests/test_test_center_admin.py \
  tests/test_bot_runtime.py
```

- [ ] **Step 2: Full verification**

```bash
rtk uv run pytest -q
rtk python -m compileall -q apps/api
rtk uv lock --check
git diff --check
```

- [ ] **Step 3: Manual checkpoint**

```text
Create a new Draft Bot through Setup Wizard
select Knowledge Service
edit behavior/rules
configure or attach a channel
run interactive Test Chat
create saved regression cases
run suite and observe PASS/WARNING/FAIL
prove FAIL blocks publish
fix/disable obsolete failing case with explicit action
publish successful Draft
send real production message and confirm new Live version is used
restore an older version -> new Draft only
publish restored Draft after tests
```

- [ ] **Step 4: Update handoff and commit**

Record active Bot/version IDs used for the checkpoint, regression/publish results, full suite result, and next plan:

`docs/superpowers/plans/2026-09-13-phase-5-operations-monitoring.md`

```bash
git add HANDOFF.md
git commit -m "docs: hand off draft test publish phase"
```
