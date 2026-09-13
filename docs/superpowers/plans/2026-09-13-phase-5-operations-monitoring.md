# Phase 5 Operations Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add action-first operations visibility: structured health checks, deduplicated incidents, Telegram alerts, Command Center, and operational/quality analytics that drill down to real conversations.

**Architecture:** Derive health from runtime/integration/behavioral signals already produced by Phases 2–4. A lightweight scheduler periodically executes health and metric evaluation, writes root-source incidents, and sends Telegram notifications according to severity/persistence policy. Command Center and Analytics read relational data directly or from small hourly aggregates; no external observability warehouse is introduced.

**Tech Stack:** Python 3.11+, FastAPI lifespan/background asyncio task, SQLModel/SQLite, Jinja2, httpx, pytest, pytest-asyncio, respx.

**Spec:** `docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

## Global Constraints

- Command Center is action-first; detailed charting belongs in Analytics.
- Health includes runtime, integrations, and behavioral quality; do not label something `Healthy` without evidence that supports that word.
- Shared dependency failures create one root incident with affected Bots, not one duplicate incident per Bot.
- Severity is limited to `info`, `warning`, `critical` in this release.
- Incident lifecycle is `open -> acknowledged -> resolved`; recovery can resolve automatically.
- Critical incidents notify Telegram immediately; warnings notify externally only after persistence/cooldown policy; info is dashboard/history only by default.
- Telegram notification failure must not break Bot runtime or incident persistence.
- Start with explicit thresholds/rolling baselines; no ML anomaly detection.
- Use relational DB and bounded aggregate tables only where direct indexed queries are insufficient.

## File Structure

- Modify `apps/api/chatbot_manager/models.py`: add `Incident`, optional `MetricAggregate`.
- Modify `apps/api/chatbot_manager/settings.py`: poll interval and default thresholds/Telegram alert env settings.
- Create `apps/api/chatbot_manager/operations/__init__.py`.
- Create `apps/api/chatbot_manager/operations/health.py`.
- Create `apps/api/chatbot_manager/operations/incidents.py`.
- Create `apps/api/chatbot_manager/operations/metrics.py`.
- Create `apps/api/chatbot_manager/operations/alerts.py`.
- Create `apps/api/chatbot_manager/operations/scheduler.py`.
- Modify `apps/api/chatbot_manager/main.py`: start/cancel lightweight scheduler in lifespan.
- Create `apps/api/chatbot_manager/admin/command_center.py`.
- Create `apps/api/chatbot_manager/admin/incidents.py`.
- Create `apps/api/chatbot_manager/admin/analytics.py`.
- Modify `apps/api/chatbot_manager/admin/__init__.py`.
- Create templates `command_center.html`, `incidents.html`, `incident_detail.html`, `analytics.html`.
- Modify `base.html`, Bot overview template, CSS.
- Create tests: `test_health_service.py`, `test_incident_service.py`, `test_metrics_service.py`, `test_alert_service.py`, `test_operations_scheduler.py`, `test_operations_admin.py`.

---

### Task 1: Add Incident Persistence and Deduplicating Incident Service

**Files:**
- Modify: `apps/api/chatbot_manager/models.py`
- Create: `apps/api/chatbot_manager/operations/__init__.py`
- Create: `apps/api/chatbot_manager/operations/incidents.py`
- Create: `tests/test_incident_service.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class IncidentSignal:
    source_type: str
    source_id: str
    incident_type: str
    severity: str
    details: dict[str, object]
    affected_bot_ids: tuple[int, ...] = ()

class IncidentService:
    def observe(self, signal: IncidentSignal) -> Incident:
        raise NotImplementedError

    def resolve(self, source_type: str, source_id: str, incident_type: str) -> Incident | None:
        raise NotImplementedError

    def acknowledge(self, incident_id: int, actor: str) -> Incident:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing deduplication/lifecycle tests**

```python
def test_same_root_problem_updates_one_open_incident(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        a = service.observe(IncidentSignal("knowledge_service", "3", "unreachable", "critical", {"error_count": 1}, (1, 2, 3)))
        b = service.observe(IncidentSignal("knowledge_service", "3", "unreachable", "critical", {"error_count": 2}, (1, 2, 3)))
        assert a.id == b.id
        assert b.first_seen_at == a.first_seen_at
        assert b.last_seen_at >= a.last_seen_at


def test_recovery_resolves_existing_incident(client) -> None:
    with Session(get_engine()) as session:
        service = IncidentService(session)
        incident = service.observe(IncidentSignal("channel", "7", "unavailable", "critical", {}, (2,)))
        resolved = service.resolve("channel", "7", "unavailable")
        assert resolved.id == incident.id
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None
```

Also test acknowledgement does not mark resolved and resolved incidents do not absorb a new later outage (a new incident row is created).

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_incident_service.py
```

- [ ] **Step 3: Add Incident model**

```python
class Incident(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    severity: str = Field(index=True)
    source_type: str = Field(index=True)
    source_id: str = Field(index=True)
    incident_type: str = Field(index=True)
    deduplication_key: str = Field(index=True)
    status: str = Field(default="open", index=True)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = None
    details_json: str = "{}"
    affected_bot_ids_json: str = "[]"
    external_notified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
```

- [ ] **Step 4: Implement service**

Dedup key is exactly:

```python
def incident_key(source_type: str, source_id: str, incident_type: str) -> str:
    return f"{incident_type}:{source_type}_{source_id}"
```

`observe()` searches only `status in {'open','acknowledged'}` with this key, updates `last_seen_at`, severity/details/affected Bots, or creates one new row. `resolve()` sets `resolved_at` and status. Do not delete incident history.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_incident_service.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/models.py apps/api/chatbot_manager/operations tests/test_incident_service.py
git commit -m "feat: add deduplicated incident tracking"
```

---

### Task 2: Add Runtime and Integration Health Checks

**Files:**
- Create: `apps/api/chatbot_manager/operations/health.py`
- Create: `tests/test_health_service.py`

**Interfaces:**
- Consumes: DB, `KnowledgeServiceClient`, `ChannelConnection` state/activity.
- Produces:

```python
@dataclass(frozen=True)
class HealthSignal:
    source_type: str
    source_id: str
    status: str          # healthy | degraded | unavailable | unknown
    severity: str | None
    code: str
    latency_ms: int | None = None
    affected_bot_ids: tuple[int, ...] = ()

class HealthService:
    async def check_system(self) -> list[HealthSignal]:
        raise NotImplementedError

    async def check_knowledge_services(self) -> list[HealthSignal]:
        raise NotImplementedError

    async def check_channels(self) -> list[HealthSignal]:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing tests**

Required cases with concrete assertions:

- `test_shared_rag_outage_reports_one_root_signal_with_all_affected_bots`: seed one Knowledge Service referenced by Live configs for Bots 1, 2, and 3; fake its health as unavailable; assert exactly one `HealthSignal` for that service with `affected_bot_ids == (1, 2, 3)`.
- `test_recent_channel_activity_is_not_called_active_health_probe`: seed a configured channel with recent inbound/outbound timestamps but no reliable provider health endpoint; assert status is `unknown` or `degraded` with an evidence code such as `channel_recent_activity`, never `healthy`.
- `test_database_check_reports_healthy_only_after_successful_query`: run against a working test DB and assert a healthy system signal; monkeypatch the DB execution to raise and assert an unavailable/critical signal with a stable code and no raw exception text.

Write explicit fake knowledge clients and channel rows. For channels where provider has no active health endpoint, return `status="unknown"` or `status="degraded"` with codes such as `channel_configured_no_active_probe` or `channel_recent_activity`, never unsupported `healthy` claims.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_health_service.py
```

- [ ] **Step 3: Implement system/Knowledge health**

System DB health uses a simple `SELECT 1`. Knowledge health calls each enabled service once and computes affected Bots from Live config bindings. Map healthy/unavailable/unauthorized/invalid statuses to stable signals.

- [ ] **Step 4: Implement channel evidence wording**

Use connection state plus recent inbound/outbound timestamps/last errors if available. Do not add provider API calls solely to make a green status icon unless the provider adapter has a reliable existing health endpoint.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_health_service.py tests/test_knowledge_client.py tests/test_channel_connections.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/operations/health.py tests/test_health_service.py
git commit -m "feat: add system and integration health checks"
```

---

### Task 3: Add Operational and Quality Metrics

**Files:**
- Create: `apps/api/chatbot_manager/operations/metrics.py`
- Optionally modify: `apps/api/chatbot_manager/models.py` for `MetricAggregate` only if query benchmarks justify it.
- Create: `tests/test_metrics_service.py`

**Interfaces:**
- Produces `MetricService.summary(start, end, bot_id=None)` and drill-down filter contracts.

- [ ] **Step 1: Write failing metric tests**

Seed conversations/messages/decisions and assert these exact cases:

- `test_quality_metrics_derive_resolution_fallback_escalation_and_rag_failure`: seed a known mix of RAG, fallback, escalation, and RAG-error decisions; assert the computed rates equal the hand-calculated fractions.
- `test_operations_metrics_derive_messages_latency_and_human_wait`: seed message counts, decision latencies, and one `needs_human` conversation older than the warning threshold; assert counts, average latency, and human-wait count.
- `test_metric_drilldown_returns_filter_contract_for_conversations`: request the fallback metric for Bot 2 and assert its drill-down contract targets `/conversations` with `bot_id=2`, `decision=fallback`, and the requested date range.

Concrete expected formulas:

```text
bot_resolution_rate = closed-or-bot-active conversations without human takeover / eligible conversations
fallback_rate       = fallback BotDecision count / BotDecision count
escalation_rate     = escalation decisions / BotDecision count
rag_failure_rate    = decisions with knowledge error_code / RAG-attempt decisions
avg_response_ms     = average BotDecision.total_latency_ms where not null
human_wait_count    = needs_human conversations above configured warning threshold
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_metrics_service.py
```

- [ ] **Step 3: Implement direct indexed queries first**

Return a typed/dict summary with counts/rates and drill-down URLs such as:

```text
/conversations?bot_id=2&decision=fallback&from=2026-09-13T00:00:00&to=2026-09-13T23:59:59
/conversations?bot_id=2&handoff_reason=knowledge_unavailable&from=2026-09-13T00:00:00&to=2026-09-13T23:59:59
```

Do not duplicate message content in metric storage.

- [ ] **Step 4: Benchmark before adding aggregate table**

Use the development DB plus a generated test fixture of at least 10,000 `BotDecision` rows and record query time in the test log. If summary queries remain comfortably sub-second on SQLite, do not create `MetricAggregate` in this phase. If measured performance is poor, add hourly aggregates containing numeric counts/sums only and tests proving aggregation equals direct-query results.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_metrics_service.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/operations/metrics.py apps/api/chatbot_manager/models.py tests/test_metrics_service.py
git commit -m "feat: add chatbot operations metrics"
```

Stage `models.py` only if Task 4 benchmarking actually introduced the aggregate table.

---

### Task 4: Add Telegram Alert Policy

**Files:**
- Modify: `apps/api/chatbot_manager/settings.py`
- Create: `apps/api/chatbot_manager/operations/alerts.py`
- Create: `tests/test_alert_service.py`
- Modify: `.env.example`

**Interfaces:**
- Produces `TelegramAlertService.notify_incident(incident) -> AlertResult` and `AlertPolicy.should_notify(incident, now) -> bool`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_critical_notifies_immediately() -> None:
    assert AlertPolicy(warning_persist_minutes=15).should_notify(critical_incident(), now) is True


def test_warning_waits_until_persistence_threshold() -> None:
    policy = AlertPolicy(warning_persist_minutes=15)
    incident = warning_incident(first_seen_at=now - timedelta(minutes=5))
    assert policy.should_notify(incident, now) is False
    incident.first_seen_at = now - timedelta(minutes=16)
    assert policy.should_notify(incident, now) is True


def test_info_does_not_notify_telegram() -> None:
    assert AlertPolicy().should_notify(info_incident(), now) is False
```

Also test cooldown avoids duplicate external messages and recovery may send one recovery message only if the incident was externally notified.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_alert_service.py
```

- [ ] **Step 3: Add settings**

```python
ops_poll_seconds: int = 60
warning_persist_minutes: int = 15
human_wait_warning_minutes: int = 10
human_wait_critical_minutes: int = 30
rag_latency_warning_ms: int = 3000
rag_latency_critical_ms: int = 8000
alert_telegram_bot_token: str = ""
alert_telegram_chat_id: str = ""
```

Document equivalent env names in `.env.example`. Do not reuse user-facing Telegram Bot credentials implicitly; operational alert credentials are a separate configuration boundary.

- [ ] **Step 4: Implement alert sender**

Use `TelegramAdapter` or a tiny httpx sender, but never include the bot token in logs/errors. Message includes severity, incident type/source, affected Bot names, started time, and a relative admin route such as `/incidents/<id>`; avoid including user conversation contents.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_alert_service.py tests/test_telegram_channel.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/settings.py apps/api/chatbot_manager/operations/alerts.py tests/test_alert_service.py .env.example
git commit -m "feat: add incident telegram alerts"
```

---

### Task 5: Add Lightweight Operations Scheduler

**Files:**
- Create: `apps/api/chatbot_manager/operations/scheduler.py`
- Modify: `apps/api/chatbot_manager/main.py`
- Create: `tests/test_operations_scheduler.py`

**Interfaces:**
- Consumes: HealthService, MetricService, IncidentService, AlertService.
- Produces one in-process periodic loop appropriate to the current single-instance deployment.

- [ ] **Step 1: Write failing single-iteration tests**

Test `OperationsScheduler.run_once()` rather than sleeping:

```python
@pytest.mark.asyncio
async def test_run_once_observes_failure_and_notifies_policy(client, monkeypatch) -> None:
    # fake HealthService returns one critical RAG failure;
    # assert IncidentService contains one incident and alert fake called once.

@pytest.mark.asyncio
async def test_run_once_resolves_recovered_incident(client, monkeypatch) -> None:
    # seed open incident, health returns healthy; assert resolved.
```

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_operations_scheduler.py
```

- [ ] **Step 3: Implement scheduler**

```python
class OperationsScheduler:
    async def run_once(self) -> None:
        # collect health signals
        # observe/resolve incidents
        # evaluate human queue + behavioral thresholds
        # send allowed notifications

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass
```

Each component failure is caught/logged with stable source/error type; one failed alert/health check must not terminate the loop.

- [ ] **Step 4: Wire into FastAPI lifespan**

In `main.py`, create a stop event/task on startup and set/cancel/await it on shutdown. Keep the `/health` request path independent of scheduler success.

- [ ] **Step 5: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_operations_scheduler.py tests/test_app_boot.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/chatbot_manager/operations/scheduler.py apps/api/chatbot_manager/main.py tests/test_operations_scheduler.py
git commit -m "feat: add lightweight operations scheduler"
```

---

### Task 6: Build Command Center, Incidents UI, and Analytics Drill-Down

**Files:**
- Create: `apps/api/chatbot_manager/admin/command_center.py`
- Create: `apps/api/chatbot_manager/admin/incidents.py`
- Create: `apps/api/chatbot_manager/admin/analytics.py`
- Modify: `apps/api/chatbot_manager/admin/__init__.py`
- Create: `apps/api/chatbot_manager/templates/command_center.html`
- Create: `apps/api/chatbot_manager/templates/incidents.html`
- Create: `apps/api/chatbot_manager/templates/incident_detail.html`
- Create: `apps/api/chatbot_manager/templates/analytics.html`
- Modify: `apps/api/chatbot_manager/templates/base.html`
- Modify: `apps/api/chatbot_manager/templates/bot_overview.html`
- Modify: `apps/api/chatbot_manager/static/styles.css`
- Create: `tests/test_operations_admin.py`

**Interfaces:**
- Produces global `/` Command Center, `/incidents`, `/analytics`, and Bot-scoped analytics links.

- [ ] **Step 1: Write failing admin tests**

Required tests and rendered assertions:

- `test_command_center_prioritizes_needs_attention_and_links_to_source`: seed one critical channel incident and one warning queue incident; assert Critical appears before Warning and each card links to its incident/source action.
- `test_shared_rag_incident_lists_affected_bots_once`: seed one root RAG incident with three affected Bot IDs; assert the page renders one incident card and names all three Bots.
- `test_fallback_metric_links_to_filtered_conversations`: fake/seed a fallback metric and assert the rendered link contains the expected Bot/date/decision filters.
- `test_system_status_is_degraded_when_one_active_bot_has_critical_dependency`: seed five healthy Bots plus one Bot affected by a critical channel incident; assert system status is `Critical` or `Degraded` according to the deterministic status function, never `Healthy`.
- `test_bot_overview_shows_health_summary_without_full_bi_charts`: GET one Bot Overview and assert dependency/status/compact activity values render while the global analytics chart container is absent.

Use explicit incidents/metrics seeded in DB/service fakes.

- [ ] **Step 2: Verify RED**

```bash
rtk uv run pytest -q tests/test_operations_admin.py
```

- [ ] **Step 3: Implement Command Center**

Replace the legacy dashboard content at `/` with:

```text
System status: Healthy / Degraded / Critical
Bots total + active/paused
Channels evidence summary
Knowledge Services health
Today's conversations
Human queue count
Open incident count
Needs Attention cards with direct actions
compact Bot fleet rows
```

No decorative chart is required on Command Center.

- [ ] **Step 4: Implement Incidents and Analytics**

Incidents list/filter by severity/status/source and detail view shows timeline, source, affected Bots, acknowledgement, recovery. Analytics has `Operations` and `Quality` sections and every actionable metric uses the drill-down filter URL returned by MetricService.

- [ ] **Step 5: Update navigation**

Approved global navigation becomes:

```text
Command Center
Bots
Conversations
Knowledge Services
Analytics
Incidents
```

User/System Settings appear in Phase 6; do not add empty pages now.

- [ ] **Step 6: Verify GREEN**

```bash
rtk uv run pytest -q tests/test_operations_admin.py tests/test_bot_admin.py tests/test_conversations_admin.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/chatbot_manager/admin apps/api/chatbot_manager/templates apps/api/chatbot_manager/static/styles.css tests/test_operations_admin.py
git commit -m "feat: add chatbot operations command center"
```

---

### Task 7: Phase 5 Verification and Incident/Alert Manual Checkpoint

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Focused verification**

```bash
rtk uv run pytest -q \
  tests/test_health_service.py \
  tests/test_incident_service.py \
  tests/test_metrics_service.py \
  tests/test_alert_service.py \
  tests/test_operations_scheduler.py \
  tests/test_operations_admin.py
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
simulate/point a test Knowledge Service at an unavailable endpoint
scheduler creates one critical root incident
all affected Bots shown under that incident
Telegram critical alert sent once when alert destination configured
restore service -> incident resolves -> recovery notification at most once
force warning-only fallback-rate threshold -> dashboard immediately; Telegram only after persistence threshold
click fallback/incident card -> filtered conversations/source detail
create human queue beyond warning threshold -> Command Center Needs Attention shows Inbox action
```

- [ ] **Step 4: Update handoff and commit**

Record thresholds used, alert destination test method (never token), incident/recovery IDs, full suite result, and next plan:

`docs/superpowers/plans/2026-09-13-phase-6-multi-user-hardening-cleanup.md`

```bash
git add HANDOFF.md
git commit -m "docs: hand off operations monitoring phase"
```
