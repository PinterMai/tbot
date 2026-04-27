# Signal Bot

Personal Telegram bot that pulls signal from a curated list of X (Twitter) accounts on AI infrastructure, compute, and chip topics.

- See `SPEC.md` for the full specification (treat as source of truth).
- See `PROGRESS.md` for current build state.
- See `CLAUDE.md` for project rules and conventions.

## Quick start (after Step A)

1. Install Python 3.11+ (Windows: `winget install Python.Python.3.11`).
2. Open PowerShell in the repo root.
3. Create venv and install:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```
4. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `ALLOWED_USER_ID`.
5. Run: `python bot.py`

## Setup so far (Step A)

- SQLite schema with all tables (tweets, handles, clusters, cluster_tweets, reports, feedback, errors, state).
- Telegram bot with `/start`, `/status`, `/pause`, `/resume`, `/handles` commands.
- Allowlist enforcement: only `ALLOWED_USER_ID` is accepted; everyone else is silently dropped.
- Env validation: missing `TELEGRAM_BOT_TOKEN` or `ALLOWED_USER_ID` causes a clean exit with a clear message.

The README will expand at Step G with the dummy X account setup, full env reference, and "Running as a service on Windows" (NSSM).
