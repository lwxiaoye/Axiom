from app.services.agent_harness import orchestrator as chat_service
from app.services.chat.turn_decision import decide_turn
from app.services.agent_harness.model_driver import _is_image_read_file_call


def _image() -> dict:
    return {
        "file_id": "img-1",
        "filename": "reference.jpg",
        "kind": "image",
    }


def test_style_reference_image_does_not_become_revision_target() -> None:
    message = "请参考我上传图片的高级感视觉语言，制作一份 8 页 PPT"
    assert chat_service._attachment_is_reference_only(message, _image()) is True
    selected = chat_service._has_revision_target_attachments(message, [_image()])
    decision = decide_turn(message, has_selected_files=selected)
    assert decision.revision is False
    assert decision.allow_create is True


def test_long_product_launch_prompt_still_routes_reference_as_input() -> None:
    message = (
        "请参考我上传图片的高级感视觉语言，制作一份 8 页、16:9 的中文产品发布会 "
        "PPT：《ORBITAL FRONTIER｜可重复使用运载系统》。不要复制参考图里的商标、水印、原文或具体版式，"
        "只迁移可复用的视觉 DNA。导出可编辑 PPTX。"
    )
    assert chat_service._attachment_is_reference_only(message, _image()) is True
    assert chat_service._has_revision_target_attachments(message, [_image()]) is False
    decision = chat_service._decide_turn_with_attachment_intent(message, [_image()])
    assert decision.intent == "execute"
    assert decision.revision is False
    assert decision.allow_create is True


def test_explicit_image_edit_keeps_revision_target() -> None:
    message = "请先修改这张图片的背景，再用它制作一份 PPT"
    assert chat_service._attachment_is_reference_only(message, _image()) is False
    assert chat_service._has_revision_target_attachments(message, [_image()]) is True


def test_selected_pptx_remains_revision_target() -> None:
    attachment = {"file_id": "ppt-1", "filename": "old.pptx", "kind": "file"}
    message = "参考原稿风格，修改这份 PPT 的第 3 页"
    assert chat_service._has_revision_target_attachments(message, [attachment]) is True


def test_flash_ppt_image_read_guard_detects_raster_images_only() -> None:
    assert _is_image_read_file_call("read_file", {"path": "media/cover.JPG"}) is True
    assert _is_image_read_file_call("read_file", {"path": "media/cover.webp?rev=2"}) is True
    assert _is_image_read_file_call("read_file", {"path": "spec.json"}) is False
    assert _is_image_read_file_call("bash", {"path": "media/cover.jpg"}) is False
