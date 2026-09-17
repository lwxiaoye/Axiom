"""Structured research ledger and report schema. Profile data, not a second loop."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field


MIN_SOURCES_PER_TOPIC = 2
MIN_RESEARCH_TOPICS = 3
MAX_RESEARCH_TOPICS = 8
MIN_SEARCH_CALLS = 3
MIN_SUCCESSFUL_QUERIES_PER_TOPIC = 1
MIN_CROSS_VALIDATION_HOSTS = 3
_MULTI_PART_TLDS = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.hk", "com.tw",
    "co.uk", "co.jp", "co.kr",
}
_TRACKING_QUERY_PREFIXES = ("utm_", "spm")


def canonical_url(url: str) -> str:
    """Strip tracking params/fragments so the same article is one source."""
    raw = str(url or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return raw[:2_000]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw[:2_000]
    host = parsed.netloc.lower().removeprefix("www.")
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
        and key.lower() not in {"fbclid", "gclid", "yclid"}
    ]
    query = urlencode(kept, doseq=True)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))[:2_000]


def registrable_host(url_or_host: str) -> str:
    """eTLD+1 so news.sina.com.cn and sina.com.cn count as one publisher."""
    raw = str(url_or_host or "").strip().lower()
    if "://" in raw:
        try:
            raw = (urlparse(raw).netloc or "").lower()
        except Exception:  # noqa: BLE001
            raw = raw
    host = raw.split(":", 1)[0].removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in _MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


class ResearchStage(str, Enum):
    CLARIFYING = "clarifying"
    PLANNING = "planning"
    RESEARCHING = "researching"
    VERIFYING = "verifying"
    SYNTHESIZING = "synthesizing"


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2_000)
    title: str = Field(default="", max_length=400)
    snippet: str = Field(default="", max_length=8_000)
    query: str = Field(default="", max_length=400)
    topic_id: str = Field(default="", max_length=128)
    topic_ids: list[str] = Field(default_factory=list)
    scraped: bool = False
    fetched_at: str = Field(default="")

    @property
    def host(self) -> str:
        return registrable_host(self.url)

    def applies_to(self, topic_id: str) -> bool:
        return self.topic_id == topic_id or topic_id in self.topic_ids


class ResearchTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=600)
    queries: list[str] = Field(default_factory=list)
    min_sources: int = Field(default=MIN_SOURCES_PER_TOPIC, ge=1, le=8)
    status: str = Field(default="pending", max_length=32)


class ResearchLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ResearchStage = ResearchStage.PLANNING
    query: str = Field(default="", max_length=2_000)
    topics: list[ResearchTopic] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    search_calls: int = Field(default=0, ge=0)
    attempted_queries: list[str] = Field(default_factory=list)
    search_failures: int = Field(default=0, ge=0)
    last_search_error: str = Field(default="", max_length=500)
    # 仅由 Research kernel 写入。partial 表示本段覆盖预算已用完但仍有证据缺口；
    # 空串表示覆盖尚在进行或已经满足。CompletionVerifier 只读取这份平台事实。
    coverage_outcome: str = Field(default="", max_length=32)
    reason_codes: list[str] = Field(default_factory=list)
    updated_at: str = Field(default="")

    def to_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def unique_urls(self, *, topic_id: str = "") -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in self.sources:
            if topic_id and item.topic_id and not item.applies_to(topic_id):
                continue
            url = canonical_url(str(item.url or "").strip()) or str(item.url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    def unique_hosts(self, *, topic_id: str = "") -> set[str]:
        return {
            item.host
            for item in self.sources
            if item.host and (not topic_id or not item.topic_id or item.applies_to(topic_id))
        }

    def topic_has_min_sources(self, topic: ResearchTopic) -> bool:
        hosts = self.unique_hosts(topic_id=topic.topic_id)
        needed = max(MIN_SOURCES_PER_TOPIC, int(topic.min_sources or MIN_SOURCES_PER_TOPIC))
        return len(hosts) >= needed

    def successful_queries(self, *, topic_id: str = "") -> set[str]:
        return {
            str(item.query or "").strip().lower()
            for item in self.sources
            if str(item.query or "").strip()
            and (not topic_id or item.applies_to(topic_id))
        }

    def topic_coverage_ready(self, topic: ResearchTopic) -> bool:
        """A topic needs diverse sources *and* independent successful queries.

        Multiple links returned by one search are useful only when they span independent hosts
        and contain actually-read text. One successful query is the platform floor; after this
        floor the main model, which can see the evidence bodies, owns the decision to issue more
        focused searches for primary sources, counterexamples, freshness, or contradictions.
        """
        return (
            self.topic_has_min_sources(topic)
            and len(self.successful_queries(topic_id=topic.topic_id))
            >= MIN_SUCCESSFUL_QUERIES_PER_TOPIC
            and any(
                item.scraped and item.applies_to(topic.topic_id) and item.snippet.strip()
                for item in self.sources
            )
        )

    def coverage_ready(self) -> bool:
        if self.search_calls < MIN_SEARCH_CALLS:
            return False
        if len(self.unique_urls()) < MIN_SOURCES_PER_TOPIC:
            return False
        active = [topic for topic in self.topics if topic.status != "skipped"]
        if not active:
            return len(self.unique_hosts()) >= MIN_SOURCES_PER_TOPIC
        return all(self.topic_coverage_ready(topic) for topic in active)

    def cross_validated(self) -> bool:
        if not self.coverage_ready():
            return False
        active = [topic for topic in self.topics if topic.status != "skipped"]
        needed_hosts = MIN_CROSS_VALIDATION_HOSTS if active else MIN_SOURCES_PER_TOPIC
        return len(self.unique_hosts()) >= needed_hosts


class ReportCover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(default="", max_length=240)
    date: str = Field(default="", max_length=64)
    query: str = Field(default="", max_length=2_000)


class ReportChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    markdown: str = Field(default="", max_length=80_000)
    source_urls: list[str] = Field(default_factory=list)


class ReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover: ReportCover
    toc: list[str] = Field(default_factory=list)
    chapters: list[ReportChapter] = Field(default_factory=list)
    references: list[dict[str, str]] = Field(default_factory=list)


class CompiledReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    markdown: str
    html: str
    document: ReportDocument


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
