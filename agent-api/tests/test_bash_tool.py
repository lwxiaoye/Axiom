"""bash 工具定向测试（2026-07-27）。

重点覆盖两类：
- **授权边界**（Harness：工具集合本身即授权边界，不能只靠 prompt）——只读/勘查轮次
  可以运行临时 bash，但 ToolSpec 必须是 scratch，执行结果不得回写用户文件区。
- 回执与入参处理：超时封顶、行数截断（借鉴 pi：只卡字节挡不住 grep -r 那种行多每行短的输出）、
  非零退出、沙箱层错误与用户命令失败的区分。
"""
import asyncio

import pytest

from app.services.chat.tools import build_tools
from app.services.chat.tools import shell as shell_tools
from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.shell import _cap_lines, _max_timeout_s, build_shell_tools


class _Res:
    def __init__(self, ok=True, stdout="", stderr="", exit_code=0, error=None, truncated=False,
                 review=None, workspace_changes=None, workspace_deleted=None,
                 timed_out=False, termination_reason=None):
        self.ok, self.stdout, self.stderr = ok, stdout, stderr
        self.exit_code, self.error, self.truncated = exit_code, error, truncated
        # 产物有效性门禁（批 3）：bash 回执要把 review 状态拼成
        # [artifact_validity_gate=...]，控制器读它驱动 LoopState 质量返工
        self.review = review
        self.workspace_changes = workspace_changes or []
        # 沙箱里被删掉/改名的 files/ 路径——回写只认执行后清单，这些改动同步不回去
        self.workspace_deleted = workspace_deleted or []
        self.timed_out = timed_out
        self.termination_reason = termination_reason


def _stub(monkeypatch, res, captured=None):
    async def fake_execute_in_sandbox(command, **kwargs):
        if captured is not None:
            captured["command"] = command
            captured.update(kwargs)
        return res

    import app.services.sandbox.sandbox_executor as cr
    monkeypatch.setattr(cr, "execute_in_sandbox", fake_execute_in_sandbox)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)


def _receipt_text(value) -> str:
    """新主对话的工具返回 ToolObservation；旧断言仍只检查给模型的文本。"""
    return str(getattr(value, "model_content", value) or "")


# ---------- 授权边界 ----------

@pytest.mark.asyncio
async def test_readonly_turn_bash_is_scratch_only():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r1", user_message="看看", turn_intent="conversation",
        action_authority="inspect",
    )
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness.policy import HarnessPolicy

    run = RunSnapshot(
        run_id="r1", thread_id="th1", user_id="u1",
        agent_mode="standard", phase="executing", capability_scope="inspect",
        state_version=1, goal_revision=0, plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    names = [t.name for t in tools if HarnessPolicy().evaluate(t.spec, run).allowed]
    assert "bash" in names
    bash = next(tool for tool in tools if tool.name == "bash")
    assert bash.spec.effect_scope.value == "scratch"
    assert "investigate" in bash.spec.semantic_tags
    assert "artifact_producer" not in bash.spec.semantic_tags
    assert "不会同步到用户『我的文件』" in bash.description


@pytest.mark.asyncio
async def test_plan_investigation_allows_scratch_bash_but_not_user_file_writes():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r-plan", user_message="先核对环境再制定计划",
        turn_intent="execute", action_authority="inspect",
    )
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness.policy import HarnessPolicy

    run = RunSnapshot(
        run_id="r-plan", thread_id="th1", user_id="u1",
        agent_mode="plan", phase="planning", capability_scope="planning",
        state_version=1, goal_revision=0, plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    visible = [tool for tool in tools if HarnessPolicy().evaluate(tool.spec, run).allowed]
    names = {tool.name for tool in visible}

    assert "bash" in names
    assert "write_file" not in names
    assert "edit_file" not in names
    assert next(tool for tool in visible if tool.name == "bash").spec.effect_scope.value == "scratch"


@pytest.mark.asyncio
async def test_research_profile_builds_scratch_bash_even_with_mutate_intent():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r-research", user_message="研究数据并做临时计算",
        turn_intent="execute", action_authority="mutate", research_profile=True,
    )
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness.policy import HarnessPolicy

    run = RunSnapshot(
        run_id="r-research", thread_id="th1", user_id="u1",
        agent_mode="research", phase="executing", capability_scope="default",
        state_version=1, goal_revision=0, plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    visible = [tool for tool in tools if HarnessPolicy().evaluate(tool.spec, run).allowed]
    names = {tool.name for tool in visible}
    bash = next(tool for tool in tools if tool.name == "bash")

    assert bash.spec.effect_scope.value == "scratch"
    assert "artifact_producer" not in bash.spec.semantic_tags
    assert "bash" in names
    assert not any(tool.spec.effect_scope.value == "user_files" for tool in visible)


@pytest.mark.asyncio
async def test_scratch_bash_never_persists_workspace_changes(monkeypatch):
    class Sync:
        persist_failed = []
        persist_calls = 0

        async def load(self, _state):
            return []

        async def persist(self, _changes):
            self.persist_calls += 1
            raise AssertionError("scratch bash must not persist")

    sync = Sync()
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="2026-08-16 UTC", workspace_changes=[
        {"path": "scratch.txt", "kind": "write"},
    ]), captured)
    monkeypatch.setattr(shell_tools, "build_sync", lambda *_args, **_kwargs: sync)

    tool = build_shell_tools(user_id="u1", persist_outputs=False)[0]
    value = await tool.execute({"command": "date '+%Y-%m-%d %H:%M:%S %Z'"})

    assert value.status == "succeeded"
    assert value.artifacts == []
    assert sync.persist_calls == 0
    assert captured["collect_workspace"] is False
    assert captured["migrate_outputs"] is False


@pytest.mark.asyncio
async def test_scratch_bash_does_not_commit_workspace(monkeypatch):
    captures = []

    async def fake_capture(**kwargs):
        captures.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.agent_harness.artifact_checkpoint.capture_work_staging",
        fake_capture,
    )
    _stub(monkeypatch, _Res(ok=True, stdout="ok", workspace_changes=[{"path": "tmp.txt", "data": b"x"}]))
    tool = build_shell_tools(
        user_id="u1", run_id="r-scratch", thread_id="th1", persist_outputs=False,
    )[0]
    value = await tool.execute({"command": "echo inspect"})
    assert value.status == "succeeded"
    assert captures == []


@pytest.mark.asyncio
async def test_persist_runs_before_workspace_capture(monkeypatch):
    order = []

    class Sync:
        persist_failed = []

        async def load(self, _state):
            return {"files": {}, "stamps": {}}

        async def persist(self, changes):
            order.append("persist")
            return [{"filename": "note.docx", "source": "generated"}]

        def persist_notice(self):
            return ""

        def notice(self):
            return ""

    async def fake_capture(**kwargs):
        order.append("capture")
        raise asyncio.CancelledError()

    monkeypatch.setattr(shell_tools, "build_sync", lambda *_a, **_k: Sync())
    monkeypatch.setattr(
        "app.services.agent_harness.artifact_checkpoint.capture_work_staging",
        fake_capture,
    )
    _stub(monkeypatch, _Res(ok=True, stdout="ok", workspace_changes=[{"path": "note.docx", "data": b"PK"}]))
    tool = build_shell_tools(user_id="u1", run_id="r1", thread_id="th1", persist_outputs=True)[0]
    with pytest.raises(asyncio.CancelledError):
        await tool.execute({"command": "echo write"})
    assert order[0] == "persist"
    assert "capture" in order


@pytest.mark.asyncio
async def test_background_job_receipt_does_not_persist(monkeypatch):
    class Sync:
        persist_failed = []
        persist_calls = 0

        async def load(self, _state):
            return {"files": {}, "stamps": {}}

        async def persist(self, _changes):
            self.persist_calls += 1
            return []

    res = _Res(ok=True, stdout="", workspace_changes=[{"path": "out.docx", "data": b"x"}])
    res.job_id = "exec-9"
    res.exit_code = None
    sync = Sync()
    monkeypatch.setattr(shell_tools, "build_sync", lambda *_a, **_k: sync)
    _stub(monkeypatch, res)
    tool = build_shell_tools(user_id="u1", run_id="r1", thread_id="th1")[0]
    value = await tool.execute({"command": "make all", "timeout": 600})
    assert "job_id=exec-9" in _receipt_text(value)
    assert "job status" in _receipt_text(value)
    assert sync.persist_calls == 0


@pytest.mark.asyncio
async def test_long_timeout_requests_background_execution(monkeypatch):
    import time

    from app.services.chat.tools.base import CURRENT_TOOL_DEADLINE

    captured: dict = {}
    class Sync:
        persist_failed = []

        async def load(self, _state):
            return {"files": {}, "stamps": {}}

        async def persist(self, _changes):
            return []

        def persist_notice(self):
            return ""

        def notice(self):
            return ""

    monkeypatch.setattr(shell_tools, "build_sync", lambda *_a, **_k: Sync())
    _stub(monkeypatch, _Res(ok=True, stdout="ok"), captured)
    token = CURRENT_TOOL_DEADLINE.set(time.monotonic() + 20)
    try:
        tool = build_shell_tools(user_id="u1", run_id="r1", thread_id="th1")[0]
        await tool.execute({"command": "make all", "timeout": 120})
    finally:
        CURRENT_TOOL_DEADLINE.reset(token)
    assert captured.get("background") is True
    assert captured.get("background_timeout_ms") == 120000


@pytest.mark.asyncio
async def test_job_status_collects_with_saved_baseline_and_persists(monkeypatch):
    from types import SimpleNamespace

    from app.services.sandbox import session_pool as pool

    captured: dict = {}

    class Sync:
        persist_failed = []

        async def persist(self, changes):
            captured["persisted"] = changes
            return [{"filename": "report.txt", "id": "f1", "size": 7}]

        def persist_notice(self):
            return ""

        def notice(self):
            return ""

    class JobSandbox:
        async def get_job_status(self, job_id):
            return SimpleNamespace(running=False, exit_code=0, error="")

    sess = SimpleNamespace(
        sandbox=JobSandbox(),
        last_job_id="job-1",
        jobs={
            "job-1": {
                "files_baseline": {"old.txt": (1, 1)},
                "collect_workspace": True,
                "migrate_outputs": True,
                "log_cursor": None,
            }
        },
        users=1,
        closed=False,
        key="r1",
    )
    monkeypatch.setattr(pool, "peek", lambda _key: sess)
    released: list = []

    def fake_release_job(session, job_id):
        released.append(job_id)
        session.jobs.pop(job_id, None)
        session.users = max(0, session.users - 1)

    monkeypatch.setattr(pool, "release_job", fake_release_job)
    monkeypatch.setattr(shell_tools, "build_sync", lambda *_a, **_k: Sync())

    async def _skip_capture(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.agent_harness.artifact_checkpoint.capture_work_staging",
        _skip_capture,
    )

    async def fake_execute(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _Res(ok=True, workspace_changes=[{"path": "report.txt", "data": b"hello\n"}])

    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute)
    tool = build_shell_tools(user_id="u1", run_id="r1", thread_id="th1", persist_outputs=True)[0]
    value = await tool.execute({"command": "job status job-1"})
    text = _receipt_text(value)
    assert captured.get("workspace_baseline") == {"old.txt": (1, 1)}
    assert captured.get("workspace_loader") is None
    assert captured.get("collect_workspace") is True
    assert captured.get("persisted")
    assert "report.txt" in text
    assert released == ["job-1"]
    assert value.artifacts and value.artifacts[0]["filename"] == "report.txt"


@pytest.mark.asyncio
async def test_job_logs_uses_content_cursor_and_keeps_tail(monkeypatch):
    from types import SimpleNamespace

    from app.services.sandbox import session_pool as pool

    calls = []

    class JobSandbox:
        async def get_job_logs(self, job_id, cursor=None):
            calls.append(cursor)
            return SimpleNamespace(content=("x" * 5000) + "RESULT_MARK", cursor=88)

    sess = SimpleNamespace(
        sandbox=JobSandbox(),
        last_job_id="job-1",
        jobs={
            "job-1": {
                "files_baseline": {},
                "collect_workspace": True,
                "migrate_outputs": True,
                "log_cursor": 3,
            }
        },
        users=1,
    )
    monkeypatch.setattr(pool, "peek", lambda _key: sess)
    tool = build_shell_tools(user_id="u1", run_id="r1", thread_id="th1")[0]
    value = await tool.execute({"command": "job logs job-1"})
    text = _receipt_text(value)
    assert calls == [3]
    assert "RESULT_MARK" in text
    assert "x" * 5000 not in text
    assert sess.jobs["job-1"]["log_cursor"] == 88


@pytest.mark.asyncio
async def test_bash_present_for_mutate_turn():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r1", user_message="跑个命令", turn_intent="execution",
        action_authority="mutate",
    )
    names = [t.name for t in tools]
    # execute_in_sandbox 已退休（能力迁到 bash）；断言跟着能力走
    assert "bash" in names
    bash = next(tool for tool in tools if tool.name == "bash")
    assert bash.spec.effect_scope.value == "user_files"
    assert "artifact_producer" in bash.spec.semantic_tags


@pytest.mark.asyncio
async def test_word_scenario_still_has_an_executor():
    """本用例的断言在 2026-07-27 被**推翻**，记下来免得来回改：

    原断言：「场景门禁收掉 execute_in_sandbox 时 bash 必须一起收」——那是为了把简单 Word/Excel 需求
    推向 build_docx 这类结构化工具。**Office 专用工具全部退休后推无可推**，继续收 bash 的
    后果是「要做 Word」场景里一个执行器都没有，模型根本做不出 docx（实测 11 个工具、
    无 bash / 无 execute_in_sandbox / 无 build_docx）。所以现在断言反过来：必须有执行器。
    """
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r1", user_message="写一份项目周报word文档",
        turn_intent="execution", action_authority="mutate",
    )
    names = [t.name for t in tools]
    assert "execute_in_sandbox" not in names, "execute_in_sandbox 已退休"
    assert "bash" in names, "Office 工具退休后，做 Word 必须靠 bash——不能一个执行器都没有"


def test_bash_is_not_readonly_nor_parallel_safe():
    tool = build_shell_tools(run_id="r1")[0]
    assert tool.readonly is False       # 有副作用：网关异常不得自动重跑
    assert tool.parallel_safe is False  # 并发写会互相踩


# ---------- 入参与回执 ----------

@pytest.mark.asyncio
async def test_empty_command_soft_error():
    tool = build_shell_tools()[0]
    with pytest.raises(ToolSoftError):
        await tool.execute({"command": "   "})


@pytest.mark.asyncio
async def test_timeout_capped_and_reported(monkeypatch):
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "echo ok", "timeout": 99999}))
    # 上限跟着 TOOL_CALL_TIMEOUT_SECONDS 走：主循环在那个点无条件 cancel，
    # 而 cancel 走异常路径 → persist 不执行 → 已产出的文件一个都不落库。
    cap = _max_timeout_s()
    assert captured["timeout_ms"] == cap * 1000
    assert "已按" in text and str(cap) in text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "abc", -5, 0])
async def test_invalid_timeout_falls_back_to_default(monkeypatch, bad):
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools()[0]
    await tool.execute({"command": "echo ok", "timeout": bad})
    assert captured["timeout_ms"] > 0


@pytest.mark.asyncio
async def test_session_key_follows_run_id(monkeypatch):
    """同一 Run 内 bash 与 execute_in_sandbox 必须共用容器，否则上一次的文件就没了。"""
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools(run_id="run-42")[0]
    await tool.execute({"command": "ls"})
    assert captured["session_key"] == "run-42"
    assert captured["language"] == "bash"


@pytest.mark.asyncio
async def test_bash_passes_task_and_vision_credentials_to_quality_review(monkeypatch):
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools(
        run_id="run-vision", user_message="做一份高级感产品 PPT", newapi_key="vision-key"
    )[0]
    await tool.execute({"command": "true"})
    assert captured["task_brief"] == "做一份高级感产品 PPT"
    assert captured["newapi_key"] == "vision-key"


@pytest.mark.asyncio
async def test_sandbox_error_is_soft_error_not_command_output(monkeypatch):
    """provider 不可用是基础设施失败，不能伪装成命令执行完成。"""
    _stub(monkeypatch, _Res(ok=False, error="docker 不可达"))
    tool = build_shell_tools()[0]
    with pytest.raises(ToolSoftError) as excinfo:
        await tool.execute({"command": "ls"})
    assert "执行环境" in str(excinfo.value)


@pytest.mark.asyncio
async def test_nonzero_exit_reported_but_not_raised(monkeypatch):
    """命令失败是正常结果（模型要看 stderr 自纠），不是工具失败。"""
    _stub(monkeypatch, _Res(ok=False, stderr="boom", exit_code=3))
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "false"}))
    assert "exit_code=3" in text and "boom" in text


@pytest.mark.asyncio
async def test_structured_timeout_is_reported_even_without_exit_124(monkeypatch):
    _stub(monkeypatch, _Res(
        ok=False, exit_code=None, timed_out=True, termination_reason="timeout",
    ))
    text = _receipt_text(await build_shell_tools()[0].execute({"command": "sleep 9"}))
    assert "执行超时" in text


@pytest.mark.asyncio
async def test_success_without_output_says_so(monkeypatch):
    _stub(monkeypatch, _Res(ok=True, stdout="", stderr="", exit_code=0))
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "true"}))
    assert "无输出" in text


@pytest.mark.asyncio
async def test_public_bash_preview_excludes_workspace_sync_warning(monkeypatch):
    class Sync:
        persist_failed = []

        async def load(self, _state):
            return []

        async def persist(self, _changes):
            return []

        def notice(self):
            return "文件区只同步了 2 个文件。未同步：old-deck.pptx、archive.pdf"

        def persist_notice(self):
            return ""

    _stub(monkeypatch, _Res(ok=True, stdout="ok", exit_code=0))
    monkeypatch.setattr(shell_tools, "build_sync", lambda *_args, **_kwargs: Sync())
    value = await build_shell_tools(user_id="u1")[0].execute({"command": "printf ok"})
    assert "未同步" in value.model_content
    assert value.ui["detail"] == "ok"
    assert "old-deck.pptx" not in value.ui["detail"]


@pytest.mark.asyncio
async def test_deletion_notice_stops_the_model_from_lying(monkeypatch):
    """沙箱里的删除/改名**同步不回**用户文件区（回写只认执行后清单里有什么）。

    不说这句的后果不是"删除失败"这么轻：模型 rm 完看到 exit 0，转头向用户宣布"已删除"，
    而用户打开「我的文件」文件原封不动——平台替模型撒了谎。
    """
    _stub(monkeypatch, _Res(ok=True, exit_code=0, workspace_deleted=["旧稿.md"]))
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "rm /workspace/files/旧稿.md"}))
    assert "旧稿.md" in text
    assert "原封不动" in text and "不要告诉用户文件已删除" in text


@pytest.mark.asyncio
async def test_no_deletion_no_noise(monkeypatch):
    """没删东西就别提这茬——每条都说一遍等于没说。"""
    _stub(monkeypatch, _Res(ok=True, stdout="hi", exit_code=0))
    text = _receipt_text(await build_shell_tools()[0].execute({"command": "echo hi"}))
    assert "原封不动" not in text


@pytest.mark.asyncio
async def test_meta_sink_records_exit_code(monkeypatch):
    _stub(monkeypatch, _Res(ok=True, stdout="hi", exit_code=0))
    meta: dict = {}
    tool = build_shell_tools(tool_meta_sink=meta)[0]
    await tool.execute({"command": "echo hi"})
    assert meta["bash"]["exit_code"] == 0
    assert meta["bash"]["action"]["operation"] == "bash"


# ---------- 行数截断（借鉴 pi） ----------

def test_cap_lines_keeps_head_and_tail():
    text = "\n".join(str(i) for i in range(1000))
    capped, was_capped = _cap_lines(text, limit=100)
    assert was_capped is True
    lines = capped.splitlines()
    assert len(lines) == 101          # 100 行 + 1 行省略提示
    assert lines[0] == "0"            # 保留头
    assert lines[-1] == "999"         # 也保留尾（错误常在末尾）
    assert "已省略" in capped


def test_cap_lines_passthrough_when_short():
    capped, was_capped = _cap_lines("a\nb\nc", limit=100)
    assert was_capped is False
    assert capped == "a\nb\nc"


@pytest.mark.asyncio
async def test_long_output_truncated_in_receipt(monkeypatch):
    _stub(monkeypatch, _Res(stdout="\n".join(str(i) for i in range(5000))))
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "seq 1 5000"}))
    assert "已省略" in text and "已按上限截断" in text


# ---------- 死线内收：产物不再因 cancel 凭空消失（2026-07-28 P1） ----------

@pytest.mark.asyncio
async def test_command_timeout_is_clamped_into_the_remaining_turn_budget(monkeypatch):
    """主循环到点是 `task.cancel()`——**异常路径**，`sync.persist()` 根本不会执行，
    沙箱里已经产出的 PPT/文档一个都不落库，模型只收到「已中止本次调用」。

    所以命令的超时必须压进「距离被 cancel 还剩多久」之内：让沙箱自己按 exit 124 终止，
    工具正常返回，产物照常落库。
    """
    import time as _time

    from app.services.chat.tools.base import CURRENT_TOOL_DEADLINE

    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools()[0]

    token = CURRENT_TOOL_DEADLINE.set(_time.monotonic() + 40)  # 本轮只剩 40 秒
    try:
        text = _receipt_text(await tool.execute({"command": "make build", "timeout": 300}))
    finally:
        CURRENT_TOOL_DEADLINE.reset(token)

    # 40s 预算 - 25s 读回/落库余量 ≈ 15s
    assert 10_000 <= captured["timeout_ms"] <= 16_000, captured["timeout_ms"]
    assert "本轮剩余执行时间不足" in text, "压低了就得说清是本轮时间不够，否则模型只会照样调大 timeout"


@pytest.mark.asyncio
async def test_no_deadline_means_no_clamp(monkeypatch):
    """没有死线（非主循环上下文/未配置超时）时行为完全不变。"""
    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools()[0]
    text = _receipt_text(await tool.execute({"command": "ls", "timeout": 60}))
    assert captured["timeout_ms"] == 60_000
    assert "本轮剩余执行时间不足" not in text


@pytest.mark.asyncio
async def test_bash_hands_the_kernel_a_loader_not_a_prebuilt_mirror(monkeypatch):
    """增量镜像的接线：bash 不再自己 await prepare()，而是把加载器交给内核 ——
    记账挂在沙箱会话上，只有内核在锁内才拿得到权威值。"""
    from app.core.config import settings as _settings

    captured: dict = {}
    _stub(monkeypatch, _Res(stdout="ok"), captured)
    tool = build_shell_tools(user_id="u1", run_id="r1")[0]
    await tool.execute({"command": "ls"})
    assert callable(captured.get("workspace_loader"))
    assert captured.get("workspace_files") is None, "预先算好的全量镜像已经不该再传了"
