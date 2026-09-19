"""执行团队一期数据层（2026-07-27）：委派扩展协议 / 验收裁定解析 / SSE 帧。

委派协议和主对话整体架构以 docs/主对话-Agent-Harness-架构与开发规范.md 为准。

⚠️ 范围说明（2026-07-27 二次修订）：文档里的「总数 6 / 并发 3 / 动态团队」**没有生产
代码支撑**——它们的实现依赖已经退休的图式调度模块。本文件只覆盖仍然活着的那一半：委派协议扩展
（role_name/manager_role/subtasks/acceptance_criteria）、验收事件化、
SSE 帧形状——这些跑在主循环上，单成员委派可视化因此完整可用。
"""
import json

import pytest

from app.services.agent_harness import model_driver
from app.services.agents import acceptance
from app.services.chat import main_tool_turn
from app.services.sse_protocol import HARNESS, SSEChannel


def _frame_payload(frame: str) -> dict:
    body = [line for line in frame.splitlines() if line.startswith("data:")][0][5:].strip()
    return json.loads(body)


def _mk_tool(runner=None, runner_stream=None, max_calls=3):
    async def _default_runner(sid, task, fids, extras):
        return {"status": "succeeded", "text": "ok", "subagent_name": "助手"}

    return model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手", "description": "d"},
         {"id": "c-2", "name": "帮手", "description": "d2"}],
        runner or _default_runner,
        max_calls=max_calls,
        runner_stream=runner_stream,
    )


@pytest.mark.asyncio
async def test_delegation_extras_sanitized_and_passed_to_runner():
    """role_name/subtasks/acceptance_criteria 经服务端护栏截断后原样进 runner。"""
    seen = {}

    async def _runner(sid, task, fids, extras):
        seen.update(extras or {})
        return {"status": "succeeded", "text": "ok"}

    tool = _mk_tool(runner=_runner)
    out = await tool.execute({
        "subagent_id": "c-1", "input": "做个数据底表",
        "role_name": "数据分析师" + "长" * 20,
        "subtasks": ["  拉取Q3流水  ", "", "算同比"] + [f"t{i}" for i in range(10)],
        "acceptance_criteria": ["底表含全部三个季度", "x" * 200],
    })
    assert out.model_content == "ok"
    assert seen["role_name"] == ("数据分析师" + "长" * 20)[:12]
    assert len(seen["subtasks"]) <= 8 and seen["subtasks"][0] == "拉取Q3流水"
    assert len(seen["acceptance_criteria"]) == 2
    assert len(seen["acceptance_criteria"][1]) == 60


@pytest.mark.asyncio
async def test_single_agent_lock_holds_without_team_mode():
    """ADR-046 铁律回归：普通对话/@ 模式换第二个子智能体必须被拒。"""
    tool = _mk_tool()
    await tool.execute({"subagent_id": "c-1", "input": "任务A"})
    with pytest.raises(model_driver.ToolSoftError) as exc:
        await tool.execute({"subagent_id": "c-2", "input": "任务B"})
    assert "一次任务只能使用一个子智能体" in str(exc.value)


@pytest.mark.asyncio
async def test_team_mode_flag_is_gone_and_cannot_reopen_multi_subagent():
    """「执行团队」多成员豁免已删除（2026-07-27）：即便有人给工具打上 team_mode
    标记，单智能体锁与 max_calls 也不再让路。

    这条替换的是原来那个**假绿**用例：它自己 `tool.team_mode = True` 再断言多成员放行，
    于是测的是「如果有人设置这个标记会怎样」——而生产里唯一的写入方
    （task_graph/orchestrator._make_runner_ctx）随 DAG Runtime 一起删了，从此没有任何
    路径能设置它。假绿的代价是：产品定义里「总数 6 / 并发 3 / 动态团队」看起来仍有测试
    背书，实际是一条永不成立的分支。

    现在的断言反过来钉死生产真实状态——谁要恢复多成员协作，必须先让这条用例红掉，
    从而被迫连并发执行（call_subagent 目前是 stream_execute 且 parallel_safe=False）
    与前端排队语义一起想清楚，而不是偷偷打个标记了事。
    """
    tool = _mk_tool(max_calls=3)
    tool.team_mode = True          # 已无人读取：留在这里正是为了证明它无效
    tool.team_max_calls = 99
    assert (await tool.execute({"subagent_id": "c-1", "input": "任务A"})).model_content == "ok"
    with pytest.raises(model_driver.ToolSoftError) as exc:
        await tool.execute({"subagent_id": "c-2", "input": "任务B"})
    assert "一次任务只能使用一个子智能体" in str(exc.value)
    # 上限同样只认 max_calls，不认 team_max_calls
    assert (await tool.execute({"subagent_id": "c-1", "input": "任务C"})).model_content == "ok"
    assert (await tool.execute({"subagent_id": "c-1", "input": "任务D"})).model_content == "ok"
    with pytest.raises(model_driver.ToolSoftError) as exc2:
        await tool.execute({"subagent_id": "c-1", "input": "任务E"})
    assert "已达上限（3 次）" in str(exc2.value)


@pytest.mark.asyncio
async def test_stream_final_frame_carries_acceptance():
    """流式末帧上浮验收单（供工具循环 → SSE 收尾帧）。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "node", "label": "执行", "status": "success"}
        yield {"type": "result", "status": "succeeded", "text": "done",
               "acceptance": {"verdicts": [], "passed_count": 2, "total": 3}}

    tool = _mk_tool(runner_stream=_stream)
    frames = [ev async for ev in tool.stream_execute({"subagent_id": "c-1", "input": "写报告"})]
    final = frames[-1]
    assert final["type"] == "tool_result" and final["text"] == "done"
    assert final["acceptance"]["passed_count"] == 2
    assert "artifacts" not in final


def test_parse_verdicts_aligns_with_criteria():
    """裁定条数与标准强对齐：漏答补 unknown 行；围栏/前后缀容错。"""
    crits = ["A", "B", "C"]
    content = '```json\n{"verdicts":[{"criterion":"A","passed":true,"evidence":"e1"},' \
              '{"criterion":"B","passed":false,"evidence":"e2"}]}\n```'
    verdicts = acceptance._parse_verdicts(content, crits)
    assert len(verdicts) == 3
    assert verdicts[0]["passed"] is True and verdicts[1]["passed"] is False
    assert verdicts[2]["passed"] is None and verdicts[2]["criterion"] == "C"
    assert acceptance._parse_verdicts("不是 JSON", crits) is None


def test_sse_frames_extended_shapes():
    """subagent.started 扩展字段 + subagent.review 新帧 + completed 附验收/清单。"""
    ch = SSEChannel(HARNESS, "t-1", "r-1")
    started = _frame_payload(ch.subagent_started(
        "s1", "助手", task="做表", role_name="数据分析师",
        subtasks=["拉数", "算同比"], acceptance_criteria=["含三个季度"], icon="/upload/agent.png"))
    assert started["data"]["role_name"] == "数据分析师"
    assert started["data"]["subtasks"] == ["拉数", "算同比"]
    assert started["data"]["icon"] == "/upload/agent.png"

    review = _frame_payload(ch.subagent_review("s1", "助手", {
        "verdicts": [{"criterion": "含三个季度", "passed": True, "evidence": "见表1"}],
        "passed_count": 1, "total": 1,
    }))
    assert review["type"] == "subagent.review"
    assert review["data"]["verdicts"][0]["passed"] is True

    done = _frame_payload(ch.subagent_completed(
        "s1", "助手", "完成",
        acceptance={"passed_count": 1, "total": 1}, role_name="数据分析师"))
    assert done["data"]["role_name"] == "数据分析师"
    assert done["data"]["acceptance"] == {"passed_count": 1, "total": 1}
    assert "files" not in done["data"]


@pytest.mark.asyncio
async def test_call_subagent_file_receipt_reaches_live_window_and_artifact_event():
    channel = SSEChannel(HARNESS, "t-1", "r-1")

    async def _events():
        yield {
            "type": "tool_result", "name": "call_subagent", "status": "completed",
            "args": {"subagent_id": "s1"}, "preview": "完成", "result_text": "完成",
            "observation": {
                "status": "succeeded",
                "structured_data": {"ui": {}},
                "artifact_refs": [{
                    "id": "f1", "file_id": "f1", "filename": "报告.docx",
                    "size": 12, "deliverable": True,
                }],
            },
        }

    frames = [
        frame async for frame in main_tool_turn.map_tool_loop_events(
            channel, _events(), {"s1": "助手"}, {"answer": "", "trace": []},
        )
    ]
    payloads = [_frame_payload(frame) for frame in frames]
    assert [payload["type"] for payload in payloads] == [
        "subagent.completed", "artifact.saved",
    ]
    assert payloads[0]["data"]["files"][0]["id"] == "f1"
    assert payloads[1]["data"]["files"][0]["filename"] == "报告.docx"


@pytest.mark.asyncio
async def test_delegation_progress_precedes_the_real_subagent_started_event():
    """先说明和打开委派；服务真正 started 后才让成员进入工作态。

    主循环里「先说明」由**模型自己**给（call_subagent 工具描述要求首次委派前先说一句公开
    说明），工具循环只负责按真实事件投影：模型 commentary → subagent.preparing（准入通过、
    委派已提交）→ 服务真正 started 后才 subagent.started。主循环不会替模型合成一句
    「接下来委派给 X」——那种只复述下一行工具名的一句话正是 is_low_value_action_commentary
    要拦的东西；合成说明只存在于用户显式 @ 的直连路径（subagent_turn）。
    """
    channel = SSEChannel(HARNESS, "t-1", "r-1")

    async def _events():
        yield {
            "type": "commentary",
            "text": "知识库卡片高度属于界面细节，这部分交给「界面审查助手」核对；核对结果回来后我再汇总。",
            "kind": "tool_round",
        }
        yield {
            "type": "tool_started",
            "name": "call_subagent",
            "args": {"subagent_id": "s1", "input": "核对知识库卡片高度"},
        }
        yield {
            "type": "subagent_event",
            "event": {
                "type": "started", "subagent_id": "s1",
                "subagent_name": "界面审查助手", "icon": "/upload/audit-agent.png",
            },
        }

    import json

    def _payload(frame: str) -> dict:
        body = [ln for ln in frame.splitlines() if ln.startswith("data:")][0][5:].strip()
        return json.loads(body)

    frames = [
        frame async for frame in main_tool_turn.map_tool_loop_events(
            channel, _events(), {"s1": "界面审查助手"}, {"answer": "", "trace": []},
            subagent_icons={"s1": "/upload/audit-agent.png"},
        )
    ]
    payloads = [_payload(frame) for frame in frames]

    assert [payload["type"] for payload in payloads] == [
        "message.commentary", "subagent.preparing", "subagent.started",
    ]
    assert "界面审查助手" in payloads[0]["data"]["text"]
    assert payloads[1]["data"]["name"] == "界面审查助手"
    assert payloads[2]["data"]["icon"] == "/upload/audit-agent.png"


@pytest.mark.asyncio
async def test_tool_loop_does_not_fabricate_delegation_commentary():
    """模型没说明时，主循环不替它编一句「接下来委派给 X」：只按真实事件投影。"""
    channel = SSEChannel(HARNESS, "t-1", "r-1")

    async def _events():
        yield {
            "type": "tool_started",
            "name": "call_subagent",
            "args": {"subagent_id": "s1", "input": "核对知识库卡片高度"},
        }
        yield {
            "type": "subagent_event",
            "event": {"type": "started", "subagent_id": "s1", "subagent_name": "界面审查助手"},
        }

    frames = [
        frame async for frame in main_tool_turn.map_tool_loop_events(
            channel, _events(), {"s1": "界面审查助手"}, {"answer": "", "trace": []},
        )
    ]
    types = [_frame_payload(frame)["type"] for frame in frames]
    assert types == ["subagent.preparing", "subagent.started"]
