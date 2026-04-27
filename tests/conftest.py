"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from signal_bot.storage import db as dbmod


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Fresh, initialized SQLite DB for each test."""
    p = tmp_path / "test.db"
    await dbmod.init_db(p)
    return p
