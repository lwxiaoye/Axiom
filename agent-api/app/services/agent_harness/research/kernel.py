"""Research team orchestration and report synthesis.

Standard and Plan never enter here. Orchestrator still seeds GoalContract first.
Team runs own evidence gathering; legacy runs retain coverage recovery.
Both reuse `main_tool_turn` + `model_driver` on the same SSE channel.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing, suppress
from typing import Any, AsyncIterator

from app.core.config import settings
from app.services.agent_harness import run_store

from .contracts import ResearchLedger, ResearchStage, registrable_host, utc_now_iso
from .engine import (
    _get_research_blob,
    _save_research_blob,
    advance_stage,
    complete_topic,
    coverage_reason_codes,
    evidence_brief,
    inherit_research_ledger,
    ingest_tool_receipt,
    progress_payload,
    record_search_attempt,
    seed_research_state,
    set_coverage_outcome,
    synthesis_sources,
)
from .plan import default_topics, rewrite_queries
from app.services.agent_harness.public_commentary import generate_public_commentary

logger = logging.getLogger(__name__)

MAX_KERNEL_SEARCHES = 24
QUERIES_PER_TOPIC = 6


class ResearchDependencyUnavailable(RuntimeError):
    """Search dependency failed repeatedly; RunHub owns same-Run recovery."""

_SYNTHESIS_GUARD = (
    "平台已达到最低多来源覆盖。先逐条检查上述证据正文是否足以支持用户真正关心的结论；"
    "若仍缺一手来源、反例、时效信息、可比口径或交叉验证，继续调用 search_web 做更聚焦的"
    "短查询。只有你基于具体证据判断信息已经足够时才停止检索。随后写出完整研究报告："
    "标题、执行摘要、核心发现/对比、证据与局限、建议；关键句用 [n] 角标。"
    "直接从 Markdown 标题开始写，不要写「已掌握来源 / 现在合成报告」这类过程句，"
    "也不要用 --- 分隔线当开场。仅当某条关键结论仍缺交叉验证时才补搜。"
    "来源若标注「仅摘要」只能当线索，不得写成已核实正文。"
    "不要深读网页，不要自己 write_file。不要把本提示复述给用户。"
)

_PARTIAL_SYNTHESIS_GUARD = (
    "本段覆盖检索预算已经用完，但平台证据门槛仍有缺口。先检查上述已有正文；如果主循环的"
    "search_web 仍可取得缺失的一手来源、反例、时效信息或交叉验证，就用聚焦短查询补齐。"
    "若补齐后证据足够，正常写完整报告；若仍不足，只能写明确标注局限的部分研究报告，"
    "不得把未核实内容写成结论，也不得声称研究已经完整完成。关键句仍用 [n] 角标。"
    "不要自己 write_file，不要把本提示复述给用户。"
)

_TEAM_SYNTHESIS_GUARD = (
    "本次团队取证阶段已经结束（个别成员可能未完成）。现在直接基于证据台账和成员公开简报撰写研究报告，"
    "不要重新制定研究计划，不要再调用网络搜索、浏览或委派工具。"
    "从 Markdown 标题开始，给出核心发现、对比、证据与局限，关键句用 [n] 角标。"
    "成员公开进展不是篇幅模板；请使用完整研究材料和证据正文，逐项回答用户的核心问题。"
    "报告范围与用户问题匹配：一般了解应重点清楚、内容充分，不额外展开工商、合规或诉讼等尽调支线。"
    "只展开与结论有关的机制、数据和反例，不要求每章机械重复所有分析维度。"
    "除非用户要求简短，否则交付充分展开的研究正文：执行摘要之后，按实际问题分章，"
    "每章说明结论、具体证据或数据、因果机制、可比口径、反例和适用边界；最后给出有依据的建议。"
    "综合不同成员的发现并解释分歧，不要简单拼接简报。成稿前核对问题覆盖、引用对应、"
    "比较条件和建议依据，修正遗漏；不以固定字数、重复背景或堆砌来源冒充深度。"
    "正文采用连续研究文稿：清晰标题、分节段落，方案或维度比较时使用简洁表格，最后归纳结论和建议。"
    "摘要先直接回答用户的问题；概念学习须讲清工作机制并提供能跟随理解的实例或伪代码，"
    "方案研究须用一致维度比较并解释选择条件，不能只有术语定义和泛泛建议。"
    "围绕已确定的核心主题充分展开，其他相关话题只在影响判断时提及。"
    "已知结论正常展开，未核实的细节在对应段落说明；不要因局部缺证把整篇改成材料概况、任务统计或待办清单。"
    "不要罗列网址、逐来源标题、网页导航或大段原文摘录；来源由平台参考来源入口承载，正文只保留必要的 [n]。"
    "来源只有摘要时明确说明未核验全文；证据仍不足之处直接在报告写明局限，"
    "不得把未核实内容写成结论，也不得声称研究已经完整完成。"
    "最终沿用对话内蓝框报告，不创建额外文件，不复述这些指令。"
)


def _kernel_policy() -> tuple[int, int, int, int]:
    max_searches = max(1, min(
        int(getattr(settings, "RESEARCH_COVERAGE_MAX_SEARCHES", MAX_KERNEL_SEARCHES) or MAX_KERNEL_SEARCHES),
        64,
    ))
    queries_per_topic = max(1, min(
        int(getattr(settings, "RESEARCH_MAX_QUERIES_PER_TOPIC", QUERIES_PER_TOPIC) or QUERIES_PER_TOPIC),
        10,
    ))
    min_successful_queries = max(1, min(
        int(getattr(settings, "RESEARCH_MIN_SUCCESSFUL_QUERIES_PER_TOPIC", 1) or 1),
        queries_per_topic,
    ))
    consecutive_failure_limit = max(1, min(
        int(getattr(settings, "RESEARCH_CONSECUTIVE_FAILURE_LIMIT", 2) or 2),
        5,
    ))
    return max_searches, queries_per_topic, min_successful_queries, consecutive_failure_limit


async def _research_progress_commentary(
    env,
    ledger: ResearchLedger,
    *,
    phase: str,
) -> str:
    """Let the selected model narrate sparse, evidence-backed research transitions."""
    completed = [topic.title for topic in ledger.topics if topic.status == "completed"]
    remaining = [
        topic.title for topic in ledger.topics
        if topic.status not in {"completed", "skipped"}
    ]
    facts = (
        f"研究主题：{ledger.query or getattr(env, 'message', '')}\n"
        f"当前阶段：{phase}\n"
        f"已完成主题：{'、'.join(completed) or '暂无'}\n"
        f"待覆盖主题：{'、'.join(remaining) or '无'}\n"
        f"已检索 {ledger.search_calls} 次，获得 {len(ledger.unique_urls())} 个唯一来源，"
        f"覆盖 {len(ledger.unique_hosts())} 个独立站点。\n"
        f"覆盖门槛：{'已满足' if ledger.coverage_ready() else '尚未满足'}；"
        f"交叉验证：{'已满足' if ledger.cross_validated() else '尚未满足'}。"
    )
    prompt = (
        "你是正在执行深度研究的资深研究合作者。根据给出的真实研究台账，写 1–2 句"
        "面向用户的阶段更新。只在有意义的阶段转换时说话：先说一个已经确认的具体结果，"
        "再自然承接下一阶段或仍需补齐的缺口。不要逐条复述搜索动作，不要列清单，不要写标题，"
        "不要使用‘我先’‘我会先’‘让我先’‘现在’‘接下来’起句，不要声称台账之外的发现，"
        "不要暴露内部字段、协议或私有推理。只输出公开叙述正文。\n\n" + facts
    )
    return await generate_public_commentary(
        model=str(getattr(env, "resolved_model", "") or ""),
        api_key=str(getattr(env, "newapi_key", "") or ""),
        developer_prompt=prompt,
        user_prompt="根据这份真实台账写本阶段的公开研究更新。",
        max_output_tokens=180,
        max_chars=420,
        run_id=str(getattr(env, "run_id", "") or ""),
        thread_id=str(getattr(env, "thread_id", "") or ""),
        root_run_id=str(getattr(env, "root_run_id", "") or ""),
        purpose="research_commentary",
        purpose_detail=str(phase or "progress")[:120],
    )


def _citations_from_results(results: list, *, limit: int = 5) -> list[dict[str, Any]]:
    """Prefer scraped bodies; keep at most one citation per registrable host."""
    rows: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    ordered = sorted(
        [item for item in results if isinstance(item, dict)],
        key=lambda item: 0 if item.get("scraped") else 1,
    )
    for item in ordered:
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        host = registrable_host(url)
        if host and host in seen_hosts:
            continue
        if host:
            seen_hosts.add(host)
        rows.append({
            "type": "web",
            "title": str(item.get("title") or url)[:400],
            "url": url,
            # search_web(research_depth=True) has already read up to 4k chars. Keep enough of
            # that body for synthesis instead of collapsing every page back to a SERP snippet.
            "snippet": str(item.get("content") or item.get("snippet") or "")[:8_000],
            "scraped": bool(item.get("scraped")),
        })
        if len(rows) >= limit:
            break
    return rows


async def _ensure_topics(run_id: str, query: str) -> ResearchLedger:
    ledger = await seed_research_state(run_id, query)
    if ledger.topics:
        return ledger
    topics = default_topics(query or ledger.query)
    updated = ledger.model_copy(update={
        "topics": topics,
        "stage": ResearchStage.RESEARCHING,
        "updated_at": utc_now_iso(),
    })
    updated = advance_stage(updated)
    await _save_research_blob(run_id, updated)
    return updated


async def _follow_blocking_tool(
    started: str,
    work,
    progress_q: asyncio.Queue,
) -> AsyncIterator[Any]:
    """Flush `tool.started` before the blocking search/scrape, then live progress."""
    yield started
    async def _run() -> None:
        try:
            await progress_q.put(("done", await work()))
        except Exception as exc:  # noqa: BLE001
            logger.exception("research kernel blocking tool failed")
            await progress_q.put(("fail", exc))

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await progress_q.get()
            if isinstance(item, str):
                yield item
                continue
            yield item
            return
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


async def _search_once(
    env,
    *,
    query: str,
    topic_title: str,
) -> AsyncIterator[tuple[str | None, ResearchLedger | None, str | None]]:
    from app.services.knowledge import web_search_service

    channel = env.channel
    progress_q: asyncio.Queue = asyncio.Queue()

    async def _reading(item: dict) -> None:
        title = str(item.get("title") or item.get("url") or "网页")[:80]
        try:
            await progress_q.put(channel.tool_progress(
                "search_web",
                "reading",
                f"正在阅读 {title}",
                detail={"url": str(item.get("url") or ""), "title": title},
            ))
        except Exception:  # noqa: BLE001
            pass

    async def _work():
        return await web_search_service.search_web(
            query,
            research_depth=True,
            progress_cb=_reading,
            caller_user_id=str(getattr(env, "user_id", "") or ""),
            caller_run_id=str(env.run_id or ""),
            caller_thread_id=str(getattr(env, "thread_id", "") or ""),
            caller_root_run_id=str(
                getattr(env, "root_run_id", "") or getattr(env, "run_id", "") or ""
            ),
            caller_parent_logical_call_id=str(
                getattr(env, "parent_logical_call_id", "") or ""
            ),
            caller_execution_segment=str(
                getattr(env, "execution_segment", "") or ""
            ),
            caller_newapi_key=str(getattr(env, "newapi_key", "") or ""),
            request_scope=getattr(env, "research_web_scope", None),
        )

    result: dict | None = None
    async for item in _follow_blocking_tool(
        channel.tool_started("search_web", {"query": query}),
        _work,
        progress_q,
    ):
        if isinstance(item, str):
            yield item, None, None
            continue
        kind, payload = item
        if kind == "fail":
            error = str(payload or "检索失败")[:300]
            await record_search_attempt(env.run_id, query=query, error=error)
            yield channel.tool_failed("search_web", "检索失败"), None, "failed"
            return
        result = payload if isinstance(payload, dict) else {}
    if result is None:
        return
    results = [row for row in (result.get("results") or []) if isinstance(row, dict)]
    urls = [str(row.get("url") or "") for row in results if row.get("url")]
    preview = str(result.get("text") or "")
    error = str(result.get("error") or "")
    if error and not results:
        await record_search_attempt(env.run_id, query=query, error=error)
        yield channel.tool_failed("search_web", error[:200]), None, "failed"
        return
    await record_search_attempt(env.run_id, query=query)
    yield channel.tool_completed(
        "search_web",
        preview[:500] or f"搜索到 {len(results)} 个网页",
        {
            "action": {"operation": "search", "target": query[:240]},
            "count": len(results),
            "urls": urls,
            "read": result.get("scraped_pages") or [],
            "search": result.get("search_meta") or {},
            "topic": topic_title,
        },
    ), None, None
    citations = _citations_from_results(results)
    if citations:
        yield channel.citations(citations), None, None
    ledger = await ingest_tool_receipt(
        env.run_id,
        tool_name="search_web",
        meta={"urls": urls, "read": result.get("scraped_pages") or [],
              "search": result.get("search_meta") or {}},
        citations=citations,
        query=query,
    )
    yield None, ledger, "succeeded"


def _topics_as_plan(ledger: ResearchLedger) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active = next(
        (item for item in ledger.topics if item.status not in {"completed", "skipped"}),
        None,
    )
    for topic in ledger.topics:
        if topic.status == "completed":
            status = "completed"
        elif topic.status in {"in_progress", "failed"}:
            status = topic.status
        elif topic.status == "skipped":
            status = "skipped"
        elif not topic.detail and active is not None and topic.topic_id == active.topic_id:
            status = "in_progress"
        else:
            status = "pending"
        rows.append({
            "key": topic.topic_id,
            "title": topic.title,
            "status": status,
            "detail": topic.detail or "；".join(topic.queries[:2]),
        })
    return rows


def _issued_queries(ledger: ResearchLedger) -> set[str]:
    issued: set[str] = set()
    for source in ledger.sources:
        key = str(source.query or "").strip().lower()
        if key:
            issued.add(key)
    return issued


async def collect_coverage(env, ledger: ResearchLedger) -> AsyncIterator[str]:
    """Build the evidence floor, then let the main model judge whether more research is needed."""
    channel = env.channel
    run_id = env.run_id
    issued = _issued_queries(ledger)
    searches = 0
    checkpoint_emitted = False
    consecutive_failures = 0
    max_searches, query_cap, min_successful_queries, failure_limit = _kernel_policy()

    def _topic_floor_ready(current: ResearchLedger, topic) -> bool:
        return (
            current.topic_coverage_ready(topic)
            and len(current.successful_queries(topic_id=topic.topic_id)) >= min_successful_queries
        )

    for topic in list(ledger.topics):
        if topic.status == "skipped":
            continue
        current, _ = await _get_research_blob(run_id)
        if _topic_floor_ready(current, topic):
            await complete_topic(run_id, topic.topic_id)
            fresh, _ = await _get_research_blob(run_id)
            yield channel.task_plan_updated(_topics_as_plan(fresh))
            continue
        queries = list(topic.queries or rewrite_queries(topic.title, current.query or ledger.query))
        searched_this_topic = 0
        for query in queries[:query_cap]:
            key = query.strip().lower()
            if not key or key in issued:
                continue
            if searches >= max_searches:
                break
            fresh, _ = await _get_research_blob(run_id)
            if searched_this_topic and _topic_floor_ready(fresh, topic):
                break
            issued.add(key)
            searches += 1
            searched_this_topic += 1
            yield channel.research_progress({
                **progress_payload(fresh),
                "stage": ResearchStage.RESEARCHING.value,
                "topic": topic.title,
                "label": f"正在研究「{topic.title[:24]}」…",
            })
            yield channel.task_plan_updated(_topics_as_plan(fresh))
            attempt_status = None
            async for frame, updated, status in _search_once(
                env, query=query, topic_title=topic.title,
            ):
                if frame:
                    yield frame
                if updated is not None:
                    yield channel.research_progress(progress_payload(updated))
                if status:
                    attempt_status = status
            if attempt_status == "failed":
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= failure_limit:
                yield channel.research_progress({
                    **progress_payload(fresh),
                    "stage": ResearchStage.RESEARCHING.value,
                    "label": "搜索服务暂时不可用，已保留研究进度并准备重试…",
                })
                raise ResearchDependencyUnavailable("research_search_dependency_unavailable")

        current, _ = await _get_research_blob(run_id)
        if _topic_floor_ready(current, topic):
            await complete_topic(run_id, topic.topic_id)
            fresh, _ = await _get_research_blob(run_id)
            yield channel.task_plan_updated(_topics_as_plan(fresh))
            if not checkpoint_emitted:
                commentary = await _research_progress_commentary(
                    env, fresh, phase="首个研究主题已形成证据覆盖",
                )
                if commentary:
                    yield channel.message_commentary(commentary)
                    checkpoint_emitted = True

    fresh, _ = await _get_research_blob(run_id)
    if not fresh.cross_validated() and searches < max_searches:
        for query in rewrite_queries(fresh.query or str(getattr(env, "message", "") or "")):
            key = query.strip().lower()
            if not key or key in issued:
                continue
            if searches >= max_searches:
                break
            issued.add(key)
            searches += 1
            yield channel.research_progress({
                **progress_payload(fresh),
                "stage": ResearchStage.VERIFYING.value,
                "label": "正在交叉验证…",
            })
            attempt_status = None
            async for frame, updated, status in _search_once(
                env, query=query, topic_title="交叉验证",
            ):
                if frame:
                    yield frame
                if updated is not None:
                    yield channel.research_progress(progress_payload(updated))
                    fresh = updated
                if status:
                    attempt_status = status
            if attempt_status == "failed":
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= failure_limit:
                yield channel.research_progress({
                    **progress_payload(fresh),
                    "stage": ResearchStage.VERIFYING.value,
                    "label": "搜索服务暂时不可用，已保留研究进度并准备重试…",
                })
                raise ResearchDependencyUnavailable("research_search_dependency_unavailable")
            if fresh.cross_validated():
                break
    final, _ = await _get_research_blob(run_id)
    advanced = advance_stage(final)
    await _save_research_blob(run_id, advanced)
    yield channel.research_progress(progress_payload(advanced))
    yield channel.task_plan_updated(_topics_as_plan(advanced))


def _inject_evidence(env, ledger: ResearchLedger) -> None:
    guard = (_TEAM_SYNTHESIS_GUARD if getattr(env, "research_team_synthesis_only", False)
             else _PARTIAL_SYNTHESIS_GUARD if ledger.coverage_outcome == "partial" else _SYNTHESIS_GUARD)
    # Stage constraints have their own priority slot in the compiled/checkpointed
    # world state. Long source text must never erase the empty-tool contract.
    env.research_synthesis_constraints = guard
    brief = evidence_brief(ledger, body_budget=24_000)
    env.turn_guard_prompt = "\n".join(filter(None, [
        guard,
        brief,
        str(getattr(env, "turn_guard_prompt", "") or ""),
    ]))
    snippet_limit = max(300, min(
        int(getattr(settings, "RESEARCH_EVIDENCE_SNIPPET_CHARS", 1_000) or 1_000),
        2_000,
    ))
    env.research_citations = [
        {
            "type": "web",
            "title": item.title or item.url,
            "url": item.url,
            "snippet": item.snippet[:snippet_limit],
        }
        for item in synthesis_sources(ledger)
    ]


async def run(env) -> AsyncIterator[str]:
    from .budget import REPORT_RESERVE_SECONDS, ensure_budget, remaining

    await ensure_budget(env)
    seconds = remaining(env, reserve=REPORT_RESERVE_SECONDS)
    if seconds > 0:
        clock = asyncio.timeout(seconds)
        try:
            async with clock:
                async with aclosing(_prepare(env)) as source:
                    async for payload in source:
                        yield payload
        except TimeoutError:
            if not clock.expired():
                raise
    # Collection stops expanding at the target. Report generation and storage
    # retain shared transport timeouts and same-Run recovery, with no phase kill.
    async for payload in _deliver(env):
        yield payload


async def _run(env) -> AsyncIterator[str]:
    """Research turn: gather evidence once, then synthesize the report."""
    async for payload in _prepare(env):
        yield payload
    async for payload in _deliver(env):
        yield payload


async def _prepare(env) -> AsyncIterator[str]:

    query = str(getattr(env, "message", "") or "")
    run_id = env.run_id
    source_run_id = str(getattr(env, "resume_source_run_id", "") or "")
    inherited = await inherit_research_ledger(run_id, source_run_id) if source_run_id else None
    yield env.channel.research_progress({
        "stage": ResearchStage.RESEARCHING.value,
        "label": "正在制定研究计划…" if inherited is None else "接着上次的研究继续…",
    })
    ledger = inherited or await seed_research_state(run_id, query)
    yield env.channel.research_progress(progress_payload(ledger))
    if ledger.topics:
        yield env.channel.task_plan_updated(_topics_as_plan(ledger))
    if inherited is not None:
        commentary = await _research_progress_commentary(
            env, ledger, phase="恢复上次研究现场",
        )
        if commentary:
            yield env.channel.message_commentary(commentary)
    from .team import run_team
    async for payload in run_team(env, ledger):
        yield payload
    ledger, _ = await _get_research_blob(run_id)
    packed = await run_store.get_run_state(run_id)
    team = ((packed or {}).get("state") or {}).get("research_team")
    env.research_team_synthesis_only = isinstance(team, dict)
    # Legacy saved runs without a team can still recover through coverage.
    # A team already owns all evidence gathering and gap discussion. Even an
    # interrupted team's last snapshot must not reopen a second search workflow.
    if not env.research_team_synthesis_only and not ledger.coverage_ready():
        from app.services.knowledge.web_request_scope import WebRequestScope
        legacy_scope = WebRequestScope()
        env.research_web_scope = legacy_scope
        try:
            ledger = await _ensure_topics(run_id, query)
            async for payload in collect_coverage(env, ledger):
                yield payload
        finally:
            await legacy_scope.close()
            if getattr(env, "research_web_scope", None) is legacy_scope:
                delattr(env, "research_web_scope")
    final, _ = await _get_research_blob(run_id)
    final = advance_stage(final)
    assessments = list((team or {}).get("assessments", {}).values())
    quality_partial = bool((team or {}).get("qualityStop")) or bool(assessments and assessments[-1].get("action") != "ready")
    if final.coverage_ready() and final.cross_validated() and not quality_partial:
        final = await set_coverage_outcome(run_id, final, outcome="", reason_codes=[])
    else:
        final = await set_coverage_outcome(
            run_id,
            final,
            outcome="partial",
            reason_codes=coverage_reason_codes(final) + (["research_quality_incomplete"] if quality_partial else []),
        )
    commentary = "" if env.research_team_synthesis_only else await _research_progress_commentary(
        env,
        final,
        phase=(
            "最低证据覆盖已满足，转入缺口判断与综合"
            if final.coverage_outcome != "partial"
            else "覆盖预算已用完，基于现有证据补缺口或给出部分报告"
        ),
    )
    if commentary:
        yield env.channel.message_commentary(commentary)


async def _deliver(env) -> AsyncIterator[str]:
    from app.services.chat import main_tool_turn
    from app.services.agent_harness.public_errors import ResearchEvidenceMissing, ResearchSourcesUnavailable
    from .sources import has_usable_evidence, search_was_unavailable

    parent = await run_store.get_run_snapshot(env.run_id)
    if parent is None:
        raise RuntimeError("研究报告运行状态不可用")
    if parent.cancel_requested:
        raise asyncio.CancelledError()
    ledger, _ = await _get_research_blob(env.run_id)
    packed = await run_store.get_run_state(env.run_id)
    saved_team = ((packed or {}).get("state") or {}).get("research_team")
    if not has_usable_evidence(ledger):
        if search_was_unavailable(ledger, saved_team):
            raise ResearchSourcesUnavailable()
        raise ResearchEvidenceMissing()
    # Recovery restores the same evidence and continues only report work.
    env.research_team_synthesis_only = True
    if not getattr(env, "research_team_handoff_ready", False):
        from .team import inject_team_handoff
        if isinstance(saved_team, dict) and saved_team.get("goalRevision") == parent.goal_revision:
            inject_team_handoff(env, saved_team)
    _inject_evidence(env, ledger)
    async for payload in main_tool_turn.run_agent_turn(env):
        yield payload
