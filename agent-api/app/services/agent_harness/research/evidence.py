"""Ingest search/deep-read receipts into the research ledger."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .contracts import ResearchLedger, SourceRecord, canonical_url, utc_now_iso


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _as_source(item: Any, *, query: str = "", topic_id: str = "") -> SourceRecord | None:
    if not isinstance(item, dict):
        return None
    url = canonical_url(str(item.get("url") or "").strip())
    if not url.startswith("http"):
        return None
    title = str(item.get("title") or item.get("name") or url)[:400]
    snippet = str(item.get("content") or item.get("snippet") or item.get("text") or "")[:8_000]
    scraped = bool(item.get("scraped"))
    return SourceRecord(
        url=url[:2_000],
        title=title,
        snippet=snippet,
        query=str(query or item.get("query") or "")[:400],
        topic_id=str(topic_id or "")[:128],
        scraped=scraped,
        fetched_at=utc_now_iso(),
    )


def sources_from_receipt(
    *,
    tool_name: str,
    meta: dict[str, Any] | None,
    citations: list | None = None,
    query: str = "",
    topic_id: str = "",
) -> list[SourceRecord]:
    """Pull durable URLs out of a search_web / deep_read receipt."""
    name = str(tool_name or "")
    if name not in {"search_web", "deep_read"}:
        return []
    found: list[SourceRecord] = []
    positions: dict[str, int] = {}

    def _push(record: SourceRecord | None) -> None:
        if record is None:
            return
        position = positions.get(record.url)
        if position is not None:
            previous = found[position]
            found[position] = previous.model_copy(update={
                "title": record.title or previous.title,
                "snippet": (
                    record.snippet
                    if len(record.snippet) > len(previous.snippet)
                    else previous.snippet
                ),
                "query": record.query or previous.query,
                "topic_id": record.topic_id or previous.topic_id,
                "scraped": bool(previous.scraped or record.scraped),
            })
            return
        positions[record.url] = len(found)
        found.append(record)

    blob = meta if isinstance(meta, dict) else {}
    for row in blob.get("read") or []:
        payload = {**row, "scraped": True} if isinstance(row, dict) else {"url": row, "scraped": True}
        _push(_as_source(payload, query=query, topic_id=topic_id))
    urls = blob.get("urls") if isinstance(blob.get("urls"), list) else []
    titles_by_url: dict[str, str] = {}
    for row in citations or []:
        if not isinstance(row, dict):
            continue
        record = _as_source(row, query=query, topic_id=topic_id)
        if record is not None:
            titles_by_url[record.url] = record.title
            _push(record)
    for url in urls:
        raw = url if isinstance(url, dict) else {"url": url, "title": titles_by_url.get(str(url) or "")}
        _push(_as_source(raw, query=query, topic_id=topic_id))
    return found


def _merge_excerpt(previous: SourceRecord, current: SourceRecord) -> str:
    if previous.scraped and not current.scraped:
        return previous.snippet
    if current.scraped and not previous.scraped:
        return current.snippet or previous.snippet
    if current.snippet in previous.snippet:
        return previous.snippet
    if previous.snippet in current.snippet:
        return current.snippet
    if current.scraped and previous.scraped:
        return (previous.snippet[:3800] + "\n\n[补充原文片段]\n" + current.snippet[:4100])[:8000]
    return current.snippet if len(current.snippet) > len(previous.snippet) else previous.snippet


def merge_sources(ledger: ResearchLedger, incoming: list[SourceRecord]) -> ResearchLedger:
    existing = {item.url: item for item in ledger.sources}
    for item in incoming:
        prev = existing.get(item.url)
        if prev is None:
            existing[item.url] = item
            continue
        existing[item.url] = prev.model_copy(update={
            "title": item.title or prev.title,
            "snippet": _merge_excerpt(prev, item),
            "query": item.query or prev.query,
            "topic_id": item.topic_id or prev.topic_id,
            "topic_ids": list(dict.fromkeys([v for v in [*prev.topic_ids, prev.topic_id, *item.topic_ids, item.topic_id] if v])),
            "scraped": bool(prev.scraped or item.scraped),
        })
    return ledger.model_copy(update={
        "sources": list(existing.values())[:80],
        "updated_at": utc_now_iso(),
    })


def active_topic_id(ledger: ResearchLedger) -> str:
    for topic in ledger.topics:
        if topic.status not in {"completed", "skipped"}:
            return topic.topic_id
    return ledger.topics[-1].topic_id if ledger.topics else ""


def distinct_host_count(urls: list[str]) -> int:
    return len({_host(url) for url in urls if _host(url)})
