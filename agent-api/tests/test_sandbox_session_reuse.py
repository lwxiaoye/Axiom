"""Run 级沙箱复用（2026-07-22）：复用、outputs 增量、三层回收、降级路径。

盯的是「改了生命周期以后最容易变成 bug 的四件事」：
1. 同作用域第二次调用不能重建容器，跨作用域必须隔离；
2. outputs/ 残留的上次产物不能被当成本次产物再收一遍（否则重复保存 + 交付判定假阳性）；
3. 复用容器失效时要静默降级为一次性沙箱，而不是把基础设施抖动当成模型的代码错误；
4. 关掉开关就必须完全退回「用完即弃」。
"""
from types import SimpleNamespace

import pytest

from app.services.sandbox import sandbox_executor, session_pool


class _FakeSandbox:
    """记录生命周期调用的假沙箱；outputs 用内存字典模拟（名字 → (size, mtime)）。"""

    instances: list = []

    def __init__(self):
        self.created = 0
        self.deleted = 0
        self.outputs: dict = {}
        self.written: list = []
        self.mkdir_ok = True
        # 脚本执行时才写进 outputs 的文件（模拟真实时序：基线在执行前拍，产物在执行中出现）
        self.produce: dict = {}
        self.files: dict = {}
        self.produce_files: dict = {}
        self.background_commands: list = []
        _FakeSandbox.instances.append(self)

    async def create(self):
        self.created += 1

    async def delete(self):
        self.deleted += 1

    async def write_files(self, entries):
        self.written.append([e.path for e in entries])

    async def read_files(self, paths):
        out = []
        for path in paths:
            rel = path.split("/workspace/files/", 1)[-1] if "/workspace/files/" in path else path
            meta = self.files.get(rel)
            if not meta:
                out.append(SimpleNamespace(ok=False, path=path, data=b"", error="missing"))
                continue
            data = meta[2] if len(meta) > 2 else b"x"
            out.append(SimpleNamespace(ok=True, path=path, data=data, error=None))
        return out

    async def execute_background(self, command, options=None):
        self.background_commands.append(command)
        self.outputs.update(self.produce)
        self.produce = {}
        self.files.update(self.produce_files)
        self.produce_files = {}
        return SimpleNamespace(
            stdout="", stderr="", exit_code=None, job_id="job-1",
            termination_reason=None, ok=True,
        )

    async def execute(self, command, options=None):
        if command.startswith("mkdir"):
            return SimpleNamespace(
                ok=self.mkdir_ok, stdout="", stderr="" if self.mkdir_ok else "No such container",
                exit_code=0 if self.mkdir_ok else 1, truncated=False,
            )
        if "os.walk" in command and "st_mtime_ns" in command:
            import json
            payload = json.dumps([
                [name, int(meta[0]), int(meta[1])]
                for name, meta in sorted(self.files.items())
            ])
            return SimpleNamespace(ok=True, stdout=payload, stderr="", exit_code=0, truncated=False)
        if "json.dumps" in command:  # _list_outputs
            import json
            payload = json.dumps([[n, [s, m]] for n, (s, m) in sorted(self.outputs.items())])
            return SimpleNamespace(ok=True, stdout=payload, stderr="", exit_code=0, truncated=False)
        if "__main__.py" in command or "__main__.sh" in command:  # 用户脚本：此刻才落产物
            self.outputs.update(self.produce)
            self.produce = {}
            self.files.update(self.produce_files)
            self.produce_files = {}
        return SimpleNamespace(ok=True, stdout="done", stderr="", exit_code=0, truncated=False)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _FakeSandbox.instances = []
    monkeypatch.setattr(sandbox_executor, "create_configured_sandbox", lambda _n: _FakeSandbox())
    monkeypatch.setattr(session_pool, "create_configured_sandbox", lambda _n: _FakeSandbox())
    monkeypatch.setattr(sandbox_executor.settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_OUTPUT_REVIEW_ENABLED", False)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_REUSE_ENABLED", True)
    monkeypatch.setattr(session_pool.settings, "SANDBOX_SESSION_BUSY_WAIT_S", 0)
    yield
    session_pool._sessions.clear()


@pytest.mark.asyncio
async def test_same_scope_reuses_one_container_and_closes_at_scope_end():
    a = await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    b = await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-1")

    assert a.ok and b.ok
    assert len(_FakeSandbox.instances) == 1, "同一 Run 内第二次调用不应再建容器"
    sandbox = _FakeSandbox.instances[0]
    assert a.reused is False and b.reused is True
    assert sandbox.deleted == 0, "Run 还没结束就把容器拆了，复用等于没做"

    assert await session_pool.close_scope("run-1") == 1
    assert sandbox.deleted == 1


@pytest.mark.asyncio
async def test_different_scopes_are_isolated():
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-2")

    assert len(_FakeSandbox.instances) == 2, "不同 Run 必须各用各的容器"
    await session_pool.close_scope("run-1")
    await session_pool.close_scope("run-2")


@pytest.mark.asyncio
async def test_stale_outputs_are_not_recollected_as_this_calls_artifacts():
    """复用沙箱里 outputs/ 会残留上次产物——不做增量就会重复保存并让交付判定假阳性。"""
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    sandbox = _FakeSandbox.instances[0]

    sandbox.produce = {"报告.pptx": (1000, 111)}      # 第一次真正产出
    r1 = await sandbox_executor.execute_in_sandbox("生成", session_key="run-1")
    assert [f["name"] for f in r1.output_files] == ["报告.pptx"]

    # 下一次调用只做检查、没改产物：既不该重复回收，也要如实告诉模型它还在
    r2 = await sandbox_executor.execute_in_sandbox("检查", session_key="run-1")
    assert r2.output_files == []
    assert r2.unchanged_outputs == ["报告.pptx"]
    assert "不必重做" in r2.to_tool_text()

    # 定向修复后 mtime 变了 → 重新算作本次产物
    sandbox.produce = {"报告.pptx": (1200, 222)}
    r3 = await sandbox_executor.execute_in_sandbox("修复", session_key="run-1")
    assert [f["name"] for f in r3.output_files] == ["报告.pptx"]
    assert r3.unchanged_outputs == []

    await session_pool.close_scope("run-1")


@pytest.mark.asyncio
async def test_dead_reused_container_falls_back_to_one_shot():
    """容器被外部清掉时，mkdir 探活失败 → 换一次性沙箱重来，不把它当模型的代码错误。"""
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    first = _FakeSandbox.instances[0]
    first.mkdir_ok = False  # 容器已不在

    r = await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-1")

    assert r.ok and r.error is None
    assert len(_FakeSandbox.instances) == 2, "应另起一个一次性沙箱顶上"
    assert r.reused is False
    assert _FakeSandbox.instances[1].deleted == 1, "降级用的一次性沙箱仍是用完即弃"
    assert session_pool.live_count() == 0, "坏会话必须摘除，别让后续调用一直撞它"


@pytest.mark.asyncio
async def test_reuse_disabled_restores_one_shot_lifecycle(monkeypatch):
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_REUSE_ENABLED", False)

    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-1")

    assert len(_FakeSandbox.instances) == 2
    assert all(s.deleted == 1 for s in _FakeSandbox.instances)
    assert session_pool.live_count() == 0


@pytest.mark.asyncio
async def test_no_scope_key_stays_one_shot():
    await sandbox_executor.execute_in_sandbox("print(1)")
    assert _FakeSandbox.instances[0].deleted == 1
    assert session_pool.live_count() == 0


@pytest.mark.asyncio
async def test_node_scope_contextvar_overrides_run_id():
    """任务模式：并行节点各用各的容器，不能因为同属一个 Run 就共用。"""
    token = session_pool.set_scope("graph-1:n1")
    try:
        await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    finally:
        session_pool.reset_scope(token)
    token = session_pool.set_scope("graph-1:n2")
    try:
        await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-1")
    finally:
        session_pool.reset_scope(token)

    assert len(_FakeSandbox.instances) == 2
    assert sorted(session_pool._sessions) == ["graph-1:n1", "graph-1:n2"]


@pytest.mark.asyncio
async def test_pool_full_degrades_instead_of_failing(monkeypatch):
    """存活上限满且全在忙时降级为一次性沙箱——绝不能像并发闸那样排队到超时再报错。"""
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_MAX_LIVE", 1)
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    assert session_pool.live_count() == 1

    # 第二个作用域：池满但 run-1 已空闲 → 驱逐它，不降级
    await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-2")
    assert sorted(session_pool._sessions) == ["run-2"]

    # 把唯一名额标成"执行中"，此时新作用域只能降级
    session_pool._sessions["run-2"].users = 1
    r = await sandbox_executor.execute_in_sandbox("print(3)", session_key="run-3")
    assert r.ok and r.error is None
    assert "run-3" not in session_pool._sessions
    session_pool._sessions["run-2"].users = 0


@pytest.mark.asyncio
async def test_idle_ttl_reaper_collects_forgotten_sessions(monkeypatch):
    """显式关闭漏了（进程还活着）时的第二层兜底。"""
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    sandbox = _FakeSandbox.instances[0]
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_IDLE_TTL_S", 30)
    session_pool._sessions["run-1"].last_used_at -= 999

    assert await session_pool.reap_once() == 1
    assert sandbox.deleted == 1
    assert session_pool.live_count() == 0


@pytest.mark.asyncio
async def test_skill_package_written_once_per_session():
    pkg = {"slug": "pptx", "files": {"SKILL.md": b"x", "scripts/gen.py": b"y"}}
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1", skill_packages=[pkg])
    await sandbox_executor.execute_in_sandbox("print(2)", session_key="run-1", skill_packages=[pkg])

    sandbox = _FakeSandbox.instances[0]
    first, second = sandbox.written[0], sandbox.written[1]
    assert any("/workspace/skills/pptx/SKILL.md" == p for p in first)
    assert not any(p.startswith("/workspace/skills/") for p in second), "技能包不该每次重传"
    await session_pool.close_scope("run-1")


@pytest.mark.asyncio
async def test_workspace_baseline_collects_files_that_a_resnapshot_would_hide():
    """后台作业结束后 files 已在容器里。若收集时重新打基线，这些文件会被当成已有。"""
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    sandbox = _FakeSandbox.instances[0]
    sandbox.files = {"report.txt": (7, 1, b"hello\n")}

    resnapshot = await sandbox_executor.execute_in_sandbox(
        "true", language="bash", collect_workspace=True, session_key="run-1",
    )
    assert [row["path"] for row in resnapshot.workspace_changes] == []

    collected = await sandbox_executor.execute_in_sandbox(
        "true",
        language="bash",
        collect_workspace=True,
        session_key="run-1",
        workspace_baseline={},
    )
    assert [row["path"] for row in collected.workspace_changes] == ["report.txt"]
    assert collected.workspace_changes[0]["data"] == b"hello\n"
    await session_pool.close_scope("run-1")


@pytest.mark.asyncio
async def test_background_start_then_collect_uses_job_baseline():
    """启动后台作业 → 作业在容器里写出文件 → 用启动时基线收集，而不是重新打基线。"""
    await sandbox_executor.execute_in_sandbox("print(1)", session_key="run-1")
    sandbox = _FakeSandbox.instances[0]
    sandbox.produce_files = {"report.txt": (7, 1, b"hello\n")}

    started = await sandbox_executor.execute_in_sandbox(
        "sleep 1 && echo hello > /workspace/files/report.txt",
        language="bash",
        background=True,
        collect_workspace=True,
        session_key="run-1",
    )
    assert started.job_id == "job-1"
    job = session_pool.peek("run-1").jobs["job-1"]
    assert "report.txt" not in job["files_baseline"]
    assert "report.txt" in sandbox.files

    collected = await sandbox_executor.execute_in_sandbox(
        "true",
        language="bash",
        collect_workspace=True,
        session_key="run-1",
        workspace_baseline=job["files_baseline"],
    )
    assert [row["path"] for row in collected.workspace_changes] == ["report.txt"]
    assert collected.workspace_changes[0]["data"] == b"hello\n"
    session_pool.release_job(session_pool.peek("run-1"), "job-1")
    await session_pool.close_scope("run-1")


@pytest.mark.asyncio
async def test_background_job_holds_session_against_pool_eviction(monkeypatch):
    monkeypatch.setattr(session_pool.settings, "SANDBOX_SESSION_MAX_LIVE", 1)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_MAX_LIVE", 1)

    started = await sandbox_executor.execute_in_sandbox(
        "sleep 99",
        language="bash",
        background=True,
        collect_workspace=True,
        session_key="run-job",
    )
    assert started.job_id == "job-1"
    session = session_pool.peek("run-job")
    assert session is not None
    assert session.users >= 1
    assert "job-1" in session.jobs
    assert session.jobs["job-1"]["files_baseline"] == {}
    assert session.sandbox.deleted == 0

    other = await sandbox_executor.execute_in_sandbox("echo other", session_key="run-other")
    assert other.ok and other.error is None
    assert "run-job" in session_pool._sessions
    assert "run-other" not in session_pool._sessions
    assert session_pool.peek("run-job").sandbox.deleted == 0
    await session_pool.close_scope("run-job")


@pytest.mark.asyncio
async def test_jobs_block_eviction_even_if_users_drop_to_zero(monkeypatch):
    """复现：后台启动后 users 被归还成 0 时，仍不能把还在跑的作业容器当空闲会话驱逐。"""
    monkeypatch.setattr(session_pool.settings, "SANDBOX_SESSION_MAX_LIVE", 1)
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_MAX_LIVE", 1)

    started = await sandbox_executor.execute_in_sandbox(
        "sleep 99", language="bash", background=True, session_key="run-job",
    )
    assert started.job_id == "job-1"
    session_pool._sessions["run-job"].users = 0

    other = await sandbox_executor.execute_in_sandbox("echo other", session_key="run-other")
    assert other.ok
    assert "run-job" in session_pool._sessions
    assert session_pool.peek("run-job").sandbox.deleted == 0
    assert "job-1" in session_pool.peek("run-job").jobs
    await session_pool.close_scope("run-job")


@pytest.mark.asyncio
async def test_one_shot_background_does_not_return_unqueryable_job(monkeypatch):
    monkeypatch.setattr(sandbox_executor.settings, "SANDBOX_SESSION_REUSE_ENABLED", False)

    async def boom(self, command, options=None):
        raise AssertionError("一次性沙箱不能启动随后无法查询的后台作业")

    monkeypatch.setattr(_FakeSandbox, "execute_background", boom)
    result = await sandbox_executor.execute_in_sandbox(
        "sleep 99", language="bash", background=True, session_key="run-1",
    )
    assert result.job_id is None
    assert result.ok
    assert _FakeSandbox.instances[0].deleted == 1
    assert session_pool.live_count() == 0
