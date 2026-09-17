from __future__ import annotations

import asyncio

from app.services.agent_harness import (
    AgentMode,
    EffectScope,
    ExecutionProfileId,
    ObservationStatus,
    RegisteredTool,
    RunPhase,
    RunSnapshot,
    ToolDispatcher,
    ToolObservation,
    ToolRegistry,
    ToolRequest,
    ToolSpec,
)


def _run(mode=AgentMode.STANDARD, phase=RunPhase.EXECUTING):
    return RunSnapshot(
        run_id="r1", thread_id="t1", user_id="u1", agent_mode=mode, phase=phase,
        state_version=1, goal_revision=2, plan_version=3, event_cursor=0,
        execution_profile={"id": "interactive"},
    )


def _tool(name, execute, *, scope=EffectScope.NONE, parallel=True, locks=()):
    return RegisteredTool(
        spec=ToolSpec(
            name=name,
            description=name,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            capability=f"test.{name}",
            effect_scope=scope,
            idempotent=scope is EffectScope.NONE,
            parallel_safe=parallel,
            resource_locks=locks,
        ),
        execute=execute,
    )


def test_unknown_tool_is_rejected_without_execution():
    dispatcher = ToolDispatcher(ToolRegistry())
    result = asyncio.run(dispatcher.dispatch(
        _run(), (ToolRequest(call_id="c1", name="missing", arguments={}),),
    ))
    assert result[0].status is ObservationStatus.REJECTED
    assert result[0].error_code == "unknown_tool"


def test_parallel_safe_reads_run_together_but_write_is_an_exclusive_barrier():
    active = 0
    peak = 0
    order = []

    async def read(_args, context):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        order.append(context.call_id)
        active -= 1
        return ToolObservation(
            call_id=context.call_id, tool_name="read", status=ObservationStatus.SUCCEEDED,
        )

    async def write(_args, context):
        assert active == 0
        order.append(context.call_id)
        return ToolObservation(
            call_id=context.call_id, tool_name="write", status=ObservationStatus.SUCCEEDED,
        )

    registry = ToolRegistry((
        _tool("read_a", read),
        _tool("read_b", read),
        _tool("write", write, scope=EffectScope.USER_FILES, parallel=False, locks=("files",)),
    ))
    result = asyncio.run(ToolDispatcher(registry).dispatch(_run(), (
        ToolRequest(call_id="a", name="read_a", arguments={}),
        ToolRequest(call_id="b", name="read_b", arguments={}),
        ToolRequest(call_id="w", name="write", arguments={}),
    )))
    assert peak == 2
    assert order[-1] == "w"
    assert all(item.status is ObservationStatus.SUCCEEDED for item in result)


def test_idempotency_key_includes_goal_revision():
    run = _run()
    key = ToolDispatcher.idempotency_key(
        run, ToolRequest(call_id="c1", name="read_a", arguments={"q": "1"}),
    )
    run2 = run.model_copy(update={"goal_revision": 9})
    key2 = ToolDispatcher.idempotency_key(
        run2, ToolRequest(call_id="c1", name="read_a", arguments={"q": "1"}),
    )
    assert key != key2
    assert len(key) == 64


def test_plan_investigation_physically_rejects_user_file_write():
    called = False

    async def write(_args, context):
        nonlocal called
        called = True
        return ToolObservation(
            call_id=context.call_id, tool_name="write", status=ObservationStatus.SUCCEEDED,
        )

    registry = ToolRegistry((
        _tool("write", write, scope=EffectScope.USER_FILES, parallel=False, locks=("files",)),
    ))
    result = asyncio.run(ToolDispatcher(registry).dispatch(
        _run(AgentMode.PLAN, RunPhase.PLANNING),
        (ToolRequest(call_id="w", name="write", arguments={}),),
    ))
    assert called is False
    assert result[0].error_code == "phase_not_allowed"


def test_execution_profile_controls_visibility_and_pre_execution_authorization():
    called = False

    async def execute(_args, context):
        nonlocal called
        called = True
        return ToolObservation(
            call_id=context.call_id,
            tool_name="interactive_only",
            status=ObservationStatus.SUCCEEDED,
        )

    registered = _tool("interactive_only", execute)
    registered = RegisteredTool(
        spec=registered.spec.model_copy(update={
            "allowed_execution_profiles": frozenset({ExecutionProfileId.INTERACTIVE}),
        }),
        execute=registered.execute,
    )
    registry = ToolRegistry((registered,))
    artifact_run = _run().model_copy(update={
        "execution_profile": {"id": "artifact_coding"},
    })
    dispatcher = ToolDispatcher(registry)
    assert dispatcher.visible_specs(artifact_run) == ()
    result = asyncio.run(dispatcher.dispatch(
        artifact_run,
        (ToolRequest(call_id="c1", name="interactive_only", arguments={}),),
    ))
    assert called is False
    assert result[0].error_code == "execution_profile_not_allowed"
