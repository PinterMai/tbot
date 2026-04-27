"""Anti-hallucination second pass (Step E).

Required, not optional. Lists every numeric claim and the URL it was sourced
from. If any number lacks a URL, marks the report ``confidence -= 20`` and
adds to ``unverified_flags``.
"""
from __future__ import annotations


async def verify_report(report: dict) -> dict:
    """Return the report with verification adjustments applied. Step E."""
    raise NotImplementedError("Step E")
