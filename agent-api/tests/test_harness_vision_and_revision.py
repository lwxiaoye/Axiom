from pathlib import Path
# -*- coding: utf-8 -*-
"""Harness: vision attachment path + revision hard-gate guidance."""
import base64

from app.services.chat.turn_decision import (
    REVISION_TARGET_UNRESOLVED_GUIDANCE,
    build_revision_candidate_guidance,
    resolve_revision_target_from_choice,
)
from app.services.chat.turn_context_builder import model_supports_vision
from app.services.files.user_file_service import _build_vision_data_url


def test_revision_unresolved_guidance_hard_gate_wording():
    g = REVISION_TARGET_UNRESOLVED_GUIDANCE
    assert "ask_user_choice" in g
    assert "未开放" in g or "收起" in g
    assert "仍然可用" not in g


def test_revision_candidate_guidance_closes_option_set():
    text = build_revision_candidate_guidance([
        {"filename": "报告A.md"},
        {"filename": "报告B.md"},
        {"filename": "报告A.md"},
    ])
    assert "报告A.md" in text and "报告B.md" in text
    assert "恰好" in text
    assert build_revision_candidate_guidance([{"filename": "only.md"}]) == ""


def test_deepseek_flash_vision_is_treated_as_multimodal():
    assert model_supports_vision("deepseek-v4-flash-vision-exp") is True
    assert model_supports_vision("deepseek-v4-flash") is False
    assert model_supports_vision("deepseek-v4-pro") is False


def test_vision_data_url_for_small_png():
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    url = _build_vision_data_url("dot.png", png, "image/png")
    assert url.startswith("data:image/")
    assert ";base64," in url
    assert _build_vision_data_url("empty.png", b"", "image/png") == ""

def test_attachment_guidance_has_no_ghost_tools():
    """附件文案不得教已下线工具——这是 prompt 与 tool surface 打架的高发源。"""
    src = Path(__file__).resolve().parents[1] / "app/services/files/user_file_service.py"
    text = src.read_text(encoding="utf-8")
    # 只检查 build_chat_attachments 函数体附近的教法字符串
    start = text.index("async def build_chat_attachments")
    end = text.index("async def delete_file", start)
    body = text[start:end]
    ghosts = [
        "update_file(file_id",
        "execute_in_sandbox 传 file_ids",
        "edit_docx(file_id",
        "apply_presentation_patch(file_id",
        "inspect_workbook(file_id",
        "verify_artifact(file_id",
        "/workspace/inputs/",
        "read_file(file_id=",
        "edit_file(file_id=",
    ]
    for g in ghosts:
        assert g not in body, f"ghost tool guidance still present: {g}"
    assert "/workspace/files/" in body
    assert "edit_file(path=" in body
    assert "write_file(path=" in body
    assert "必须嵌入这份真实文件" in body

def test_resolve_revision_target_from_choice():
    cands = [
        {"file_id": "1", "filename": "a.pptx"},
        {"file_id": "2", "filename": "b.pptx"},
    ]
    hit = resolve_revision_target_from_choice("b.pptx", cands)
    assert hit and hit["file_id"] == "2"
    assert resolve_revision_target_from_choice("请改 a.pptx 那份", cands)["file_id"] == "1"
    assert resolve_revision_target_from_choice("都不是", cands) is None

