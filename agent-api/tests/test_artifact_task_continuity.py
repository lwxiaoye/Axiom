"""Unfinished artifact-task lifetime: checkpoint, contract inherit, progress policy, single-flight."""
from __future__ import annotations

import asyncio
import io
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.agent_harness.artifact_checkpoint import (
    attach_checkpoint_to_profile,
    checkpoint_store_decision,
    parse_media_count,
)
from app.services.agent_harness.goal_contract import (
    GoalContract,
    inherit_agent_mode_for_resume,
    seed_goal_contract,
    patch_goal_contract_on_steer,
)
from app.services.agent_harness.progress_policy import (
    PPT_BASE_TOOLS,
    PPT_PHOTO_TOOLS,
    required_progress_tools,
)
from app.services.agent_harness.run_store import job_claim_action
from app.services.chat.main_tool_turn import truthful_action_failure_message
from app.services.chat.tools.base import MainTool
from app.services.files.deliverable import STAGING_CHECKPOINT_SOURCE, is_deliverable
from app.services.tasks.task_run_service import run_execution_is_claimable


PPT_PROFILE = {
    "id": "artifact_coding",
    "artifact_kind": "presentation",
    "authoring_backend": "pptd",
    "qa_contract": {
        "image_requirement": {
            "mode": "searched_photos", "min_images": 3, "min_sources": 0, "brief": "",
        },
    },
    "workspace_policy": {"mode": "scratch"},
}

PRIOR_FILE = GoalContract(
    goal="制作库里生涯 PPT",
    deliverable="文件",
    success_criteria=["发布 pptx 到我的文件"],
    forbidden=["不要只给大纲"],
    budget_hint="heavy",
)

PRIOR_RESEARCH = GoalContract(
    goal="调研国产数据库选型",
    deliverable="研究报告",
    success_criteria=["覆盖检索并给出引用"],
    forbidden=["不要自己 write_file"],
    budget_hint="heavy",
)


def _openai_tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def test_checkpoint_tar_roundtrip_keeps_tree():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ppt-project"
        (root / "media").mkdir(parents=True)
        (root / "DESIGN.md").write_text("gold-blue", encoding="utf-8")
        (root / "media" / "game.jpg").write_bytes(b"img")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(str(root), arcname=".")
        dest = Path(tmp) / "restored"
        dest.mkdir()
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            tf.extractall(dest)
        assert (dest / "DESIGN.md").read_text(encoding="utf-8") == "gold-blue"
        assert (dest / "media" / "game.jpg").is_file()


def test_checkpoint_store_decision_and_media_meta():
    assert checkpoint_store_decision(10, 100) == "full"
    assert checkpoint_store_decision(200, 100, slim_bytes=40) == "slim"
    assert checkpoint_store_decision(200, 100, slim_bytes=200) == "skip"
    assert parse_media_count('PPT_STAGING_META {"media_count": 4}\nPPT_STAGING_PACKED') == 4
    profile = attach_checkpoint_to_profile(PPT_PROFILE, {"media_count": 4, "restored": True})
    assert profile["workspace_policy"]["checkpoint_media_count"] == 4
    assert profile["workspace_policy"]["checkpoint_restored"] is True
    assert len(STAGING_CHECKPOINT_SOURCE) <= 16
    assert is_deliverable(".ppt-project-run.tgz", "staging_ckpt") is False
    assert is_deliverable(".ppt-project-run.tgz", "staging_checkpoint") is False


def test_seed_continue_inherits_file_contract():
    bare = seed_goal_contract("继续", prior=PRIOR_FILE)
    assert bare.deliverable.startswith("文件")
    assert "制作库里生涯 PPT" in bare.goal
    revised = seed_goal_contract("继续，改成红色", prior=PRIOR_FILE)
    assert revised.deliverable.startswith("文件")
    assert "改成红色" in revised.goal
    hello = seed_goal_contract("继续")
    assert hello.deliverable.startswith("对话答复")


def test_seed_continue_inherits_research_contract():
    bare = seed_goal_contract("继续", prior=PRIOR_RESEARCH)
    assert bare.deliverable.startswith("研究")
    assert "国产数据库" in bare.goal
    hello = seed_goal_contract("你好", prior=PRIOR_RESEARCH)
    assert hello.deliverable.startswith("对话答复")


def test_inherit_research_mode_on_continue_even_if_prior_completed():
    assert inherit_agent_mode_for_resume(
        previous_mode="research",
        previous_status="completed",
        requested_mode="standard",
        message="继续",
    ) == "research"
    assert inherit_agent_mode_for_resume(
        previous_mode="research",
        previous_status="cancelled",
        requested_mode="standard",
        message="接着做",
    ) == "research"
    assert inherit_agent_mode_for_resume(
        previous_mode="research",
        previous_status="completed",
        requested_mode="standard",
        message="今天天气怎么样",
    ) == "standard"
    assert inherit_agent_mode_for_resume(
        previous_mode="research",
        previous_status="cancelled",
        requested_mode="plan",
        message="继续",
    ) == "plan"


def test_patch_goal_contract_on_steer_keeps_file_deliverable():
    constraint = patch_goal_contract_on_steer("改配色成红色", PRIOR_FILE)
    assert constraint.deliverable.startswith("文件")
    assert "制作库里生涯 PPT" in constraint.goal
    assert "红色" in constraint.goal

    theme = patch_goal_contract_on_steer("改成做只狼主题的PPT", PRIOR_FILE)
    assert theme.deliverable.startswith("文件")
    assert "只狼" in theme.goal
    assert theme.deliverable.startswith("对话答复") is False

    empty = patch_goal_contract_on_steer("  ", PRIOR_FILE)
    assert empty.goal == PRIOR_FILE.goal
    assert empty.deliverable == PRIOR_FILE.deliverable


def test_required_progress_tools_photos_and_force_product():
    empty = required_progress_tools(PPT_PROFILE, None, publish_receipt=False, trace=[])
    assert PPT_BASE_TOOLS <= empty
    assert PPT_PHOTO_TOOLS <= empty

    enough_profile = attach_checkpoint_to_profile(PPT_PROFILE, {"media_count": 5, "restored": True})
    enough = required_progress_tools(
        enough_profile, [{"key": "s1", "title": "写盘", "status": "in_progress", "requires": ["productive"]}],
        checkpoint_meta={"media_count": 5, "restored": True},
    )
    assert PPT_BASE_TOOLS <= enough
    assert "search_web" not in enough

    investigate = required_progress_tools(
        enough_profile,
        [{"key": "s2", "title": "搜图", "status": "in_progress", "requires": ["investigate"]}],
        checkpoint_meta={"media_count": 5, "restored": True},
    )
    assert PPT_PHOTO_TOOLS <= investigate
    assert required_progress_tools(PPT_PROFILE, None, publish_receipt=True) == frozenset()


def test_forced_product_does_not_strip_progress_tools():
    from app.services.agent_harness.model_driver import _forced_product_payload_tools

    async def _execute(_args):
        return "ok"

    tools = [
        MainTool(
            name="bash", description="bash", parameters={}, execute=_execute,
            effect_scope="user_files", semantic_tags=("productive", "artifact_producer"),
        ),
        MainTool(
            name="publish_ppt_artifact", description="publish", parameters={},
            execute=_execute, capability="artifact.export", effect_scope="user_files",
        ),
        MainTool(
            name="search_web", description="search", parameters={}, execute=_execute,
            readonly=True, semantic_tags=("investigate", "web_search"),
        ),
        MainTool(
            name="fetch_ppt_asset", description="fetch", parameters={}, execute=_execute,
            effect_scope="scratch", semantic_tags=("download",),
        ),
        MainTool(
            name="update_plan", description="plan", parameters={}, execute=_execute,
            internal=True, control_command=True, semantic_tags=("control",),
        ),
    ]
    tool_map = {tool.name: tool for tool in tools}
    payload = [_openai_tool(name) for name in tool_map]
    plan_rows = [
        {"key": "step-1", "title": "理清演示结构", "status": "completed"},
        {
            "key": "step-2",
            "title": "搜索并下载库里比赛照片素材",
            "status": "in_progress",
            "requires": ["investigate"],
        },
    ]
    names = {
        ((item.get("function") or {}).get("name"))
        for item in _forced_product_payload_tools(
            payload, tool_map, plan_rows=plan_rows, execution_profile=PPT_PROFILE, trace=[],
        )
    }
    assert names >= {"bash", "publish_ppt_artifact", "search_web", "fetch_ppt_asset", "update_plan"}


def test_job_claim_action_and_run_execution_claimable():
    assert job_claim_action(
        status="leased", lease_owner="w1", worker_id="w1",
        attempt_count=16, max_attempts=3,
    ) == "renew_skip"
    assert job_claim_action(
        status="leased", lease_owner="w-other", worker_id="w1",
        attempt_count=3, max_attempts=3,
    ) == "backoff"
    assert job_claim_action(
        status="queued", lease_owner="", worker_id="w1",
        attempt_count=0, max_attempts=3,
    ) == "claim"
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=90)
    assert run_execution_is_claimable(
        owner_instance_id="abc", heartbeat_at=now, stale_before=stale_before,
    ) is False
    assert run_execution_is_claimable(
        owner_instance_id="abc",
        heartbeat_at=now - timedelta(seconds=120),
        stale_before=stale_before,
    ) is True
    assert run_execution_is_claimable(
        owner_instance_id=None, heartbeat_at=now, stale_before=stale_before,
    ) is True


def test_truthful_failure_mentions_tools_when_trace_exists():
    empty = truthful_action_failure_message("400 Invalid request", 0)
    assert "未执行任何操作" in empty
    used = truthful_action_failure_message("400 Invalid request parameters", 3)
    assert "未执行任何操作" not in used
    assert "400" in used
    assert "3 次工具" in used


def test_first_checkpoint_run_id_picks_newest_with_pointer():
    from app.services.agent_harness.artifact_checkpoint import first_checkpoint_run_id

    rows = [
        ("new", {}),
        ("mid", {"ppt_staging_checkpoint": {"file_id": "f1"}}),
        ("old", {"ppt_staging_checkpoint": {"file_id": "f0"}}),
    ]
    assert first_checkpoint_run_id(rows) == "mid"
    assert first_checkpoint_run_id([("a", {"ppt_staging_checkpoint": {}})]) == ""


def test_sandbox_should_outlive_waiting_runs():
    from app.services.chat.run_hub import sandbox_should_outlive_pump

    assert sandbox_should_outlive_pump("waiting_confirmation") is True
    assert sandbox_should_outlive_pump("waiting_user") is True
    assert sandbox_should_outlive_pump("waiting_system") is True
    assert sandbox_should_outlive_pump("completed") is False
    assert sandbox_should_outlive_pump("failed") is False
    assert sandbox_should_outlive_pump(None) is False


def test_stale_staging_note_rejects_plan_as_files():
    from app.services.agent_harness.artifact_checkpoint import STALE_STAGING_NOTE

    assert "completed" in STALE_STAGING_NOTE
    assert "/workspace/tmp/ppt-project" in STALE_STAGING_NOTE


def test_capture_skips_without_owner():
    from app.services.agent_harness.artifact_checkpoint import capture_ppt_staging

    assert asyncio.run(capture_ppt_staging(
        run_id="r1", thread_id="t1", user_id="", force=True,
    )) is None


def test_non_ppt_resume_pulls_session_workspace(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint as ac

    called = {}

    async def fake_pull(**kwargs):
        called.update(kwargs)
        return {"source": "workspace", "media_count": 0}

    class FakePool:
        def has_live_session(self, _run_id):
            return False

    monkeypatch.setattr(ac, "_pull_workspace", fake_pull)
    monkeypatch.setattr(ac, "session_pool", FakePool())
    meta, note = asyncio.run(ac.hydrate_ppt_staging(
        run_id="r-new",
        user_id="u1",
        thread_id="t1",
        resume_source_run_id="r-old",
        user_wants_resume=True,
        execution_profile={"id": "interactive"},
    ))
    assert called["run_id"] == "r-new"
    assert called["include_tree"] is True
    assert meta["source"] == "workspace"
    assert "整份重写" in note


def test_power_loss_reclaim_uses_own_checkpoint_without_continue_phrase():
    from app.services.agent_harness.artifact_checkpoint import resolve_staging_restore_source

    assert resolve_staging_restore_source(
        run_id="run-a", own_pointer=True, user_wants_resume=False,
    ) == "run-a"
    assert resolve_staging_restore_source(
        run_id="run-b", own_pointer=False, user_wants_resume=False,
        resume_source_run_id="old",
    ) == ""
    assert resolve_staging_restore_source(
        run_id="run-c", own_pointer=False, user_wants_resume=True,
        resume_source_run_id="old",
    ) == "old"
    assert resolve_staging_restore_source(
        run_id="run-d", own_pointer=True, has_live_session=True,
        user_wants_resume=False,
    ) == ""


def test_token_fuse_does_not_stop_unpublished_ppt():
    from app.services.agent_harness.model_driver import (
        should_defer_forced_final_for_missing_file,
    )

    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="tokens", rounds_left=30,
    ) is True
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="wall", rounds_left=10,
    ) is True
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="rounds", rounds_left=8,
    ) is True
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=True, reason="tokens", rounds_left=30,
    ) is False
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="tokens", rounds_left=0,
    ) is False
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="stagnation", rounds_left=30,
    ) is False
    assert should_defer_forced_final_for_missing_file(
        need_file=False, has_file=False, reason="tokens", rounds_left=30,
    ) is False


def test_execution_mode_budget_low_and_tool_filter():
    from app.services.agent_harness.model_driver import (
        execution_mode_payload_tools,
        should_enter_execution_mode,
    )

    assert should_enter_execution_mode(
        need_file=True, has_file=False, budget_ratio=0.2,
    ) is True
    assert should_enter_execution_mode(
        need_file=True, has_file=False, budget_ratio=0.5,
    ) is False
    assert should_enter_execution_mode(
        need_file=True, has_file=False, budget_ratio=0.9, already=True,
    ) is True
    assert should_enter_execution_mode(
        need_file=True, has_file=True, budget_ratio=0.1,
    ) is False

    def _item(name: str) -> dict:
        return {"type": "function", "function": {"name": name}}

    kept = {
        str(((item.get("function") or {}).get("name") or ""))
        for item in execution_mode_payload_tools(
            [
                _item("search_web"),
                _item("update_plan"),
                _item("bash"),
                _item("publish_ppt_artifact"),
                _item("ask_user_choice"),
            ],
            {},
        )
    }
    assert kept == {"bash", "publish_ppt_artifact"}


def test_reasoning_tokens_do_not_count_as_delivery():
    from app.services.agent_harness.model_driver import delivery_tokens_from_usage

    delivery, reasoning = delivery_tokens_from_usage(
        {
            "completion_tokens": 150_000,
            "completion_tokens_details": {"reasoning_tokens": 149_000},
        },
        150_000,
    )
    assert reasoning == 149_000
    assert delivery == 1_000
    # 未发布 PPT 仍不得 tool_choice=none：思考烧光 completion 也不等于交付保险丝见底。
    from app.services.agent_harness.model_driver import (
        should_defer_forced_final_for_missing_file,
        should_enter_execution_mode,
    )
    assert should_defer_forced_final_for_missing_file(
        need_file=True, has_file=False, reason="tokens", rounds_left=20,
    ) is True
    assert should_enter_execution_mode(
        need_file=True, has_file=False, budget_ratio=0.0,
    ) is True


@pytest.mark.asyncio
async def test_hydrate_pulls_workspace_on_fresh_ppt_run(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint as ac

    monkeypatch.setattr(ac.session_pool, "has_live_session", lambda _rid: False)

    async def _state(_rid):
        return {}, {}

    monkeypatch.setattr(ac, "_profile_and_state", _state)
    pulled_kwargs = {}

    async def _pull(**kwargs):
        pulled_kwargs.update(kwargs)
        return {"restored": True, "workspace_object_id": "tree1", "include_tree": True}

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.pull_into_run",
        _pull,
    )
    patched = {}

    async def _patch(run_id, data):
        patched["run_id"] = run_id
        patched["data"] = data

    monkeypatch.setattr(
        "app.services.agent_harness.run_store.patch_run_state",
        _patch,
    )

    meta, note = await ac.hydrate_ppt_staging(
        run_id="run-new",
        user_id="u1",
        thread_id="t1",
        user_wants_resume=False,
        execution_profile=PPT_PROFILE,
    )
    assert pulled_kwargs.get("include_tree") is True
    assert pulled_kwargs.get("thread_id") == "t1"
    assert meta and meta["restored"] is True
    assert patched.get("run_id") == "run-new"
    assert "media/" in note
    assert "read_file" in note


@pytest.mark.asyncio
async def test_hydrate_restores_tar_then_overlays_assets_only(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint as ac

    monkeypatch.setattr(ac.session_pool, "has_live_session", lambda _rid: False)

    async def _state(_rid):
        return {}, {"ppt_staging_checkpoint": {"file_id": "ckpt1"}}

    monkeypatch.setattr(ac, "_profile_and_state", _state)

    async def _restore(**kwargs):
        return {"file_id": "ckpt1", "restored": True}

    monkeypatch.setattr(ac, "restore_ppt_staging", _restore)
    pulled_kwargs = {}

    async def _pull(**kwargs):
        pulled_kwargs.update(kwargs)
        return {"restored": True, "include_tree": False}

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.pull_into_run",
        _pull,
    )

    meta, note = await ac.hydrate_ppt_staging(
        run_id="run-resume",
        user_id="u1",
        thread_id="t1",
        user_wants_resume=True,
        execution_profile=PPT_PROFILE,
    )
    assert pulled_kwargs.get("include_tree") is False
    assert meta and meta.get("file_id") == "ckpt1"
    assert "恢复" in note
