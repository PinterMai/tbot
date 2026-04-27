"""Schema + helper smoke tests for the SQLite layer."""
from __future__ import annotations

from pathlib import Path

import pytest

from signal_bot.storage import db as dbmod

pytestmark = pytest.mark.asyncio


async def test_all_tables_exist(db_path: Path) -> None:
    expected = {
        "tweets", "handles", "clusters", "cluster_tweets",
        "reports", "feedback", "errors", "state",
    }
    async with dbmod.connect(db_path) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
    actual = {r["name"] for r in rows}
    missing = expected - actual
    assert not missing, f"missing tables: {missing}"


async def test_init_db_is_idempotent(db_path: Path) -> None:
    # Calling init_db twice on the same file must not error.
    await dbmod.init_db(db_path)
    await dbmod.init_db(db_path)


async def test_state_round_trip(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        await dbmod.set_state(conn, "last_poll_at", "2026-04-27T10:00:00+00:00")
        v = await dbmod.get_state(conn, "last_poll_at")
        # Updating same key should overwrite, not duplicate.
        await dbmod.set_state(conn, "last_poll_at", "2026-04-27T11:00:00+00:00")
        v2 = await dbmod.get_state(conn, "last_poll_at")
    assert v == "2026-04-27T10:00:00+00:00"
    assert v2 == "2026-04-27T11:00:00+00:00"


async def test_state_default_when_missing(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        v = await dbmod.get_state(conn, "missing_key", "fallback")
    assert v == "fallback"


async def test_pause_toggle(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        assert await dbmod.is_paused(conn) is False
        await dbmod.set_paused(conn, True)
        assert await dbmod.is_paused(conn) is True
        await dbmod.set_paused(conn, False)
        assert await dbmod.is_paused(conn) is False


async def test_handles_upsert_and_list(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        await dbmod.upsert_handle(conn, "karpathy", priority=1)
        await dbmod.upsert_handle(conn, "sama", priority=0)
        await dbmod.upsert_handle(conn, "karpathy", priority=1)  # idempotent
        rows = await dbmod.list_handles(conn)
    handles = {r["handle"] for r in rows}
    assert handles == {"karpathy", "sama"}
    karpathy_row = next(r for r in rows if r["handle"] == "karpathy")
    assert karpathy_row["priority"] == 1
    assert karpathy_row["enabled"] == 1


async def test_handles_priority_ordering(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        await dbmod.upsert_handle(conn, "low", priority=0)
        await dbmod.upsert_handle(conn, "high", priority=1)
        rows = await dbmod.list_handles(conn)
    assert rows[0]["handle"] == "high"
    assert rows[1]["handle"] == "low"


async def test_error_log_and_recent_returns_newest_first(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        for i in range(7):
            await dbmod.log_error(
                conn, error_type="TestErr", message=f"boom {i}", handle="x"
            )
        recent = await dbmod.recent_errors(conn, limit=5)
    assert len(recent) == 5
    assert recent[0]["message"] == "boom 6"  # most recent first


async def test_counts_includes_all_expected_tables(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        await dbmod.upsert_handle(conn, "a")
        c = await dbmod.counts(conn)
    for table in ("tweets", "clusters", "reports", "errors", "handles", "feedback"):
        assert table in c, f"counts() missing {table}"
    assert c["handles"] == 1
    assert c["tweets"] == 0
    assert c["errors"] == 0


async def test_foreign_keys_are_enabled(db_path: Path) -> None:
    async with dbmod.connect(db_path) as conn:
        async with conn.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
    assert row[0] == 1
