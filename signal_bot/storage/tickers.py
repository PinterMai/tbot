"""Validated ticker cache (Step E).

Refreshed weekly from a free source. Any LLM-emitted ticker not in this
cache gets stripped and flagged ``ticker_not_validated:XYZ``.
"""
from __future__ import annotations


def is_valid_ticker(symbol: str) -> bool:
    raise NotImplementedError("Step E")
