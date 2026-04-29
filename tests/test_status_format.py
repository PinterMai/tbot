"""Pure-function tests for the /status formatters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from signal_bot import bot as botmod


def test_format_when_handles_none_and_garbage() -> None:
    assert botmod._format_when(None) == "never"
    assert botmod._format_when("") == "never"
    assert botmod._format_when("not-a-date") == "not-a-date"


def test_format_when_yields_utc_and_relative() -> None:
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    out = botmod._format_when(five_min_ago)
    assert "UTC" in out
    assert "ago" in out
    assert "5m ago" in out or "4m ago" in out  # tolerate sub-second drift


def test_humanize_seconds_units() -> None:
    assert botmod._humanize_seconds(30) == "30s ago"
    assert botmod._humanize_seconds(120) == "2m ago"
    assert botmod._humanize_seconds(3 * 3600) == "3h ago"
    assert botmod._humanize_seconds(2 * 86400) == "2d ago"


def test_humanize_remaining_units() -> None:
    assert botmod._humanize_remaining(30) == "in 30s"
    assert botmod._humanize_remaining(120) == "in 2m"
    assert botmod._humanize_remaining(3 * 3600) == "in 3h"
    assert botmod._humanize_remaining(2 * 86400) == "in 2d"


def test_circuit_line_none_when_no_value_or_expired() -> None:
    assert botmod._circuit_line(None) is None
    assert botmod._circuit_line("") is None
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert botmod._circuit_line(past) is None


def test_circuit_line_present_when_open() -> None:
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    line = botmod._circuit_line(future)
    assert line is not None
    assert "Circuit: OPEN" in line
    assert "UTC" in line
    assert "in " in line
