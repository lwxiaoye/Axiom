# -*- coding: utf-8 -*-
"""主对话状态契约反例：新指令修订与记忆写入必须由运行时事实解锁。"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.agent_harness import model_driver
from app.services.chat.capability_broker import (
    detect_explicit_memory_tools,
    resolve_core_pins,
)
from app.services.agent_harness import memory_tools
from app.services.chat.tools.base import MainTool, ToolValue
from app.services.chat.main_tool_turn import _merge_tool_result_meta


DONE = "data: [DONE]"


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def _tool_call(call_id: str, name: str, args: dict) -> str:
    return _sse({
        "tool_calls": [{
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    })


class _Response:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _Client:
    responses: list = []
    requests: list = []
    last_response: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, _method, _url, json=None, headers=None):  # noqa: A002
        payload = dict(json or {})
        payload["messages"] = [dict(item) for item in payload.get("messages") or []]
        type(self).requests.append(payload)
        if type(self).responses:
            type(self).last_response = type(self).responses.pop(0)
        return _Response(type(self).last_response)


def _artifact_tool(name: str, calls: list[dict]) -> MainTool:
    async def execute(args):
        calls.append(dict(args or {}))
        return ToolValue(
            status="succeeded",
            model_content=f"已保存 报告.docx（{name}）",
            artifacts=[{"file_id": f"f-{len(calls)}", "filename": "报告.docx"}],
        )

    return MainTool(
        name=name,
        description=name,
        parameters={},
        execute=execute,
        output_model=ToolValue,
        internal=True,
        effect_scope="user_files",
        idempotent=False,
        resource_locks=("user-files",),
    )


@pytest.mark.asyncio
async def test_new_instruction_opens_a_new_artifact_revision(monkeypatch):
    """旧版本已落盘后收到“补一节”：edit_file 必须执行，不能被旧交付护栏当返工拦掉。

    注意这里不再让模型先「空口说已更新」再等 Harness 推回：Codex 式回合语义下，一条不带
    tool call 的 assistant 消息就结束本回合（_try_continue_unverified_completion 恒 False，
    验证缺口由 CompletionVerifier / finalize_terminal 在循环外裁决）。本用例只钉住：插话打开
    新修订代次后，同代次的 edit_file 照常执行并带上新的 revision_epoch。
    """
    write_calls: list[dict] = []
    edit_calls: list[dict] = []
    injected = False

    async def inject_once(_gateway, messages, state=None):
        nonlocal injected
        if injected:
            return
        injected = True
        messages.append({"role": "user", "content": "补充风险说明一节"})
        assert state is not None, "注入指令必须同时推进运行时修订代次"
        state.begin_input_revision(
            "instr-1",
            content="补充风险说明一节",
        )
        yield {
            "type": "instruction_applied",
            "input_id": "instr-1",
            "content": "补充风险说明一节",
            "scope": "turn",
            "revision_epoch": state.revision_epoch,
        }

    _Client.responses = [
        [_tool_call("write-1", "write_file", {"path": "报告.docx"}), DONE],
        # 插话在 write 回执之后注入；模型据此在新修订代次里调 edit_file
        [_tool_call("edit-1", "edit_file", {"path": "报告.docx", "change": "补充风险说明"}), DONE],
        [_sse({"content": "报告.docx 已按追加要求更新。"}), DONE],
        [_sse({"content": "报告.docx 已按追加要求更新。"}), DONE],
    ]
    _Client.requests = []
    _Client.last_response = []

    monkeypatch.setattr(model_driver, "_inject_run_inputs", inject_once)
    # durable-before-visible：带 run_id 的网关会先把 observation 落 Runtime 库再发事件，
    # 单测环境没有 Runtime 库（fail_closed 直接抛）——这里只验状态机，落库打桩。
    from app.services.tasks import task_run_service
    monkeypatch.setattr(task_run_service, "record_tool_observations", lambda *_a, **_k: _async(0))
    from app.services.agent_harness import run_store
    from app.services.agent_harness.contracts import AgentMode, RunPhase, RunSnapshot
    monkeypatch.setattr(
        run_store,
        "get_run_snapshot",
        lambda _run_id: _async(RunSnapshot(
            run_id="r1",
            thread_id="t1",
            user_id="u1",
            agent_mode=AgentMode.STANDARD,
            phase=RunPhase.EXECUTING,
            state_version=1,
            goal_revision=0,
            plan_version=0,
            event_cursor=0,
            execution_profile={"id": "interactive"},
        )),
    )
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [event async for event in model_driver.drive_model(
            model="m",
            api_key="k",
            user_input="生成报告.docx",
            raw_user_message="生成报告.docx",
            tools=[
                _artifact_tool("write_file", write_calls),
                _artifact_tool("edit_file", edit_calls),
            ],
            gateway={"run_id": "r1", "thread_id": "t1", "user_id": "u1", "steerable": True},
        )]

    assert len(write_calls) == 1
    assert len(edit_calls) == 1, "追加要求后的合法修订被 product_block_rework_after_deliverable 误杀"
    assert any(event.get("type") == "instruction_applied" for event in events)
    edit_results = [
        event for event in events
        if event.get("type") == "tool_result" and event.get("name") == "edit_file"
    ]
    assert edit_results and edit_results[0]["status"] == "completed"
    # 修订代次：write 在第 0 代，插话后 edit 落在第 1 代
    write_results = [
        event for event in events
        if event.get("type") == "tool_result" and event.get("name") == "write_file"
    ]
    assert write_results[0]["revision_epoch"] == 0
    assert edit_results[0]["revision_epoch"] == 1
    assert events[-1]["type"] == "final"


def test_revision_cannot_commit_without_successful_same_epoch_write():
    state = model_driver.LoopState()
    state.begin_input_revision(
        "instr-2",
        content="把同一个文件里的版本A改为版本B",
    )
    assert state.revision_requires_mutation is True
    assert state.commit_revision() is False
    assert state.revision_open is True
    read_tool = _artifact_tool("reader", [])
    read_tool.spec = read_tool.spec.model_copy(update={"semantic_tags": frozenset({"investigate"})})
    assert state.record_revision_mutation(read_tool) is False
    assert state.commit_revision() is False
    assert state.record_revision_mutation(_artifact_tool("editor", [])) is True
    assert state.commit_revision() is True
    assert state.revision_open is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("请记住我以后都要简短回答", {"remember_fact"}),
        ("忘记我之前的称呼偏好", {"forget_memory"}),
        ("删除那条长期记忆", {"forget_memory"}),
        ("你记得什么是单调栈吗？", set()),
        ("介绍一下记忆功能", set()),
    ],
)
def test_explicit_memory_intent_deterministically_pins_mutation_tool(text, expected):
    detected = detect_explicit_memory_tools(text)
    assert detected == expected
    pinned = resolve_core_pins(
        action_authority="mutate",
        explicit_memory_tools=detected,
    )
    assert expected <= pinned


@pytest.mark.asyncio
async def test_memory_write_returns_a_verifiable_receipt(monkeypatch):
    monkeypatch.setattr(memory_tools.memory_service, "is_enabled", lambda _uid: _async(True))
    monkeypatch.setattr(memory_tools.memory_service, "store_memory", lambda **_kw: _async(("mem-123", True)))
    from app.services.memory import personalization_service
    monkeypatch.setattr(personalization_service, "auto_manage_enabled", lambda _uid: _async(True))

    # 记忆治理：写入必须逐字引用本轮用户原话（source_quote），且原话只能来自平台持有的
    # 用户消息——这里没有 thread/run，来源就是 build 时传入的 user_message。
    tools = memory_tools.build_memory_tools(user_id="u1", user_message="请记住：以后回答简短")
    remember = next(tool for tool in tools if tool.name == "remember_fact")
    observation = await remember.observe({
        "type": "preference",
        "content": "以后回答简短",
        "user_requested": True,
        "source_quote": "以后回答简短",
    })

    assert observation.status == "succeeded"
    assert observation.receipts == [{
        "kind": "memory",
        "action": "created",
        "id": "mem-123",
    }]


def test_unverified_memory_success_claim_is_replaced_with_truthful_failure():
    assert model_driver.scrub_unverified_memory_claim(
        "好的，已经记住了。",
        user_message="请记住我以后回答要简短",
        trace=[],
    ) == "未能确认这条信息已写入长期记忆，本次不能声称已经记住。请重试。"

    verified_trace = [{
        "name": "remember_fact",
        "status": "completed",
        "observation": {
            "status": "succeeded",
            "receipts": [{"kind": "memory", "action": "created", "id": "mem-123"}],
        },
    }]
    assert model_driver.scrub_unverified_memory_claim(
        "好的，已经记住了。",
        user_message="请记住我以后回答要简短",
        trace=verified_trace,
    ) == "好的，已经记住了。"


@pytest.mark.asyncio
async def test_tool_loop_final_cannot_publish_unverified_memory_success():
    _Client.responses = [[_sse({"content": "好的，已经记住了。"}), DONE]]
    _Client.requests = []
    _Client.last_response = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [event async for event in model_driver.drive_model(
            model="m",
            api_key="k",
            user_input="请记住我以后回答要简短",
            raw_user_message="请记住我以后回答要简短",
            tools=[],
        )]

    final = next(event for event in events if event.get("type") == "final")
    assert final["answer"] == "未能确认这条信息已写入长期记忆，本次不能声称已经记住。请重试。"


def test_revision_state_recovers_from_suspended_message_cursor():
    messages = [
        {"role": "user", "content": "生成报告.docx"},
        {"role": "user", "content": model_driver._format_run_input("补一节风险说明", [])},
    ]
    recovered = model_driver._recover_loop_state(messages, {})
    state = model_driver.LoopState.recover_from(recovered)
    assert state.revision_epoch == 1
    assert state.revision_open is True
    assert state.revision_requires_mutation is True
    assert state.revision_mutation_verified is False


def test_loop_state_reconciles_stale_delivery_flags_before_revision_tool_choice():
    state = model_driver.LoopState(
        revision_epoch=1,
        revision_open=True,
        delivery_checked=True,
        bare_confirm_only=True,
        force_converge="product_delivered",
    )
    repairs = state.reconcile_policy()
    assert set(repairs) == {
        "delivery_checked_overridden",
        "bare_confirm_overridden",
        "delivery_convergence_overridden",
    }
    assert state.delivery_checked is False
    assert state.bare_confirm_only is False
    assert state.force_converge == ""


def test_real_resource_convergence_survives_revision_reconciliation():
    state = model_driver.LoopState(
        revision_epoch=1,
        revision_open=True,
        force_converge="stagnation",
    )
    assert state.reconcile_policy() == ()
    assert state.force_converge == "stagnation"


def test_memory_receipt_is_persistable_without_memory_content():
    meta = _merge_tool_result_meta({
        "name": "remember_fact",
        "observation": {
            "ui": {"summary": "长期记忆", "action": "已记录"},
            "receipts": [{
                "kind": "memory",
                "action": "created",
                "id": "mem-123",
                "content": "不应进入事件审计的正文",
            }],
        },
    }, {})
    assert meta["receipts"] == [{
        "kind": "memory",
        "action": "created",
        "id": "mem-123",
    }]
    assert "content" not in str(meta)


async def _async(value):
    return value
