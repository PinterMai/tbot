"""Anthropic SDK wrapper.

Model IDs are pinned here as constants. ALL LLM call sites must
``from signal_bot.llm.client import MODEL_OPUS, MODEL_SONNET`` —
never hardcode a model string elsewhere in the codebase.
"""
from __future__ import annotations

MODEL_OPUS: str = "claude-opus-4-7"
MODEL_SONNET: str = "claude-sonnet-4-6"


class LLMClient:
    """Async Anthropic client wrapper. STUB — Step D will implement."""

    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY missing — set it in .env before running LLM steps."
            )
        self._api_key = api_key

    async def complete(self, *, model: str, system: str, messages: list[dict]) -> str:
        raise NotImplementedError("Step D")
