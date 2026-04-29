"""Ingest cycle — pulls new tweets per handle, persists, manages circuit + alerts.

Step B scope. Wired into PTB's JobQueue from ``main.py``.

Behavior:
- Skips if ``paused`` is set or the circuit is open (``circuit_open_until`` in future).
- For each enabled handle (priority desc, name asc): call ``source.fetch_new``.
- New tweets → ``tweets`` table (deduped by id); ``handles.last_seen_tweet_id`` advances.
- Per-handle errors → ``errors`` table; consecutive-error counter advances.
- 10 consecutive errors → open circuit for ``CIRCUIT_PAUSE_SECONDS``; send 1 alert.
- 3 consecutive empty cycles → send 1 alert (re-armed once a non-empty cycle occurs).
- All counters and alert state are persisted in the ``state`` table.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

import aiosqlite

from signal_bot.sources.base import Tweet
from signal_bot.storage import db as dbmod

log = logging.getLogger(__name__)

CIRCUIT_ERROR_THRESHOLD: int = 10
CIRCUIT_PAUSE_SECONDS: int = 60 * 60          # 1h
EMPTY_POLL_ALERT_THRESHOLD: int = 3

# State keys (string-typed values in `state` table)
S_LAST_POLL_AT = "last_poll_at"
S_LAST_POLL_STATUS = "last_poll_status"            # ok|empty|errors|circuit_open
S_CONSECUTIVE_ERRORS = "consecutive_errors"
S_CONSECUTIVE_EMPTY = "consecutive_empty_polls"
S_CIRCUIT_OPEN_UNTIL = "circuit_open_until"        # ISO timestamp or empty
S_ALERT_STATE = "alert_state"                       # ""|"broken_empty"|"broken_circuit"


@runtime_checkable
class TweetSource(Protocol):
    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]: ...


AlertFn = Callable[[str], Awaitable[None]]


@dataclass
class CycleStats:
    handles_polled: int = 0
    new_tweets: int = 0
    errors: int = 0
    skipped_circuit: bool = False
    skipped_paused: bool = False


async def _get_int(conn: aiosqlite.Connection, key: str, default: int = 0) -> int:
    raw = await dbmod.get_state(conn, key, str(default))
    try:
        return int(raw or default)
    except ValueError:
        return default


async def _set_int(conn: aiosqlite.Connection, key: str, value: int) -> None:
    await dbmod.set_state(conn, key, str(value))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _circuit_is_open(conn: aiosqlite.Connection) -> bool:
    raw = await dbmod.get_state(conn, S_CIRCUIT_OPEN_UNTIL, "")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > _utcnow()


async def _open_circuit(conn: aiosqlite.Connection, seconds: int) -> str:
    until = _utcnow().timestamp() + seconds
    until_iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
    await dbmod.set_state(conn, S_CIRCUIT_OPEN_UNTIL, until_iso)
    return until_iso


async def _close_circuit_if_expired(conn: aiosqlite.Connection) -> bool:
    """If the circuit was open and has now expired, clear it. Returns True if cleared."""
    raw = await dbmod.get_state(conn, S_CIRCUIT_OPEN_UNTIL, "")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        await dbmod.set_state(conn, S_CIRCUIT_OPEN_UNTIL, "")
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until <= _utcnow():
        await dbmod.set_state(conn, S_CIRCUIT_OPEN_UNTIL, "")
        await _set_int(conn, S_CONSECUTIVE_ERRORS, 0)
        return True
    return False


async def _alert_once(
    conn: aiosqlite.Connection,
    alert_fn: AlertFn | None,
    state_label: str,
    message: str,
) -> None:
    """Send an alert iff we're not already in this broken state."""
    current = await dbmod.get_state(conn, S_ALERT_STATE, "")
    if current == state_label:
        return
    await dbmod.set_state(conn, S_ALERT_STATE, state_label)
    if alert_fn is None:
        log.warning("alert (no chat configured): %s", message)
        return
    try:
        await alert_fn(message)
    except Exception:  # noqa: BLE001
        log.exception("Failed to deliver alert: %s", message)


async def _clear_alert_state(conn: aiosqlite.Connection) -> None:
    await dbmod.set_state(conn, S_ALERT_STATE, "")


async def _ingest_one_handle(
    conn: aiosqlite.Connection,
    source: TweetSource,
    handle: dict[str, Any],
) -> tuple[int, str | None]:
    """Fetch + persist for one handle. Returns (new_count, error_type_or_none)."""
    name: str = handle["handle"]
    since_id: str | None = handle.get("last_seen_tweet_id")
    is_first_poll = since_id is None

    try:
        tweets = await source.fetch_new(name, since_id)
    except Exception as exc:  # noqa: BLE001
        await dbmod.log_error(
            conn,
            error_type=type(exc).__name__,
            message=str(exc)[:500],
            handle=name,
            traceback=traceback.format_exc()[:4000],
        )
        return 0, type(exc).__name__

    inserted = 0
    newest_id: str | None = None
    for tw in tweets:
        ok = await dbmod.insert_tweet(
            conn,
            tweet_id=tw.id,
            handle=name,
            text=tw.text,
            created_at=tw.created_at.isoformat(),
            url=tw.url,
            raw_json=json.dumps(tw.raw, default=str),
            is_backfill=is_first_poll,
        )
        if ok:
            inserted += 1
        if newest_id is None or tw.id > newest_id:
            newest_id = tw.id

    await dbmod.update_handle_seen(conn, name, newest_id)
    return inserted, None


async def run_ingest_cycle(
    db_path: Path,
    source: TweetSource,
    *,
    alert_fn: AlertFn | None = None,
) -> CycleStats:
    """Run one full polling cycle. Safe to call from a JobQueue tick."""
    stats = CycleStats()

    async with dbmod.connect(db_path) as conn:
        if await dbmod.is_paused(conn):
            stats.skipped_paused = True
            log.info("ingest: skipped (paused)")
            return stats

        if await _close_circuit_if_expired(conn):
            log.info("ingest: circuit breaker expired — resuming polling")
            await _clear_alert_state(conn)

        if await _circuit_is_open(conn):
            stats.skipped_circuit = True
            await dbmod.set_state(conn, S_LAST_POLL_STATUS, "circuit_open")
            log.info("ingest: skipped (circuit open)")
            return stats

        handles = await dbmod.list_handles(conn)
        active = [h for h in handles if h.get("enabled", 1)]

        for h in active:
            inserted, err = await _ingest_one_handle(conn, source, h)
            stats.handles_polled += 1
            stats.new_tweets += inserted
            if err is not None:
                stats.errors += 1

        consecutive_errors = await _get_int(conn, S_CONSECUTIVE_ERRORS, 0)
        consecutive_empty = await _get_int(conn, S_CONSECUTIVE_EMPTY, 0)

        if stats.errors == stats.handles_polled and stats.handles_polled > 0:
            consecutive_errors += 1
        elif stats.errors == 0:
            consecutive_errors = 0

        if stats.new_tweets == 0 and stats.errors == 0 and stats.handles_polled > 0:
            consecutive_empty += 1
        elif stats.new_tweets > 0:
            consecutive_empty = 0

        await _set_int(conn, S_CONSECUTIVE_ERRORS, consecutive_errors)
        await _set_int(conn, S_CONSECUTIVE_EMPTY, consecutive_empty)
        await dbmod.set_state(conn, S_LAST_POLL_AT, dbmod.utcnow_iso())

        if stats.errors > 0 and stats.new_tweets == 0:
            status = "errors"
        elif stats.new_tweets == 0:
            status = "empty"
        else:
            status = "ok"
        await dbmod.set_state(conn, S_LAST_POLL_STATUS, status)

        if consecutive_errors >= CIRCUIT_ERROR_THRESHOLD:
            until_iso = await _open_circuit(conn, CIRCUIT_PAUSE_SECONDS)
            await _alert_once(
                conn,
                alert_fn,
                "broken_circuit",
                (
                    f"⚠️ Ingest circuit breaker OPEN: {consecutive_errors} consecutive "
                    f"failed cycles. Polling paused until {until_iso} (UTC). "
                    f"Use /status to inspect recent errors."
                ),
            )
        elif consecutive_empty >= EMPTY_POLL_ALERT_THRESHOLD:
            await _alert_once(
                conn,
                alert_fn,
                "broken_empty",
                (
                    f"⚠️ Ingest may be broken: {consecutive_empty} consecutive empty "
                    f"polling cycles ({stats.handles_polled} handles each). "
                    f"Use /status for details."
                ),
            )
        elif consecutive_errors == 0 and consecutive_empty == 0:
            await _clear_alert_state(conn)

        log.info(
            "ingest cycle: handles=%d new=%d errors=%d status=%s "
            "consec_err=%d consec_empty=%d",
            stats.handles_polled, stats.new_tweets, stats.errors, status,
            consecutive_errors, consecutive_empty,
        )
    return stats


__all__ = [
    "CycleStats",
    "TweetSource",
    "run_ingest_cycle",
    "CIRCUIT_ERROR_THRESHOLD",
    "CIRCUIT_PAUSE_SECONDS",
    "EMPTY_POLL_ALERT_THRESHOLD",
    "S_LAST_POLL_AT",
    "S_LAST_POLL_STATUS",
    "S_CONSECUTIVE_ERRORS",
    "S_CONSECUTIVE_EMPTY",
    "S_CIRCUIT_OPEN_UNTIL",
    "S_ALERT_STATE",
]
