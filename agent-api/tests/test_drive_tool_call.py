"""_drive_tool_call 心跳节奏与进度透传（主对话过程体验升级 §2）。

验收基线：bash 长任务（PPT/Word 60s+）执行阶段「任意连续两秒内都能收到
进度或心跳」（心跳间隔 1s）。单测把间隔等比压缩后验证同一逻辑——节奏、
elapsed_ms 单调、阶段透传、跨工具不串线、失败经 outcome 上报。
"""
import asyncio
import time

import pytest

from app.services.agent_harness.model_driver import MainTool, _drive_tool_call


def _tool(fn) -> MainTool:
    # 名字必须是**现役执行器**（2026-07-29）：心跳判据是 `name in _ARTIFACT_EXECUTORS`，
    # 而下线的 execute_in_sandbox 已不在那个集合里 —— 用它当夹具，"长任务持续有反馈"这条测的就变成
    # "不发心跳的工具确实没发心跳"，与标题声称的正好相反，却只表现为一句 `0 >= 8`。
    return MainTool(name="bash", description="test", parameters={}, execute=fn)


async def _collect(tool, queue, outcome, interval):
    events, stamps = [], []
    async for ev in _drive_tool_call(
        tool, "bash", {}, None, None,
        tool_progress_queue=queue, outcome=outcome, heartbeat_interval=interval,
    ):
        events.append(ev)
        stamps.append(time.monotonic())
    return events, stamps


@pytest.mark.asyncio
async def test_heartbeat_cadence_during_long_blocking_execution():
    interval = 0.1  # 生产 1s；等比压缩（验收阈值 2s → 2×interval）

    async def slow_ppt_job(_args):
        await asyncio.sleep(interval * 12)  # 单次阻塞调用，期间无真实阶段可报
        return "done"

    outcome: dict = {}
    events, stamps = await _collect(_tool(slow_ppt_job), asyncio.Queue(), outcome, interval)

    assert outcome["result"] == "done" and outcome["failed"] is False
    assert len(events) >= 8, "长任务执行期间必须持续有反馈"
    assert all(ev["type"] == "tool_progress" and ev.get("heartbeat") for ev in events)
    # 任意连续两个事件间隔 ≤ 2×心跳间隔（等比对应「任意连续两秒」；留调度抖动余量）
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert max(gaps, default=0) <= interval * 2 + 0.15
    # 已用时单调不减、不伪造百分比
    elapsed = [ev["elapsed_ms"] for ev in events]
    assert elapsed == sorted(elapsed)
    assert all("%" not in str(ev.get("label", "")) for ev in events)


@pytest.mark.asyncio
async def test_stage_events_pass_through_and_tail_drained_after_completion():
    queue: asyncio.Queue = asyncio.Queue()

    async def job(_args):
        await queue.put({"name": "bash", "stage": "creating", "label": "正在创建安全执行环境", "detail": {}})
        await asyncio.sleep(0.05)
        await queue.put({"name": "other_tool", "stage": "x", "label": "不该出现", "detail": {}})
        # 完成前一刻入队：终态 drain 必须把队列尾巴吐干净，saved 阶段不丢
        await queue.put({"name": "bash", "stage": "saved", "label": "已生成并保存 1 个文件", "detail": {}})
        return "ok"

    outcome: dict = {}
    events, _ = await _collect(_tool(job), queue, outcome, 0.02)

    stages = [ev["stage"] for ev in events if not ev.get("heartbeat")]
    assert stages == ["creating", "saved"], "阶段按序透传；其它工具的进度不串线"
    assert outcome["result"] == "ok" and outcome["failed"] is False


@pytest.mark.asyncio
async def test_tool_failure_reported_via_outcome():
    async def boom(_args):
        raise RuntimeError("沙箱炸了")

    outcome: dict = {}
    events, _ = await _collect(_tool(boom), asyncio.Queue(), outcome, 0.02)

    assert outcome["failed"] is True
    assert "工具执行失败" in outcome["result"]
    assert all(ev["type"] == "tool_progress" for ev in events)


@pytest.mark.asyncio
async def test_unknown_tool_reply_lists_available_tools():
    """未知工具回执必须教路（2026-07-27 真机：提示词与注册表脱节时，模型调
    write_file/bash 连败 4 次才被错误指纹拦住——因为回执没告诉它有哪些工具可用）。"""
    outcome: dict = {}
    async for _ in _drive_tool_call(
        None, "write_file", {}, None, None,
        outcome=outcome, heartbeat_interval=0.02,
        available_tools=["bash", "edit_file", "glob"],
    ):
        pass

    assert outcome["failed"] is True
    assert "未知工具 write_file" in outcome["result"]
    # 清单按字典序稳定输出，便于模型逐一核对
    assert "bash, edit_file, glob" in outcome["result"]
    assert "不要再调用不存在的工具" in outcome["result"]


@pytest.mark.asyncio
async def test_unknown_tool_reply_without_roster_still_names_the_tool():
    """未注入清单（直调/旧调用点）时不编造，但至少点名是哪个工具未知。"""
    outcome: dict = {}
    async for _ in _drive_tool_call(
        None, "write_file", {}, None, None, outcome=outcome, heartbeat_interval=0.02,
    ):
        pass

    assert outcome["failed"] is True
    assert outcome["result"] == "未知工具 write_file"


# ---------- 单工具超时：cancel 之后必须收敛（2026-07-28 P2） ----------
#
# 原实现是 `task.cancel()` 后**直接 return，从不 await**。三个后果都真实发生过：
# ① 主循环立刻拿着「已中止」回执进下一轮、甚至再起一个 bash，而被取消的协程要到下一个
#    await 点才收敛 —— 两条调用对同一个沙箱会话重叠；
# ② task 若恰在 cancel 落地前以**异常**完成，结果永不被取回，asyncio 打
#    "Task exception was never retrieved"，真故障被噪声淹没；
# ③ 它也可能刚好**成功**完成 —— 那份结果（含已落库的产物清单）被白扔掉，
#    等于把一次成功谎报成中止。

async def _drive(tool, *, timeout_s, interval=0.02, outcome=None):
    import app.services.agent_harness.model_driver as ma

    events = []
    async for ev in _drive_tool_call(
        tool, tool.name, {}, None, None, outcome=outcome,
        heartbeat_interval=interval,
    ):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_timeout_takes_the_real_result_when_the_task_lands_first(monkeypatch):
    """cancel 与「工具刚好完成」撞车：必须用真结果，不能谎报中止。

    这条不是理论竞态 —— bash 的收尾正是「读回产物 + 落库」，它落在 cancel 前后只差毫秒。
    """
    import app.services.agent_harness.model_driver as ma
    monkeypatch.setattr(ma.settings, "TOOL_CALL_TIMEOUT_SECONDS", 0.05, raising=False)

    async def lands_right_at_the_deadline(_args):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # 收尾（真实场景=把沙箱里的产物落库）跑完再交出结果，而不是被打断就什么都不做
            return "已保存到「我的文件」：报告.pptx"

    outcome: dict = {}
    await _drive(_tool_named("bash", lands_right_at_the_deadline),
                 timeout_s=0.05, outcome=outcome)
    assert outcome["result"] == "已保存到「我的文件」：报告.pptx"
    assert outcome["failed"] is False, "工具自己收敛出了结果，不能算失败"


@pytest.mark.asyncio
async def test_timeout_awaits_the_cancelled_task_before_returning(monkeypatch):
    """不 await 就返回 → 主循环下一轮起新调用时，上一个还在跑（沙箱会话重叠）。"""
    import app.services.agent_harness.model_driver as ma
    monkeypatch.setattr(ma.settings, "TOOL_CALL_TIMEOUT_SECONDS", 0.05, raising=False)

    state = {"converged": False}

    async def slow_cleanup(_args):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["converged"] = True
            raise

    outcome: dict = {}
    await _drive(_tool_named("bash", slow_cleanup), timeout_s=0.05, outcome=outcome)
    assert state["converged"] is True, "返回前必须等被取消的工具真正收敛"
    assert outcome["failed"] is True
    # 「已中止」必须**如实**说清副作用可能已经发生、产物可能没保存 ——
    # 否则模型据此换路，而副作用还在。
    assert "可能没能保存" in outcome["result"] or "没能保存到" in outcome["result"]
    assert "不要据此告诉用户产物已交付" in outcome["result"]


@pytest.mark.asyncio
async def test_timeout_consumes_exception_instead_of_leaking_it(monkeypatch):
    """cancel 前以异常完成：结果要被取回并当成普通工具失败，不能变成未处理异常噪声。"""
    import app.services.agent_harness.model_driver as ma
    monkeypatch.setattr(ma.settings, "TOOL_CALL_TIMEOUT_SECONDS", 0.05, raising=False)

    async def blows_up_at_the_deadline(_args):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise RuntimeError("落库时数据库连接断了")

    outcome: dict = {}
    await _drive(_tool_named("bash", blows_up_at_the_deadline), timeout_s=0.05, outcome=outcome)
    assert outcome["failed"] is True
    assert "落库时数据库连接断了" in outcome["result"]


@pytest.mark.asyncio
async def test_tool_can_see_its_own_deadline(monkeypatch):
    """长跑工具要能把自己的内部超时收进死线之内 —— 那是 bash 产物不再凭空消失的根本办法：
    命令被沙箱按 exit 124 正常终止时工具照常返回、产物照常落库；被主循环 cancel 则一个都存不下。"""
    import app.services.agent_harness.model_driver as ma
    from app.services.chat.tools.base import remaining_tool_budget_s
    monkeypatch.setattr(ma.settings, "TOOL_CALL_TIMEOUT_SECONDS", 30, raising=False)

    seen: dict = {}

    async def peek(_args):
        seen["budget"] = remaining_tool_budget_s()
        return "ok"

    outcome: dict = {}
    await _drive(_tool_named("bash", peek), timeout_s=30, outcome=outcome)
    assert 25 < seen["budget"] <= 30, f"工具应能看到剩余预算，实际 {seen.get('budget')}"


def _tool_named(name, fn) -> MainTool:
    return MainTool(name=name, description="test", parameters={}, execute=fn)
