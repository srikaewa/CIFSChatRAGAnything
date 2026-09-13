# Chatbot Manager Handoff

## Current Repo

`/home/srikaewa/Data-II/Projects/ChatBot/CIFSChatbotManager`

User prefers terse caveman-style replies. Shell commands should use `rtk` prefix.

## Architectural Redesign — 2026-09-13

The user approved all nine design sections for the next-generation CIFSChatbotManager architecture and then approved the written design spec.

Formal design spec:

`docs/superpowers/specs/2026-09-13-chatbot-operations-console-redesign-design.md`

Core boundary:

- CIFSChatbotManager becomes the chatbot control plane and operations console: Bots, behavior/rules, channels, conversations/handoff, Draft/Test/Publish, regression testing, monitoring, analytics, incidents, users/roles, and audit.
- External RAG-Anything/LightRAG owns documents, parsing, indexing, graph/vector storage, Knowledge Graph, retrieval, document lifecycle, and final grounded answer generation.
- One Bot configuration binds to one Knowledge Service at a time; many Bots may share the same service.
- Knowledge binding lives on `BotConfigVersion`, not directly on `Bot`, so Draft can test a different service without changing production.
- Encrypted credential handling begins in Phase 2 when the Knowledge Service Registry is introduced; it is not deferred to the later hardening phase.

## Approved Implementation Plans

Master roadmap:

`docs/superpowers/plans/2026-09-13-chatbot-operations-console-master-roadmap.md`

Phase plans:

1. `docs/superpowers/plans/2026-09-13-phase-1-bot-foundation.md`
2. `docs/superpowers/plans/2026-09-13-phase-2-external-knowledge-service.md`
3. `docs/superpowers/plans/2026-09-13-phase-3-runtime-conversations.md`
4. `docs/superpowers/plans/2026-09-13-phase-4-draft-test-publish.md`
5. `docs/superpowers/plans/2026-09-13-phase-5-operations-monitoring.md`
6. `docs/superpowers/plans/2026-09-13-phase-6-multi-user-hardening-cleanup.md`

Planning self-review completed: no TODO/TBD/FIXME or placeholder test bodies remained, and the plan set was checked for spec coverage and interface/type consistency.

## Current Redesign Status — START HERE IN NEXT CHAT

- Design/spec commit: `98afaec` — `docs: design chatbot operations console redesign`
- Planning commit: `b8e40cc` — `docs: plan chatbot operations console implementation`
- The user selected **Execution option 2: Inline Execution**.
- Superpowers `executing-plans` was loaded and the next implementation target is **Phase 1 — Bot Foundation & Migration**.
- **No redesign production code has been implemented yet.**
- **Do not implement directly on `main`.** Superpowers requires an isolated Git worktree before starting execution.
- A native JJ_ACC `git_worktree_spawn` dry-run was performed only; **no worktree was actually created** in the prior chat.
- The new chat should first create an isolated worktree from current `HEAD`, then execute `docs/superpowers/plans/2026-09-13-phase-1-bot-foundation.md` task-by-task using TDD and the `executing-plans` workflow.
- Keep the original `main` checkout untouched except for intentional documentation/handoff commits.
- Preserve existing unrelated untracked tool/config files: `.agents/`, `.claude/`, `.codex/`, `.impeccable/`, `.serena/`.

### Mandatory phase workflow

For every implementation phase:

1. Read the design spec and the current phase plan before coding.
2. Use Superpowers `executing-plans` for Inline Execution.
3. Use an isolated worktree.
4. Follow TDD: failing test -> verify RED -> minimal implementation -> verify GREEN.
5. Run the phase verification commands and the full regression suite before claiming completion.
6. Review the diff and commit only intentional files.
7. **Update this `HANDOFF.md` after every successfully completed task/phase and before moving to the next phase or starting a new chat session.**
8. Do not remove legacy paths until the relevant phase says verification proves they are unused.

## Recent Work Before Redesign

Implemented revised Knowledge Graph dashboard based on `graph_module_implementation_guide_for_coding_ai.md`.

Browser verification of the Knowledge Graph was completed on 2026-09-12 using JJ ACC managed Chrome. No production-code change was required during this verification pass.

A release-state reconciliation was then performed on 2026-09-13. The old six-phase roadmap is closed: the gap register has no open P0-P3 gaps, and the feature inventory/release note have been reconciled with the completed Phase 2-6 evidence and the latest Knowledge Graph verification.

That reconciliation was later committed as `8a84bd6 chore: reconcile release verification state` and pushed to `origin/main`; divergence was 0/0 immediately after the push.

## Previous Verification Baseline

### Targeted Knowledge Graph / admin tests

Fresh run on 2026-09-12:

```bash
rtk uv run pytest -v tests/test_phase4_knowledge_graph.py tests/test_admin_routes.py
```

Result:

```text
30 passed in 1.26s
```

### Full suite

Fresh reconciliation run on 2026-09-13:

```bash
rtk uv run pytest -q
```

Result:

```text
184 passed in 4.08s
```

### Lockfile verification

```bash
rtk uv lock --check
```

Result: PASS, resolving 175 packages.

## Previous Knowledge Graph Browser Verification

Completed with JJ ACC managed Chrome against `http://127.0.0.1:8001/knowledge-graph` after authenticating with the development admin account.

Verified:

- Cytoscape canvas renders and is not blank.
- Initial all-graph view loaded approximately 32 nodes / 22 edges with the current development database.
- Label dropdown populated; current development database exposed 32 entity labels.
- Around-entity mode correctly enables label/depth controls.
- Depth and max-nodes controls submit and reload graph data correctly.
- `Forensic Physics`, depth 1, max 50 returned 3 nodes / 3 edges.
- Search filter works and clearing search restores the graph.
- Node-type filter works.
- Layout selector works; changing to `circle` changed node positions.
- Node click populates detail/metadata/neighbors and enables Expand 1-hop.
- Edge click populates edge detail/metadata and highlight state.
- Expand 1-hop reloads the selected node neighborhood correctly.

The combined JJ ACC browser console/network diagnostic helper returned an internal tool error during that session, so do not claim a separate clean-console diagnostic. Functional browser interaction checks completed successfully.

## Historical Release-Cleanup Notes

Repository-wide tracked churn initially made most files appear modified. It was classified before cleanup:

- `git diff --ignore-cr-at-eol --numstat` showed almost all tracked files had no content changes.
- The PNG working-tree hashes matched their HEAD blob hashes exactly.
- Widespread executable-bit/CRLF-only churn was discarded only for files proven content-identical to HEAD.
- Real changes were preserved.
- Pre-existing untracked tool/config folders such as `.agents/`, `.claude/`, `.codex/`, `.impeccable/`, and `.serena/` were not removed or staged.

## Server Reference

Historical verification server command:

```bash
DATABASE_URL=sqlite:///./data/chatbot.sqlite3 rtk uv run uvicorn chatbot_manager.main:app --app-dir apps/api --host 127.0.0.1 --port 8001
```

Admin login used for local verification:

- Email: `admin@example.local`
- Password: `admin1234!`

## Caveats

- Real provider/RAG/LLM/Tailscale services remain mocked in automated verification; optional authorized deployment smoke checks are documented in the release candidate.
- Preserve pre-existing untracked tool/config folders unless the user explicitly asks to remove them.
- Do not assume an isolated worktree exists: the prior session stopped after dry-run only.

## Exact Next Step

In the next chat session:

1. Open/read this `HANDOFF.md`.
2. Load Superpowers `executing-plans` and `using-git-worktrees`.
3. Create an isolated worktree from current `HEAD` using the native JJ_ACC worktree tool.
4. Run a clean baseline test suite inside that worktree.
5. If baseline passes, start **Task 1 of `docs/superpowers/plans/2026-09-13-phase-1-bot-foundation.md`** and continue Phase 1 inline with TDD.
6. Update `HANDOFF.md` again at each meaningful completion checkpoint.
