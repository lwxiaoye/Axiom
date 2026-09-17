"""Team reports bypass legacy coverage; non-team recovery retains its gates."""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.agent_harness.research.contracts import ResearchLedger
from app.services.agent_harness.research.engine import evidence_brief
from app.services.agent_harness.research.kernel import (
    ResearchDependencyUnavailable,
    _run as run_research_turn,  # Coverage core; durable outer deadline has its own suite.
)
from app.services.agent_harness.research.plan import default_topics


@pytest.fixture(autouse=True)
def isolate_coverage_kernel(monkeypatch):
    # This suite exercises coverage/fallback. Real member orchestration has its own suite.
    async def members(env, ledger):
        if False:
            yield ""
    monkeypatch.setattr("app.services.agent_harness.research.team.run_team", members)
    async def snapshot(_):
        return SimpleNamespace(cancel_requested=False, goal_revision=1)
    monkeypatch.setattr("app.services.agent_harness.run_store.get_run_snapshot", snapshot)


class _Channel:
    def __init__(self):
        self.events = []

    def tool_started(self, name, args=None):
        self.events.append(("started", name, args or {}))
        return f"started:{name}"

    def tool_progress(self, name, stage="", label="", elapsed_ms=0, detail=None, heartbeat=False):
        self.events.append(("progress_tool", name, label, detail or {}))
        return f"progress:{name}"

    def tool_completed(self, name, preview="", meta=None):
        self.events.append(("completed", name, meta or {}))
        return f"completed:{name}"

    def tool_failed(self, name, error, meta=None):
        self.events.append(("failed", name, error))
        return f"failed:{name}"

    def citations(self, sources):
        self.events.append(("citations", sources))
        return "citations"

    def research_progress(self, payload=None):
        self.events.append(("progress", dict(payload or {})))
        return "progress"

    def task_plan_updated(self, steps=None):
        self.events.append(("plan", list(steps or [])))
        return "plan"

    def message_commentary(self, text=""):
        self.events.append(("commentary", text))
        return "commentary"


@pytest.mark.asyncio
async def test_kernel_searches_before_synthesis(monkeypatch):
    state: dict = {"research": None}

    async def get_run_state(run_id):  # noqa: ARG001
        return {"state": dict(state)}

    async def patch_run_state(run_id, patch):  # noqa: ARG001
        state.update(patch)

    searches: list[str] = []

    async def fake_search(query, **kwargs):  # noqa: ARG001
        searches.append(query)
        n = len(searches)
        return {
            "enabled": True,
            "results": [
                {
                    "url": f"https://a{n}.example/p",
                    "title": f"来源 A{n}",
                    "content": "正文" * 40,
                    "scraped": True,
                },
                {
                    "url": f"https://b{n}.example/p",
                    "title": f"来源 B{n}",
                    "content": "评测" * 40,
                    "scraped": True,
                },
            ],
            "text": "搜索到 2 个网页",
            "scraped_pages": [
                {"url": f"https://a{n}.example/p", "title": f"来源 A{n}"},
            ],
            "images": [],
            "error": "",
        }

    synthesis = {"called": False, "prompt": ""}

    async def fake_turn(env):
        synthesis["called"] = True
        synthesis["prompt"] = str(getattr(env, "turn_guard_prompt", "") or "")
        yield "synth"

    monkeypatch.setattr(
        "app.services.agent_harness.run_store.get_run_state", get_run_state,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.run_store.patch_run_state", patch_run_state,
    )
    monkeypatch.setattr(
        "app.services.knowledge.web_search_service.search_web", fake_search,
    )
    monkeypatch.setattr(
        "app.services.chat.main_tool_turn.run_agent_turn", fake_turn,
    )

    env = SimpleNamespace(
        channel=_Channel(),
        run_id="run-research-1",
        user_id="u1",
        newapi_key="",
        message="折叠屏手机怎么选",
        turn_guard_prompt="【目标契约】研究报告",
        fallback_plain=False,
    )
    frames = [frame async for frame in run_research_turn(env)]
    assert searches, "kernel must issue search_web itself"
    assert any(event[0] == "started" and event[1] == "search_web" for event in env.channel.events)
    assert synthesis["called"] is True
    assert "平台已完成的分主题检索台账" in synthesis["prompt"]
    assert "仅摘要" in synthesis["prompt"] or "独立站点" in synthesis["prompt"]
    assert "【目标契约】" in synthesis["prompt"]
    assert "synth" in frames
    ledger = ResearchLedger.model_validate(state["research"])
    assert ledger.search_calls >= 3
    assert len(ledger.unique_hosts()) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("team_stage", ["synthesizing", "reviewing"])
async def test_finished_team_goes_directly_to_report_without_second_search(monkeypatch, team_stage):
    state: dict = {"research": None}
    order: list[str] = []

    async def get_run_state(run_id):  # noqa: ARG001
        return {"state": dict(state)}

    async def patch_run_state(run_id, patch):  # noqa: ARG001
        state.update(patch)

    async def members(env, ledger):  # noqa: ARG001
        order.append("team")
        state["research_team"] = {"id": "team", "stage": team_stage}
        from app.services.agent_harness.research.contracts import SourceRecord
        state["research"] = ledger.model_copy(update={"sources": [SourceRecord(
            url="https://example.org/review", snippet="团队已取得的评测正文", scraped=True,
        )]}).to_state()
        if False:
            yield ""

    async def fake_search(query, **kwargs):  # noqa: ARG001
        order.append("search")
        n = order.count("search")
        return {
            "enabled": True,
            "results": [
                {
                    "url": f"https://a{n}.example/p",
                    "title": f"来源 A{n}",
                    "content": "正文" * 40,
                    "scraped": True,
                },
                {
                    "url": f"https://b{n}.example/p",
                    "title": f"来源 B{n}",
                    "content": "评测" * 40,
                    "scraped": True,
                },
            ],
            "text": "搜索到 2 个网页",
            "scraped_pages": [{"url": f"https://a{n}.example/p", "title": f"来源 A{n}"}],
            "images": [],
            "error": "",
        }

    async def fake_turn(env):  # noqa: ARG001
        order.append("synth")
        assert env.research_team_synthesis_only is True
        assert "不要再调用网络搜索" in env.turn_guard_prompt
        assert "不得声称研究已经完整完成" in env.turn_guard_prompt
        yield "synth"

    async def no_commentary(*args, **kwargs):  # noqa: ARG001
        return ""

    monkeypatch.setattr("app.services.agent_harness.run_store.get_run_state", get_run_state)
    monkeypatch.setattr("app.services.agent_harness.run_store.patch_run_state", patch_run_state)
    monkeypatch.setattr("app.services.agent_harness.research.team.run_team", members)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", fake_search)
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", fake_turn)
    monkeypatch.setattr(
        "app.services.agent_harness.research.kernel._research_progress_commentary",
        no_commentary,
    )

    env = SimpleNamespace(
        channel=_Channel(),
        run_id="run-research-team-order",
        user_id="u1",
        newapi_key="",
        message="折叠屏手机怎么选",
        turn_guard_prompt="",
        fallback_plain=False,
    )
    frames = [frame async for frame in run_research_turn(env)]
    assert order[0] == "team"
    assert order == ["team", "synth"]
    assert "synth" in frames


@pytest.mark.asyncio
async def test_kernel_flushes_search_started_before_search_returns(monkeypatch):
    state: dict = {"research": None}

    async def get_run_state(run_id):  # noqa: ARG001
        return {"state": dict(state)}

    async def patch_run_state(run_id, patch):  # noqa: ARG001
        state.update(patch)

    entered = {"n": 0}
    release = asyncio.Event()

    async def fake_search(query, **kwargs):  # noqa: ARG001
        entered["n"] += 1
        await release.wait()
        return {
            "enabled": True,
            "results": [{
                "url": "https://a.example/p",
                "title": "来源 A",
                "content": "正文" * 40,
                "scraped": True,
            }],
            "text": "搜索到 1 个网页",
            "scraped_pages": [],
            "images": [],
            "error": "",
        }

    async def fake_turn(env):  # noqa: ARG001
        yield "synth"

    monkeypatch.setattr("app.services.agent_harness.run_store.get_run_state", get_run_state)
    monkeypatch.setattr("app.services.agent_harness.run_store.patch_run_state", patch_run_state)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", fake_search)
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", fake_turn)

    env = SimpleNamespace(
        channel=_Channel(),
        run_id="run-research-flush",
        user_id="u1",
        newapi_key="",
        message="国产模型与美国模型差距",
        turn_guard_prompt="",
        fallback_plain=False,
    )
    agen = run_research_turn(env)
    saw_started = False
    try:
        async for frame in agen:
            if frame == "started:search_web":
                assert entered["n"] == 0, "tool.started must flush before search_web returns"
                saw_started = True
                break
    finally:
        await agen.aclose()
        release.set()
    assert saw_started


@pytest.mark.asyncio
async def test_kernel_preserves_partial_evidence_and_does_not_claim_full_coverage(monkeypatch):
    state: dict = {"research": None}

    async def get_run_state(run_id):  # noqa: ARG001
        return {"state": dict(state)}

    async def patch_run_state(run_id, patch):  # noqa: ARG001
        state.update(patch)

    async def fake_search(query, **kwargs):  # noqa: ARG001
        return {
            "enabled": True,
            "results": [{
                "url": "https://a.example/report",
                "title": "来源 A",
                "content": "抓取后的完整正文" * 180,
                "scraped": True,
            }],
            "text": "搜索到 1 个网页",
            "scraped_pages": [{"url": "https://a.example/report", "title": "来源 A"}],
            "images": [],
            "error": "",
        }

    observed: dict = {}

    async def fake_turn(env):
        observed["prompt"] = str(env.turn_guard_prompt)
        observed["citations"] = list(env.research_citations)
        yield "partial-synth"

    async def no_commentary(*args, **kwargs):  # noqa: ARG001
        return ""

    monkeypatch.setattr("app.services.agent_harness.run_store.get_run_state", get_run_state)
    monkeypatch.setattr("app.services.agent_harness.run_store.patch_run_state", patch_run_state)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", fake_search)
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", fake_turn)
    monkeypatch.setattr(
        "app.services.agent_harness.research.kernel._research_progress_commentary",
        no_commentary,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.research.kernel._kernel_policy",
        lambda: (1, 1, 1, 2),
    )

    env = SimpleNamespace(
        channel=_Channel(), run_id="run-research-partial", user_id="u1",
        newapi_key="", message="复杂研究题", turn_guard_prompt="", fallback_plain=False,
    )
    frames = [frame async for frame in run_research_turn(env)]

    ledger = ResearchLedger.model_validate(state["research"])
    assert ledger.coverage_outcome == "partial"
    assert "不得声称研究已经完整完成" in observed["prompt"]
    assert observed["citations"][0]["url"] == "https://a.example/report"
    assert len(observed["citations"][0]["snippet"]) >= 900
    assert "partial-synth" in frames


@pytest.mark.asyncio
async def test_kernel_repeated_search_dependency_failure_waits_for_same_run_recovery(monkeypatch):
    state: dict = {"research": None}

    async def get_run_state(run_id):  # noqa: ARG001
        return {"state": dict(state)}

    async def patch_run_state(run_id, patch):  # noqa: ARG001
        state.update(patch)

    attempts: list[str] = []

    async def fake_search(query, **kwargs):  # noqa: ARG001
        attempts.append(query)
        return {
            "enabled": True,
            "results": [],
            "text": "搜索服务不可用",
            "scraped_pages": [],
            "images": [],
            "error": "upstream timeout",
        }

    synthesis = {"called": False}

    async def fake_turn(env):  # noqa: ARG001
        synthesis["called"] = True
        yield "must-not-run"

    monkeypatch.setattr("app.services.agent_harness.run_store.get_run_state", get_run_state)
    monkeypatch.setattr("app.services.agent_harness.run_store.patch_run_state", patch_run_state)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", fake_search)
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", fake_turn)
    monkeypatch.setattr(
        "app.services.agent_harness.research.kernel._kernel_policy",
        lambda: (8, 6, 1, 2),
    )

    env = SimpleNamespace(
        channel=_Channel(), run_id="run-research-failed", user_id="u1",
        newapi_key="", message="复杂研究题", turn_guard_prompt="", fallback_plain=False,
    )
    with pytest.raises(ResearchDependencyUnavailable):
        _ = [frame async for frame in run_research_turn(env)]

    ledger = ResearchLedger.model_validate(state["research"])
    assert len(attempts) == 2
    assert ledger.search_failures == 2
    assert ledger.last_search_error == "upstream timeout"
    assert synthesis["called"] is False
    assert any(
        event[0] == "progress" and "准备重试" in str(event[1].get("label"))
        for event in env.channel.events
    )


def test_default_topics_cover_four_research_angles():
    topics = default_topics("折叠屏手机怎么选")
    assert len(topics) == 4
    titles = " ".join(item.title for item in topics)
    assert "定义" in titles
    assert "评测" in titles or "对比" in titles
    assert all(item.queries for item in topics)


def test_evidence_brief_numbers_sources():
    from app.services.agent_harness.research.contracts import SourceRecord

    brief = evidence_brief(ResearchLedger(
        query="q",
        search_calls=3,
        sources=[
            SourceRecord(url="https://a.example/1", title="评测 A", snippet="铰链更稳"),
            SourceRecord(url="https://b.example/2", title="官方 B"),
        ],
    ))
    assert "[1] 评测 A" in brief
    assert "https://a.example/1" in brief
    assert "[2] 官方 B" in brief
