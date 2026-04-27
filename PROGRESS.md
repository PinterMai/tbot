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
2026-04-27 — session 1. Built repo skeleton; awaiting user verification (Python install + manual run).

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
