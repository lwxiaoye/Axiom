"""P1-3/P1-5 regression tests: durable Skill facts and scoped recovery files."""

import inspect

import pytest

from app.services.chat import turn_context_builder as tcb


def test_skill_state_merge_keeps_explicit_selection_priority():
    explicit = {
        "skill_id": "same-skill",
        "record_id": "rec-explicit",
        "selection_source": "explicit",
        "selection_method": "plus",
        "status": "authorized",
    }
    dynamic = {
        "skill_id": "same-skill",
        "record_id": "rec-model",
        "version": "v3",
        "package_id": "pkg-v3",
        "selection_source": "model",
        "selection_method": "use_skill",
        "status": "loaded",
    }

    merged = tcb._merge_skill_state_records([explicit], dynamic)
    assert len(merged) == 1
    assert merged[0]["selection_source"] == "explicit"
    assert merged[0]["selection_method"] == "plus"
    assert merged[0]["record_id"] == "rec-model"
    assert merged[0]["version"] == "v3"
    assert merged[0]["package_id"] == "pkg-v3"
    assert tcb.skill_ids_for_recovery([
        dynamic,
        {"skill_id": "explicit-only", "selection_source": "explicit"},
    ]) == ["explicit-only", "same-skill"]


def test_recovery_observation_never_claims_missing_skill_loaded():
    text = tcb.format_skill_recovery_observation([
        {
            "skill_id": "lost-skill",
            "record_id": "rec-7",
            "selection_source": "model",
            "selection_method": "use_skill",
            "status": "loaded",
        }
    ], [])
    assert "lost-skill" in text
    assert "revalidation_required" in text
    assert "没有加载" in text


def test_resume_checkpoint_source_excludes_global_file_index():
    source = inspect.getsource(tcb.build_resume_checkpoint)
    assert "list_resume_file_names" in source
    assert "list_recent_file_names" not in source
    assert "list_files" not in source


def test_resume_file_service_query_has_thread_and_explicit_id_scope():
    from app.services.files import user_file_service

    source = inspect.getsource(user_file_service.list_resume_file_names)
    assert "AgentUserFile.user_id == user_id" in source
    assert "AgentUserFile.thread_id" in source
    assert "AgentUserFile.id.in_(selected)" in source
    assert "list_recent_file_names" not in source


@pytest.mark.asyncio
async def test_resume_checkpoint_passes_only_scoped_file_inputs(monkeypatch):
    from app.services.files import user_file_service
    from app.services.tasks import snapshot_service

    calls = {}

    async def no_snapshot(thread_id):
        return None

    async def scoped_files(
        user_id,
        thread_id,
        *,
        selected_file_ids=None,
        revision_target=None,
        limit=12,
    ):
        calls.update({
            "user_id": user_id,
            "thread_id": thread_id,
            "selected_file_ids": list(selected_file_ids or []),
            "revision_target": dict(revision_target or {}),
        })
        return [{"filename": "session-output.md", "size": 12}]

    async def global_recent(*args, **kwargs):
        raise AssertionError("恢复上下文不得调用用户全局最近文件")

    monkeypatch.setattr(snapshot_service, "get_latest_task_snapshot", no_snapshot)
    monkeypatch.setattr(user_file_service, "list_resume_file_names", scoped_files)
    monkeypatch.setattr(user_file_service, "list_recent_file_names", global_recent)
    monkeypatch.setattr(tcb, "_last_run_tool_progress", lambda *args, **kwargs: _empty())

    out = await tcb.build_resume_checkpoint(
        "u-1",
        thread_id="thread-1",
        selected_file_ids=["selected-1"],
        revision_target={"file_id": "revision-1"},
    )

    assert "session-output.md" in out
    assert calls == {
        "user_id": "u-1",
        "thread_id": "thread-1",
        "selected_file_ids": ["selected-1"],
        "revision_target": {"file_id": "revision-1"},
    }


async def _empty():
    return ""
