"""Render the isolated, browser-safe published-agent iframe document."""
from __future__ import annotations

import html
from pathlib import Path


_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "agent_embed.html"


def render_embed_page(parent_origin: str) -> str:
    """Return a template whose only parent-controlled value is HTML escaped."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__PARENT_ORIGIN__", html.escape(parent_origin, quote=True))


def frame_content_security_policy(parent_origin: str) -> str:
    """The ticket freezes this exact ancestor; never widen it with a wildcard."""
    return (
        "default-src 'none'; "
        "connect-src 'self'; "
        "style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; "
        f"frame-ancestors {parent_origin}; "
        "base-uri 'none'; form-action 'none'"
    )
