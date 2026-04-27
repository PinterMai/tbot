"""Embedding seam.

Single function ``embed(texts) -> list[list[float]]``. Local
sentence-transformers in MVP; future swap (Voyage, Cohere, etc.) replaces
only this file.
"""
from __future__ import annotations

EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM: int = 384


def embed(texts: list[str]) -> list[list[float]]:
    """Return one embedding per input text. Lazy-loads the model. Step C."""
    raise NotImplementedError("Step C")
