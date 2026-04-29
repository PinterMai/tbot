"""twikit-based tweet source.

Cookie auth: prefers ``cookies_file`` (full session JSON exported from a
logged-in browser) and falls back to ``auth_token`` alone. The fallback is
known to fail on twikit's anti-bot transaction-id flow — see README section
"Dummy X account setup" for how to export the right cookies.

Rate-limited to 1 request / 3s to stay well under twikit's empirical
~500 req/day soft cap. The first ``fetch_new`` for a handle (no ``since_id``)
returns up to ``backfill_count`` tweets. Subsequent calls page through
everything newer than ``since_id`` and stop at the first known tweet.

Errors propagate — the ingest pipeline is responsible for logging them
to the ``errors`` table and tripping the circuit breaker.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tweet

if TYPE_CHECKING:
    from twikit import Client as TwikitClient
    from twikit import Tweet as TwikitTweet

log = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_SECONDS: float = 3.0
DEFAULT_BACKFILL_COUNT: int = 5
DEFAULT_PAGE_COUNT: int = 40


class TwikitSource:
    """Cookie-auth source via the ``twikit`` library."""

    def __init__(
        self,
        auth_token: str | None,
        *,
        cookies_file: Path | None = None,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
        backfill_count: int = DEFAULT_BACKFILL_COUNT,
    ) -> None:
        if not auth_token and cookies_file is None:
            raise RuntimeError(
                "X auth missing: set X_COOKIES_FILE (preferred) or X_AUTH_TOKEN — "
                "see README section 'Dummy X account setup'"
            )
        if cookies_file is not None and not cookies_file.exists():
            raise RuntimeError(
                f"X_COOKIES_FILE points to a non-existent path: {cookies_file}"
            )
        self._auth_token = auth_token
        self._cookies_file = cookies_file
        self._rate_limit = rate_limit_seconds
        self._backfill_count = backfill_count
        self._sem = asyncio.Semaphore(1)
        self._client: TwikitClient | None = None
        self._user_id_cache: dict[str, str] = {}

    def _build_client(self) -> "TwikitClient":
        from twikit import Client

        client = Client(language="en-US")
        if self._cookies_file is not None:
            with self._cookies_file.open("r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not isinstance(cookies, dict):
                raise RuntimeError(
                    f"X_COOKIES_FILE must contain a JSON object of cookies "
                    f"(got {type(cookies).__name__})"
                )
            client.set_cookies(cookies)
            log.info(
                "twikit: loaded %d cookies from %s",
                len(cookies), self._cookies_file,
            )
        else:
            client.set_cookies({"auth_token": self._auth_token})
            log.warning(
                "twikit: using X_AUTH_TOKEN alone — likely to fail on X's "
                "anti-bot transaction-id flow. Export full cookies to "
                "X_COOKIES_FILE (see README)."
            )
        return client

    @property
    def client(self) -> "TwikitClient":
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def _throttled(self):
        return _Throttle(self._sem, self._rate_limit)

    async def _resolve_user_id(self, handle: str) -> str:
        if handle in self._user_id_cache:
            return self._user_id_cache[handle]
        async with await self._throttled():
            user = await self.client.get_user_by_screen_name(handle)
        user_id: str = user.id
        self._user_id_cache[handle] = user_id
        return user_id

    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        """Fetch tweets for ``handle`` newer than ``since_id``.

        First poll (``since_id is None``): returns up to ``backfill_count`` tweets.
        Subsequent polls: returns everything strictly newer than ``since_id``.
        """
        user_id = await self._resolve_user_id(handle)

        if since_id is None:
            async with await self._throttled():
                page = await self.client.get_user_tweets(
                    user_id, "Tweets", count=self._backfill_count
                )
            collected = [t for t in page if not _is_retweet(t)][: self._backfill_count]
            return [_to_tweet(t, handle) for t in collected]

        new_tweets: list["TwikitTweet"] = []
        async with await self._throttled():
            page = await self.client.get_user_tweets(
                user_id, "Tweets", count=DEFAULT_PAGE_COUNT
            )
        if _walk_page(page, since_id, new_tweets):
            return [_to_tweet(t, handle) for t in new_tweets]

        # Walk forward up to a safety cap of 5 pages to avoid runaway scrolling
        for _ in range(4):
            async with await self._throttled():
                page = await page.next()
            if not page:
                break
            if _walk_page(page, since_id, new_tweets):
                break
        return [_to_tweet(t, handle) for t in new_tweets]


class _Throttle:
    """Async context manager: hold semaphore + sleep on exit so calls are spaced."""

    def __init__(self, sem: asyncio.Semaphore, delay: float) -> None:
        self._sem = sem
        self._delay = delay

    async def __aenter__(self) -> "_Throttle":
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await asyncio.sleep(self._delay)
        finally:
            self._sem.release()


def _is_retweet(t: "TwikitTweet") -> bool:
    try:
        return getattr(t, "retweeted_tweet", None) is not None
    except Exception:
        return False


def _walk_page(page, since_id: str, sink: list) -> bool:
    """Append tweets newer than ``since_id``. Return True if we hit ``since_id``."""
    for t in page:
        if t.id == since_id:
            return True
        if _is_retweet(t):
            continue
        sink.append(t)
    return False


def _to_tweet(t: "TwikitTweet", handle: str) -> Tweet:
    created = _parse_created_at(t)
    url = f"https://twitter.com/{handle}/status/{t.id}"
    raw = _safe_raw(t)
    return Tweet(
        id=str(t.id),
        handle=handle,
        text=t.text or "",
        created_at=created,
        url=url,
        raw=raw,
    )


def _parse_created_at(t: "TwikitTweet") -> datetime:
    try:
        dt = t.created_at_datetime
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _safe_raw(t: "TwikitTweet") -> dict:
    raw = getattr(t, "_data", None) or getattr(t, "_legacy", None) or {}
    try:
        json.dumps(raw)
        return raw
    except (TypeError, ValueError):
        return {"id": str(t.id), "text": t.text or ""}
