# Phase 1 Baseline

Date: 2026-08-26

## Repository State

- Original checkout: `Z:\ChatBot\CIFSChatbotManager` on `main`, HEAD `3afbc42cbb55ffca08cfa90d598ee4e43bcd4c39`.
- Isolated worktree: `C:\Users\CCS\.codex\visualizations\2026\08\25\01a039fd-ec02-7e21-8390-223e4b33fc63\phase2-stabilization`.
- Work branch: `codex/phase2-stabilization`.
- Transferred product state: 25 tracked modifications and 10 product-related untracked paths.
- Excluded from transfer: `.agents`, `.claude`, `.codex`, `.impeccable`, and `.serena` tool metadata.
- Preservation check: the original dirty checkout remained unchanged after worktree creation and transfer.

## Host and Tooling

| Tool | Observed state | Status |
|---|---|---|
| PowerShell | Codex bundled PowerShell on Windows | available |
| rtk | command not found | unavailable; raw diagnostic commands used under the project fallback rule |
| uv | command not found | unavailable |
| Repository `.venv` | Linux-format virtual environment | not executable by Windows |
| Bundled Windows Python | Python 3.12.13 | available |
| WSL Python | Python 3.12.3 | available, but mapped `Z:` repository is not visible |

## Dependency Setup

An isolated virtual environment was created at `C:\Users\CCS\.codex\visualizations\2026\08\25\01a039fd-ec02-7e21-8390-223e4b33fc63\phase2-venv`.

Full editable installation reached the RAG-Anything/MinerU/Torch stack but failed with Windows error 206 because a Torch license path exceeded the host path-length limit. The deterministic test environment therefore uses the core application dependencies plus `lightrag-hku` and `openai`. RAG-Anything itself is lazy-loaded by the application and is not required by the mocked unit tests.

Pytest also required an explicit `--basetemp` under the writable visualization root because the default `C:\Users\CCS\AppData\Local\Temp\pytest-of-CCS` directory returned access denied.

## Reproduction Command

From the isolated worktree:

```powershell
& '..\phase2-venv\Scripts\python.exe' -m pytest -q --basetemp='..\pytest-tmp-finalbaseline'
```

## Test Results

- Collected: 96
- Passed: 90
- Failed: 6
- Skipped: 0
- Warnings: 0 in the final baseline run
- Duration: 4.04 seconds

## Focused Results

| Suite | Result | Evidence |
|---|---|---|
| Boot and models | passing | All boot, migration, model, and timestamp tests passed in the full run. |
| Engine | passing | Command, rule, condition, regex, escalation decision, RAG fallback, and error fallback tests passed. |
| Channel adapters | passing | LINE, Messenger, and Telegram adapter tests passed. |
| Webhooks | passing | LINE, Messenger, and Telegram webhook route tests passed under their current expectations. |
| Admin routes | 3 failing | Knowledge path assertions use POSIX separators and fail on Windows. |
| RAG service | 3 failing | Index/reindex mock tests do not provide the newly required LLM API key. |

## Failures and Blockers

| ID | Category | Evidence | Impact | Target phase |
|---|---|---|---|---|
| BASE-001 | cross-platform test contract | `tests/test_admin_routes.py:190`, `:211`, and `:232` compare `data/uploads/menu.txt` with the valid Windows path `data\\uploads\\menu.txt`. | Three tests fail on Windows although the path refers to the same file. | Phase 2 |
| BASE-002 | stale test setup | `tests/test_rag_service.py:62`, `:121`, and `:175` construct indexing services without an API key after `RagAnythingService` began validating the key. | Three intended behavior tests stop before reaching the behavior they claim to test. | Phase 2 |
| BASE-003 | Windows dependency path limit | Full `pip install -e .` fails inside the Torch license tree with WinError 206. | Full MinerU/RAG runtime cannot be freshly installed at the long Codex worktree path. | Phase 5 |
| BASE-004 | test temp permission | Default pytest temp root returns WinError 5. | Tests require an explicit writable `--basetemp`. | Phase 5 |

## Baseline Conclusion

The current product state is reproducibly testable on Windows with an explicit writable pytest temp directory and the deterministic dependency subset. Ninety of 96 tests pass. Six failures are verified test-contract regressions rather than external-service failures. The real MinerU/Torch runtime remains unverified on this host because the long worktree path exceeds Windows path limits.

## Phase 1 Gate

- Reproducible environment: yes; the isolated Python 3.12 environment collects all 96 tests and reruns consistently with an explicit writable `--basetemp`.
- Complete feature inventory: yes; all current user-visible application, channel, engine, RAG, graph, admin, security, operations, and documentation surfaces are classified.
- Prioritized evidence-backed gap register: yes; 16 gaps have source/test evidence, impact, acceptance checks, and phase assignments.
- Pre-existing worktree preserved: yes; source status remained unchanged after the tracked patch and selected product files were copied into isolation.
- Decision: PASS.
- Phase 2 starting order: GAP-007, GAP-001, GAP-004, GAP-002, GAP-005, GAP-003, GAP-006, GAP-008, GAP-009, and GAP-010.
