"""SQLite schema and async helpers via aiosqlite.

All tables are defined in Step A — empty tables don't hurt, and we'll need
them later. The schema is idempotent: ``init_db()`` can be called repeatedly.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS tweets (
    id              TEXT PRIMARY KEY,
    handle          TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL,        -- ISO8601 UTC
    url             TEXT NOT NULL,
    raw_json        TEXT NOT NULL,        -- full source payload
    processed_at    TEXT,                 -- when pipeline last touched
    filtered_out    INTEGER NOT NULL DEFAULT 0,
    filter_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tweets_handle   ON tweets(handle);
CREATE INDEX IF NOT EXISTS idx_tweets_created  ON tweets(created_at);
CREATE INDEX IF NOT EXISTS idx_tweets_filtered ON tweets(filtered_out);

CREATE TABLE IF NOT EXISTS handles (
    handle              TEXT PRIMARY KEY,
    added_at            TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    priority            INTEGER NOT NULL DEFAULT 0,   -- 0=normal, 1=high
    last_seen_tweet_id  TEXT,
    last_polled_at      TEXT
);

CREATE TABLE IF NOT EXISTS clusters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'    -- pending|researched|reported|skipped
);

CREATE TABLE IF NOT EXISTS cluster_tweets (
    cluster_id  INTEGER NOT NULL,
    tweet_id    TEXT NOT NULL,
    PRIMARY KEY (cluster_id, tweet_id),
    FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE,
    FOREIGN KEY (tweet_id)   REFERENCES tweets(id)   ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id            INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    topic                 TEXT NOT NULL,
    summary               TEXT NOT NULL,
    key_claims_json       TEXT NOT NULL,
    tech_trend_json       TEXT NOT NULL,
    investment_json       TEXT NOT NULL,
    recommendation        TEXT NOT NULL,             -- STRONG_BUY|BUY|WATCH|SKIP|AVOID
    confidence            INTEGER NOT NULL,
    unverified_flags_json TEXT NOT NULL,
    raw_response          TEXT,                      -- full LLM payload, for debugging
    sent_to_telegram      INTEGER NOT NULL DEFAULT 0,
    sent_at               TEXT,
    FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reports_sent ON reports(sent_to_telegram);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,
    timestamp   TEXT NOT NULL,
    action      TEXT NOT NULL,            -- 'useful' | 'noise' | 'track'
    user_id     INTEGER NOT NULL,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    handle      TEXT,
    error_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    traceback   TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_timestamp ON errors(timestamp);

CREATE TABLE IF NOT EXISTS state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db(db_path: Path) -> None:
    """Create the DB file (and parent dir) and apply the schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.executescript(SCHEMA)
        await conn.commit()


@asynccontextmanager
async def connect(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Async context-manager for a DB connection with row factory + FK enabled."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn


# ---------- state ----------

async def get_state(
    conn: aiosqlite.Connection, key: str, default: str | None = None
) -> str | None:
    async with conn.execute("SELECT value FROM state WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_state(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        """
        INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, utcnow_iso()),
    )
    await conn.commit()


async def is_paused(conn: aiosqlite.Connection) -> bool:
    return (await get_state(conn, "paused", "0")) == "1"


async def set_paused(conn: aiosqlite.Connection, paused: bool) -> None:
    await set_state(conn, "paused", "1" if paused else "0")


# ---------- handles ----------

async def upsert_handle(
    conn: aiosqlite.Connection,
    handle: str,
    *,
    priority: int = 0,
    enabled: bool = True,
) -> None:
    await conn.execute(
        """
        INSERT INTO handles (handle, added_at, enabled, priority)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(handle) DO UPDATE SET
            enabled = excluded.enabled,
            priority = excluded.priority
        """,
        (handle, utcnow_iso(), 1 if enabled else 0, priority),
    )
    await conn.commit()


async def list_handles(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    async with conn.execute(
        """
        SELECT handle, enabled, priority, last_polled_at, last_seen_tweet_id
        FROM handles
        ORDER BY priority DESC, handle ASC
        """
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------- errors ----------

async def log_error(
    conn: aiosqlite.Connection,
    *,
    error_type: str,
    message: str,
    handle: str | None = None,
    traceback: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO errors (timestamp, handle, error_type, message, traceback)
        VALUES (?, ?, ?, ?, ?)
        """,
        (utcnow_iso(), handle, error_type, message, traceback),
    )
    await conn.commit()


async def recent_errors(
    conn: aiosqlite.Connection, limit: int = 5
) -> list[dict[str, Any]]:
    async with conn.execute(
        """
        SELECT timestamp, handle, error_type, message
        FROM errors
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------- counts (for /status) ----------

_COUNT_TABLES: tuple[str, ...] = (
    "tweets", "clusters", "reports", "errors", "handles", "feedback",
)


async def counts(conn: aiosqlite.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in _COUNT_TABLES:
        async with conn.execute(f"SELECT COUNT(*) AS c FROM {table}") as cur:
            row = await cur.fetchone()
        out[table] = int(row["c"])
    return out


__all__ = [
    "SCHEMA",
    "init_db",
    "connect",
    "utcnow_iso",
    "get_state",
    "set_state",
    "is_paused",
    "set_paused",
    "upsert_handle",
    "list_handles",
    "log_error",
    "recent_errors",
    "counts",
]
