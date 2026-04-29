"""Tests for the Step-B ingest cycle: backfill, persistence, errors,
empty-poll alert, and circuit-breaker behavior."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from signal_bot.pipeline import ingest as ingest_mod
from signal_bot.pipeline.ingest import (
    CIRCUIT_ERROR_THRESHOLD,
    EMPTY_POLL_ALERT_THRESHOLD,
    S_ALERT_STATE,
    S_CIRCUIT_OPEN_UNTIL,
    S_CONSECUTIVE_EMPTY,
    S_CONSECUTIVE_ERRORS,
    S_LAST_POLL_AT,
    S_LAST_POLL_STATUS,
    run_ingest_cycle,
)
from signal_bot.sources.base import Tweet
from signal_bot.storage import db as dbmod

pytestmark = pytest.mark.asyncio


def _make_tweet(handle: str, tid: str, *, text: str = "hello") -> Tweet:
    return Tweet(
        id=tid,
        handle=handle,
        text=text,
        created_at=datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc),
        url=f"https://twitter.com/{handle}/status/{tid}",
        raw={"id": tid, "text": text},
    )


class FakeSource:
    """Programmable TweetSource for tests. ``per_handle`` maps handle → tweets/exception."""

    def __init__(self, per_handle: dict | None = None) -> None:
        self.per_handle = per_handle or {}
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        self.calls.append((handle, since_id))
        result = self.per_handle.get(handle, [])
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(since_id)
        return list(result)


async def _seed_handle(db_path: Path, handle: str, *, priority: int = 0) -> None:
    async with dbmod.connect(db_path) as conn:
        await dbmod.upsert_handle(conn, handle, priority=priority)


async def _state(db_path: Path, key: str, default: str = "") -> str:
    async with dbmod.connect(db_path) as conn:
        return await dbmod.get_state(conn, key, default) or ""


async def test_backfill_marks_first_poll(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": [_make_tweet("alice", "1"), _make_tweet("alice", "2")]})

    stats = await run_ingest_cycle(db_path, src)

    assert stats.new_tweets == 2
    assert src.calls == [("alice", None)]
    async with dbmod.connect(db_path) as conn:
        async with conn.execute(
            "SELECT id, is_backfill FROM tweets ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    assert [(r["id"], r["is_backfill"]) for r in rows] == [("1", 1), ("2", 1)]


async def test_subsequent_poll_uses_since_id_and_no_backfill(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": [_make_tweet("alice", "1")]})
    await run_ingest_cycle(db_path, src)  # backfill

    src.per_handle["alice"] = [_make_tweet("alice", "2")]
    src.calls.clear()
    await run_ingest_cycle(db_path, src)

    assert src.calls == [("alice", "1")]
    async with dbmod.connect(db_path) as conn:
        async with conn.execute(
            "SELECT id, is_backfill FROM tweets WHERE id = '2'"
        ) as cur:
            row = await cur.fetchone()
    assert row["is_backfill"] == 0


async def test_dedupe_does_not_re_insert(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": [_make_tweet("alice", "1")]})
    await run_ingest_cycle(db_path, src)
    stats = await run_ingest_cycle(db_path, src)
    # Same tweet still returned by source — should not double-insert.
    assert stats.new_tweets == 0


async def test_skip_when_paused(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    async with dbmod.connect(db_path) as conn:
        await dbmod.set_paused(conn, True)
    src = FakeSource({"alice": [_make_tweet("alice", "1")]})
    stats = await run_ingest_cycle(db_path, src)
    assert stats.skipped_paused is True
    assert src.calls == []


async def test_per_handle_error_logged_and_counted(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    await _seed_handle(db_path, "bob")
    src = FakeSource({
        "alice": [_make_tweet("alice", "1")],
        "bob": RuntimeError("network blew up"),
    })

    stats = await run_ingest_cycle(db_path, src)
    assert stats.handles_polled == 2
    assert stats.errors == 1
    assert stats.new_tweets == 1

    async with dbmod.connect(db_path) as conn:
        errs = await dbmod.recent_errors(conn, limit=5)
    assert any(e["handle"] == "bob" and e["error_type"] == "RuntimeError" for e in errs)


async def test_consecutive_errors_only_when_all_fail(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    await _seed_handle(db_path, "bob")
    src = FakeSource({
        "alice": [_make_tweet("alice", "1")],
        "bob": RuntimeError("x"),
    })
    await run_ingest_cycle(db_path, src)
    # Mixed result: not all-fail, so consecutive_errors should be 0.
    assert (await _state(db_path, S_CONSECUTIVE_ERRORS)) == "0"


async def test_circuit_opens_after_threshold(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": RuntimeError("boom")})

    sent: list[str] = []

    async def alert(msg: str) -> None:
        sent.append(msg)

    for _ in range(CIRCUIT_ERROR_THRESHOLD):
        await run_ingest_cycle(db_path, src, alert_fn=alert)

    assert (await _state(db_path, S_CIRCUIT_OPEN_UNTIL)) != ""
    assert (await _state(db_path, S_ALERT_STATE)) == "broken_circuit"
    assert any("circuit breaker OPEN" in m for m in sent)
    assert len(sent) == 1  # alert-once

    # Subsequent cycles while circuit is open should be skipped silently.
    src.calls.clear()
    stats = await run_ingest_cycle(db_path, src, alert_fn=alert)
    assert stats.skipped_circuit is True
    assert src.calls == []
    assert (await _state(db_path, S_LAST_POLL_STATUS)) == "circuit_open"


async def test_circuit_resets_after_expiry(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    # Open the circuit manually with a past-due "until".
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    async with dbmod.connect(db_path) as conn:
        await dbmod.set_state(conn, S_CIRCUIT_OPEN_UNTIL, past)
        await dbmod.set_state(conn, S_CONSECUTIVE_ERRORS, "9")

    src = FakeSource({"alice": [_make_tweet("alice", "1")]})
    stats = await run_ingest_cycle(db_path, src)

    assert stats.skipped_circuit is False
    assert stats.new_tweets == 1
    assert (await _state(db_path, S_CIRCUIT_OPEN_UNTIL)) == ""
    assert (await _state(db_path, S_CONSECUTIVE_ERRORS)) == "0"


async def test_empty_poll_alert_fires_once(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": []})

    sent: list[str] = []

    async def alert(msg: str) -> None:
        sent.append(msg)

    for _ in range(EMPTY_POLL_ALERT_THRESHOLD):
        await run_ingest_cycle(db_path, src, alert_fn=alert)

    assert len(sent) == 1
    assert "empty polling cycles" in sent[0]
    assert (await _state(db_path, S_ALERT_STATE)) == "broken_empty"

    # Another empty cycle should NOT re-alert (we're still in broken_empty).
    await run_ingest_cycle(db_path, src, alert_fn=alert)
    assert len(sent) == 1


async def test_empty_alert_clears_after_recovery(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    sent: list[str] = []

    async def alert(msg: str) -> None:
        sent.append(msg)

    src_empty = FakeSource({"alice": []})
    for _ in range(EMPTY_POLL_ALERT_THRESHOLD):
        await run_ingest_cycle(db_path, src_empty, alert_fn=alert)
    assert (await _state(db_path, S_ALERT_STATE)) == "broken_empty"

    # Recovery cycle delivers a new tweet → consec_empty resets, alert state clears.
    src_ok = FakeSource({"alice": [_make_tweet("alice", "9")]})
    await run_ingest_cycle(db_path, src_ok, alert_fn=alert)
    assert (await _state(db_path, S_ALERT_STATE)) == ""
    assert (await _state(db_path, S_CONSECUTIVE_EMPTY)) == "0"


async def test_last_poll_state_is_recorded(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": [_make_tweet("alice", "1")]})
    await run_ingest_cycle(db_path, src)

    assert (await _state(db_path, S_LAST_POLL_AT)) != ""
    assert (await _state(db_path, S_LAST_POLL_STATUS)) == "ok"


async def test_handle_last_seen_advances_to_newest(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({
        "alice": [
            _make_tweet("alice", "5"),
            _make_tweet("alice", "9"),
            _make_tweet("alice", "7"),
        ]
    })
    await run_ingest_cycle(db_path, src)

    async with dbmod.connect(db_path) as conn:
        async with conn.execute(
            "SELECT last_seen_tweet_id FROM handles WHERE handle = 'alice'"
        ) as cur:
            row = await cur.fetchone()
    assert row["last_seen_tweet_id"] == "9"


async def test_status_label_is_empty_when_no_errors_no_new(db_path: Path) -> None:
    await _seed_handle(db_path, "alice")
    src = FakeSource({"alice": []})
    await run_ingest_cycle(db_path, src)
    assert (await _state(db_path, S_LAST_POLL_STATUS)) == "empty"
