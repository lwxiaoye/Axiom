"""Lossless input partitioning and token-bounded verbatim excerpts for compaction."""

from __future__ import annotations

from app.services.platform.token_estimator import estimate_tokens


def prefix_length(text: str, tokens: int) -> int:
    """Largest estimated-token prefix; works for CJK as well as Latin text."""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= tokens:
            low = middle
        else:
            high = middle - 1
    return low


def bounded_excerpt(text: str, tokens: int) -> str:
    """Keep both ends within budget; this is an excerpt, never full-source coverage."""
    if tokens <= 0:
        return ""
    if estimate_tokens(text) <= tokens:
        return text
    marker = "\n[原文节选，中段省略，以下保留末尾]\n"
    available = tokens - estimate_tokens(marker) - 2
    if available <= 0:
        # Tiny budgets still preserve the newest instruction, not just the opening.
        return text[len(text) - prefix_length(text[::-1], tokens):] if tokens else ""
    head = prefix_length(text, available // 2)
    tail = prefix_length(text[::-1], available - estimate_tokens(text[:head]))
    return text[:head] + marker + (text[-tail:] if tail else "")
