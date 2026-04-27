"""Nitter RSS source. Implemented in Step B as a fallback.

Health-check budget on startup: 30 s total. If all instances dead, log
warning and silently fall back to twikit (per session-1 user decision).
"""
from __future__ import annotations

from .base import Tweet


class NitterSource:
    def __init__(self, instances: list[str]) -> None:
        self._instances = instances

    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        raise NotImplementedError("nitter source: Step B")
