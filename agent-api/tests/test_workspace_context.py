"""Session workspace inventory: names only, recompiled each round."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness.workspace_context import (
    WORKSPACE_STATUS_MARK,
    compile_workspace_status,
    format_workspace_status,
    parse_workspace_inventory,
    upsert_workspace_status,
)
from app.services.skills.ppt_agentic_adapter import STAGING_ROOT


PPT_PROFILE = {
    "id": "artifact_coding",
    "artifact_kind": "presentation",
    "authoring_backend": "pptd",
    "workspace_policy": {"checkpoint_media_count": 3, "checkpoint_restored": True},
}


@pytest.fixture(autouse=True)
def no_selected_folder(monkeypatch):
    # Legacy scratch inventory tests have no business DB; folder integration has
    # its own isolated database tests in test_work_folders.py.
    monkeypatch.setattr("app.services.files.work_folders.folder_for_thread", AsyncMock(return_value=None))


def test_parse_and_format_lists_pages_and_materials_not_pixels():
    stdout = (
        'WORKSPACE_INVENTORY {"exists": true, "pages": ['
        '{"name": "01-cover.page", "bytes": 120},'
        '{"name": "02-roster.page", "bytes": 200}], "media": ['
        '{"name": "kyrie.jpg", "bytes": 20480},'
        '{"name": "logo.png", "bytes": 4096}], "root_files": ['
        '{"name": "DESIGN.md", "bytes": 80}]}'
    )
    inventory = parse_workspace_inventory(stdout)
    block = format_workspace_status(inventory)
    assert block.startswith(WORKSPACE_STATUS_MARK)
    assert STAGING_ROOT in block
    assert "01-cover.page" in block
    assert "kyrie.jpg (20KB)" in block
    assert "DESIGN.md" in block
    assert "整表重建" in block
    assert "read_file" in block
    assert "edit_file" in block
    assert "base64" not in block.lower()
    assert "image_url" not in block


def test_empty_workspace_does_not_trust_plan_status():
    block = format_workspace_status({
        "exists": False,
        "checkpoint_present": True,
        "checkpoint_media_count": 4,
    })
    assert "当前：空" in block
    assert "计划 completed 不能证明" in block
    assert "素材 4 张" in block


def test_upsert_replaces_previous_snapshot():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": f"{WORKSPACE_STATUS_MARK}，旧）\n素材 1：a.jpg"},
        {"role": "user", "content": "继续"},
    ]
    upsert_workspace_status(messages, f"{WORKSPACE_STATUS_MARK}，新）\n素材 2：b.png")
    marks = [m["content"] for m in messages if WORKSPACE_STATUS_MARK in str(m.get("content") or "")]
    assert marks == [f"{WORKSPACE_STATUS_MARK}，新）\n素材 2：b.png"]
    assert messages[0]["content"] == "rules"
    assert messages[-1]["content"] == "继续"


@pytest.mark.asyncio
async def test_non_ppt_profile_does_not_inject_workspace_status():
    block = await compile_workspace_status(
        run_id="run-x",
        execution_profile={"id": "standard"},
    )
    assert block == ""


@pytest.mark.asyncio
async def test_non_ppt_profile_injects_uploaded_lecture_names(monkeypatch):
    async def _stored(_thread, _user):
        return {
            "exists": True,
            "pages": [],
            "media": [],
            "root_files": [{"name": "高中物理讲义.pdf", "bytes": 1200}],
        }

    async def _snippets(_thread, _user, _query):
        return ["高中物理讲义.pdf:\n牛顿第一定律"]

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.inventory_for_compiler",
        _stored,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.text_snippets",
        _snippets,
    )
    block = await compile_workspace_status(
        run_id="run-notes",
        thread_id="t1",
        user_id="u1",
        user_query="根据讲义出课件",
        execution_profile={"id": "standard"},
    )
    assert "高中物理讲义.pdf" in block
    assert "牛顿第一定律" in block
    assert "如实说明" in block


@pytest.mark.asyncio
async def test_empty_sandbox_uses_stored_workspace_inventory(monkeypatch):
    async def _no_live(_run_id):
        return None

    async def _stored(_thread, _user):
        return {
            "exists": True,
            "pages": [],
            "media": [{"name": "kyrie.jpg", "bytes": 20}],
            "root_files": [],
            "checkpoint_present": True,
        }

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_context.inspect_live_inventory",
        _no_live,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.inventory_for_compiler",
        _stored,
    )
    block = await compile_workspace_status(
        run_id="run-empty",
        thread_id="t1",
        user_id="u1",
        execution_profile=PPT_PROFILE,
    )
    assert "当前：空" not in block
    assert "kyrie.jpg" in block
