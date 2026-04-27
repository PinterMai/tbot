# Project rules for Claude Code

These are persistent operating instructions for any Claude Code session that opens this repo. Read this first, then `PROGRESS.md`.

## Style

- Python 3.11+, type hints everywhere, ruff + black formatting (line length 100).
- Async where it makes sense (Telegram, HTTP, twikit, DB via aiosqlite).
- No premature abstraction — concrete first, refactor only when a 2nd use case appears.
- Use `pathlib.Path` for ALL file paths. NEVER string-concatenate paths. Host is Windows.
- LF line endings everywhere (enforced via `.gitattributes`); only `*.bat`/`*.ps1`/`*.cmd` are CRLF.

## Models — single source of truth

- Pinned in `signal_bot/llm/client.py`:
  - `MODEL_OPUS = "claude-opus-4-7"`
  - `MODEL_SONNET = "claude-sonnet-4-6"`
- All LLM call sites must `from signal_bot.llm.client import MODEL_OPUS, MODEL_SONNET`. **Never hardcode a model string anywhere else.**

## Embeddings — single seam

- Local only: `sentence-transformers/all-MiniLM-L6-v2`.
- Wrapped behind `signal_bot/llm/embeddings.py::embed(texts) -> list[list[float]]`.
- Future swap (Voyage, Cohere, etc.) is one file. Do not call sentence-transformers from anywhere else.

## Before any LLM-calling code change

- Update or add a fixture in `tests/fixtures/`.
- Run the function once with the fixture and paste output in chat for review.
- Never deploy a prompt change without showing the user the diff.

## Anti-hallucination is non-negotiable

- Every numeric claim must have a source URL or an `UNVERIFIED` tag.
- The `verify.py` second pass is required, not optional.
- `DRY_RUN=true` is the default — never auto-send to Telegram while developing.

## Session hygiene

- Read `PROGRESS.md` first.
- Update `PROGRESS.md` last.
- Commit after every working subtask with a descriptive message.
- If context > ~70% of the window, **STOP** and tell the user. Don't try to squeeze in one more change — that's where bugs sneak in.
- Ask before transitioning between Steps (A → B → … → G). The user confirms each transition.

## When stuck

- Don't guess. Ask the user.
- Don't invent API signatures — read the actual library source (twikit, python-telegram-bot, anthropic) before using a method.
- If a test fails twice with the same fix attempt, stop and explain.

## Files I should never touch without asking

- Anything outside the repo root.
- Git history (no force push, no rebase of pushed commits).
- `.env` (only `.env.example`).

## Files I should not read or analyze (runtime artifacts, waste tokens)

- `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`
- `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`
- `*.log`, `dry_run.log`
- `node_modules/` (if any sneaks in)
- `.env`
- `data/` (DB lives here; gitignored)

## Secrets

- Never ask the user to paste tokens in chat.
- All secrets live in `.env` (gitignored). `.env.example` has empty values.

## Step B specific (twikit) — for the next session

- Every twikit error → `errors` table (`timestamp, handle, error_type, message, traceback`).
- `/status` shows the last 5 errors.
- 3 consecutive empty polling cycles across all handles → Telegram alert: "ingest may be broken".
- Circuit breaker: 10 consecutive errors → pause polling for 1 hour, log it loudly, surface in `/status`.
- Rate limit: max 1 request / 3 sec, ~500 tweets/day total via `asyncio.Semaphore`.
- Persist `last_seen_tweet_id` per handle in the `handles` table (column already present).
- `X_AUTH_TOKEN` missing → fail with `"X_AUTH_TOKEN missing — see README section 'Dummy X account setup'"`.
