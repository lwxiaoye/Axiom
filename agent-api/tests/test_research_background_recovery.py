"""Research report progress survives phase targets and background recovery."""
import hashlib
import json

import pytest

from app.services.agent_harness import run_store
from app.services.agent_harness.research.engine import evidence_brief
from app.services.agent_harness.research.review import reviewed_report
from tests.test_research_report_review import ACCEPT, add_source
from tests.test_research_team import runtime  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_failure", [False, True])
async def test_expired_report_target_does_not_prevent_delivery(runtime, legacy_failure):
    add_source(runtime)
    evidence_key = hashlib.sha256(evidence_brief(runtime.ledger).encode()).hexdigest()
    runtime.env.research_deadline = 1
    runtime.state["research_report_phases"] = {
        phase: {"goalRevision": 1, "evidenceKey": evidence_key, "deadline": 1}
        for phase in ("draft", "review")
    }
    if legacy_failure:
        runtime.state["research_report_review_failure"] = {
            "goalRevision": 1, "reason": "report_call_timeout"}
    calls = []

    async def driver(**kwargs):
        calls.append(kwargs)
        yield {"type": "final", "answer": ACCEPT if kwargs.get("gateway", {}).get("audit_scope") else "已核实的内容[1]"}

    events = [e async for e in reviewed_report(runtime.env, driver)]
    assert events[-1]["answer"] == "已核实的内容[1]"
    assert len(calls) == 2
    assert not runtime.state.get("research_report_review_failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["draft", "review"])
async def test_model_timeout_recovers_only_unfinished_report_work(runtime, phase):
    add_source(runtime)
    calls, failed, events = [], False, []

    async def driver(**kwargs):
        nonlocal failed
        stage = "review" if kwargs.get("gateway", {}).get("audit_scope") else "draft"
        calls.append(stage)
        if stage == phase and not failed:
            failed = True
            yield {"type": "delta", "text": "未核验草稿"}
            raise TimeoutError("provider read timeout")
        yield {"type": "final", "answer": ACCEPT if stage == "review" else "已核实的内容[1]"}

    with pytest.raises(TimeoutError, match="provider read timeout"):
        async for event in reviewed_report(runtime.env, driver):
            events.append(event)
    assert not events
    assert not runtime.state.get("research_report_review_failure")
    assert runtime.state["research"]["sources"]
    delivered = [e async for e in reviewed_report(runtime.env, driver)]
    assert delivered[-1]["answer"] == "已核实的内容[1]"
    assert calls == (["draft", "draft", "review"] if phase == "draft" else ["draft", "review", "review"])


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["draft", "review"])
async def test_completed_model_result_is_saved_before_connection_cleanup(runtime, phase):
    add_source(runtime)
    calls, failed = [], False

    async def driver(**kwargs):
        nonlocal failed
        stage = "review" if kwargs.get("gateway", {}).get("audit_scope") else "draft"
        calls.append(stage)
        try:
            yield {"type": "final", "answer": ACCEPT if stage == "review" else "已核实的内容[1]"}
        finally:
            if stage == phase and not failed:
                failed = True
                raise OSError("connection cleanup interrupted")

    with pytest.raises(OSError, match="connection cleanup interrupted"):
        _ = [e async for e in reviewed_report(runtime.env, driver)]
    assert runtime.state["research_report_draft"]["final"]["answer"] == "已核实的内容[1]"
    if phase == "review":
        assert runtime.state["research_report_review_response"]["answer"] == ACCEPT
    delivered = [e async for e in reviewed_report(runtime.env, driver)]
    assert delivered[-1]["answer"] == "已核实的内容[1]"
    assert calls == ["draft", "review"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["goal", "evidence"])
async def test_saved_review_response_cannot_validate_changed_input(runtime, monkeypatch, change):
    add_source(runtime)
    calls = []

    async def driver(**kwargs):
        calls.append(kwargs)
        yield {"type": "final", "answer": ACCEPT if kwargs.get("gateway", {}).get("audit_scope") else "已核实的内容[1]"}

    _ = [e async for e in reviewed_report(runtime.env, driver)]
    if change == "goal":
        async def snapshot(_):
            return runtime.parent.model_copy(update={"goal_revision": 2})
        monkeypatch.setattr(run_store, "get_run_snapshot", snapshot)
    else:
        runtime.ledger.sources[0].snippet = "发生变化的证据正文"
        runtime.state["research"] = runtime.ledger.to_state()
    _ = [e async for e in reviewed_report(runtime.env, driver)]
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_real_projection_state_survives_json_storage_and_report_recovery(runtime, monkeypatch):
    from app.services.agent_harness.context import ProjectionLedgerState
    from tests.test_thread_projection_store import _state

    add_source(runtime)
    original = run_store.patch_run_state
    calls = []

    async def json_store(run_id, value):
        # Runtime JSON has this boundary; deepcopy-only doubles conceal TypeError.
        return await original(run_id, json.loads(json.dumps(value)))

    monkeypatch.setattr(run_store, 'patch_run_state', json_store)
    state = _state()

    async def driver(**kwargs):
        calls.append(kwargs)
        if kwargs.get('gateway', {}).get('audit_scope'):
            yield {'type': 'final', 'answer': ACCEPT}
        else:
            yield {'type': 'final', 'answer': '已核实的内容[1]', '_projection_commit': {
                'state': state, 'assistant_item': {'role': 'assistant', 'content': '草稿'},
                'display_history': [], 'metrics': {'canary_candidate': False}}}

    first = [e async for e in reviewed_report(runtime.env, driver)]
    restored = [e async for e in reviewed_report(runtime.env, driver)]
    assert len(calls) == 2
    assert first == restored
    for final in (first[-1], restored[-1]):
        assert isinstance(final['_projection_commit']['state'], ProjectionLedgerState)
        assert final['_projection_commit']['state'] == state
        assert final['_projection_commit']['assistant_item']['content'] == final['answer']
