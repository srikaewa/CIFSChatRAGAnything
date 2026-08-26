# CIFS Chatbot Manager Operations Guide

## Windows development and WinError 206

The optional RAG/parser dependency tree can create paths too long for a deeply nested Git worktree. If installation fails with **WinError 206**, do not change system-wide long-path policy as the first workaround. Keep the repository where it is and create/use a short-path Python environment, for example `C:\cifs-venv`, then run the project from the repository with that interpreter.

Core application and tests can run without exercising live RAG parsing. A typical short-path workflow is:

```powershell
py -3.11 -m venv C:\cifs-venv
C:\cifs-venv\Scripts\python.exe -m pip install -U pip
# Install the project/dependencies using the project's supported uv or pip workflow.
C:\cifs-venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\cifs-pytest"
```

If pytest cannot create its default temporary directory, always provide a writable `--basetemp` path such as `$env:TEMP\cifs-pytest`.

## RAG dependency levels

The admin, provider adapters, rules, Test Chat fallback paths, and mocked tests do not require a live parser or LLM credential. Full RAG ingestion needs the RAG-Anything/parser stack plus compatible LLM/embedding settings. When the optional RAG package is missing or initialization fails, RAG/graph endpoints should show a safe operator-facing unavailable/error state rather than exposing secrets.

Recovery checklist:

1. Confirm the short-path environment is active.
2. Confirm `LLM_BASE_URL`, `LLM_API_KEY`, model names, and `RAG_WORKING_DIR`.
3. Install the optional parser/RAG dependencies in the short-path environment.
4. Reindex a known small document.
5. Check Knowledge/Graph error text and application logs; do not paste API keys into support notes.

## Telegram and Tailscale Funnel

Telegram webhook automation requires the local `tailscale` CLI, an authenticated tailnet, Funnel permission, a configured Telegram bot token/webhook secret, and a valid HTTP/HTTPS `API_PUBLIC_URL`.

Setup flow:

1. Save Telegram credentials in Channels.
2. Confirm `tailscale status --json` works in the same account/service context that runs the app.
3. Click **Setup Webhook (Tailscale)**.
4. The app derives the service port using structured URL parsing. Explicit ports are used as-is; otherwise HTTP defaults to 80 and HTTPS to 443.
5. The app runs `tailscale funnel --bg <port>` directly on Windows and POSIX. It does not invoke interactive `sudo`.
6. Telegram `setWebhook` is called only after Funnel setup succeeds.

Disable flow deletes the Telegram webhook first, clears the stored active webhook URL after Telegram confirms deletion, then attempts `tailscale funnel off <port>`. A Funnel cleanup failure is reported separately; it does not pretend Telegram is still enabled.

Common recovery:

- **Tailscale CLI not found:** install/start Tailscale and ensure `tailscale` is on PATH for the app process.
- **Access denied:** authorize the local account/operator for Tailscale/Funnel; do not add `sudo` to application code.
- **Timeout/status failed:** verify the local Tailscale service and retry.
- **Invalid public URL:** set `API_PUBLIC_URL` to a valid `http://host[:port]` or `https://host[:port]` URL.
- **Telegram rejects webhook:** verify bot token, webhook secret, and public HTTPS reachability.

## Backup

Stop writes or stop the application before a consistent filesystem backup. Back up these locations together:

- SQLite database configured by `DATABASE_URL` (default local data database).
- Uploaded knowledge source files under the configured upload directory.
- `RAG_WORKING_DIR` if you want to preserve the generated RAG/index state.
- `.env` only in a secure secrets backup; never commit it to Git.

Example PowerShell backup layout:

```powershell
$stamp = Get-Date -Format yyyyMMdd-HHmmss
$dest = "C:\cifs-backups\$stamp"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item .\data\chatbot.sqlite3 $dest
Copy-Item .\data\uploads "$dest\uploads" -Recurse
Copy-Item .\data\rag "$dest\rag" -Recurse
```

Adjust paths to match `.env`.

## Restore

1. Stop the application.
2. Restore the SQLite file, uploaded knowledge directory, and RAG directory from the same Backup set.
3. Restore secure environment configuration separately.
4. Start the app and verify login, Channels status, Knowledge records, Test Chat, and Graph.
5. If RAG files were not restored, keep the SQLite/upload records and explicitly reindex documents rather than inventing index state.

## Common application failures

- **Channel Incomplete:** save all required provider authenticity/send credentials before enabling live traffic.
- **Channel Failed:** inspect safe UI status and server logs; credentials remain masked in pages.
- **Knowledge indexing failed:** preserve the source file/record, correct RAG configuration, then use reindex.
- **Graph unavailable:** index knowledge and verify the optional RAG stack; the UI should reset stale graph data on errors.
- **Pytest temp permission error:** rerun with explicit writable `--basetemp`.

## Verification commands

```powershell
C:\cifs-venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\cifs-pytest"
C:\cifs-venv\Scripts\python.exe -m compileall -q apps/api
git diff --check
```
