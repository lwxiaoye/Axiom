"""Research stage machine and evidence gates — no second runtime."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_harness.contracts import AgentMode, ObservationStatus, ToolObservation
from app.services.agent_harness.research.contracts import (
    MIN_SEARCH_CALLS,
    ResearchLedger,
    ResearchStage,
    ResearchTopic,
    SourceRecord,
)
from app.services.agent_harness.research.engine import (
    advance_stage,
    completion_reasons,
    progress_payload,
    stage_prompt,
)
from app.services.agent_harness.research.evidence import sources_from_receipt
from app.services.agent_harness.research.plan import rewrite_queries, topics_from_plan_steps


def _obs(name: str, *urls: str) -> ToolObservation:
    return ToolObservation(
        call_id=f"c-{name}-{urls[0][-4:] if urls else 'x'}",
        tool_name=name,
        status=ObservationStatus.SUCCEEDED,
        evidence_refs=[{"url": url} for url in urls],
    )


def test_rewrite_queries_adds_comparison_and_primary_angles():
    variants = rewrite_queries("折叠屏手机", "折叠屏手机")
    assert variants[0] == "折叠屏手机"
    assert any("评测" in item or "对比" in item for item in variants)
    assert any("官方" in item or "一手" in item for item in variants)
    assert any("风险" in item or "局限" in item for item in variants)
    assert len(variants) >= 3


def test_topics_from_plan_steps_attach_rewritten_queries():
    topics = topics_from_plan_steps(
        [{"key": "a", "title": "国内评测"}, {"key": "b", "title": "海外一手"}],
        query="折叠屏",
    )
    assert 2 <= len(topics) <= 8
    assert all(item.queries for item in topics)


def test_advance_stage_cannot_skip_coverage_or_cross_validation():
    empty = advance_stage(ResearchLedger(query="q"))
    assert empty.stage is ResearchStage.PLANNING

    planned = ResearchLedger(
        query="q",
        topics=[ResearchTopic(topic_id="t1", title="主题一", queries=["q"])],
        search_calls=0,
    )
    assert advance_stage(planned).stage is ResearchStage.RESEARCHING

    thin = planned.model_copy(update={
        "search_calls": MIN_SEARCH_CALLS,
        "sources": [SourceRecord(url="https://a.example/1", topic_id="t1")],
    })
    assert advance_stage(thin).stage is ResearchStage.RESEARCHING

    covered = planned.model_copy(update={
        "search_calls": MIN_SEARCH_CALLS,
        "sources": [
            SourceRecord(
                url="https://a.example/1", topic_id="t1", query="q",
                snippet="已读取的一手正文 A", scraped=True,
            ),
            SourceRecord(
                url="https://b.example/2", topic_id="t1", query="q",
                snippet="已读取的独立正文 B", scraped=True,
            ),
            SourceRecord(
                url="https://c.example/3", topic_id="t1", query="q",
                snippet="用于交叉验证的正文 C", scraped=True,
            ),
        ],
    })
    advanced = advance_stage(covered)
    assert advanced.stage in {ResearchStage.VERIFYING, ResearchStage.SYNTHESIZING}
    assert advanced.cross_validated()
    assert advanced.stage is ResearchStage.SYNTHESIZING


def test_same_publisher_subdomains_do_not_satisfy_topic_coverage():
    from app.services.agent_harness.research.contracts import canonical_url, registrable_host

    assert canonical_url("https://WWW.Sina.com.cn/a/?utm_source=x#frag") == "https://sina.com.cn/a"
    assert registrable_host("https://news.sina.com.cn/a") == "sina.com.cn"
    topic = ResearchTopic(topic_id="t1", title="主题一", queries=["q"])
    same_publisher = ResearchLedger(
        query="q",
        topics=[topic],
        search_calls=MIN_SEARCH_CALLS,
        sources=[
            SourceRecord(url="https://news.sina.com.cn/a", topic_id="t1"),
            SourceRecord(url="https://sina.com.cn/b?utm_source=x", topic_id="t1"),
        ],
    )
    assert same_publisher.unique_hosts(topic_id="t1") == {"sina.com.cn"}
    assert same_publisher.topic_has_min_sources(topic) is False

    two_urls_same_host = ResearchLedger(
        query="q",
        topics=[topic],
        search_calls=MIN_SEARCH_CALLS,
        sources=[
            SourceRecord(url="https://a.example/1", topic_id="t1"),
            SourceRecord(url="https://a.example/2", topic_id="t1"),
        ],
    )
    assert two_urls_same_host.topic_has_min_sources(topic) is False


def test_research_kernel_ledger_unblocks_completion_without_agent_tool_calls():
    from app.services.agent_harness.completion import CompletionClaim, CompletionVerifier
    from app.services.agent_harness.contracts import RunPhase, RunSnapshot

    run = RunSnapshot(
        run_id="r-research",
        thread_id="t1",
        user_id="u1",
        agent_mode=AgentMode.RESEARCH,
        phase=RunPhase.VERIFYING,
        state_version=1,
        goal_revision=0,
        plan_version=0,
        event_cursor=0,
    )
    blocked = CompletionVerifier().verify(
        run,
        CompletionClaim(summary="# T\n\n## 执行摘要\nok", requires_citations=True),
        (),
    )
    assert blocked.continuation_required is True
    assert "research_search_coverage_unmet" in blocked.reason_codes

    ledger = ResearchLedger(
        search_calls=4,
        sources=[
            SourceRecord(url="https://a.example/1", title="A"),
            SourceRecord(url="https://b.example/2", title="B"),
        ],
    )
    accepted = CompletionVerifier().verify(
        run,
        CompletionClaim(summary="# T\n\n## 执行摘要\nok", requires_citations=True),
        (),
        research_ledger=ledger,
    )
    assert accepted.accepted is True
    assert accepted.terminal is True
    assert accepted.resolution == "completed"


def test_fold_research_coverage_uses_sources_without_artifact_receipt():
    from app.services.agent_harness.completion import fold_research_coverage

    folded = fold_research_coverage(
        [],
        {"research": {
            "search_calls": 3,
            "sources": [
                {"url": "https://a.example/1", "title": "A"},
                {"url": "https://b.example/2", "title": "B"},
            ],
            "report_file_ids": ["fid-html"],
        }},
        run_id="r1",
    )
    assert folded
    assert folded[-1].artifact_refs == []
    assert folded[-1].evidence_refs
    assert folded[-1].evidence_refs[0]["url"] == "https://a.example/1"


def test_research_terminal_does_not_auto_persist_document_artifact():
    app_root = Path(__file__).resolve().parents[1] / "app" / "services"
    main_turn = (app_root / "chat" / "main_tool_turn.py").read_text(encoding="utf-8")
    finalizer = (app_root / "chat" / "turn_finalizer.py").read_text(encoding="utf-8")
    engine = (app_root / "agent_harness" / "research" / "engine.py").read_text(encoding="utf-8")

    assert "persist_research_report" not in main_turn
    assert "persist_research_report" not in finalizer
    assert "def persist_research_report" not in engine


def test_completion_reasons_block_thin_search_and_single_host():
    run = SimpleNamespace(agent_mode=AgentMode.RESEARCH)
    reasons = completion_reasons(run, tuple(), ResearchLedger())
    assert "research_search_coverage_unmet" in reasons

    observations = (
        _obs("search_web", "https://a.example/1"),
        _obs("search_web", "https://a.example/2"),
        _obs("deep_read", "https://a.example/3"),
    )
    same_host = completion_reasons(run, observations, ResearchLedger(search_calls=3))
    assert "research_cross_validation_unmet" in same_host

    mixed = (
        _obs("search_web", "https://a.example/1"),
        _obs("search_web", "https://b.example/2"),
        _obs("deep_read", "https://c.example/3"),
    )
    assert completion_reasons(run, mixed, ResearchLedger(search_calls=3)) == []


def test_stage_prompt_and_progress_payload_describe_current_topic():
    ledger = ResearchLedger(
        stage=ResearchStage.RESEARCHING,
        topics=[ResearchTopic(topic_id="t1", title="国内评测", queries=["q1"])],
        search_calls=1,
    )
    prompt = stage_prompt(ledger)
    assert "当前阶段：researching" in prompt
    assert "search_web" in prompt
    payload = progress_payload(ledger)
    assert payload["stage"] == "researching"
    assert payload["topic"] == "国内评测"
    assert payload["topicIndex"] == 1
    assert "正在研究" in payload["label"]


def test_is_user_clarification_matches_scope_question():
    from app.services.agent_harness.research.engine import is_user_clarification

    ask = (
        "在正式开始系统检索前，我想确认一个会直接影响研究方向的问题："
        "你想研究威少生涯数据时，是否需要与特定球员做横向对比，"
        "还是纯粹聚焦威少本人的生涯数据梳理？"
    )
    assert is_user_clarification(ask)
    assert not is_user_clarification("威少生涯场均约 22 分 7 篮板 8 助攻。")
    long_report = "威少生涯数据综述。" * 80 + "这是否意味着他的影响力被低估？"
    assert not is_user_clarification(long_report)


def test_research_kernel_searches_without_deep_read():
    import inspect
    from app.services.agent_harness.research import kernel

    src = inspect.getsource(kernel)
    assert "_deep_read_once" not in src
    assert "MAX_DEEP_READS" not in src
    assert "MAX_KERNEL_SEARCHES = 24" in src
    assert "RESEARCH_COVERAGE_MAX_SEARCHES" in src
    assert "只有你基于具体证据判断信息已经足够时才停止检索" in src
    assert "inherit_research_ledger" in src
    assert 'phase="恢复上次研究现场"' in src
    assert "generate_public_commentary" in src
    assert "task_plan_updated" in src


def test_research_web_tools_omit_deep_read():
    from app.services.chat.tools.web import build_web_tools

    names = [item.name for item in build_web_tools(research_profile=True)]
    assert "search_web" in names
    assert "deep_read" not in names


def test_stage_prompt_does_not_ask_for_deep_read():
    ledger = ResearchLedger(stage=ResearchStage.RESEARCHING, query="q")
    prompt = stage_prompt(ledger)
    assert "search_web" in prompt
    assert "deep_read" not in prompt


def test_receipt_duplicate_keeps_scraped_body_instead_of_empty_read_marker():
    sources = sources_from_receipt(
        tool_name="search_web",
        meta={
            "read": [{"url": "https://a.example/report", "title": "阅读进度"}],
            "urls": ["https://a.example/report"],
        },
        citations=[{
            "url": "https://a.example/report?utm_source=test",
            "title": "正式报告",
            "snippet": "这是抓取后保留下来的完整证据正文" * 20,
            "scraped": True,
        }],
        query="q",
        topic_id="t1",
    )

    assert len(sources) == 1
    assert sources[0].title == "正式报告"
    assert "完整证据正文" in sources[0].snippet
    assert sources[0].scraped is True
    assert sources[0].query == "q"
    assert sources[0].topic_id == "t1"


def test_research_search_result_nudge_is_gap_driven_not_count_driven():
    from app.services.chat.tools import web

    source = Path(web.__file__).read_text(encoding="utf-8")
    research_branch = source.split("if research_profile:", 1)[1].split("else:", 1)[0]
    assert "一手来源、反例、时效证据、可比口径或独立交叉验证" in source
    assert "搜索次数不是完成条件" in source
    assert "不要再调用 search_web" not in research_branch
