"""TwikitSource unit tests with the underlying twikit Client mocked.

We don't exercise twikit's real network paths — those need live credentials
and would make tests flaky. We pin the contract this layer depends on:
- Constructor rejects empty token.
- ``fetch_new`` resolves user id once (cached) and constructs Tweet objects.
- Backfill returns up to ``backfill_count``; subsequent calls page until ``since_id``.
- Retweets are filtered out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from signal_bot.sources.twikit_source import TwikitSource


def _fake_tweet(tid: str, *, text: str = "hi", retweeted: bool = False) -> SimpleNamespace:
    """Mimics enough of twikit.Tweet for the source to consume."""
    return SimpleNamespace(
        id=tid,
        text=text,
        created_at_datetime=datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc),
        retweeted_tweet=SimpleNamespace(id="rt") if retweeted else None,
        _data={"id": tid, "text": text},
        _legacy={"full_text": text},
    )


class _FakePage(list):
    def __init__(self, items, next_page=None):
        super().__init__(items)
        self._next = next_page

    async def next(self):
        return self._next


class _FakeClient:
    def __init__(self, tweets_by_id: dict, user_id: str = "U1") -> None:
        self.tweets_by_id = tweets_by_id
        self.user_id = user_id
        self.user_calls = 0
        self.tweet_calls: list[tuple[str, str, int]] = []

    async def get_user_by_screen_name(self, screen_name: str):
        self.user_calls += 1
        return SimpleNamespace(id=self.user_id, screen_name=screen_name)

    async def get_user_tweets(self, user_id, kind, count=40, cursor=None):
        self.tweet_calls.append((user_id, kind, count))
        return _FakePage(self.tweets_by_id.get(user_id, []))


def test_missing_credentials_raises() -> None:
    with pytest.raises(RuntimeError, match="X auth missing"):
        TwikitSource(None)
    with pytest.raises(RuntimeError, match="X auth missing"):
        TwikitSource("")


def test_cookies_file_must_exist(tmp_path) -> None:
    bogus = tmp_path / "does-not-exist.json"
    with pytest.raises(RuntimeError, match="non-existent path"):
        TwikitSource("dummy", cookies_file=bogus)


def test_cookies_file_loaded(tmp_path) -> None:
    import json as _json

    p = tmp_path / "cookies.json"
    p.write_text(_json.dumps({"auth_token": "a", "ct0": "c"}), encoding="utf-8")
    src = TwikitSource(None, cookies_file=p, rate_limit_seconds=0)
    # Don't actually build a real client; just verify the path is stored
    # and would be honoured by _build_client.
    assert src._cookies_file == p


async def test_backfill_caps_at_count(monkeypatch) -> None:
    src = TwikitSource("dummy", rate_limit_seconds=0, backfill_count=2)
    fake_tweets = [_fake_tweet(str(i)) for i in (10, 9, 8, 7, 6)]
    fake = _FakeClient({"U1": fake_tweets})
    src._client = fake  # type: ignore[assignment]

    out = await src.fetch_new("alice", since_id=None)
    assert [t.id for t in out] == ["10", "9"]
    assert all(t.handle == "alice" for t in out)
    assert all(t.url.endswith(f"/status/{t.id}") for t in out)


async def test_user_id_is_cached(monkeypatch) -> None:
    src = TwikitSource("dummy", rate_limit_seconds=0)
    fake = _FakeClient({"U1": [_fake_tweet("1")]})
    src._client = fake  # type: ignore[assignment]

    await src.fetch_new("alice", since_id=None)
    await src.fetch_new("alice", since_id="1")
    assert fake.user_calls == 1


async def test_retweets_filtered_in_backfill() -> None:
    src = TwikitSource("dummy", rate_limit_seconds=0, backfill_count=5)
    fake = _FakeClient({"U1": [
        _fake_tweet("3"),
        _fake_tweet("2", retweeted=True),
        _fake_tweet("1"),
    ]})
    src._client = fake  # type: ignore[assignment]

    out = await src.fetch_new("alice", since_id=None)
    assert [t.id for t in out] == ["3", "1"]


async def test_subsequent_fetch_stops_at_since_id() -> None:
    src = TwikitSource("dummy", rate_limit_seconds=0)
    fake = _FakeClient({"U1": [
        _fake_tweet("5"),
        _fake_tweet("4"),
        _fake_tweet("3"),  # ← since_id, stop here
        _fake_tweet("2"),
        _fake_tweet("1"),
    ]})
    src._client = fake  # type: ignore[assignment]

    out = await src.fetch_new("alice", since_id="3")
    assert [t.id for t in out] == ["5", "4"]


async def test_tweet_text_and_url_shape() -> None:
    src = TwikitSource("dummy", rate_limit_seconds=0, backfill_count=1)
    fake = _FakeClient({"U1": [_fake_tweet("42", text="hello world")]})
    src._client = fake  # type: ignore[assignment]

    out = await src.fetch_new("dylan522p", since_id=None)
    assert len(out) == 1
    t = out[0]
    assert t.id == "42"
    assert t.text == "hello world"
    assert t.handle == "dylan522p"
    assert t.url == "https://twitter.com/dylan522p/status/42"
    assert t.created_at.tzinfo is timezone.utc
