# -*- coding: utf-8 -*-
"""危险动作 → 审批网关的**贯通**验证（2026-07-27）。

单测 danger.assess 只证明「判据对」，不证明「判据接上了」。这里从 `_run_one_tool` 打进去，
用真的（sqlite 内存版）网关表，确认三件事：

1. 危险命令 → 不执行、回 needs_approval、审批卡拿到**具体理由**；
2. 普通命令 → 照常执行，不弹任何东西（这条同样重要：审批弹太勤等于没有审批）；
3. 用户点「通过」后重试 → 同一幂等键命中 approved 行，真正执行一次。
"""
import pytest
import pytest_asyncio
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base
from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool
from app.services.gateway import tool_gateway


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    return "TEXT"


@pytest_asyncio.fixture
async def gw(monkeypatch):
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness import run_store

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tool_gateway, "async_session", factory)
    async def _snapshot(run_id: str):
        return RunSnapshot(
            run_id=run_id, thread_id="t-1", user_id="u-1",
            agent_mode="standard", phase="executing", capability_scope="default",
            state_version=1, goal_revision=0, plan_version=0, event_cursor=0,
            execution_profile={"id": "interactive"},
        )
    monkeypatch.setattr(run_store, "get_run_snapshot", _snapshot)
    yield factory
    await engine.dispose()


def _bash_tool(ran: list) -> MainTool:
    async def _exec(args: dict) -> str:
        ran.append(str(args.get("command") or ""))
        return "（已执行）"

    return MainTool(
        name="bash", description="沙箱命令", parameters={"type": "object", "properties": {}},
        execute=_exec, approval_policy="conditional",
    )


GATEWAY = {"thread_id": "t-1", "user_id": "u-1", "run_id": "r-1"}


@pytest.mark.asyncio
async def test_destructive_bash_is_held_for_approval(gw):
    ran: list = []
    sink: list = []
    text, failed, call_id = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", {"command": "rm -rf /workspace/files"},
        GATEWAY, sink, tool_call_id="c-1",
    )
    assert ran == [], "危险命令在审批前绝不能执行"
    assert failed is False          # 等待审批不是失败
    assert call_id
    assert "审批" in text
    assert sink and sink[0]["toolName"] == "bash"
    # 理由要一路带到卡片：只说「bash 需要确认」用户判断不了该不该点
    assert "删除" in sink[0]["reason"]
    assert "删除" in text


@pytest.mark.asyncio
async def test_ordinary_bash_runs_without_any_approval(gw):
    ran: list = []
    sink: list = []
    text, failed, _ = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", {"command": "ls /workspace/files"},
        GATEWAY, sink, tool_call_id="c-2",
    )
    assert ran == ["ls /workspace/files"], "普通命令必须照常执行"
    assert failed is False
    assert sink == [], "普通命令弹审批 = 训练用户闭眼点通过"
    assert "已执行" in text


@pytest.mark.asyncio
async def test_after_approval_the_retry_actually_executes(gw):
    ran: list = []
    sink: list = []
    cmd = {"command": "rm -rf /workspace/tmp/build"}

    _, _, call_id = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-3",
    )
    assert ran == []

    await tool_gateway.approve(call_id=call_id, user_id="u-1")

    # 重试：同参数 → 同 thread 级幂等键 → 命中刚批准的那一行
    text, failed, _ = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-4",
    )
    assert ran == ["rm -rf /workspace/tmp/build"], "审批通过后应真正执行一次"
    assert failed is False
    assert "已执行" in text


@pytest.mark.asyncio
async def test_rejected_call_never_executes_but_can_be_asked_again(gw):
    """拒绝**不得**在会话里永久粘住（2026-07-28 修）。

    修之前：thread 级幂等键落到 rejected 之后仍被命中 —— 该命令在这个会话里**永远**
    执行不了，回执恒为「该操作已被你拒绝，未执行」，而且没有任何线索告诉模型这是上一次
    的历史判定。用户改主意想执行也没有任何办法。

    修之后：不变量仍然是"没批准就绝不执行"，但再次发起会重新走一次审批 —— 危险动作再来
    一次本来就该再问一次人，而不是拿上一次的判定替用户做决定。
    """
    ran: list = []
    sink: list = []
    cmd = {"command": "mkfs.ext4 /dev/sda1"}

    _, _, call_id = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-5",
    )
    await tool_gateway.reject(call_id=call_id, user_id="u-1")

    text, failed, call_id2 = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-6",
    )
    assert ran == [], "被拒绝的操作重试也不能执行"
    assert "审批" in text and call_id2 != call_id, "要重新申请审批，而不是回放上次的拒绝"
    # 用户这次点通过 → 才真正执行
    await tool_gateway.approve(call_id=call_id2, user_id="u-1")
    _text, _failed, _ = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-7",
    )
    assert ran == ["mkfs.ext4 /dev/sda1"], "重新审批通过后应能执行"


@pytest.mark.asyncio
async def test_same_dangerous_command_twice_is_not_served_from_cache(gw):
    """同一会话里两次发出**完全相同**的危险命令，第二次必须重新审批 + 重新执行。

    修之前：thread 级幂等键命中 completed 行，第二次**根本不执行**，直接把第一次的 stdout
    标 cached 回给模型 —— 模型以为清理成功了，而那些文件还在。危险动作的"缓存命中"是
    最不该有的那一种：它偏偏对有副作用的操作说谎。
    """
    ran: list = []
    sink: list = []
    cmd = {"command": "rm -rf /workspace/tmp/build"}

    _, _, cid1 = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-10")
    await tool_gateway.approve(call_id=cid1, user_id="u-1")
    await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-11")
    assert len(ran) == 1

    # 第二次发同一条命令：不能吃缓存，要重新问
    text, _failed, cid2 = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-12")
    assert len(ran) == 1, "没批准就不能执行"
    assert "审批" in text and cid2 != cid1
    await tool_gateway.approve(call_id=cid2, user_id="u-1")
    await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-13")
    assert len(ran) == 2, "第二次批准后必须真的再执行一次，而不是回放上次的输出"


@pytest.mark.asyncio
async def test_approval_survives_a_new_run(gw):
    """审批卡与重发之间隔着一整个 Run：稳定键在**审批回合还开着**时必须继续命中同一行，
    否则用户点了「通过」，模型再发一次却建了条新记录，又要审批一次，永远执行不了。"""
    ran: list = []
    sink: list = []
    cmd = {"command": "rm -rf /workspace/files/old"}

    _, _, cid = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-20")
    await tool_gateway.approve(call_id=cid, user_id="u-1")

    later_run = {**GATEWAY, "run_id": "r-2"}   # 用户点通过后，模型在**新的 Run** 里重发
    _text, failed, _ = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, later_run, sink, tool_call_id="c-21")
    assert ran == ["rm -rf /workspace/files/old"]
    assert failed is False


@pytest.mark.asyncio
async def test_later_safe_tool_path_supersedes_obsolete_approval(gw):
    """A blocked destructive attempt must not leave a card after the model changes course."""
    ran: list = []
    sink: list = []
    cmd = {"command": "rm -rf /workspace/tmp/deck && mkdir /workspace/tmp/deck"}

    _, _, stale_call_id = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-30",
    )
    assert sink and ran == []

    superseded = await model_driver._supersede_pending_approvals(sink, GATEWAY)
    assert superseded == 1
    assert sink == [], "已被安全替代路径取代的审批不得冒泡到最终答复"

    stale = await tool_gateway.get_call(stale_call_id, "u-1")
    assert stale and stale["status"] == "rejected"
    assert stale["approvedBy"] is None, "系统失效不能伪装成用户拒绝"
    assert stale["error"] == "superseded_by_later_tool_batch"

    safe = {"command": "mkdir -p /workspace/tmp/deck && unzip -o source.pptx -d /workspace/tmp/deck"}
    _, safe_failed, _ = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", safe, GATEWAY, sink, tool_call_id="c-safe",
    )
    assert safe_failed is False
    assert ran == [safe["command"]]
    assert sink == [], "安全替代命令不应产生新审批"

    # If the model truly asks for the same dangerous action again, it starts a fresh approval
    # episode instead of inheriting the obsolete request or silently executing it.
    _, _, fresh_call_id = await model_driver._run_one_tool(
        _bash_tool(ran), "bash", cmd, GATEWAY, sink, tool_call_id="c-31",
    )
    assert ran == [safe["command"]]
    assert sink and fresh_call_id != stale_call_id
