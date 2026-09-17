"""v3.0 技能夹缝修复：resume 轮从任务快照恢复技能上下文。

- resolve_resume_skill_ids 三态（无快照/无技能/去重上限）
- 断点硬闸文案口径（已重载技能禁整包重载、快照未列技能可正常 use_skill）
- _format_skill_block 的「已由服务端预加载」标记被 model_driver skill_docs_preloaded 命中
- chat_service 两处 resume 注入点都接了快照技能合并
"""
import pytest

from app.services.chat import turn_context_builder as tcb
from app.services.tasks import snapshot_service as ss


@pytest.mark.asyncio
async def test_resolve_resume_skill_ids(monkeypatch):
    async def _none(thread_id):
        return None
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _none)
    assert await ss.resolve_resume_skill_ids("u-1", "th-1") == []

    async def _some(thread_id):
        return {"summary": {"skill_ids": ["a", "a", "", "b"]}}
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _some)
    assert await ss.resolve_resume_skill_ids("u-1", "th-1") == ["a", "b"]


@pytest.mark.asyncio
async def test_resolve_resume_skill_ids_uses_the_selected_source_run(monkeypatch):
    calls = []

    async def _specific(run_id, *, thread_id="", user_id=""):
        calls.append((run_id, thread_id, user_id))
        return {"summary": {"skill_ids": ["opaque-ppt-id"]}}

    async def _latest(_thread_id):
        raise AssertionError("source-bound resume must not query an independent latest snapshot")

    monkeypatch.setattr(ss, "get_task_snapshot", _specific)
    monkeypatch.setattr(ss, "get_latest_task_snapshot", _latest)

    result = await ss.resolve_resume_skill_ids(
        "u-1", "th-1", source_run_id="source-run-1",
    )

    assert result == ["opaque-ppt-id"]
    assert calls == [("source-run-1", "th-1", "u-1")]


def test_resume_merge_dedup_semantics():
    """chat_service 的合并表达式语义：显式技能在前、快照技能在后、去重保序。"""
    skill_ids = ["ppt_skill"]
    snap = ["ppt_skill", "doc_skill"]
    merged = list(dict.fromkeys([str(s) for s in (skill_ids or [])] + snap))
    # PPT 显式已有时不再追加平台默认（effective_ppt_skill_ids 显式命中守卫）
    assert merged == ["ppt_skill", "doc_skill"]


def test_hard_gate_wording_updated():
    """恢复只提供 Skill 事实；不再注入整包重载或静默选择闸门。"""
    src = open("app/services/chat/turn_context_builder.py", encoding="utf-8").read()
    assert "revalidation_required" in src
    assert "model/use_skill" in src
    assert "快照未列出的技能如确有需要可正常 use_skill" not in src


def test_preloaded_marker_hits_skill_docs_gate():
    """恢复轮可看到真实预加载事实，但不通过隐藏的 Skill 重载闸门改变模型决策。"""
    block = tcb._format_skill_block({
        "id": "s1", "name": "测试技能",
        "description": "d", "instructions": "i",
    })
    assert "本轮已由服务端预加载完整说明与脚本" in block
    main_src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "skill_docs_preloaded" not in main_src


def test_skill_block_supports_resume_state():
    """Skill 身份和来源可见，但模型仍保有是否再次调用能力的决定权。"""
    block = tcb._format_skill_block({
        "id": "s", "name": "n", "description": "", "instructions": "",
    })
    assert "不得静默替换为默认 Skill" in block
    assert "不要再调用 use_skill" not in block
