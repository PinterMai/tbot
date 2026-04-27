# Build Progress

## Current step
Step A complete (code) — awaiting user manual verification (Python install + run)

## Completed
- [x] Step A: repo skeleton, SQLite schema, Telegram bot with `/start /status /pause /resume /handles`, allowlist, env validation, pytest tests (commit `2171ebd`)
- [ ] Step B: twikit source + ingest loop, errors table population, circuit breaker, alert on 3 empty polls
- [ ] Step C: keyword filter + embedding cluster (sentence-transformers local)
- [ ] Step D: Research step (Claude + web search), strict JSON schema
- [ ] Step E: Verify pass + ticker validation + ranking
- [ ] Step F: Telegram digest formatter + inline keyboard + feedback storage
- [ ] Step G: README expansion (dummy X account, NSSM Windows service)

## Last session ended
2026-04-28 — session 1 fully closed. Repo skeleton built, Python 3.11.9 installed via winget, venv + deps OK, all 14 pytest tests pass, env validation verified (exit 1 + clear missing-vars message), bot boots cleanly and connects to Telegram (`getMe` 200 OK). **User-confirmed: Telegram bot responds to `/start /status /handles /pause /resume`, allowlist works.** Step A is DONE.

## Next session — Step B start checklist
1. User adds `X_AUTH_TOKEN=` to `.env` (dummy account cookie).
2. Confirm decisions from session 1 still stand (see "Decisions made this session" + "Step B prep" below).
3. Begin Step B implementation: twikit source + ingest loop + errors table population + circuit breaker.

## Decisions made this session (autonomous, per user instruction 2026-04-28)
- **Token-leak fix:** `httpx`, `httpcore`, `apscheduler`, `telegram.ext.Application` loggers pinned to `WARNING` in `main.py::_setup_logging`. Reason: `httpx` at INFO logs full request URLs, which include `bot<TOKEN>/...`. Project-owned loggers (`signal_bot.*`) still honour `LOG_LEVEL`.
- **Event-loop fix:** original `main.py` did `asyncio.run(setup)` *then* `app.run_polling()`, which crashed on Python 3.11 (no current event loop after `asyncio.run` closes it). Refactored to PTB v21's `post_init` callback pattern — `init_db` and handle seeding now run inside PTB's own event loop. `build_application` gained an optional `post_init: PostInitFn | None` parameter.
- **PowerShell venv activation:** documented `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force` as the standard fix for the "running scripts is disabled" error.

## Step B prep (locked in this session)
- Dummy X account confirmed by user (research-only, 0 followers, 22 follows backed up in Discord).
- All 8 Step-B clarifications accepted defaults: pin twikit version after first run, alert once per broken state, explicit `Circuit:` line in `/status`, UTC + relative time, `set_my_commands` on startup, log rotation immediate, backfill 5 with `is_backfill` flag, graceful shutdown, health endpoint as TODO.

## Open decisions (resolved this session)
- Embeddings: local `sentence-transformers/all-MiniLM-L6-v2` only.
- Model IDs: `claude-opus-4-7` (`MODEL_OPUS`), `claude-sonnet-4-6` (`MODEL_SONNET`) pinned in `signal_bot/llm/client.py`.
- Chrome/Playwright source: OUT of MVP scope; revisit after twikit has run 2+ weeks.
- Nitter health-check budget: 30 s, then silently fall back to twikit.

## Open for Step B
- User confirms dummy X account exists before pasting `X_AUTH_TOKEN` into `.env`.
- Pin `twikit` version after first successful run (note in `pyproject.toml`).
- Decide log-rotation strategy for `dry_run.log` once we generate volume.
- Confirm whether `/status` should also show circuit-breaker state explicitly when active.

## Known issues / TODOs
- Python is NOT installed on the host (only the Microsoft Store shim). Step A's first manual test step is `winget install Python.Python.3.11`.
- All test-running and `python bot.py` smoke-checks must be run by the user; this session could not execute them.
