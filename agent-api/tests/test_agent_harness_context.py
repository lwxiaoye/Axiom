from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.services.agent_harness import (
    AgentMode,
    ContextCompiler,
    ContextFacts,
    ContextProjectionLedger,
    ObservationStatus,
    PlanSnapshot,
    PlanStepSnapshot,
    RunPhase,
    RunSnapshot,
    ToolObservation,
)
from app.services.agent_harness.responses_protocol import messages_to_responses_input
from app.services.agent_harness.context import ProjectionLedgerState
from app.services.agent_harness.context import (
    canonical_hash,
    canonical_json,
    display_history_hash,
    normalize_display_history,
)


def _plan(*, version: int = 1, status: str = "pending") -> PlanSnapshot:
    return PlanSnapshot(
        run_id="run-1",
        goal_revision=0,
        plan_version=version,
        goal="运行命令并回复",
        steps=(
            PlanStepSnapshot(
                step_id="step-1",
                order=0,
                title="运行命令",
                status=status,
            ),
            PlanStepSnapshot(
                step_id="obsolete",
                order=1,
                title="旧步骤",
                status="invalidated",
            ),
        ),
        updated_at=datetime.utcnow(),
    )


def _run(*, plan: PlanSnapshot | None = None, phase: RunPhase = RunPhase.EXECUTING) -> RunSnapshot:
    return RunSnapshot(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_mode=AgentMode.STANDARD,
        phase=phase,
        state_version=plan.plan_version if plan else 1,
        goal_revision=0,
        plan_version=plan.plan_version if plan else 0,
        event_cursor=0,
        execution_profile={"id": "artifact_coding", "artifact_kind": "presentation"},
        plan=plan,
    )


def _event(message: dict) -> dict:
    return json.loads(message["content"])


def _apply_merge_patch(target, patch):
    if not isinstance(patch, dict):
        return patch
    output = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            output.pop(key, None)
        else:
            output[key] = _apply_merge_patch(output.get(key), value)
    return output


def test_compiler_emits_deterministic_sections_without_budget_or_operational_time():
    plan = _plan()
    run = _run(plan=plan).model_copy(update={
        "execution_control": {
            "segment_index": 3,
            "checkpoint_sequence": 8,
            "recovery_reason": "worker_restart",
            "last_progress_at": "2026-08-28T10:01:02",
            "recovery_count": 2,
        },
    })
    snapshot = ContextCompiler().compile(ContextFacts(run=run, plan=plan))

    assert set(snapshot.sections) == {"runtime", "plan"}
    assert snapshot.sections["runtime"] == {
        "agent_mode": "standard",
        "execution_profile": {"artifact_kind": "presentation", "id": "artifact_coding"},
        "phase": "executing",
        "capability_scope": "default",
        "execution_control": {"recovery_reason": "worker_restart"},
    }
    assert "budget" not in json.dumps(snapshot.sections)
    assert "updated_at" not in json.dumps(snapshot.sections)
    assert "last_progress_at" not in json.dumps(snapshot.sections)
    assert "checkpoint_sequence" not in json.dumps(snapshot.sections)
    assert "recovery_count" not in json.dumps(snapshot.sections)
    assert snapshot.sections["plan"]["steps"][0]["title"] == "运行命令"
    assert snapshot.sections["plan"]["steps"][0]["order"] == 0
    assert len(snapshot.sections["plan"]["steps"]) == 1


def test_workspace_is_a_fresh_state_section_not_a_reordered_history_message():
    fresh = "（会话工作区当前快照）\n素材 2：a.jpg, b.png"
    snapshot = ContextCompiler().compile(ContextFacts(
        run=_run(),
        workspace_status=fresh,
    ))
    assert snapshot.sections["workspace"] == {"status": fresh}


def test_world_state_keeps_only_day_and_timezone_facts():
    snapshot = ContextCompiler().compile(ContextFacts(
        run=_run(),
        world_state={
            "current_date": "2026-08-28",
            "timezone": "Asia/Shanghai",
            "current_time": "2026-08-28T10:01:02+08:00",
        },
    ))
    assert snapshot.sections["world_state"] == {
        "current_date": "2026-08-28",
        "timezone": "Asia/Shanghai",
    }
    assert "10:01" not in json.dumps(snapshot.sections)


def test_world_state_has_one_deterministic_total_budget_with_priority():
    snapshot = ContextCompiler().compile(ContextFacts(
        run=_run(),
        world_state={
            "current_date": "2026-08-31",
            "timezone": "Asia/Singapore",
            "conversation_summary": "p" * 40_000,
            "selected_skills": "s" * 40_000,
            "connectors": "c" * 40_000,
            "memory": "m" * 40_000,
        },
    ))
    state = snapshot.sections["world_state"]
    assert len(canonical_json(state)) <= 64_000
    assert state["current_date"] == "2026-08-31"
    assert state["timezone"] == "Asia/Singapore"
    assert len(state["conversation_summary"]) == 40_000
    assert "memory" not in state
    assert snapshot.snapshot_hash == ContextCompiler().compile(ContextFacts(
        run=_run(),
        world_state={
            "current_date": "2026-08-31",
            "timezone": "Asia/Singapore",
            "conversation_summary": "p" * 40_000,
            "selected_skills": "s" * 40_000,
            "connectors": "c" * 40_000,
            "memory": "m" * 40_000,
        },
    )).snapshot_hash


def test_workspace_and_recent_observations_are_bounded_and_deterministic():
    observations = tuple(
        ToolObservation(
            call_id=f"call-{index}",
            tool_name="read_file",
            status=ObservationStatus.SUCCEEDED,
            summary=("x" * 2_000) + str(index),
            structured_data={"rows": list(range(40))},
            started_at=datetime(2026, 8, 28, 10, index),
            completed_at=datetime(2026, 8, 28, 10, index),
        )
        for index in range(10)
    )
    snapshot = ContextCompiler().compile(ContextFacts(
        run=_run(),
        observations=observations,
        workspace_status="w" * 5_000,
    ))
    rows = snapshot.sections["recent_observations"]
    assert [row["call_id"] for row in rows] == [f"call-{index}" for index in range(2, 10)]
    assert all("started_at" not in row and "completed_at" not in row for row in rows)
    assert all(len(row["summary"]) == 1_000 for row in rows)
    assert all(len(row["structured_data"]["rows"]) == 12 for row in rows)
    assert len(snapshot.sections["workspace"]["status"]) == 4_000
    assert snapshot.snapshot_hash == ContextCompiler().compile(ContextFacts(
        run=_run(),
        observations=observations,
        workspace_status="w" * 5_000,
    )).snapshot_hash


def test_compiler_keeps_short_structured_stop_fact_without_model_claim():
    run = _run(phase=RunPhase.VERIFYING).model_copy(update={
        "completion_observation": {
            "kind": "run_terminal_report",
            "source": "completion_verifier",
            "resolution": "waiting_system",
            "verified": False,
            "reason": "external_dependency_temporarily_unavailable",
            "reason_codes": ["external_dependency_temporarily_unavailable"],
            "unmet_conditions": ["provider response is still unavailable"],
            "existing_evidence": [
                {"tool_name": "search_web", "status": "failed", "error_code": "timeout"},
            ],
            "claim": {"summary": "模型自称因为用户没有确认所以停下"},
        },
    })
    snapshot = ContextCompiler().compile(ContextFacts(run=run))
    fact = snapshot.sections["completion_verification"]
    assert fact["resolution"] == "waiting_system"
    assert fact["source"] == "completion_verifier"
    assert fact["existing_evidence"][0]["error_code"] == "timeout"
    assert fact["unmet_conditions"] == ["provider response is still unavailable"]
    assert "claim" not in fact


def test_projection_is_append_only_and_plan_change_is_a_tail_patch():
    compiler = ContextCompiler()
    ledger = ContextProjectionLedger()
    plan_v1 = _plan(version=1)
    snapshot_v1 = compiler.compile(ContextFacts(run=_run(plan=plan_v1), plan=plan_v1))
    source_v1 = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "start"},
    ]
    first = ledger.project(
        source_messages=source_v1,
        snapshot=snapshot_v1,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    full = _event(first.messages[-1])
    assert full["mode"] == "full"
    assert full["context_epoch"] == 0

    plan_v2 = _plan(version=2, status="completed")
    snapshot_v2 = compiler.compile(ContextFacts(run=_run(plan=plan_v2), plan=plan_v2))
    source_v2 = source_v1 + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]
    second = ledger.project(
        source_messages=source_v2,
        snapshot=snapshot_v2,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert list(second.messages[:len(first.messages)]) == list(first.messages)
    first_responses_input = messages_to_responses_input(list(first.messages))
    second_responses_input = messages_to_responses_input(list(second.messages))
    assert second_responses_input[:len(first_responses_input)] == first_responses_input
    patch = _event(second.messages[-1])
    assert patch["mode"] == "merge_patch"
    assert _apply_merge_patch(full["sections"], patch["patch"]) == snapshot_v2.sections


def test_projection_retry_is_identical_and_schema_change_starts_one_epoch():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    source = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
    ledger = ContextProjectionLedger(initial_reason="resume_rebuild")
    first = ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools-v1",
        transport="responses",
    )
    retry = ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools-v1",
        transport="responses",
    )
    assert retry.messages == first.messages
    assert retry.epoch_reason == "resume_rebuild"

    changed = ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools-v2",
        transport="responses",
    )
    assert changed.context_epoch == 1
    assert changed.epoch_reason == "tool_schema_changed"
    assert _event(changed.messages[-1])["mode"] == "full"


def test_explicit_compaction_reset_increments_epoch_once():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=[{"role": "system", "content": "rules"}],
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    ledger.reset("compaction")
    compacted = ledger.project(
        source_messages=[{"role": "system", "content": "rules"}, {"role": "user", "content": "summary"}],
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert compacted.context_epoch == 1
    assert compacted.epoch_reason == "compaction"


def test_history_base_prompt_and_transport_changes_use_explicit_epochs():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))

    cases = (
        (
            "history_replaced",
            {"source_messages": [{"role": "system", "content": "rules-v2"}]},
        ),
        ("base_prompt_changed", {"base_prompt_hash": "base-v2"}),
        ("transport_changed", {"transport": "chat_completions"}),
    )
    for expected_reason, override in cases:
        ledger = ContextProjectionLedger()
        original = {
            "source_messages": [{"role": "system", "content": "rules"}],
            "snapshot": snapshot,
            "base_prompt_hash": "base-v1",
            "tool_schema_hash": "tools-v1",
            "transport": "responses",
        }
        ledger.project(**original)
        changed = ledger.project(**{**original, **override})
        assert changed.context_epoch == 1
        assert changed.epoch_reason == expected_reason
        event = _event(changed.messages[-1])
        assert event["mode"] == "full"
        assert event["epoch_reason"] == expected_reason


def test_persisted_projection_shadow_extends_exact_prefix_without_provider_cursor_fields():
    compiler = ContextCompiler()
    first_snapshot = compiler.compile(ContextFacts(
        run=_run(),
        world_state={"current_date": "2026-08-28", "timezone": "Asia/Shanghai"},
    ))
    source = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "start"},
    ]
    ledger = ContextProjectionLedger()
    first = ledger.project(
        source_messages=source,
        snapshot=first_snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    state = ledger.to_persisted_state(
        thread_id="thread-1",
        model="deepseek-v4-flash",
    )
    serialized = state.model_dump(mode="json")
    assert state.mode == "shadow"
    assert "previous_response_id" not in json.dumps(serialized)
    assert "prompt_cache_key" not in json.dumps(serialized)

    extended_source = source + [{"role": "user", "content": "continue"}]
    shadow, decision = ContextProjectionLedger.from_persisted_shadow(
        state,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=extended_source,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert decision.eligible
    assert not decision.provider_reuse_allowed
    provider_ledger, provider_decision = ContextProjectionLedger.from_persisted_for_provider(
        state,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=extended_source,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert provider_ledger is None
    assert provider_decision.reason == "append_only_shadow"

    second_snapshot = compiler.compile(ContextFacts(
        run=_run(),
        world_state={"current_date": "2026-08-29", "timezone": "Asia/Shanghai"},
    ))
    second = shadow.project(
        source_messages=extended_source,
        snapshot=second_snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert list(second.messages[:len(first.messages)]) == list(first.messages)
    assert _event(second.messages[-1])["mode"] == "merge_patch"


def test_cross_run_canonical_tool_history_is_bound_to_public_transcript():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    public_before = [
        {"role": "user", "content": "生成报告"},
        {"role": "assistant", "content": "报告已经生成"},
    ]
    first_source = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "生成报告"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "saved"},
        {"role": "assistant", "content": "报告已经生成"},
    ]
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=first_source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    state = ledger.to_persisted_state(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        display_history=public_before,
    )

    next_source = ContextProjectionLedger.restore_canonical_source(
        state,
        display_history=public_before,
        current_run_delta=[{"role": "user", "content": "继续"}],
    )
    assert next_source == [*first_source, {"role": "user", "content": "继续"}]
    _, decision = ContextProjectionLedger.from_persisted_shadow(
        state,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=next_source,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
        display_history=public_before,
    )
    assert decision.eligible is True
    assert state.display_history_hash == display_history_hash(public_before)

    edited = [
        {"role": "user", "content": "生成另一个报告"},
        public_before[1],
    ]
    assert ContextProjectionLedger.restore_canonical_source(
        state,
        display_history=edited,
        current_run_delta=[{"role": "user", "content": "继续"}],
    ) is None
    _, edited_decision = ContextProjectionLedger.from_persisted_shadow(
        state,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=next_source,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
        display_history=edited,
    )
    assert edited_decision.eligible is False
    assert edited_decision.reason == "display_history_hash_mismatch"

    without_base = ContextProjectionLedger.restore_canonical_source(
        state,
        display_history=public_before,
        current_run_delta=[{"role": "user", "content": "new base is empty"}],
        replacement_base_message=None,
        replace_base_message=True,
        target_transport="responses",
    )
    assert without_base is not None
    assert without_base[0] == first_source[1]
    assert all(item.get("content") != "rules" for item in without_base)


def test_public_transcript_fingerprint_binds_attachment_identity_not_preview_bytes():
    base = [{
        "role": "user",
        "content": "看看这个文件",
        "attachments_json": json.dumps([{
            "filename": "report.pdf",
            "kind": "text",
            "status": "ok",
            "file_id": "file-1",
            "sha256": "abc",
            "preview_url": "data:image/jpeg;base64,first",
        }]),
    }]
    preview_changed = [{
        **base[0],
        "attachments_json": json.dumps([{
            "filename": "report.pdf",
            "kind": "text",
            "status": "ok",
            "file_id": "file-1",
            "sha256": "abc",
            "preview_url": "data:image/jpeg;base64,second",
        }]),
    }]
    identity_changed = [{
        **base[0],
        "attachments_json": json.dumps([{
            "filename": "report.pdf",
            "kind": "text",
            "status": "ok",
            "file_id": "file-2",
            "sha256": "def",
        }]),
    }]

    assert display_history_hash(base) == display_history_hash(preview_changed)
    assert display_history_hash(base) != display_history_hash(identity_changed)

    legacy_preview_a = [{
        "role": "user",
        "content": "legacy",
        "attachments": [{"preview_url": "data:image/jpeg;base64,first"}],
    }]
    legacy_preview_b = [{
        "role": "user",
        "content": "legacy",
        "attachments": [{"preview_url": "data:image/jpeg;base64,second"}],
    }]
    assert display_history_hash(legacy_preview_a) != display_history_hash(legacy_preview_b)
    normalized = normalize_display_history(legacy_preview_a)
    assert "data:image" not in canonical_json(normalized)

    single_object_a = [{
        "role": "user",
        "content": "legacy object",
        "attachments": {
            "file_id": "file-1",
            "filename": "report.pdf",
            "preview_url": "data:image/jpeg;base64,first",
        },
    }]
    single_object_b = [{
        "role": "user",
        "content": "legacy object",
        "attachments": {
            "file_id": "file-1",
            "filename": "report.pdf",
            "preview_url": "data:image/jpeg;base64,second",
        },
    }]
    assert display_history_hash(single_object_a) == display_history_hash(
        single_object_b
    )


def test_terminal_source_item_advances_only_after_public_history_is_committed():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "生成报告"},
        ],
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    pending = ledger.to_persisted_state(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        display_history=[{"role": "user", "content": "生成报告"}],
    )
    committed = ContextProjectionLedger.with_committed_source_items(
        pending,
        source_items=[{
            "role": "assistant",
            "content": "原始模型终答",
            "_responses_output_items": [{"type": "message", "id": "msg-1"}],
        }],
        display_history=[
            {"role": "user", "content": "生成报告"},
            {"role": "assistant", "content": "清洗后的权威终答"},
        ],
    )
    assert committed is not None
    source = ContextProjectionLedger.source_messages_from_state(committed)
    assert source[-1]["content"] == "原始模型终答"
    assert source[-1]["_responses_output_items"][0]["id"] == "msg-1"
    assert committed.display_history_hash == display_history_hash([
        {"role": "user", "content": "生成报告"},
        {"role": "assistant", "content": "清洗后的权威终答"},
    ])


def test_new_epoch_replaces_base_but_preserves_hidden_tool_history():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    ledger = ContextProjectionLedger()
    source = [
        {"role": "system", "content": "old tool rules"},
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
            "_responses_output_items": [{
                "type": "reasoning",
                "encrypted_content": "opaque",
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "verified result"},
        {"role": "assistant", "content": "public answer"},
    ]
    ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="old-base",
        tool_schema_hash="tools",
        transport="responses",
    )
    state = ledger.to_persisted_state(
        thread_id="thread-1",
        model="model-1",
        display_history=[
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "public answer"},
        ],
    )

    restored = ContextProjectionLedger.restore_canonical_source(
        state,
        display_history=[
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "public answer"},
        ],
        current_run_delta=[{"role": "user", "content": "explain directly"}],
        replacement_base_message={"role": "system", "content": "new direct rules"},
        target_transport="chat_completions",
    )
    assert restored is not None
    assert restored[0]["content"] == "new direct rules"
    assert restored[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert "_responses_output_items" not in restored[2]
    assert restored[3]["content"] == "verified result"
    assert restored[-1] == {"role": "user", "content": "explain directly"}

    transitioned = ContextProjectionLedger.from_persisted_transport_transition(
        state.model_copy(update={"storage_revision": 9}),
        display_history=[
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "public answer"},
        ],
        current_run_delta=[{"role": "user", "content": "explain directly"}],
        replacement_base_message={"role": "system", "content": "new direct rules"},
        target_transport="chat_completions",
    )
    assert transitioned is not None
    transition_ledger, transition_source, decision = transitioned
    assert transition_source == restored
    assert decision.reason == "transport_changed"
    projected = transition_ledger.project(
        source_messages=transition_source,
        snapshot=snapshot,
        base_prompt_hash="new-base",
        tool_schema_hash="tools",
        transport="chat_completions",
    )
    assert projected.context_epoch == state.context_epoch + 1
    target_state = transition_ledger.to_persisted_state(
        thread_id="thread-1",
        model="model-1",
        display_history=[
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "public answer"},
            {"role": "user", "content": "explain directly"},
        ],
    )
    assert target_state.storage_revision == 0


def test_dynamic_memory_skills_and_knowledge_change_only_in_tail_patch():
    compiler = ContextCompiler()
    ledger = ContextProjectionLedger()
    source = [{"role": "system", "content": "stable rules"}]
    first_snapshot = compiler.compile(ContextFacts(
        run=_run(),
        world_state={
            "current_date": "2026-08-30",
            "timezone": "Asia/Shanghai",
            "memory": "prefers concise answers",
            "selected_skills": "spreadsheet",
            "knowledge_bases": ["kb-1"],
        },
    ))
    first = ledger.project(
        source_messages=source,
        snapshot=first_snapshot,
        base_prompt_hash="stable-base",
        tool_schema_hash="stable-tools",
        transport="responses",
    )
    second_snapshot = compiler.compile(ContextFacts(
        run=_run(),
        world_state={
            "current_date": "2026-08-30",
            "timezone": "Asia/Shanghai",
            "memory": "prefers detailed answers",
            "selected_skills": "spreadsheet,pdf",
            "knowledge_bases": ["kb-1", "kb-2"],
        },
    ))
    second = ledger.project(
        source_messages=source,
        snapshot=second_snapshot,
        base_prompt_hash="stable-base",
        tool_schema_hash="stable-tools",
        transport="responses",
    )
    assert list(second.messages[:len(first.messages)]) == list(first.messages)
    patch = _event(second.messages[-1])
    assert patch["mode"] == "merge_patch"
    assert patch["patch"]["world_state"]["memory"] == "prefers detailed answers"
    assert patch["patch"]["world_state"]["knowledge_bases"] == ["kb-1", "kb-2"]


def test_persisted_projection_revision_and_forced_compaction_reset_are_explicit():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    source = [{"role": "system", "content": "rules"}]
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    initial = ledger.to_persisted_state(thread_id="thread-1", model="m")
    assert initial.storage_revision == 0
    ledger.mark_persisted()
    persisted = ledger.to_persisted_state(thread_id="thread-1", model="m")
    assert persisted.storage_revision == 1

    rebuilt, decision = ContextProjectionLedger.from_persisted_shadow(
        persisted,
        forced_reset_reason="compaction",
        thread_id="thread-1",
        model="m",
        source_messages=source,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert not decision.eligible
    assert decision.reset_reason == "compaction"
    compacted = rebuilt.project(
        source_messages=[{"role": "system", "content": "summary"}],
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert compacted.epoch_reason == "compaction"
    assert compacted.context_epoch == 1
    assert rebuilt.to_persisted_state(thread_id="thread-1", model="m").storage_revision == 1


def test_semantic_epoch_reset_preserves_the_existing_row_cas_revision():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    source = [{"role": "system", "content": "old rules"}]
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="old-base",
        tool_schema_hash="tools",
        transport="responses",
    )
    persisted = ledger.to_persisted_state(
        thread_id="thread-1",
        model="m",
    ).model_copy(update={"storage_revision": 7})

    rebuilt, decision = ContextProjectionLedger.from_persisted_shadow(
        persisted,
        thread_id="thread-1",
        model="m",
        source_messages=[{"role": "system", "content": "new rules"}],
        base_prompt_hash="new-base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert decision.reason == "base_prompt_changed"
    rebuilt.project(
        source_messages=[{"role": "system", "content": "new rules"}],
        snapshot=snapshot,
        base_prompt_hash="new-base",
        tool_schema_hash="tools",
        transport="responses",
    )
    replacement = rebuilt.to_persisted_state(thread_id="thread-1", model="m")
    assert replacement.storage_revision == 7
    assert replacement.after_successful_save().storage_revision == 8


def test_persisted_projection_canary_requires_exact_append_only_identity():
    snapshot = ContextCompiler().compile(ContextFacts(run=_run()))
    source = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=source,
        snapshot=snapshot,
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    shadow_state = ledger.to_persisted_state(
        thread_id="thread-1",
        model="deepseek-v4-flash",
    )
    canary_state = shadow_state.model_copy(update={"mode": "canary"})
    provider_ledger, decision = ContextProjectionLedger.from_persisted_for_provider(
        canary_state,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=source + [{"role": "user", "content": "next"}],
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert provider_ledger is not None
    assert decision.provider_reuse_allowed

    cases = (
        ({"base_prompt_hash": "base-v2"}, "base_prompt_changed"),
        ({"tool_schema_hash": "tools-v2"}, "tool_schema_changed"),
        ({"transport": "chat_completions"}, "transport_changed"),
        (
            {"source_messages": [{"role": "system", "content": "replaced"}]},
            "history_replaced",
        ),
    )
    original = {
        "thread_id": "thread-1",
        "model": "deepseek-v4-flash",
        "source_messages": source + [{"role": "user", "content": "next"}],
        "base_prompt_hash": "base",
        "tool_schema_hash": "tools",
        "transport": "responses",
    }
    for overrides, reset_reason in cases:
        rejected = ContextProjectionLedger.evaluate_persisted_state(
            canary_state,
            **{**original, **overrides},
        )
        assert not rejected.eligible
        assert rejected.reset_reason == reset_reason


def test_persisted_projection_rejects_corrupt_projected_state():
    invalid = ProjectionLedgerState(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        transport="responses",
        context_epoch=0,
        epoch_reason="initial",
        base_prompt_hash="base",
        tool_schema_hash="tools",
        state_snapshot_hash="not-a-real-hash",
        source_cursor=1,
        source_history_hash="not-a-real-hash",
        display_history_count=0,
        display_history_hash=display_history_hash([]),
        projected_items=({"role": "user", "content": "hi"},),
    )
    decision = ContextProjectionLedger.evaluate_persisted_state(
        invalid,
        thread_id="thread-1",
        model="deepseek-v4-flash",
        source_messages=[{"role": "user", "content": "hi"}],
        base_prompt_hash="base",
        tool_schema_hash="tools",
        transport="responses",
    )
    assert not decision.eligible
    assert decision.reset_reason == "history_replaced"
    assert ContextProjectionLedger.restore_canonical_source(
        invalid,
        display_history=[],
        current_run_delta=[{"role": "user", "content": "next"}],
    ) is None


@pytest.mark.asyncio
async def test_plain_projection_is_shadow_safe_then_replays_canonical_provider_history(
    monkeypatch,
):
    from app.services.agent_harness import plan_store, run_store
    from app.services.agent_harness import thread_context_projection as projection_module

    async def get_run(_run_id):
        return _run()

    async def get_plan(_run_id):
        return None

    async def shadow_mode(*_args, **_kwargs):
        return "shadow"

    async def on_mode(*_args, **_kwargs):
        return "on"

    persisted = None

    async def load(**_kwargs):
        return persisted

    async def load_transitions(**_kwargs):
        return []

    async def save(state, **_kwargs):
        nonlocal persisted
        persisted = state.model_copy(update={"storage_revision": state.storage_revision + 1})
        return True

    async def mark(**_kwargs):
        return True

    monkeypatch.setattr(run_store, "get_run_snapshot", get_run)
    monkeypatch.setattr(plan_store, "get_plan_snapshot", get_plan)
    monkeypatch.setattr(projection_module.thread_projection_store, "load", load)
    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "load_transport_transition_candidates",
        load_transitions,
    )
    monkeypatch.setattr(projection_module.thread_projection_store, "save", save)
    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "effective_rollout_mode",
        shadow_mode,
    )
    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "mark_canary_candidate",
        mark,
    )

    first_ordinary = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "dynamic-v1"},
    ]
    first = await projection_module.prepare_plain_thread_projection(
        run_id="run-1",
        thread_id="thread-1",
        model="model-1",
        transport="responses",
        stable_base="stable",
        ordinary_provider_messages=first_ordinary,
        source_messages=first_ordinary[:-1],
        current_run_delta=[{"role": "user", "content": "hello"}],
        display_history_before_run=[],
        display_history_current=[{"role": "user", "content": "hello"}],
        world_state={"turn_guard": "dynamic-v1"},
    )
    assert first.provider_messages == first_ordinary
    assert first.provider_reuse_active is False
    bundle = projection_module.terminal_commit_bundle(
        first,
        assistant_item={"role": "assistant", "content": "provider raw answer"},
    )
    assert await projection_module.commit_projection_bundle(
        bundle,
        answer="public sanitized answer",
        run_id="run-1",
        display_assistant_message={
            "role": "assistant",
            "content": "public sanitized answer",
            "attachments_json": json.dumps([{
                "filename": "result.pdf",
                "kind": "text",
                "status": "ok",
                "file_id": "file-result",
            }]),
        },
    )
    assert persisted is not None
    first_provider_prefix = list(persisted.projected_items)

    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "effective_rollout_mode",
        on_mode,
    )
    second_ordinary = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "public sanitized answer"},
        {"role": "user", "content": "next"},
        {"role": "system", "content": "dynamic-v2"},
    ]
    second = await projection_module.prepare_plain_thread_projection(
        run_id="run-2",
        thread_id="thread-1",
        model="model-1",
        transport="responses",
        stable_base="stable",
        ordinary_provider_messages=second_ordinary,
        source_messages=second_ordinary[:-1],
        current_run_delta=[{"role": "user", "content": "next"}],
        display_history_before_run=[
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "public sanitized answer",
                "attachments_json": json.dumps([{
                    "filename": "result.pdf",
                    "kind": "text",
                    "status": "ok",
                    "file_id": "file-result",
                }]),
            },
        ],
        display_history_current=[
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "public sanitized answer",
                "attachments_json": json.dumps([{
                    "filename": "result.pdf",
                    "kind": "text",
                    "status": "ok",
                    "file_id": "file-result",
                }]),
            },
            {"role": "user", "content": "next"},
        ],
        world_state={"turn_guard": "dynamic-v2"},
    )
    assert second.provider_reuse_active is True
    assert second.provider_messages[:len(first_provider_prefix)] == first_provider_prefix
    assert {"role": "assistant", "content": "provider raw answer"} in second.provider_messages
    assert second.provider_messages != second_ordinary


@pytest.mark.asyncio
async def test_plain_projection_keeps_ordinary_body_when_canary_marker_fails(monkeypatch):
    from app.services.agent_harness import AgentMode, RunPhase, RunSnapshot
    from app.services.agent_harness import plan_store, run_store
    from app.services.agent_harness import thread_context_projection as projection_module

    run_snapshot = RunSnapshot(
        run_id="run-marker-fail",
        thread_id="thread-marker-fail",
        user_id="u1",
        agent_mode=AgentMode.STANDARD,
        phase=RunPhase.EXECUTING,
        state_version=1,
        goal_revision=0,
        plan_version=0,
        event_cursor=0,
    )

    async def get_run(_run_id):
        return run_snapshot

    async def get_plan(_run_id):
        return None

    async def enabled(*_args, **_kwargs):
        return "on"

    base_source = [{"role": "system", "content": "stable"}]
    ledger = ContextProjectionLedger()
    ledger.project(
        source_messages=base_source,
        snapshot=ContextCompiler().compile(ContextFacts(run=run_snapshot)),
        base_prompt_hash=canonical_hash({"system_prompt": "stable"}),
        tool_schema_hash=canonical_hash([]),
        transport="responses",
    )
    persisted = ledger.to_persisted_state(
        thread_id="thread-marker-fail",
        model="model-1",
        mode="shadow",
        display_history=[],
    )

    async def load(**_kwargs):
        return persisted

    async def mark(**_kwargs):
        return False

    monkeypatch.setattr(run_store, "get_run_snapshot", get_run)
    monkeypatch.setattr(plan_store, "get_plan_snapshot", get_plan)
    monkeypatch.setattr(projection_module.thread_projection_store, "load", load)
    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "effective_rollout_mode",
        enabled,
    )
    monkeypatch.setattr(
        projection_module.thread_projection_store,
        "mark_canary_candidate",
        mark,
    )

    ordinary = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "new"},
        {"role": "system", "content": "dynamic"},
    ]
    prepared = await projection_module.prepare_plain_thread_projection(
        run_id="run-marker-fail",
        thread_id="thread-marker-fail",
        model="model-1",
        transport="responses",
        stable_base="stable",
        ordinary_provider_messages=ordinary,
        source_messages=ordinary[:-1],
        current_run_delta=[{"role": "user", "content": "new"}],
        display_history_before_run=[],
        display_history_current=[{"role": "user", "content": "new"}],
        world_state={"turn_guard": "dynamic"},
    )

    assert prepared.provider_reuse_active is False
    assert prepared.provider_messages == ordinary
    assert prepared.eligibility_reason == "canary_marker_unavailable"
    assert prepared.commit_bundle["state"].mode == "shadow"
