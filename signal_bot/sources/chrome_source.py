"""Chrome / Playwright source — OUT of MVP scope.

Per session-1 user decision (2026-04-27): build only after twikit has run
live for 2+ weeks. Premature optimization otherwise.
"""
from __future__ import annotations

from .base import Tweet


class ChromeSource:
    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        raise NotImplementedError("chrome source: out of scope for MVP")
