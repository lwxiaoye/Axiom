# -*- coding: utf-8 -*-
"""主对话生命周期状态矩阵：防止交付、修订、质检和收敛规则再次互殴。"""
from __future__ import annotations

import pytest

from app.services.chat.run_policy import (
    RunPolicyPhase,
    RunPolicySnapshot,
    convergence_allowed,
    reconcile_run_policy,
)
from app.services.chat.tools.base import MainTool


async def _ok(_args):
    return "ok"


def _spec(name: str):
    return MainTool(
        name, name, {}, _ok, effect_scope="user_files",
        semantic_tags=("investigate", "artifact_producer"),
    ).spec


@pytest.mark.parametrize(
    ("snapshot", "phase"),
    [
        (RunPolicySnapshot(), RunPolicyPhase.WORKING),
        (RunPolicySnapshot(revision_epoch=1, revision_open=True), RunPolicyPhase.REVISING),
        (RunPolicySnapshot(artifact_review_pending=True), RunPolicyPhase.REVIEWING),
        (RunPolicySnapshot(has_deliverable=True, delivery_checked=True), RunPolicyPhase.DELIVERED),
        (RunPolicySnapshot(force_converge="stagnation"), RunPolicyPhase.CONVERGING),
        # 新修订覆盖旧版本的交付收敛，但不覆盖真实资源熔断。
        (RunPolicySnapshot(
            revision_epoch=1,
            revision_open=True,
            force_converge="product_delivered",
        ), RunPolicyPhase.REVISING),
        (RunPolicySnapshot(
            revision_epoch=1,
            revision_open=True,
            force_converge="wall",
        ), RunPolicyPhase.CONVERGING),
    ],
)
def test_phase_precedence_matrix(snapshot, phase):
    assert snapshot.phase is phase


@pytest.mark.parametrize("tool_name", [
    "search_web", "browser_fetch", "use_skill", "download_url",
    "bash", "write_file", "edit_file",
])
def test_closed_deliverable_keeps_tool_agency(tool_name):
    snapshot = RunPolicySnapshot(has_deliverable=True, delivery_checked=True)
    assert snapshot.blocks_deliverable_tool(_spec(tool_name)) is False


@pytest.mark.parametrize("override", [
    {"revision_open": True, "revision_epoch": 1},
    {"artifact_review_pending": True},
])
@pytest.mark.parametrize("tool_name", ["search_web", "write_file", "edit_file", "bash"])
def test_revision_or_quality_review_overrides_old_delivery_guard(override, tool_name):
    snapshot = RunPolicySnapshot(
        has_deliverable=True,
        delivery_checked=True,
        **override,
    )
    spec = _spec(tool_name)
    assert snapshot.blocks_deliverable_tool(spec) is False
    assert snapshot.should_force_final_after_blocked_batch([True]) is False
    assert snapshot.should_stop_post_delivery_batch([spec]) is False


def test_post_delivery_does_not_stop_model_selected_tools():
    open_snapshot = RunPolicySnapshot(has_deliverable=True)
    closed_snapshot = RunPolicySnapshot(
        has_deliverable=True,
        claimed_delivery=True,
    )
    read_spec = _spec("read_file")
    future_spec = _spec("some_future_tool")
    assert open_snapshot.should_stop_post_delivery_batch([read_spec]) is False
    assert closed_snapshot.should_stop_post_delivery_batch([read_spec]) is False
    assert closed_snapshot.should_stop_post_delivery_batch([future_spec]) is False


def test_reconcile_clears_only_stale_delivery_state():
    repair = reconcile_run_policy(RunPolicySnapshot(
        revision_epoch=2,
        revision_open=True,
        delivery_checked=True,
        bare_confirm_only=True,
        force_converge="post_delivery_rework",
    ))
    assert repair.clear_delivery_checked is True
    assert repair.clear_bare_confirm_only is True
    assert repair.clear_force_converge is True
    assert set(repair.reasons) == {
        "delivery_checked_overridden",
        "bare_confirm_overridden",
        "delivery_convergence_overridden",
    }

    real_limit = reconcile_run_policy(RunPolicySnapshot(
        revision_epoch=2,
        revision_open=True,
        force_converge="stagnation",
    ))
    assert real_limit.clear_force_converge is False


def test_delivery_convergence_cannot_close_open_revision():
    revising = RunPolicySnapshot(revision_epoch=1, revision_open=True)
    assert convergence_allowed(revising, "product_delivered") is False
    assert convergence_allowed(revising, "post_delivery_idle") is False
    assert convergence_allowed(revising, "stagnation") is True


@pytest.mark.parametrize(
    ("snapshot", "ready"),
    [
        (RunPolicySnapshot(), True),
        (RunPolicySnapshot(revision_open=True), True),
        (RunPolicySnapshot(
            revision_open=True,
            revision_requires_mutation=True,
        ), False),
        (RunPolicySnapshot(
            revision_open=True,
            revision_requires_mutation=True,
            revision_mutation_verified=True,
        ), True),
    ],
)
def test_revision_commit_requires_same_epoch_mutation_evidence(snapshot, ready):
    assert snapshot.revision_commit_ready is ready
