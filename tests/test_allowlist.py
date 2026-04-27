"""Allowlist behavior: only ALLOWED_USER_ID gets through; others silent-dropped."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_bot.bot import allowlist

pytestmark = pytest.mark.asyncio


def _fake_update(user_id: int | None) -> MagicMock:
    upd = MagicMock()
    if user_id is None:
        upd.effective_user = None
    else:
        upd.effective_user = MagicMock()
        upd.effective_user.id = user_id
        upd.effective_user.username = "tester"
    upd.effective_message = MagicMock()
    upd.effective_message.reply_text = AsyncMock()
    return upd


async def test_allowed_user_passes_through() -> None:
    inner = AsyncMock()
    wrapped = allowlist(allowed_user_id=12345)(inner)
    upd = _fake_update(12345)
    await wrapped(upd, MagicMock())
    inner.assert_awaited_once()


async def test_other_user_silently_dropped() -> None:
    inner = AsyncMock()
    wrapped = allowlist(allowed_user_id=12345)(inner)
    upd = _fake_update(99999)
    await wrapped(upd, MagicMock())
    inner.assert_not_awaited()
    # Silent: the wrapper must not send any reply.
    upd.effective_message.reply_text.assert_not_awaited()


async def test_no_user_silently_dropped() -> None:
    inner = AsyncMock()
    wrapped = allowlist(allowed_user_id=12345)(inner)
    upd = _fake_update(None)
    await wrapped(upd, MagicMock())
    inner.assert_not_awaited()


async def test_zero_user_id_is_treated_as_disallowed() -> None:
    # If ALLOWED_USER_ID=12345 and update user is 0, must be rejected.
    inner = AsyncMock()
    wrapped = allowlist(allowed_user_id=12345)(inner)
    upd = _fake_update(0)
    await wrapped(upd, MagicMock())
    inner.assert_not_awaited()
