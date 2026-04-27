"""Telegram MarkdownV2 helpers. Minimal in Step A; expanded in Step F."""
from __future__ import annotations

# https://core.telegram.org/bots/api#markdownv2-style — every reserved char
# must be backslash-escaped when used as literal text.
_MD2_SPECIALS: frozenset[str] = frozenset("_*[]()~`>#+-=|{}.!\\")


def escape_md2(text: str) -> str:
    """Escape every reserved MarkdownV2 character with a backslash."""
    out: list[str] = []
    for ch in text:
        if ch in _MD2_SPECIALS:
            out.append("\\")
        out.append(ch)
    return "".join(out)
