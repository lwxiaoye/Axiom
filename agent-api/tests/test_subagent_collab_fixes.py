"""主对话 ↔ 子智能体协作链路修复批（2026-07-28）。

覆盖：
1. 「有输出的部分失败」不再被静默吞成 succeeded——error_message 必须进回灌正文；
2. 交付验收单必须回灌模型（此前只进 SSE 事件，模型从未见过 verdicts）；
4. subagent.review 进历史回放白名单（否则刷新后验收明细永久丢失、handler 是死代码）；
5. 召回 ACL 与执行 ACL 口径一致（不可执行的不进候选）；
6. 子智能体产物只通过已签发 file_id 的文件回执上浮；
7. 准入护栏先于 tool_started 发帧（不再留幽灵成员卡）；
8. checkpoint 归属校验先于状态注入。

第 3 条（沙箱并发闸泄漏）在 tests/test_agent_executor_sandbox_slot.py。
"""
import json
from unittest.mock import patch

import pytest

from app.services.agent_harness import model_driver
from app.services.agents import subagent_service


# ============ 假 LLM 流（沿用 tests/test_artifact_quality_gate.py 的口径） ============

DONE = "data: [DONE]"


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def _tool_call(sid: str, task: str, call_id: str = "c1", args_extra: str = "") -> str:
    args = {"subagent_id": sid, "input": task}
    return _sse({
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": "call_subagent", "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    })


class _Stream:
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
    """脚本化 LLM 客户端；同时把每次请求的 messages 存进 requests 供断言回灌内容。"""

    responses: list = []
    requests: list = []
    last_response: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        payload = kwargs.get("json") or {}
        type(self).requests.append(payload.get("messages") or [])
        # 脚本耗尽时重放最后一条：主循环的收尾自查等额外往返对用例透明
        if not type(self).responses:
            return _Stream(type(self).last_response or [])
        type(self).last_response = type(self).responses.pop(0)
        return _Stream(type(self).last_response)


def _reset_client(responses):
    _Client.responses = list(responses)
    _Client.requests = []
    _Client.last_response = []


def _tool_messages() -> list:
    """所有请求里出现过的 role=tool 消息内容（去重保序）。"""
    seen, out = set(), []
    for messages in _Client.requests:
        for m in messages:
            if m.get("role") == "tool":
                content = str(m.get("content") or "")
                if content not in seen:
                    seen.add(content)
                    out.append(content)
    return out


# ---------------- 1. 有输出的部分失败 ----------------


def _fake_app():
    class _App:
        id = "app-1"
        name = "报表助手"

    return _App()


def test_partial_failure_note_is_fed_back_on_success_with_output():
    """success + errorMessage（D-5：节点炸了但整图仍有输出）→ 状态仍 succeeded，
    但错误文本必须出现在回灌正文里，并明确要求最终回答如实交代。"""
    result = subagent_service._map_workflow_result(
        {"status": "success", "output": "第一季度营收 12 万",
         "errorMessage": "汇总节点: 除零错误"},
        _fake_app(),
    )
    assert result["status"] == "succeeded"
    assert "第一季度营收 12 万" in result["text"]
    assert "本次委派有节点执行失败" in result["text"]
    assert "汇总节点: 除零错误" in result["text"]
    assert result["partial_failure"] == "汇总节点: 除零错误"
    # 这段 text 在 `@` 整轮委派模式下就是**用户看到的最终回答**（没有主模型在中间），
    # 也会进委派会话历史与执行卡「结果报告」——不得夹带对模型下指令的句子
    for leak in ("最终回答", "不得宣布", "请在最终回答里", "现在就动手补"):
        assert leak not in result["text"], f"用户可见文案里泄漏了给模型的指令：{leak}"


def test_clean_success_is_untouched():
    """没有 errorMessage 的正常成功一个字都不加（防误伤纯净交付）。"""
    result = subagent_service._map_workflow_result(
        {"status": "success", "output": "全部完成"}, _fake_app())
    assert result["text"] == "全部完成"
    assert "partial_failure" not in result


def test_failed_and_waiting_paths_unchanged():
    failed = subagent_service._map_workflow_result(
        {"status": "failed", "errorMessage": "起不来"}, _fake_app())
    assert failed["status"] == "failed" and failed["text"] == "起不来"
    waiting = subagent_service._map_workflow_result(
        {"status": "waiting", "output": "请补全",
         "interactive": {"resumeId": "r1", "type": "formInput"}}, _fake_app())
    assert waiting["status"] == "needs_input" and waiting["resume_id"] == "r1"
    assert "本次委派有节点执行失败" not in waiting["text"]


def test_partial_failure_note_helper_is_noop_without_error():
    assert subagent_service._with_partial_failure_note("正文", "") == "正文"
    assert subagent_service._with_partial_failure_note("正文", "   ") == "正文"


def test_partial_failure_model_instruction_is_model_only():
    """给模型的行动要求是独立一块，只拼进工具回执（用户看不到）。"""
    block = model_driver._partial_failure_feedback_block("汇总节点: 除零错误")
    assert "不得宣布" in block and "如实说明" in block
    assert model_driver._partial_failure_feedback_block("") == ""
    assert model_driver._partial_failure_feedback_block(None) == ""


@pytest.mark.asyncio
async def test_partial_failure_reaches_the_model_through_tool_loop():
    """端到端：子智能体部分失败 → 事实进正文、行动要求进工具回执。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded",
               "text": "季度表已出\n\n【⚠️ 本次委派有节点执行失败，以上结果可能不完整。"
                       "失败详情：汇总节点: 除零错误】",
               "partial_failure": "汇总节点: 除零错误"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}], _ok_runner, runner_stream=_stream)
    _reset_client([
        [_tool_call("c-1", "做季度表"), DONE],
        [_sse({"content": "季度表已出，但汇总那一步失败了。"}), DONE],
    ])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="做季度表", tools=[tool])]

    fed = "\n".join(_tool_messages())
    assert "本次委派有节点执行失败" in fed
    assert "不得宣布「已完成」" in fed
    # SSE 展示层不下发 partial_failure（用户读的是正文里那句事实陈述）
    result_ev = next(e for e in events if e.get("type") == "tool_result")
    assert "本次委派有节点执行失败" in (result_ev.get("result_text") or "")


# ---------------- 2. 验收单回灌模型 ----------------


def test_acceptance_block_names_unmet_criteria():
    block = model_driver._acceptance_feedback_block({
        "verdicts": [
            {"criterion": "含三个季度", "passed": True, "evidence": "见表1"},
            {"criterion": "标注数据来源", "passed": False, "evidence": "全文未见来源"},
            {"criterion": "结论可复算", "passed": None, "evidence": "交付内容中未体现"},
        ],
        "passed_count": 1, "total": 3,
    })
    assert "1/3 达标" in block
    assert "✘ 未达标｜标注数据来源" in block
    assert "? 无法核验｜结论可复算" in block
    assert "必须在最终回答里如实点名说明" in block
    # 长度预算：委派侧标准已封顶 5×60，这块不该失控
    assert len(block) < 1200


def test_acceptance_block_all_passed_and_empty_cases():
    ok = model_driver._acceptance_feedback_block({
        "verdicts": [{"criterion": "A", "passed": True, "evidence": "e"}],
        "passed_count": 1, "total": 1,
    })
    assert "1/1 达标" in ok and "可以正常收尾" in ok
    assert model_driver._acceptance_feedback_block(None) == ""
    assert model_driver._acceptance_feedback_block({}) == ""
    assert model_driver._acceptance_feedback_block({"verdicts": [], "total": 0}) == ""
    assert model_driver._acceptance_feedback_block({"verdicts": ["坏形状"]}) == ""


@pytest.mark.asyncio
async def test_verdicts_actually_reach_the_model():
    """端到端：验收单必须出现在回灌给模型的 tool 消息里。

    此前 acceptance 只走 SSE，模型手里只有子智能体正文——「在总结中如实标注哪几条
    未达标」无从谈起，验收 0/3 时照样宣布交付完成。
    """
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded", "text": "报告已交付",
               "acceptance": {
                   "verdicts": [
                       {"criterion": "含三个季度", "passed": False, "evidence": "只有 Q3"},
                       {"criterion": "标注来源", "passed": True, "evidence": "尾注可查"},
                   ],
                   "passed_count": 1, "total": 2,
               }}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手", "description": "d"}],
        _ok_runner, runner_stream=_stream,
    )
    _reset_client([
        [_tool_call("c-1", "写报告"), DONE],
        [_sse({"content": "报告已交付，但只覆盖了 Q3。"}), DONE],
    ])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="做份报告", tools=[tool])]

    fed = "\n".join(_tool_messages())
    assert "报告已交付" in fed
    assert "交付验收单" in fed and "1/2 达标" in fed
    assert "✘ 未达标｜含三个季度" in fed
    assert "必须在最终回答里如实点名说明" in fed
    # SSE 侧行为不变（验收摘要仍随 tool_result 上浮给前端）
    result_ev = next(e for e in events if e.get("type") == "tool_result")
    assert result_ev["acceptance"]["passed_count"] == 1


async def _ok_runner(sid, task, fids, extras):
    return {"status": "succeeded", "text": "ok"}


# ---------------- 4. subagent.review 进历史回放白名单 ----------------


def test_subagent_review_is_in_history_replay_whitelist():
    from app.services.tasks import task_run_service

    assert "subagent.review" in task_run_service._TRACE_EVENT_TYPES, (
        "subagent.review 不在 SQL 过滤白名单里，历史回放取不到它——"
        "task_run_service 中 subagent_run['review'] = {...} 那段会变成死代码，"
        "表现为活流期验收明细正常、刷新后永久消失"
    )
    # 白名单同时是「不拉 per-token 事件」的护栏，顺带钉死这条边界
    assert "subagent.delta" not in task_run_service._TRACE_EVENT_TYPES
    assert "subagent.reasoning" not in task_run_service._TRACE_EVENT_TYPES
    # 处理分支存在（白名单与 handler 是一对，缺一即死代码）
    import inspect
    source = inspect.getsource(task_run_service.get_execution_traces_by_thread)
    assert 'event.type == "subagent.review"' in source


# ---------------- 6. 持久化文件回执 ----------------


def test_persisted_subagent_files_are_part_of_the_result_protocol():
    """工作流已能把沙箱产物落库；协议只接受有 id 的真实回执。"""
    assert not hasattr(subagent_service, "_collect_artifacts")
    result = subagent_service._map_workflow_result(
        {"status": "success", "output": "done",
         "files": [{"filename": "报告.docx", "id": "f1", "size": 12}]},
        _fake_app(),
    )
    assert result["files"] == [{"filename": "报告.docx", "id": "f1", "size": 12}]

    from app.services.agents import acceptance
    from app.services.sse_protocol import SSEChannel
    frame = SSEChannel("harness/1", "t", "r").subagent_completed(
        "s1", "助手", "done",
        files=[
            {"filename": "报告.docx", "id": "f1", "size": 12},
            {"filename": "伪回执.docx"},
        ],
    )
    payload = json.loads(frame.removeprefix("data: "))
    assert payload["data"]["files"] == [{"id": "f1", "filename": "报告.docx", "size": 12}]

    import inspect
    assert "artifacts" not in inspect.signature(acceptance.review).parameters


@pytest.mark.asyncio
async def test_call_subagent_tool_projects_persisted_files_as_artifacts():
    async def _runner(_sid, _task, _fids, _extras):
        return {
            "status": "succeeded", "text": "done",
            "files": [
                {"id": "f1", "filename": "报告.docx", "size": 12, "deliverable": True},
                {"filename": "只有文件名.docx"},
            ],
        }

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}], _runner,
    )
    value = await tool.execute({"subagent_id": "c-1", "input": "写报告"})
    assert value.artifacts == [{
        "id": "f1", "file_id": "f1", "filename": "报告.docx",
        "size": 12, "deliverable": True,
    }]
    assert value.ui["files"] == value.artifacts


# ---------------- 7. 守卫先于发帧 ----------------


@pytest.mark.asyncio
async def test_precheck_is_pure_and_catches_every_guard():
    """precheck 只校验不记账：连问三次不得吃掉调用次数，也不得锁定。"""
    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}, {"id": "c-2", "name": "帮手"}], _ok_runner, max_calls=1)
    assert tool.precheck is not None
    for _ in range(3):
        assert tool.precheck({"subagent_id": "c-1", "input": "任务A"}) is None
    # 三次 precheck 之后仍能真正调用一次（说明没有偷偷记账）
    assert (await tool.execute({"subagent_id": "c-1", "input": "任务A"})).model_content == "ok"
    assert "已达上限" in (tool.precheck({"subagent_id": "c-1", "input": "任务B"}) or "")
    assert "一次任务只能使用一个子智能体" in (
        tool.precheck({"subagent_id": "c-2", "input": "任务C"}) or "")
    assert "不在候选清单内" in (tool.precheck({"subagent_id": "nope", "input": "x"}) or "")
    assert "input 不能为空" in (tool.precheck({"subagent_id": "c-1", "input": "  "}) or "")


@pytest.mark.asyncio
async def test_guard_rejected_call_emits_no_subagent_frames():
    """守卫拒绝的委派：整轮不得出现 tool_started —— chat/main_tool_turn 会把它无条件
    转成 subagent.started，前端据此 push 一张成员卡，随即被 failed 收尾，于是「执行团队」
    面板上留下一张名字可能还是空的幽灵卡。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded", "text": "ok"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}, {"id": "c-2", "name": "帮手"}],
        _ok_runner, max_calls=3, runner_stream=_stream)
    # 先锁定 c-1（模拟本轮已用过一个子智能体）
    tool.lock_state["locked_id"] = "c-1"
    tool.lock_state["n"] = 1

    _reset_client([
        [_tool_call("c-2", "换个人做"), DONE],
        [_sse({"content": "本次任务只能用一个子智能体，我基于已有结果回答。"}), DONE],
    ])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="换个人", tools=[tool])]

    kinds = [e.get("type") for e in events]
    assert "tool_started" not in kinds, "守卫拒绝的调用仍发了 tool_started（幽灵成员卡的来源）"
    assert "tool_result" not in kinds
    # 模型仍收到拒绝原因，并能正常收尾
    fed = "\n".join(_tool_messages())
    assert "一次任务只能使用一个子智能体" in fed
    final = next(e for e in events if e.get("type") == "final")
    assert final["answer"]
    assert not any(t.get("name") == "call_subagent" for t in final["trace"]), (
        "被护栏短路的调用不是实际动作，不能写入用户可见的执行轨迹"
    )


@pytest.mark.asyncio
async def test_repeated_guard_rejection_is_still_accounted():
    """守卫短路不得绕开止损记账：同一个被拒调用反复来，必须逐次计数并最终给出
    「不要再原样重试」的硬提示（否则模型可以对护栏无限重试到轮次熔断）。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded", "text": "ok"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}, {"id": "c-2", "name": "帮手"}],
        _ok_runner, max_calls=3, runner_stream=_stream)
    tool.lock_state["locked_id"] = "c-1"
    tool.lock_state["n"] = 1

    rejected_round = [_tool_call("c-2", "换个人做"), DONE]
    _reset_client([rejected_round] * (model_driver.LoopState.SAME_ERROR_MAX + 1)
                  + [[_sse({"content": "只能用一个子智能体。"}), DONE]])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="换个人", tools=[tool])]

    fed = "\n".join(_tool_messages())
    assert "不要再原样重试" in fed, "护栏连拒多次却没有升级提示，模型会一直撞同一堵墙"
    assert "tool_started" not in [e.get("type") for e in events]


@pytest.mark.asyncio
async def test_repeated_guard_rejection_hard_disables_tool():
    """护栏连拒达到硬上限后必须物理停用工具，不能只继续劝模型换参数。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded", "text": "ok"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}, {"id": "c-2", "name": "帮手"}],
        _ok_runner, max_calls=3, runner_stream=_stream)
    tool.lock_state["locked_id"] = "c-1"
    tool.lock_state["n"] = 1

    rejected = [
        [_tool_call("c-2", f"换人做-{i}"), DONE]
        for i in range(model_driver.LoopState.SAME_ERROR_HARD_MAX + 1)
    ]
    _reset_client(rejected + [[_sse({"content": "只能用一个子智能体。"}), DONE]])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="换人", tools=[tool])]

    fed = "\n".join(_tool_messages())
    assert "本轮已停用" in fed
    assert "已在本轮停用" in fed
    assert "tool_started" not in [e.get("type") for e in events]


@pytest.mark.asyncio
async def test_accepted_call_still_emits_started_then_result():
    """回归：护栏放行的正常委派帧序不变（started 在前、result 在后）。"""
    async def _stream(sid, task, fids, extras):
        yield {"type": "result", "status": "succeeded", "text": "干完了"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手"}], _ok_runner, runner_stream=_stream)
    _reset_client([
        [_tool_call("c-1", "做个表"), DONE],
        [_sse({"content": "做好了。"}), DONE],
    ])
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [ev async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="做个表", tools=[tool])]
    kinds = [e.get("type") for e in events]
    assert kinds.index("tool_started") < kinds.index("tool_result")


# ---------------- 8. checkpoint 归属校验顺序 ----------------


class _patched:
    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.old = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.old)
        return False


@pytest.mark.asyncio
async def test_checkpoint_owner_check_runs_before_hydrate():
    """伪造 resume_id 时，别人 checkpoint 的 outputs/variables 不得在抛异常之前
    被灌进本次 ctx（纵深防御顺序：先关门再搬东西）。"""
    import contextlib

    from app.services.workflow_runtime import compiler

    order = []

    async def _fake_owner(compiled, engine, config):
        order.append("owner")
        raise compiler.CheckpointAccessError("无权恢复该运行：运行归属其他用户")

    async def _fake_hydrate(compiled, engine, config):
        order.append("hydrate")
        engine.ctx.outputs["leaked"] = {"answerText": "别人的数据"}

    class _Ctx:
        def __init__(self):
            self.outputs = {}
            self.variables = {}
            self.user_id = "u-me"
            self.app_id = "app-1"

    class _Engine:
        def __init__(self):
            self.ctx = _Ctx()
            self.nodes = {}
            self.edges = []

    engine = _Engine()

    async def _no_checkpointer(*args, **kwargs):
        return None

    with contextlib.ExitStack() as stack:
        stack.enter_context(_patched(compiler, "_assert_checkpoint_owner", _fake_owner))
        stack.enter_context(_patched(compiler, "_hydrate_engine_from_checkpoint", _fake_hydrate))
        stack.enter_context(_patched(compiler, "get_checkpointer", _no_checkpointer))
        stack.enter_context(_patched(compiler, "compile_fastgpt_graph", lambda *a, **k: object()))
        with pytest.raises(compiler.CheckpointAccessError):
            await compiler.run_engine_with_langgraph(engine, resume_value="yes")

    assert order == ["owner"], f"归属校验必须先跑且短路，实际顺序：{order}"
    assert "leaked" not in engine.ctx.outputs


# ---------------- 5. 召回 ACL 与执行 ACL 口径一致 ----------------


class _User:
    user_id = "u-1"
    tenant_id = "0"
    role_ids = ["r-1"]
    dept_ids: list = []


def _fake_session_factory(rows):
    class _FakeSession:
        async def execute(self, *args, **kwargs):
            class _R:
                @staticmethod
                def all():
                    return rows
            return _R()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return lambda: _FakeSession()


def _mk_async(fn):
    async def _inner(*args, **kwargs):
        return fn(*args, **kwargs)
    return _inner


@pytest.mark.asyncio
async def test_recall_rejects_apps_without_authoritative_approved_version(monkeypatch):
    """app_role/app_dept 命中不得豁免「存在权威 approved 版本」这一执行侧前提。

    真实场景：published_version 指针指向的版本被撤回/删除/漂移 → 执行侧
    user_can_run_published_app 必拒，而召回侧此前照样把它塞进候选清单，于是模型
    看得见、选得中、一执行就报「子智能体不存在或无访问权限」，召回日志却一切正常。
    """
    class _U(_User):
        role_ids = ["r-9"]      # 版本可见性靠它命中（ACL 表豁免已于 2026-07-28 删除）

    rows = [("app-ok", "someone-else", 3), ("app-drifted", "someone-else", 9)]
    monkeypatch.setattr(subagent_service, "async_session", _fake_session_factory(rows))
    monkeypatch.setattr(subagent_service, "load_user_relation_ids",
                        _mk_async(lambda *a, **k: ([], [])))
    # 两个应用的版本可见性都会命中 r-9；差别只在 app-drifted 的指针取不到 approved 版本
    monkeypatch.setattr(subagent_service, "load_approved_visibility_map",
                        _mk_async(lambda *a, **k: {"app-ok": ('["r-9"]', "[]")}))

    allowed = await subagent_service.list_callable_subagent_ids(_U())
    assert allowed == {"app-ok"}, f"指针漂移的应用仍进了候选：{allowed}"


@pytest.mark.asyncio
async def test_recall_uses_the_same_ruler_as_execution(monkeypatch):
    """召回与执行必须同一把尺子（2026-07-28 用户拍板，反转了 2026-07-22 的设计）。

    原设计：ACL 表（app_role/app_dept）命中即放行，用来覆盖 WorkflowVersion 上 visible_*
    缺失的存量应用。问题是 **user_can_run_published_app 从不读那两张表** —— 这条支路放进
    候选的恰好是执行侧必拒的那批，表现为「模型看得见、选得中，一执行就报『子智能体不存在
    或无访问权限』」。deny 方向无越权风险，但「委派中途莫名失败」是最难排查的形态。

    拍板口径：这类「可见但不可执行」的应用不进委派候选，改以**推荐卡**形式给用户自己点
    （推荐卡走 turn_context_builder._retrieve_agents → Qdrant，不经过本函数，见下一条用例）。
    """
    rows = [("app-a", "other", 1)]
    monkeypatch.setattr(subagent_service, "async_session", _fake_session_factory(rows))
    monkeypatch.setattr(subagent_service, "load_user_relation_ids",
                        _mk_async(lambda *a, **k: ([], [])))
    # 版本 visible_* 为空 → publish_visibility_allows_user 返回 False → 执行侧必拒
    monkeypatch.setattr(subagent_service, "load_approved_visibility_map",
                        _mk_async(lambda *a, **k: {"app-a": ("[]", "[]")}))
    assert await subagent_service.list_callable_subagent_ids(_User()) == set(), (
        "执行侧会拒绝的应用不得进入委派候选——否则模型选中它必然失败")


def test_acl_table_authority_is_really_gone():
    """那条支路必须**物理上**没了，不是被绕过。

    只要 acl_table_app_hits 还在并被 import，下一个人很容易把它接回去而不知道
    执行侧读不到它。同理，执行侧判据里也不许出现这两张表。
    """
    from app.services.agents import published_visibility

    assert not hasattr(published_visibility, "acl_table_app_hits")
    assert not hasattr(subagent_service, "acl_table_app_hits")

    import inspect
    src = inspect.getsource(published_visibility.user_can_run_published_app)
    for table in ("app_role", "app_dept"):
        assert table not in src, f"执行侧读了 {table}，等于放宽权限——那是另一个方向的决定"


@pytest.mark.asyncio
async def test_recall_still_falls_back_to_version_visibility(monkeypatch):
    """ACL 表不可用（返回 None）时仍按版本可见性放行——兜底路径没被本次修复打断。"""
    class _U(_User):
        role_ids = ["r-9"]

    rows = [("app-a", "other", 1), ("app-b", "other", 1)]
    monkeypatch.setattr(subagent_service, "async_session", _fake_session_factory(rows))
    monkeypatch.setattr(subagent_service, "load_user_relation_ids",
                        _mk_async(lambda *a, **k: ([], [])))
    monkeypatch.setattr(subagent_service, "load_approved_visibility_map",
                        _mk_async(lambda *a, **k: {
                            "app-a": ('["r-9"]', "[]"),   # 命中用户角色
                            "app-b": ('["r-x"]', "[]"),   # 不命中
                        }))
    assert await subagent_service.list_callable_subagent_ids(_U()) == {"app-a"}


@pytest.mark.asyncio
async def test_owner_is_still_allowed_without_extra_queries(monkeypatch):
    """owner 短路不受影响（与执行侧 owner 恒放行一致）。"""
    rows = [("app-mine", "u-1", 5)]
    monkeypatch.setattr(subagent_service, "async_session", _fake_session_factory(rows))
    assert await subagent_service.list_callable_subagent_ids(_User()) == {"app-mine"}


def test_recommendation_cards_do_not_go_through_the_delegation_recall():
    """收紧委派候选不能把推荐卡一起收掉——这是 2026-07-28 那个拍板成立的前提。

    用户口径：「可见但不可执行」的应用不进 call_subagent 候选（选了必失败），
    改以推荐卡形式给用户自己点。这条只有在两条路**物理上分开**时才成立：

      委派候选：capability_registry → subagent_service.list_callable_subagent_ids
                → 版本可见性（= 执行侧判据）
      推荐卡：  turn_context_builder._retrieve_agents → Qdrant 向量检索
                → 自带 role/dept 过滤，不经过上面那条

    谁要是哪天把推荐卡接到 list_callable_subagent_ids 上，这条会红——那时被收紧的
    应用会从推荐里一并消失，兜底就没了。
    """
    import inspect

    from app.services.chat import turn_context_builder

    src = inspect.getsource(turn_context_builder._retrieve_agents)
    assert "list_callable_subagent_ids" not in src, (
        "推荐卡走了委派候选的召回——委派侧一收紧，推荐兜底就跟着消失了")
    assert "vector_service.search" in src, "推荐卡应当走向量检索这条独立的路"
