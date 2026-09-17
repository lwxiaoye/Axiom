# -*- coding: utf-8 -*-
"""阶段 1：核工具 pin + 落库失败语义 + 答案去重（shipped 入口）。"""
from __future__ import annotations

import pytest

from app.services.chat.capability_broker import CapabilityBroker, build_capability_search_tool, resolve_core_pins
from app.services.chat.tools import shell as shell_tools
from app.services.chat.tools.base import MainTool, ToolSoftError
from app.services.chat.tools.shell import build_shell_tools
from app.services.chat.turn_decision import REVISION_TARGET_UNRESOLVED_GUIDANCE
from app.services.chat.turn_finalizer import collapse_exact_double_answer
from app.services.agent_harness import model_driver
from app.services.agent_harness.contracts import AgentMode, RunPhase, RunSnapshot
from app.services.agent_harness.policy import HarnessPolicy
from app.services.chat.tools import build_tools


def _tool(name: str) -> MainTool:
    async def execute(_args):
        return "ok"
    return MainTool(name=name, description=name, parameters={}, execute=execute)


def _visible(tools, *, scope: str):
    run = RunSnapshot(
        run_id="test-run", thread_id="test-thread", user_id="test-user",
        agent_mode=AgentMode.STANDARD, phase=RunPhase.EXECUTING,
        capability_scope=scope, state_version=0, goal_revision=0,
        plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    policy = HarnessPolicy()
    return [tool for tool in tools if policy.evaluate(tool.spec, run).allowed]


def test_core_executors_pinned_without_capability_search_prerequisite():
    names = [
        "ask_user_choice", "bash", "write_file", "edit_file", "read_file", "glob",
        "download_url", "fetch_ppt_asset", "search_web", "update_plan",
    ]
    tools = [_tool(n) for n in names]
    pinned = resolve_core_pins(action_authority="mutate", web_enabled=True)
    broker = CapabilityBroker(tools, pinned=pinned)
    search = build_capability_search_tool(broker)
    broker.register(search, active=True)
    initial = {t.name for t in broker.initial_tools()}
    for core in (
        "bash", "write_file", "edit_file", "read_file", "glob",
        "search_web", "fetch_ppt_asset",
    ):
        assert core in initial


def test_collapse_exact_double_answer():
    assert collapse_exact_double_answer("已新建 x。已新建 x。") == "已新建 x。"
    assert collapse_exact_double_answer("hello") == "hello"
    once = "这是正常的一段不会被误伤的答案。"
    assert collapse_exact_double_answer(once) == once


class _BashRes:
    def __init__(self, ok=True, stdout="ok", stderr="", exit_code=0, error=None,
                 workspace_changes=None):
        self.ok, self.stdout, self.stderr = ok, stdout, stderr
        self.exit_code, self.error, self.truncated = exit_code, error, False
        self.review = None
        self.workspace_changes = workspace_changes or [{"path": "a.md", "kind": "write"}]
        self.workspace_deleted = []
        self.workspace_oversized = []
        self.outputs_migrated = []
        self.outputs_conflicts = []


class _SyncPersistFailed:
    """模拟 WorkspaceSync：persist 后 persist_failed 非空（shipped bash 必升 ToolSoftError）。"""

    def __init__(self):
        self.persist_failed = []
        self.conflict_files = []

    async def load(self, mirror=None):
        return {"files": {}, "stamps": {}}

    async def prepare(self):
        return {}

    async def persist(self, _changes):
        self.persist_failed = ["a.md"]
        return []

    def notice(self):
        return ""

    def persist_notice(self):
        return "产物未能写入「我的文件」：a.md"


@pytest.mark.asyncio
async def test_bash_persist_failed_raises_tool_soft_error(monkeypatch):
    """真实 shipped 路径：sync.persist_failed 非空 → bash 抛 ToolSoftError（非字符串契约戏）。"""
    async def fake_execute_in_sandbox(command, **kwargs):
        return _BashRes(stdout="wrote", workspace_changes=[{"path": "a.md"}])

    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    monkeypatch.setattr(shell_tools, "build_sync", lambda *a, **kw: _SyncPersistFailed())

    tool = build_shell_tools(user_id="u-phase1", run_id="r-phase1", thread_id="th1")[0]
    with pytest.raises(ToolSoftError) as ei:
        await tool.execute({"command": "echo hi > /workspace/files/a.md"})
    msg = str(ei.value)
    assert "未能写入" in msg
    assert "我的文件" in msg
    assert "a.md" in msg


@pytest.mark.asyncio
async def test_build_tools_agent_mutate_includes_bash_and_files():
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-phase1",
        action_authority="mutate",
        turn_intent="execute",
        revision_mode=False,
        allow_create=True,
    )
    names = {t.name for t in tools}
    for core in ("bash", "write_file", "edit_file", "read_file", "glob"):
        assert core in names, f"missing {core} in {names}"


@pytest.mark.asyncio
async def test_build_tools_revision_unresolved_hard_gate():
    """修订目标未解析：Claude Code 式硬闸——写/执行工具物理收起，只留勘查 + ask_user。"""
    tools = await build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-phase1-rev",
        action_authority="mutate",
        turn_intent="revise",
        revision_mode=True,
        allow_create=False,
        revision_target=None,
    )
    names = {t.name for t in _visible(tools, scope="revision_pending")}
    for core in ("read_file", "glob"):
        assert core in names, f"revision unresolved must keep read core {core}, got {names}"
    for banned in ("bash", "write_file", "edit_file"):
        assert banned not in names, f"revision unresolved must hard-gate {banned}, got {names}"
    g = REVISION_TARGET_UNRESOLVED_GUIDANCE
    assert "ask_user_choice" in g
    assert "未开放" in g or "收起" in g


@pytest.mark.asyncio
async def test_build_tools_inspect_excludes_mutating_core():
    """inspect 保留 scratch bash，但不得暴露任何持久化写入能力。"""
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-phase1-inspect",
        action_authority="inspect",
        turn_intent="conversation",
        revision_mode=False,
        allow_create=False,
    )
    visible = _visible(tools, scope="inspect")
    names = {t.name for t in visible}
    for core in ("read_file", "glob", "bash"):
        assert core in names, f"inspect must keep {core}, got {names}"
    for banned in ("write_file", "edit_file"):
        assert banned not in names, f"inspect must not expose {banned}, got {names}"
    bash = next(tool for tool in visible if tool.name == "bash")
    assert bash.spec.effect_scope.value == "scratch"
    assert "artifact_producer" not in bash.spec.semantic_tags


@pytest.mark.asyncio
async def test_tool_v3_output_contract_and_projection():
    """V3 闸：canonical value 经强 Schema 校验后投影为 runtime observation。"""
    from app.services.chat.tools.base import ToolValue

    value = ToolValue(
        model_content="已新建 a.md",
        ui={"summary": "已新建 a.md"},
        artifacts=[{"filename": "a.md", "file_id": "f1"}],
    )
    async def execute(_args):
        return value
    tool = MainTool("write_file", "write", {}, execute, output_model=ToolValue)
    obs = await tool.observe({})
    assert obs.status == "succeeded"
    assert obs.model_content == "已新建 a.md"
    assert obs.artifacts == [{"filename": "a.md", "file_id": "f1"}]
    assert "properties" in tool.output_contract.json_schema


@pytest.mark.asyncio
async def test_tool_v3_contract_violation_becomes_typed_failure():
    from app.services.chat.tools.base import ToolValue

    async def invalid(_args):
        return "arbitrary legacy string"

    tool = MainTool("bad", "bad", {}, invalid, output_model=ToolValue)
    result, failed, _call_id = await model_driver._run_one_tool(
        tool, "bad", {}, None, [], observation_sink=(sink := {}),
    )
    assert failed is True
    assert "输出不符合" in result
    assert sink["observation"]["error_code"] == "tool_contract_violation"
