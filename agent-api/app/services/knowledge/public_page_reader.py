"""Small static-page reader; dynamic pages fall back to the configured scraper.

Use the existing IP-pinned transport for *every* redirect. Do not inherit a
process proxy which would replace the validated destination with a new DNS lookup.
"""
import asyncio
from html.parser import HTMLParser
import re

import httpx

from app.services.gateway.mcp_client import PinnedPublicTransport

MAX_PAGE_BYTES = 2_000_000
READ_SECONDS = 4


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "nav", "footer", "svg", "template"}:
            self.ignored.append(tag)
        if not self.ignored and tag in {"p", "br", "div", "h1", "h2", "h3", "li", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.ignored = self.ignored[:self.ignored.index(tag)]
        if not self.ignored and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def extract_text(body, content_type):
    if "html" not in content_type:
        return body.strip()
    parser = PageText()
    parser.feed(body)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


async def read_public_page(url):
    async with asyncio.timeout(READ_SECONDS):
        async with httpx.AsyncClient(
            transport=PinnedPublicTransport(), trust_env=False,
            timeout=READ_SECONDS, follow_redirects=True, max_redirects=3,
            headers={"User-Agent": "AXIOM-Research/1.0", "Accept": "text/html,text/plain,application/json"},
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(kind in content_type for kind in ("text/html", "text/plain", "text/markdown", "application/json", "application/xhtml")):
                    raise ValueError("unsupported_content_type")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_PAGE_BYTES:
                        raise ValueError("page_too_large")
                return extract_text(chunks.decode(response.encoding or "utf-8", errors="replace"), content_type)
