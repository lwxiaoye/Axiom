"""任务状态快照（v3.0）：快照组装/落库重试/断点注入/降级链。

无 DB：纯逻辑直接测；DB 触达函数用 monkeypatch 钉住（沿用 tests/test_memory_write_concurrency.py
的无 DB 原则）。
"""
import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.tasks import snapshot_service as ss


def _patch_resolve(
    mp, *, goal="", thread_id="th-1", user_id="u-1", status="failed", error="",
):
    async def _fake(run_id):
        return {
            "thread_id": thread_id,
            "user_id": user_id,
            "goal": goal,
            "status": status,
            "error": error,
        }
    mp.setattr(ss, "_resolve_run", _fake)


def _patch_common(mp, activity, *, interrupted=False):
    async def _act(thread_id, *, limit=12, run_id=None):
        return activity
    async def _interrupted(thread_id, run_id):
        return interrupted
    mp.setattr(ss, "collect_run_activity", _act)
    mp.setattr(ss, "_run_has_interrupted_message", _interrupted)
    _patch_resolve(mp, goal="g")


# ---- format_snapshot_block：纯渲染 ----


def test_format_snapshot_block_full():
    block = ss.format_snapshot_block({
        "goal": "做一份教师节 PPT",
        "plan": [{"title": "整理内容", "status": "completed"},
                 {"title": "生成幻灯片", "status": "in_progress"}],
        "artifacts": ["教师节.pptx"],
        "artifact_receipts": [{"file_id": "f-1", "filename": "教师节.pptx"}],
        "tool_summary": ["- use_skill (ok)：ppt-studio", "- bash (ok)：生成 教师节.pptx"],
        "skill_ids": ["s1", "s2"],
        "pending_decisions": ["深色还是浅色？"],
        "interrupted": True,
    })
    assert block.startswith("【任务快照（平台注入，上一轮断点事实）】")
    assert "任务目标：做一份教师节 PPT" in block
    assert "整理内容[completed] → 生成幻灯片[in_progress]" in block
    assert "最近文件（仅供定位半成品，不代表本任务已交付）" not in block
    assert "已持久化交付回执（平台已核验，可确认完成）：" in block
    assert "- 教师节.pptx（file_id=f-1）" in block
    assert "已执行工具：" in block and "- bash (ok)" in block
    assert "技能记录：2 个（本轮须按持久化 ID 重新校验 ACL 与取包" in block
    assert "待确认决策：深色还是浅色？" in block
    assert "中断/未完整交付" in block


def test_format_snapshot_block_empty():
    assert ss.format_snapshot_block({}) == ""
    assert ss.format_snapshot_block(None) == ""
    assert ss.format_snapshot_block({"schema_version": 1}) == ""


def test_format_snapshot_block_keeps_recent_files_without_receipts():
    block = ss.format_snapshot_block({
        "goal": "继续修改半成品",
        "artifacts": ["半成品.pptx"],
        "interrupted": True,
    })
    assert "同线程恢复范围文件（仅供定位半成品，不代表本任务已交付）：半成品.pptx" in block


# ---- build_task_snapshot_summary：字段组装与降级 ----


@pytest.mark.asyncio
async def test_build_summary_core(monkeypatch):
    _patch_common(monkeypatch, {
        "plan": [{"title": "写文档", "status": "completed"}],
        "lines": ["- write_file (ok)：报告.md"],
        "skill_ids": ["s9"],
        "artifact_receipts": [{"file_id": "f9", "filename": "报告.md"}],
    }, interrupted=True)
    summary = await ss.build_task_snapshot_summary(run_id="r1", thread_id="th-1", user_id="u-1")
    assert summary["schema_version"] == ss.TASK_SNAPSHOT_SCHEMA_VERSION
    assert summary["goal"] == "g"
    assert summary["plan"][0]["title"] == "写文档"
    assert summary["tool_summary"] == ["- write_file (ok)：报告.md"]
    assert summary["skill_ids"] == ["s9"]
    assert summary["artifact_receipts"] == [{"file_id": "f9", "filename": "报告.md"}]
    assert summary["interrupted"] is True


@pytest.mark.asyncio
async def test_build_summary_skill_ids_precedence(monkeypatch):
    """技能 id 优先级：调用方（HITL tool_env）→ use_skill 事件 → run.state。"""
    _patch_common(monkeypatch, {"plan": [], "lines": [], "skill_ids": ["s_event"]})
    summary = await ss.build_task_snapshot_summary(
        run_id="r1", thread_id="th-1", user_id="u-1", skill_ids=["s_caller"],
    )
    assert summary["skill_ids"] == ["s_caller", "s_event"]

    # 无调用方、无事件 → run.state 兜底
    _patch_common(monkeypatch, {"plan": [], "lines": [], "skill_ids": []})
    async def _state(run_id):
        return ["s_state"]
    monkeypatch.setattr(ss, "_run_state_skill_ids", _state)
    summary = await ss.build_task_snapshot_summary(run_id="r1", thread_id="th-1", user_id="u-1")
    assert summary["skill_ids"] == ["s_state"]


@pytest.mark.asyncio
async def test_build_summary_light_skips_heavy(monkeypatch):
    """light=True 跳过产物清单（周期写）；重活字段不出现。"""
    _patch_common(monkeypatch, {"plan": [{"title": "t", "status": "pending"}], "lines": [], "skill_ids": []})
    summary = await ss.build_task_snapshot_summary(run_id="r1", thread_id="th-1", user_id="u-1", light=True)
    assert "artifacts" not in summary


@pytest.mark.asyncio
async def test_bare_continue_snapshot_inherits_prior_persisted_receipts(monkeypatch):
    _patch_common(monkeypatch, {
        "plan": [],
        "lines": [],
        "skill_ids": [],
        "artifact_receipts": [],
    })

    async def _previous(thread_id, *, exclude_run_id):
        assert thread_id == "th-1"
        assert exclude_run_id == "r1"
        return {
            "goal": "制作验收 PPT",
            "plan": [{"title": "发布文件", "status": "completed"}],
            "skill_ids": ["ppt-studio"],
            "artifact_receipts": [
                {"file_id": "ppt-1", "filename": "验收.pptx"},
                {"file_id": "zip-1", "filename": "验收-source.zip"},
            ],
        }

    monkeypatch.setattr(ss, "_previous_thread_snapshot_summary", _previous)
    summary = await ss.build_task_snapshot_summary(
        run_id="r1",
        thread_id="th-1",
        user_id="u-1",
        goal="继续",
        light=True,
    )

    assert summary["goal"] == "制作验收 PPT"
    assert summary["artifact_receipts"] == [
        {"file_id": "ppt-1", "filename": "验收.pptx"},
    ]
    assert summary["plan"] == [{"title": "发布文件", "status": "completed"}]
    assert summary["skill_ids"] == ["ppt-studio"]


def test_source_project_zip_is_not_a_deliverable_receipt():
    assert ss._DELIVERABLE_EXT_RE.search("验收-source.zip") is None


def test_snapshot_reads_plan_store_not_retired_plan_event():
    source = open(ss.__file__, encoding="utf-8").read()
    collect = source[
        source.index("async def collect_run_activity"):
        source.index("async def collect_skill_work_lines")
    ]
    assert "get_current_plan" in collect
    assert '"task.plan"' not in collect


def test_snapshot_plan_projection_omits_full_evidence():
    compact = ss._compact_plan_projection([{
        "key": "deliver",
        "title": "发布文件",
        "status": "completed",
        "detail": "已通过校验",
        "required": True,
        "plan_version": 9,
        "goal_revision": 2,
        "evidence": [{"artifact_refs": [{"id": "file-1"}]}],
        "acceptance_criteria": ["必须可下载"],
    }])
    assert compact == [{
        "key": "deliver",
        "title": "发布文件",
        "status": "completed",
        "detail": "已通过校验",
        "reason": "",
        "required": True,
        "plan_version": 9,
        "goal_revision": 2,
    }]


@pytest.mark.asyncio
async def test_last_event_sequence_reads_runtime_max(monkeypatch):
    class _Session:
        async def scalar(self, statement):
            assert statement is not None
            return 27

    class _Context:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.core.runtime_db.runtime_session", lambda: lambda: _Context())
    assert await ss._last_event_sequence("run-27") == 27


# ---- save_task_snapshot：IntegrityError 重试一次 ----


@pytest.mark.asyncio
async def test_save_snapshot_retries_on_integrity_error(monkeypatch):
    calls = {"n": 0}

    async def _flaky(run_id, *, covered_sequence, summary):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("stmt", {}, Exception("uq_agent_run_context_snapshot"))
        return {"id": "ok", "version": 1}

    async def _summary(**kw):
        return {"goal": "g"}
    monkeypatch.setattr(ss, "build_task_snapshot_summary", _summary)
    async def _seq(run_id):
        return 5
    monkeypatch.setattr(ss, "_last_event_sequence", _seq)
    from app.services.agent_harness import run_store as rvs
    monkeypatch.setattr(rvs, "create_context_snapshot", _flaky)
    out = await ss.save_task_snapshot(run_id="r1")
    assert out == {"id": "ok", "version": 1}
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_save_snapshot_skips_empty(monkeypatch):
    """无可侧写内容不落空快照。"""
    async def _summary(**kw):
        return {"schema_version": 1}
    monkeypatch.setattr(ss, "build_task_snapshot_summary", _summary)
    out = await ss.save_task_snapshot(run_id="r1")
    assert out is None


@pytest.mark.asyncio
async def test_policy_rejected_terminal_never_writes_resumable_snapshot(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.agent_harness.public_errors import (
        SENSITIVE_WORDS_REJECTION_MESSAGE,
    )

    _patch_resolve(
        monkeypatch,
        goal="qz_sensitive_test_92741",
        status="failed",
        error=SENSITIVE_WORDS_REJECTION_MESSAGE,
    )
    save = AsyncMock()
    monkeypatch.setattr(ss, "save_task_snapshot", save)

    assert await ss.save_terminal_snapshot("r-policy") is None
    save.assert_not_awaited()


# ---- get_latest_task_snapshot / resolve_resume_skill_ids ----


@pytest.mark.asyncio
async def test_resolve_resume_skill_ids_states(monkeypatch):
    async def _none(thread_id):
        return None
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _none)
    assert await ss.resolve_resume_skill_ids("u-1", "th-1") == []

    async def _no_skills(thread_id):
        return {"summary": {"skill_ids": []}}
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _no_skills)
    assert await ss.resolve_resume_skill_ids("u-1", "th-1") == []

    async def _skills(thread_id):
        return {"summary": {"skill_ids": ["s1", "s1", "", "s2"]}}
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _skills)
    assert await ss.resolve_resume_skill_ids("u-1", "th-1") == ["s1", "s2"]


# ---- 断点注入：build_resume_checkpoint 含快照块、无快照降级 ----
# 注意：build_resume_checkpoint 的锚点块会真查 MySQL，失败被内部 except 吞掉（降级），
# 本环境无 DB 时锚点块自然为空，不影响对快照块/断点现场块/工具进度的断言。


@pytest.mark.asyncio
async def test_build_resume_checkpoint_injects_snapshot_block(monkeypatch):
    async def _snap(thread_id):
        return {
            "summary": {
                "goal": "整理课件",
                "plan": [{"title": "改一页", "status": "in_progress"}],
                "skill_ids": ["s1"],
            }
        }
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _snap)
    import app.services.files.user_file_service as ufs
    async def _no_files(user_id, thread_id, *, selected_file_ids=None, revision_target=None, limit=12):
        return []
    monkeypatch.setattr(ufs, "list_resume_file_names", _no_files)

    from app.services.chat import turn_context_builder as tcb
    async def _no_progress(thread_id):
        return ""
    monkeypatch.setattr(tcb, "_last_run_tool_progress", _no_progress)

    from app.services.chat.turn_context_builder import build_resume_checkpoint
    out = await build_resume_checkpoint("u-1", thread_id="th-1")
    assert "【任务快照（平台注入，上一轮断点事实）】" in out
    assert "任务目标：整理课件" in out
    assert "技能记录：1 个" in out
    # 有快照时不再重复注入断点现场与工具进度
    assert "【断点现场（平台注入，强制遵守）】" not in out
    assert "【上轮工具进度" not in out


@pytest.mark.asyncio
async def test_build_resume_checkpoint_falls_back_without_snapshot(monkeypatch):
    async def _none(thread_id):
        return None
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _none)
    import app.services.files.user_file_service as ufs
    async def _files(user_id, thread_id, *, selected_file_ids=None, revision_target=None, limit=12):
        return [{"filename": "旧稿.md"}]
    monkeypatch.setattr(ufs, "list_resume_file_names", _files)

    from app.services.chat import turn_context_builder as tcb
    async def _progress(thread_id):
        return "【上轮工具进度（同线程最近 Run，强制沿用）】\n- bash (ok)"
    monkeypatch.setattr(tcb, "_last_run_tool_progress", _progress)

    from app.services.chat.turn_context_builder import build_resume_checkpoint
    out = await build_resume_checkpoint("u-1", thread_id="th-1")
    assert "【断点现场（平台注入，事实清单）】" in out
    assert "【上轮工具进度" in out
    assert "【任务快照" not in out


# ---- 源码级钩子断言 ----


def test_terminal_snapshot_hooks_present():
    src = open("app/services/tasks/task_run_service.py", encoding="utf-8").read()
    # 终态 CAS 与僵尸收敛都挂上侧写
    assert "_spawn_terminal_snapshot(run_id)" in src
    assert src.count("_spawn_terminal_snapshot(") >= 1

    chat = open("app/services/agent_harness/orchestrator.py", encoding="utf-8").read()
    # 请求级技能清单持久化到 run.state（快照恢复技能上下文的数据源）
    assert '"skill_ids": list(skill_ids or [])' in chat
    assert "save_hitl_snapshot" in chat


def test_snapshot_table_model_exists():
    src = open("app/runtime_models.py", encoding="utf-8").read()
    assert "class AgentRunContextSnapshot" in src
    assert "uq_agent_run_context_snapshot" in src
