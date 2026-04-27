"""TweetSource protocol — every ingest backend conforms to this."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Tweet:
    id: str
    handle: str
    text: str
    created_at: datetime
    url: str
    raw: dict


@runtime_checkable
class TweetSource(Protocol):
    """Pluggable source of tweets. All concrete impls live in this package."""

    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        """Return tweets newer than ``since_id`` for ``handle``. May raise."""
        ...
