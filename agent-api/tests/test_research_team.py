import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app.services.agent_harness import run_store, model_driver
from app.services.agent_harness.contracts import RunSnapshot
from app.services.agent_harness.research import team
from app.services.agent_harness.research.contracts import ResearchLedger
from app.services.agent_harness.research.plan import default_topics


def test_search_result_cards_are_bounded_and_do_not_publish_internal_fields():
    rows = [{"url": "https://example.com/doc", "title": "Source", "snippet": "s" * 500,
             "content": "private full body", "token": "secret"},
            {"url": "javascript:alert(1)"}, {"url": "https://user:password@example.com/"}]
    cards = team.public_search_results(rows)
    assert cards == [{"url": "https://example.com/doc", "title": "Source", "snippet": "s" * 280}]
    assert len(team.public_search_results([rows[0]] * 50)) == 30


@pytest.mark.parametrize("facts, expected", [
    ({"draftMaterials": {"findings": {"material": "已保存但尚未交付的材料"}}}, "已保存研究材料；尚无可核验来源。"),
    ({"findings": "已查证发现", "materials": {"findings": "完整材料"}}, "已提交研究材料；尚无可核验来源。"),
    ({"searches": [{"status": "succeeded", "count": 1}]}, "已找到相关网页。"),
])
def test_incomplete_member_retains_partial_handoff_without_claiming_success(facts, expected):
    member = {"status": "failed", "error": "成员执行中断", **copy.deepcopy(facts)}
    team.settle_incomplete_member(member)
    assert member["status"] == "partial"
    assert "error" not in member
    assert member["note"] == expected
    assert all(member[key] == value for key, value in facts.items())


def test_member_contribution_counts_distinct_pages_without_claiming_read_or_review():
    member = {"searches": [
        {"status": "succeeded", "count": 2, "results": [
            {"url": "https://www.example.com/doc/?utm_source=search#intro"},
            {"url": "https://example.org/other"},
        ]},
        {"status": "succeeded", "count": 1, "results": [{"url": "https://example.com/doc"}]},
        {"status": "failed", "count": 8},
    ], "materials": {"findings": "不能在公开快照展示的完整材料"}}
    team.settle_incomplete_member(member)
    assert member["status"] == "partial"
    assert member["note"] == "已找到 2 个网页；已提交研究材料。"


def test_partial_snapshot_replaces_old_boilerplate_without_mutating_saved_facts():
    member = {"id": "m1", "role": "researcher", "status": "partial", "note": "部分完成：旧说明",
              "draftMaterials": {"findings": {"material": "private material"}},
              "searches": [{"id": "s1", "query": "官方来源", "status": "succeeded", "count": 3,
                            "results": [{"url": "https://example.com/doc"}]}]}
    before = copy.deepcopy(member)
    public = team.public_snapshot({"id": "t1", "version": 1, "members": [member]})
    assert public["members"][0]["note"] == "已找到相关网页；已保存研究材料。"
    assert "private material" not in json.dumps(public)
    assert member == before


def test_incomplete_member_with_no_material_remains_failed():
    member = {"status": "researching", "searches": [{"status": "failed", "count": 0}]}
    team.settle_incomplete_member(member)
    assert member["status"] == "failed"
    assert "note" not in member
    assert "未取得可用材料" in member["error"]


def test_private_search_provider_trace_is_bounded_and_allowlisted():
    assert team._search_provider_meta({
        "mode": "research_hybrid",
        "attempted": ["searxng", "deepseek-official", "unknown", "searxng"],
        "providers": ["deepseek-official"],
        "failures": {"searxng": "engine_captcha", "tavily": "secret", "unknown": "secret"},
        "providerPayload": {"secret": True},
    }) == {
        "searchMode": "research_hybrid",
        "providersAttempted": ["searxng", "deepseek-official"],
        "providersUsed": ["deepseek-official"],
        "providerFailures": {"searxng": "engine_captcha", "tavily": "provider_error"},
    }


@pytest.fixture
def runtime(monkeypatch):
    ledger = ResearchLedger(query="研究可公开验证的事实", topics=default_topics("研究可公开验证的事实"))
    state = {"goal_revision": 1, "research": ledger.to_state(), "loop_checkpoint": {"root": True}}
    parent = RunSnapshot(run_id="r1", thread_id="t1", user_id="u1", agent_mode="research",
                         phase="executing", goal_revision=1, state_version=1, plan_version=0,
                         event_cursor=0, execution_profile={"id": "interactive"})
    events = []

    async def get_state(run_id):
        return {"state": copy.deepcopy(state)}

    async def patch(run_id, value):
        state.update(copy.deepcopy(value))
        return {"state": copy.deepcopy(state)}

    async def snapshot(run_id):
        return parent

    def publish(payload):
        events.append(copy.deepcopy(payload))
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(run_store, "get_run_state", get_state)
    monkeypatch.setattr(run_store, "patch_run_state", patch)
    monkeypatch.setattr(run_store, "get_run_snapshot", snapshot)
    async def keep_existing_plan(runtime, *, revision):
        pass
    monkeypatch.setattr("app.services.agent_harness.research.planning.coordinate_plan", keep_existing_plan)
    async def ready(runtime, round_index):
        return {"action": "ready"}
    monkeypatch.setattr(team, "assess", ready)
    env = SimpleNamespace(run_id="r1", user_id="u1", thread_id="t1", message=ledger.query,
                          resolved_model="test", newapi_key="", turn_guard_prompt="原有报告规则",
                          channel=SimpleNamespace(research_team=publish, citations=publish,
                                                  research_progress=publish, task_plan_updated=publish))
    return SimpleNamespace(state=state, parent=parent, events=events, env=env, ledger=ledger)


@pytest.mark.asyncio
async def test_team_shares_query_but_keeps_individual_authorized_receipts(runtime, monkeypatch):
    calls = []
    async def search(query, **kwargs):
        calls.append(query)
        await asyncio.sleep(0.01)
        return {
            "results": [{"url": "https://example.org/fact", "content": "公开摘要", "scraped": False}],
            "search_meta": {
                "mode": "research_hybrid",
                "attempted": ["searxng", "deepseek-official"],
                "providers": ["searxng", "deepseek-official"],
                "failures": {},
            },
        }
    async def driver(**kwargs):
        if kwargs["gateway"]["audit_scope"].endswith("findings"):
            data = json.loads(kwargs["user_input"])
            receipt = await kwargs["tools"][0].observe({"query": "same question", "topic_id": data["研究主题"][0]["id"]})
            assert receipt.status == "succeeded"
            assert json.loads(receipt.model_content)["source_quality"][0]["full_text_read"] is False
        yield {"type": "final", "answer": "仅得到摘要，全文未核验。"}
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    monkeypatch.setattr(team, "drive_model", driver)
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    searches = [a for a in runtime.state["research_team"]["activity"] if a["kind"] == "search"]
    assert calls == ["same question"]
    assert len(searches) == 3
    assert sum(a["cacheHit"] for a in searches) == 2
    assert all(a["snippetOnly"] for a in searches)
    assert all(a["providersUsed"] == ["searxng", "deepseek-official"] for a in searches)
    public = team.public_snapshot(runtime.state["research_team"])
    assert all("providersUsed" not in row for row in public["activity"])


@pytest.mark.asyncio
async def test_team_failure_projects_safe_reason_instead_of_generic_retry(runtime, monkeypatch):
    async def search(query, **kwargs):
        return {
            "results": [], "error": "secret upstream detail", "error_code": "engine_captcha",
            "search_meta": {
                "mode": "research_hybrid",
                "attempted": ["searxng", "deepseek-official"],
                "providers": [],
                "failures": {"searxng": "engine_captcha", "deepseek-official": "missing_structured_result"},
            },
        }
    async def driver(**kwargs):
        if kwargs["gateway"]["audit_scope"].endswith("findings"):
            data = json.loads(kwargs["user_input"])
            receipt = await kwargs["tools"][0].observe({"query": "same question", "topic_id": data["研究主题"][0]["id"]})
            assert receipt.status == "failed"
            assert "engine_captcha" in receipt.model_content
            assert "secret" not in receipt.model_content
        yield {"type": "final", "answer": "当前搜索服务故障，不能下没有资料的结论。"}
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    monkeypatch.setattr(team, "drive_model", driver)
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    public = team.public_snapshot(runtime.state["research_team"])
    searches = [a for a in public["activity"] if a["kind"] == "search"]
    assert len(searches) == 4  # shared failed query receives one bounded recovery attempt
    assert all(a["errorCode"] == "engine_captcha" and "验证码" in a["error"] for a in searches)
    assert all("providerFailures" not in a for a in searches)
    assert "secret" not in json.dumps(public)


@pytest.mark.asyncio
async def test_parallel_members_share_evidence_and_resume_without_second_full_review(runtime, monkeypatch):
    active = 0
    peak = 0
    entered = asyncio.Event()
    calls = []
    reviews = []

    async def search(query, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 3:
            entered.set()
        await asyncio.wait_for(entered.wait(), 2)
        active -= 1
        return {"results": [{"url": f"https://{query}.example.org/fact", "content": "可核验正文", "scraped": True}]}

    async def driver(**kwargs):
        scope = kwargs["gateway"]["audit_scope"]
        calls.append(scope)
        assert kwargs["gateway"]["run_id"] == ""
        assert kwargs["gateway"]["audit_run_id"] == "r1"
        from app.services.agent_harness.model_usage_audit import MODEL_CALL_PURPOSES
        assert kwargs["gateway"]["audit_purpose"] in MODEL_CALL_PURPOSES
        assert kwargs.get("history") in (None, [])
        assert [t.name for t in kwargs["tools"]] == ["search_web", "share_research_update", "read_research_source", "submit_research_findings"]
        await kwargs["gateway"]["checkpoint_sink"]([{"role": "user", "content": "private cursor"}], step=1)
        data = json.loads(kwargs["user_input"])
        if scope.endswith("findings"):
            query = scope.split("|")[-2].split("_")[-1]
            shared = await kwargs["tools"][1].observe({"message": f"我正在核对 {query} 的来源"})
            assert shared.status == "succeeded"
            receipt = await kwargs["tools"][0].observe({"query": query, "topic_id": data["研究主题"][0]["id"]})
            assert receipt.status == "succeeded"
            updates = json.loads(receipt.model_content)["team_updates"]
            assert len(updates) >= 2
            assert all(item["memberId"] != scope.split("|")[-2] for item in updates)
            yield {"type": "final", "answer": f"已查证 {query} https://{query}.example.org/fact"}
        else:
            peers = json.loads(data["队友发现"])
            reviews.append(peers)
            assert len(peers) == 2
            assert all("已查证" in p["findings"] for p in peers)
            yield {"type": "final", "answer": "已经对照队友发现；局限仍需在报告注明。"}

    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    monkeypatch.setattr(team, "drive_model", driver)
    frames = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert frames and peak == 3 and len(calls) == 3 and not reviews
    assert runtime.state["loop_checkpoint"] == {"root": True}
    blob = runtime.state["research_team"]
    for item in blob["activity"]:
        if item["kind"] == "search" and item["status"] == "succeeded":
            assert len(item["results"]) == item["count"]
            assert item["results"][0]["url"].endswith('/fact')
    projected = team.public_snapshot(blob)
    assert [item.get("results") for item in projected["activity"]] == [item.get("results") for item in blob["activity"]]
    assert len({m["name"] for m in blob["members"]}) == 3
    assert all(m["name"] in team.MEMBER_NAMES for m in blob["members"])
    assert all(m["status"] == "completed" for m in blob["members"])
    assert runtime.state["research"]["search_calls"] == 3
    assert "已查证" in runtime.env.turn_guard_prompt
    assert "checkpoint" not in json.dumps(runtime.events)
    assert "private cursor" not in json.dumps(runtime.events)
    versions = [e["version"] for e in runtime.events if isinstance(e, dict) and "version" in e]
    assert versions == sorted(set(versions))
    previous_names = [m["name"] for m in blob["members"]]
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert len(calls) == 3
    assert [m["name"] for m in runtime.state["research_team"]["members"]] == previous_names


@pytest.mark.asyncio
async def test_save_retries_contention_without_publishing_unsaved_state(runtime, monkeypatch):
    attempts = 0
    async def contended(run_id, patch):
        nonlocal attempts
        attempts += 1
        assert not runtime.events
        if attempts < 3:
            return None
        runtime.state.update(copy.deepcopy(patch))
        return {"state": runtime.state}
    monkeypatch.setattr(run_store, "patch_run_state", contended)
    member = {"id": "m1", "role": "researcher", "name": "小澈", "status": "researching"}
    rt = team.ResearchTeam(runtime.env, {"id": "rt", "version": 0, "goalRevision": 1, "members": [member]})
    async with rt.lock:
        await rt.save()
    assert attempts == 3 and len(runtime.events) == 1
    assert runtime.state["loop_checkpoint"] == {"root": True}


@pytest.mark.asyncio
async def test_save_missing_parent_fails_without_public_success(runtime, monkeypatch):
    async def missing(*args, **kwargs):
        return None
    monkeypatch.setattr(run_store, "patch_run_state", missing)
    monkeypatch.setattr(run_store, "get_run_snapshot", missing)
    rt = team.ResearchTeam(runtime.env, {"id": "rt", "version": 0, "goalRevision": 1, "members": []})
    with pytest.raises(RuntimeError, match="状态不可用"):
        await rt.save()
    assert not runtime.events


@pytest.mark.asyncio
async def test_no_search_receipt_cannot_be_success(runtime, monkeypatch):
    async def driver(**kwargs):
        yield {"type": "final", "answer": "已完成所有研究"}
    monkeypatch.setattr(team, "drive_model", driver)
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert all(m["status"] == "failed" for m in runtime.state["research_team"]["members"])


@pytest.mark.asyncio
async def test_cancellation_joins_every_member(runtime, monkeypatch):
    started = set()
    stopped = set()
    ready = asyncio.Event()

    async def driver(**kwargs):
        scope = kwargs["gateway"]["audit_scope"]
        started.add(scope)
        if len(started) == 3:
            ready.set()
        try:
            await asyncio.Event().wait()
            yield {}
        finally:
            stopped.add(scope)
    monkeypatch.setattr(team, "drive_model", driver)
    async def consume():
        return [event async for event in team.run_team(runtime.env, runtime.ledger)]
    task = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(3):
            await task
    assert stopped == started and len(stopped) == 3


@pytest.mark.asyncio
async def test_readonly_tools_still_check_parent_policy(runtime, monkeypatch):
    async def snapshot(run_id):
        return runtime.parent.model_copy(update={"execution_profile": None})
    monkeypatch.setattr(run_store, "get_run_snapshot", snapshot)
    async def driver(**kwargs):
        result = await kwargs["tools"][0].observe({"query": "test", "topic_id": runtime.ledger.topics[0].topic_id})
        assert result.status == "failed"
        yield {"type": "final", "answer": "无法搜索"}
    monkeypatch.setattr(team, "drive_model", driver)
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert all(not m["searches"] for m in runtime.state["research_team"]["members"])


@pytest.mark.asyncio
async def test_member_checkpoint_sink_never_writes_root(monkeypatch):
    saved = []
    async def sink(messages, **kwargs):
        saved.append((messages, kwargs))
    async def forbidden(*args, **kwargs):
        raise AssertionError("root checkpoint must not be touched")
    monkeypatch.setattr(run_store, "persist_loop_checkpoint", forbidden)
    await model_driver._persist_loop_checkpoint({"checkpoint_sink": sink, "run_id": "r1"}, [{"role": "user", "content": "hello"}], step=2)
    assert saved[0][1]["step"] == 2


@pytest.mark.asyncio
async def test_team_runtime_error_still_injects_synthesis_guard(runtime, monkeypatch):
    async def explode(self):  # noqa: ARG001
        raise RuntimeError("team exploded")

    monkeypatch.setattr(team.ResearchTeam, "execute", explode)
    frames = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert frames
    assert runtime.state["research_team"]["stage"] == "synthesizing"
    assert all(m["status"] == "failed" for m in runtime.state["research_team"]["members"])
    assert "原有报告规则" in runtime.env.turn_guard_prompt
    assert "研究团队公开简报" in runtime.env.turn_guard_prompt
    assert "只在对应结论说明具体的证据缺口" in runtime.env.turn_guard_prompt
    assert "成员未提交总结本身不代表结论缺少来源" in runtime.env.turn_guard_prompt


def test_public_snapshot_allowlist():
    raw = {"id": "team", "version": 1, "secret": "hidden", "members": [
        {"id": "m1", "role": "researcher", "checkpoint": {"token": "secret"}, "searches": [{"id": "s", "secret": "token"}]},
    ]}
    value = team.public_snapshot(raw)
    assert "secret" not in json.dumps(value) and "checkpoint" not in json.dumps(value)


@pytest.mark.asyncio
async def test_continue_inherits_delivered_members_only_from_same_conversation(runtime, monkeypatch):
    inherited = {
        "id": "rt_prior", "version": 9, "goalRevision": 0, "query": runtime.ledger.query,
        "stage": "synthesizing", "startedAt": "2026-09-07T00:00:00Z",
        "topicIds": [t.topic_id for t in runtime.ledger.topics], "topics": [],
        "members": [{"id": "m1", "role": "researcher", "name": "资料研究员", "task": "查资料",
                     "status": "completed", "findings": "已查证", "review": "已互审", "searches": []}],
    }
    current_get = run_store.get_run_state
    async def get_state(run_id):
        if run_id == "previous":
            return {"state": {"research_team": inherited}}
        return await current_get(run_id)
    monkeypatch.setattr(run_store, "get_run_state", get_state)
    async def forbidden(**kwargs):
        raise AssertionError("delivered members must not rerun")
        yield {}
    monkeypatch.setattr(team, "drive_model", forbidden)
    runtime.env.resume_source_run_id = "previous"
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert runtime.state["research_team"]["id"] == "rt_prior"
    assert runtime.state["research_team"]["goalRevision"] == 1
    assert runtime.state["research_team"]["members"][0]["review"] == "已互审"

    runtime.state.pop("research_team")
    async def wrong_owner(run_id):
        return runtime.parent.model_copy(update={"thread_id": "other"}) if run_id == "previous" else runtime.parent
    monkeypatch.setattr(run_store, "get_run_snapshot", wrong_owner)
    async def empty_driver(**kwargs):
        yield {"type": "final", "answer": "未取证"}
    monkeypatch.setattr(team, "drive_model", empty_driver)
    _ = [event async for event in team.run_team(runtime.env, runtime.ledger)]
    assert runtime.state["research_team"]["id"] != "rt_prior"
    assert all(m["status"] == "failed" for m in runtime.state["research_team"]["members"])
