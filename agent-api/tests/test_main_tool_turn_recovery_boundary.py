"""Focused contract tests for the main tool-turn exception boundary."""

import ast
import inspect
from pathlib import Path

import pytest

from app.services.chat.main_tool_turn import (
    _persist_recovery_evidence,
    run_agent_turn,
)

AGENT_API_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_exception_checkpoint_saves_observations_and_interrupted_draft():
    calls = []
    trace = [{
        "name": "execute_in_sandbox",
        "args": {"command": "write output"},
        "status": "succeeded",
        "observation": {"status": "succeeded", "artifact_refs": [{"id": "f1"}]},
    }]

    async def record_observations(run_id, received_trace, *, fail_closed):
        calls.append(("observations", run_id, received_trace, fail_closed))

    def persist_draft(run_id, thread_id, content, *, status):
        calls.append(("draft", run_id, thread_id, content, status))

    out = {"trace": trace, "answer": "已经写出一部分", "streamed_any": True}
    await _persist_recovery_evidence(
        run_id="run-1",
        thread_id="thread-1",
        out=out,
        assistant_persisted=False,
        spawn_partial_persist=persist_draft,
        record_tool_observations=record_observations,
        error=RuntimeError("model gateway reset"),
    )

    assert calls == [
        ("observations", "run-1", trace, True),
        ("draft", "run-1", "thread-1", "已经写出一部分", "interrupted"),
    ]
    assert "run_disposition" not in out
    assert "completion_interrupted" not in out


@pytest.mark.asyncio
async def test_evidence_persistence_failure_does_not_replace_original_exception():
    async def record_observations(*_args, **_kwargs):
        raise RuntimeError("runtime store unavailable")

    def persist_draft(*_args, **_kwargs):
        raise RuntimeError("mysql unavailable")

    await _persist_recovery_evidence(
        run_id="run-2",
        thread_id="thread-2",
        out={
            "trace": [{"name": "bash", "observation": {"status": "failed"}}],
            "answer": "可见草稿",
            "streamed_any": True,
        },
        assistant_persisted=False,
        spawn_partial_persist=persist_draft,
        record_tool_observations=record_observations,
        error=TimeoutError("sandbox timeout"),
    )


def _outer_execution_exception_handler():
    tree = ast.parse(
        (AGENT_API_ROOT / "app/services/chat/main_tool_turn.py").read_text(encoding="utf-8")
    )
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "run_agent_turn"
    )
    for node in ast.walk(function):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
            continue
        if node.name == "e":
            return node
    raise AssertionError("run_agent_turn execution exception handler not found")


def test_execution_exception_is_rethrown_without_a_second_terminal_route():
    handler = _outer_execution_exception_handler()
    calls = [
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    ]
    assigned_names = [
        node.targets[0].id
        for node in ast.walk(handler)
        if isinstance(node, ast.Assign)
        and node.targets
        and isinstance(node.targets[0], ast.Name)
    ]

    assert "_persist_recovery_evidence" in calls
    assert "finalize_terminal" not in calls
    assert "run_disposition" not in assigned_names
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "fallback_plain"
        for node in ast.walk(handler)
    )
    assert any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def test_cancel_and_generator_exit_handler_still_reraises():
    source = inspect.getsource(run_agent_turn)
    assert "except (asyncio.CancelledError, GeneratorExit):" in source
    assert "env.spawn_partial_persist(run_id, thread_id, out[\"answer\"])" in source
    assert source.count("                raise") >= 2
