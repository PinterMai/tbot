"""Rank and filter reports for the digest (Step E).

Drops anything with ``confidence < 60`` or ``len(unverified_flags) > 2``.
Sorts by ``trend.strength + investment.conviction_score``. Keeps top N.
"""
from __future__ import annotations


def rank_reports(reports: list[dict], limit: int = 5) -> list[dict]:
    raise NotImplementedError("Step E")
