"""Research-plan helpers: topic coverage and query diversity."""

from __future__ import annotations

import re

from .contracts import (
    MAX_RESEARCH_TOPICS,
    MIN_RESEARCH_TOPICS,
    MIN_SOURCES_PER_TOPIC,
    ResearchTopic,
)


_SPACE_RE = re.compile(r"\s+")


def rewrite_queries(topic: str, base_query: str = "") -> list[str]:
    """Deterministic query pool used until the evidence gates pass.

    The kernel normally stops after two successful formulations per topic. The additional
    variants are not unconditional traffic: they are gap-filling options when the first searches
    lack source diversity, primary evidence, counterexamples, or current material.
    """
    seed = _SPACE_RE.sub(" ", (base_query or topic or "").strip())
    if not seed:
        return []
    variants = [seed]
    if not re.search(r"(评测|对比|vs\.?|versus)", seed, re.I):
        variants.append(f"{seed} 评测 对比")
    if not re.search(r"(官方|一手|primary|official)", seed, re.I):
        variants.append(f"{seed} 官方 一手")
    if not re.search(r"(风险|局限|争议)", seed, re.I):
        variants.append(f"{seed} 风险 局限")
    if not re.search(r"(数据|统计|报告)", seed, re.I):
        variants.append(f"{seed} 数据 统计")
    if not re.search(r"(论文|研究|方法|paper|study)", seed, re.I):
        variants.append(f"{seed} 论文 研究 方法")
    if not re.search(r"(最新|进展|趋势|latest|recent)", seed, re.I):
        variants.append(f"{seed} 最新 进展 趋势")
    seen: set[str] = set()
    out: list[str] = []
    for item in variants:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out[:6]


def topics_from_plan_steps(steps: list[dict] | tuple, *, query: str = "") -> list[ResearchTopic]:
    """Project the semantic plan into research topics with query coverage."""
    topics: list[ResearchTopic] = []
    for index, raw in enumerate(list(steps or [])[:MAX_RESEARCH_TOPICS]):
        if isinstance(raw, dict):
            title = str(raw.get("title") or raw.get("step") or "").strip()
            step_id = str(raw.get("key") or raw.get("step_id") or f"topic-{index + 1}")
            detail = str(raw.get("detail") or "")
            status = str(raw.get("status") or "pending")
        else:
            title = str(getattr(raw, "title", "") or "").strip()
            step_id = str(getattr(raw, "step_id", "") or f"topic-{index + 1}")
            detail = str(getattr(raw, "detail", "") or "")
            status = str(getattr(raw, "status", "pending") or "pending")
        if not title:
            continue
        seed = f"{title} {detail}".strip()
        topics.append(ResearchTopic(
            topic_id=step_id[:128],
            title=title[:200],
            queries=rewrite_queries(title, seed if seed else query),
            min_sources=MIN_SOURCES_PER_TOPIC,
            status="skipped" if status in {"skipped", "invalidated"} else (
                "completed" if status in {"completed"} else "pending"
            ),
        ))
    return topics


def missing_coverage_hint(topic_count: int) -> str:
    if topic_count < MIN_RESEARCH_TOPICS:
        return (
            f"研究计划至少拆成 {MIN_RESEARCH_TOPICS}–{MAX_RESEARCH_TOPICS} 个可验证主题，"
            "每个主题都要多角度检索。"
        )
    return ""


_DEFAULT_ANGLES = (
    ("定义与边界", "{} 定义 边界"),
    ("方案对比与评测", "{} 评测 对比"),
    ("一手来源与官方口径", "{} 官方 一手"),
    ("风险局限与建议", "{} 风险 局限 建议"),
)


def default_topics(query: str) -> list[ResearchTopic]:
    """Legacy coverage recovery only; new teams get their topics from the lead model."""
    seed = _SPACE_RE.sub(" ", str(query or "")).strip()[:80] or "研究主题"
    topics: list[ResearchTopic] = []
    for index, (title, template) in enumerate(_DEFAULT_ANGLES, start=1):
        angle_query = template.format(seed)
        topics.append(ResearchTopic(
            topic_id=f"topic-{index}",
            title=title,
            queries=rewrite_queries(title, angle_query),
            min_sources=MIN_SOURCES_PER_TOPIC,
            status="pending",
        ))
    return topics[:MAX_RESEARCH_TOPICS]
