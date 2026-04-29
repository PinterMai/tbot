# Build Progress

## Current step
Step B complete (code) — awaiting user manual verification (live ingest cycle with valid cookies)

## Completed
- [x] Step A: repo skeleton, SQLite schema, Telegram bot with `/start /status /pause /resume /handles`, allowlist, env validation, pytest tests (commit `2171ebd`)
- [x] Step B: twikit source + ingest loop, errors table, circuit breaker, alert on 3 empty polls, backfill, log rotation, set_my_commands, /status enrichment
- [ ] Step C: keyword filter + embedding cluster (sentence-transformers local)
- [ ] Step D: Research step (Claude + web search), strict JSON schema
- [ ] Step E: Verify pass + ticker validation + ranking
- [ ] Step F: Telegram digest formatter + inline keyboard + feedback storage
- [ ] Step G: README expansion (dummy X account, NSSM Windows service)

## Last session ended
2026-04-29 — Step B code complete. 41 pytest tests pass. Bot boots cleanly, JobQueue fires the
first ingest cycle at +10s, all 10 seeded handles get polled, errors are correctly captured to
the `errors` table, circuit breaker counters advance, `set_my_commands` registers, log rotation
file is created at `logs/signal_bot.log`. **Live fetch fails with `AttributeError:
'ClientTransaction' object has no attribute 'key'` because `X_AUTH_TOKEN` alone is insufficient
for twikit's anti-bot transaction-id flow** — see "Known issues" below.

## Next session — Step B verification checklist
1. User exports full cookies (auth_token + ct0 + everything else from a logged-in
   `x.com` browser tab) into `data/x_cookies.json` and sets `X_COOKIES_FILE=data/x_cookies.json`
   in `.env`. **Do NOT paste cookies in chat.**
2. Confirm `pytest -q` is still 41 passed.
3. Run `python bot.py` (or `python -m signal_bot.main`) and wait ~15 s for the first cycle.
4. Expected: log shows `ingest cycle: handles=10 new=N errors=0 status=ok`, `data/signal_bot.db`
   has new rows in `tweets`, `/status` shows non-zero `Tweets:` and a non-`never` `Last poll:`.
5. If verified: ask user to confirm Step C transition (keyword filter + embedding cluster).

## Step B — what got built
- `signal_bot/sources/twikit_source.py::TwikitSource` — cookie auth (preferring
  `X_COOKIES_FILE`, falling back to `X_AUTH_TOKEN`), 1 req/3 s rate limiting via
  `asyncio.Semaphore`, retweet filtering, page-walk on subsequent fetches that
  stops at the previously-seen `since_id` (max 5 pages of look-back as a safety cap).
- `signal_bot/pipeline/ingest.py::run_ingest_cycle` — single cycle: respects `paused`,
  honours/expires the circuit breaker, polls each enabled handle in priority order,
  persists tweets (deduped by id), logs per-handle errors, advances counters,
  emits one-shot alerts.
- `signal_bot/main.py` — JobQueue scheduling, `set_my_commands`, RotatingFileHandler
  (10 MB × 5 backups at `logs/signal_bot.log`), graceful "ingest disabled" fallback when
  no source can be built.
- `signal_bot/bot.py::cmd_status` — UTC + relative time on `Last poll:`, optional
  `Circuit: OPEN until ...` line, status suffix (`[ok]`/`[empty]`/`[errors]`/`[circuit_open]`).
- `signal_bot/storage/db.py` — `insert_tweet` (with `is_backfill` flag), `update_handle_seen`,
  lightweight `_migrate_tweets_columns` ALTER TABLE migration so existing DBs gain
  `is_backfill` and `fetched_at` without dropping data.
- Tests: 16 new (`tests/test_ingest.py`, `tests/test_twikit_source.py`,
  `tests/test_status_format.py`). All 41 pass.

## Decisions made this session (autonomous, per user instruction 2026-04-28)
- **Cookie auth strategy:** `X_COOKIES_FILE` (path to JSON dict) is preferred and documented
  as the working path; `X_AUTH_TOKEN` alone is kept as a fallback but documented to be
  broken on twikit 2.3.3's `ClientTransaction.init` flow. Reason: twikit needs the homepage
  anti-bot key, which X only serves to fully-authenticated sessions.
- **Twikit version pin:** `twikit==2.3.3` in `pyproject.toml`. Reason: matches what was
  installed and verified to load + invoke. Bumping later is one-line.
- **State storage:** circuit/empty-poll counters live in the existing `state` table as
  string-encoded ints, not in dedicated columns. Reason: zero schema churn, fits the
  "no premature abstraction" rule, and `_get_int`/`_set_int` helpers wrap the cast.
- **Alert dedup:** stored as a single `alert_state` key in `state` (`""`, `"broken_empty"`,
  `"broken_circuit"`). Re-armed when counters return to 0. Reason: simpler than per-alert
  timestamps, and "alert once per broken state" is exactly what was requested.
- **Page-walk safety cap:** 5 pages on subsequent fetches before giving up looking for
  `since_id`. Reason: protects against unbounded scrolling if X stops returning the
  expected `since_id` (e.g. tweet deleted upstream).
- **Schema migration:** `ALTER TABLE tweets ADD COLUMN ... DEFAULT 0` for `is_backfill`
  and `fetched_at` instead of dropping the existing `data/signal_bot.db`. Reason:
  preserves Step A handle seed; SQLite supports this cleanly.

## Known issues / TODOs
- **Live fetch is blocked until full cookies are provided.** Concrete error
  observed during smoke test: `AttributeError: 'ClientTransaction' object has no
  attribute 'key'` from `twikit/x_client_transaction/transaction.py:145` —
  `ClientTransaction.init()` runs but its `validate_response`/`get_key` step
  fails to extract the homepage key when the request is only partially
  authenticated. Fix path: export full cookies (auth_token + ct0 + all others)
  to `data/x_cookies.json` and point `X_COOKIES_FILE` at it.
- Step B's READMe/dummy-account doc work is deferred to Step G per the original plan.
- The Windows event-loop policy gets flipped to `WindowsSelectorEventLoopPolicy`
  by twikit's `__init__.py` at import time. PTB v21 still works under it
  (verified during smoke test), but if we ever need subprocess support in
  this process we'll need to revisit.
- A `BLE001` (broad-except) lint warning would fire on the per-handle and
  set_my_commands try/except blocks — these are intentional (we never want a
  single-handle failure to abort the whole cycle, and `set_my_commands` is
  best-effort). If we add ruff to CI, add `# noqa: BLE001` or scope the catch.

## Open for Step C
- Confirm Step C scope: keyword filter (regex from `config/topics.yaml`) +
  semantic clustering with `sentence-transformers/all-MiniLM-L6-v2`.
- Decide cluster cadence: every cycle vs. dedicated job? Default plan: run after
  each ingest cycle on the unprocessed-tweets backlog.
