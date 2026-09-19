"""图片附件原生进多模态模型（2026-09-19 用户拍板「OCR 不需要，我们用的是多模态模型」）。

复现的故障：主对话带一张通知截图问「期末考试是什么时候」，图片被当成「待修改的文件」记成
revision target、灌进工作区、再经视觉模型转述——模型连开十个工具、300 秒没有回答。
"""
from __future__ import annotations

import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.core.config import settings
from app.core.model_endpoint import bind_model_connection
from app.services.agent_harness import orchestrator, workspace_service
from app.services.chat.turn_context_builder import model_supports_vision
from app.services.files import document_parse_service, user_file_service
from tests.test_builtin_visual_entrypoint import _prepare_through_real_entry

QUESTION = "这张通知里期末考试是什么时候？"


def _image(name: str = "notice.png") -> dict:
    return {"file_id": "img-1", "filename": name, "kind": "image"}


# ---- 附件分类：图片默认只是给模型看的参考 ----

@pytest.mark.parametrize("message", [
    QUESTION,
    "帮我看看这张图讲了什么",
    "这张图里的字是什么意思",
    "翻译这张图上的英文",
    "图片里的表格数据帮我整理成 markdown 表格",
])
def test_plain_image_question_is_reference_only(message):
    assert orchestrator._attachment_is_reference_only(message, _image()) is True
    assert orchestrator._has_revision_target_attachments(message, [_image()]) is False
    decision = orchestrator._decide_turn_with_attachment_intent(message, [_image()])
    assert decision.revision is False
    # 看图问答不能被硬改成 execute（那是 PPT 风格参考轮才有的覆盖）
    assert decision.reason_code != "ppt_style_reference_create"


@pytest.mark.parametrize("message", [
    "把这张图片的背景换成蓝色",
    "这张照片的水印去掉",
    "帮我裁剪一下这张图",
    "用这张图做一张海报",
    "把这张图做成 PPT 的封面",
    "根据这张截图生成一份 Word 文档",
])
def test_image_edit_or_material_request_keeps_workspace_path(message):
    assert orchestrator._attachment_is_reference_only(message, _image()) is False
    assert orchestrator._has_revision_target_attachments(message, [_image()]) is True


def test_non_image_attachments_are_never_reference_only():
    docx = {"file_id": "doc-1", "filename": "plan.docx", "kind": "text"}
    assert orchestrator._attachment_is_reference_only("这份文档讲了什么", docx) is False
    assert orchestrator._workspace_bound_attachments(QUESTION, [docx, _image()]) == [docx]


def test_ppt_style_reference_override_still_applies():
    message = "请参考我上传图片的高级感视觉语言，制作一份 8 页 PPT"
    reference = _image("reference.jpg")
    decision = orchestrator._decide_turn_with_attachment_intent(message, [reference])
    assert decision.reason_code == "ppt_style_reference_create"
    assert decision.intent == "execute" and decision.allow_create is True
    # 风格参考图不是修订目标，但仍进工作区（PPT 流程既有语义）；看图问答的图两者都不是
    assert orchestrator._image_attachment_role(message, reference) == orchestrator.IMAGE_ROLE_STYLE_REFERENCE
    assert orchestrator._workspace_bound_attachments(message, [reference]) == [reference]
    assert orchestrator._image_attachment_role(QUESTION, reference) == orchestrator.IMAGE_ROLE_VIEW_ONLY
    assert orchestrator._workspace_bound_attachments(QUESTION, [reference]) == []


# ---- 平台对话模型视为多模态 ----

def test_platform_connection_model_counts_as_vision(monkeypatch):
    monkeypatch.setattr(settings, "VISION_MODEL_KEYWORDS", "vl,vision")
    bind_model_connection(None)
    assert model_supports_vision("campus-chat-model") is False
    bind_model_connection({"base_url": "https://proxy.example/v1", "model": "campus-chat-model", "api_key": "k"})
    try:
        assert model_supports_vision("campus-chat-model") is True
        assert model_supports_vision("CAMPUS-CHAT-MODEL") is True
        assert model_supports_vision("other-model") is False
        monkeypatch.setattr(settings, "MODEL_CONNECTION_MULTIMODAL", False)
        assert model_supports_vision("campus-chat-model") is False
    finally:
        bind_model_connection(None)


def test_grok_is_in_default_vision_keywords():
    assert model_supports_vision("grok-4.6") is True


# ---- 编排器单轮：图片以 image 内容块进模型，不 OCR、不进工作区、不是修订目标 ----

@pytest.mark.asyncio
async def test_image_question_goes_native_without_ocr_or_workspace(monkeypatch, caplog):
    from app.services.files import work_folders
    # 工作文件夹解析要查库；这条单轮夹具不该依赖真实数据库
    monkeypatch.setattr(work_folders, "folder_for_thread", AsyncMock(return_value=None))
    caplog.set_level(logging.INFO, logger="app.services.agent_harness.orchestrator")
    captured, _frames, provider, read_bytes, image_url = await _prepare_through_real_entry(
        monkeypatch, preset="", model="grok-4.6", vision_output="不该被调用",
        inline_image=False, message=QUESTION,
    )
    # 像素直接进 user message 的多模态内容块
    assert captured["model_content"][0] == {"type": "text", "text": QUESTION}
    assert captured["model_content"][-1] == {"type": "image_url", "image_url": {"url": image_url}}
    assert captured["text_attachments"] == []
    read_bytes.assert_awaited_once_with("image-owner", "owned-image")
    # 没有「视觉模型描述成文字」这一层
    provider.assert_not_awaited()
    # 不是修订目标
    routing = [r.getMessage() for r in caplog.records if "attachment.routing" in r.getMessage()]
    assert routing and "revision_targets=0" in routing[-1] and "reference_only=[True]" in routing[-1]
    # 参考图不进会话工作区（否则每轮都为它拉沙箱并注入「请接着改现有文件」）
    ingest = workspace_service.ingest_user_files_into_workspace
    ingest.assert_awaited_once()
    assert ingest.await_args.kwargs["attachments"] == []


# ---- 直传图片大小上限 ----

def test_native_vision_payload_keeps_small_images_untouched():
    data = b"\x89PNG small"
    assert user_file_service._native_vision_payload(data, "image/png") == (data, "image/png")


def test_native_vision_payload_downscales_oversized_images():
    import os
    width = height = 1800
    noise = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    noise.save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) > user_file_service._NATIVE_VISION_MAX_BYTES
    payload, mime = user_file_service._native_vision_payload(raw, "image/png")
    assert len(payload) <= user_file_service._NATIVE_VISION_MAX_BYTES
    assert mime == "image/jpeg"
    shrunk = Image.open(io.BytesIO(payload))
    assert max(shrunk.size) == user_file_service._NATIVE_VISION_MAX_EDGE


# ---- 「我的文件」图片：多模态模型不再先转述 ----

@pytest.mark.asyncio
async def test_build_chat_attachments_skips_vision_ocr_for_multimodal_model(monkeypatch):
    row = SimpleNamespace(id="f1", filename="photo.png", mime="image/png")
    monkeypatch.setattr(user_file_service, "read_bytes", AsyncMock(return_value=(row, b"\x89PNGbytes")))
    parse = AsyncMock(side_effect=AssertionError("multimodal chat model must not pre-describe images"))
    monkeypatch.setattr(user_file_service, "_parse_chat_attachment", parse)
    out = await user_file_service.build_chat_attachments("u1", ["f1"], newapi_key="k", ocr_visual=False)
    assert out[0]["kind"] == "image" and out[0]["image_url"].startswith("data:image/png;base64,")
    parse.assert_not_awaited()


# ---- 扫描件 / 文档内嵌图的视觉模型来源：平台对话模型 ----

@pytest.mark.asyncio
async def test_vision_runtime_prefers_explicit_endpoint_then_platform_model(monkeypatch):
    from app.services.platform import model_connection
    platform = {"base_url": "https://chat.example/v1/", "api_key": "platform-key", "model": "grok-4.6"}
    monkeypatch.setattr(model_connection, "runtime_platform", AsyncMock(return_value=platform))

    explicit = {"model": "qwen-vl", "visionBaseUrl": "https://vision.example/v1/", "visionApiKey": "vk"}
    resolved = await document_parse_service._resolve_vision_runtime(explicit, "caller-key")
    assert resolved == {"base_url": "https://vision.example/v1", "api_key": "vk", "model": "qwen-vl", "source": "ocr_config"}

    # 三者缺任一 → 整体回落平台对话模型，不做半套拼接
    for partial in ({"model": "qwen-vl"}, {"model": "qwen-vl", "visionBaseUrl": "https://vision.example/v1"}, {}):
        resolved = await document_parse_service._resolve_vision_runtime(partial, "caller-key")
        assert resolved == {"base_url": "https://chat.example/v1", "api_key": "platform-key", "model": "grok-4.6", "source": "platform_model"}


@pytest.mark.asyncio
async def test_vision_runtime_falls_back_to_gateway_then_none(monkeypatch):
    from app.services.platform import model_connection
    monkeypatch.setattr(model_connection, "runtime_platform", AsyncMock(return_value=None))
    resolved = await document_parse_service._resolve_vision_runtime({"model": "glm-4v"}, "caller-key")
    assert resolved is not None and resolved["source"] == "gateway" and resolved["model"] == "glm-4v"
    assert await document_parse_service._resolve_vision_runtime({}, "caller-key") is None
    assert await document_parse_service._resolve_vision_runtime({"model": "glm-4v"}, "") is None


@pytest.mark.asyncio
async def test_scanned_pdf_uses_platform_model_when_ocr_row_has_no_vision_endpoint(monkeypatch):
    from app.services.platform import model_connection
    platform = {"base_url": "https://chat.example/v1", "api_key": "platform-key", "model": "grok-4.6"}
    monkeypatch.setattr(model_connection, "runtime_platform", AsyncMock(return_value=platform))
    monkeypatch.setattr(document_parse_service.cfg, "get_ocr_config", AsyncMock(return_value={
        "enabled": True, "strategy": "multimodal_model", "endpointUrl": "", "apiKey": "",
        "model": "", "visionBaseUrl": "", "visionApiKey": "",
    }))
    calls = []

    async def fake_post(client, url, **kwargs):
        calls.append({"url": url, "model": kwargs["model"], "key": kwargs["provider_api_key"]})
        return None, {"choices": [{"message": {"content": "期末考试：1月6日至1月12日"}}]}

    monkeypatch.setattr(document_parse_service, "_audited_document_model_post", fake_post)
    output = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(output, format="PDF")
    parsed = await document_parse_service.parse_upload("scan.pdf", output.getvalue(), newapi_key="")
    assert parsed["status"] == "ok" and "1月6日" in parsed["text"]
    assert calls == [{"url": "https://chat.example/v1/chat/completions", "model": "grok-4.6", "key": "platform-key"}]


def test_ocr_defaults_enable_platform_multimodal_fallback():
    from app.services.platform import platform_config_service as cfg
    assert cfg.OCR_DEFAULTS["enabled"] is True
    assert cfg.OCR_DEFAULTS["strategy"] == "multimodal_model"
    assert cfg.OCR_DEFAULTS["model"] == "" and cfg.OCR_DEFAULTS["visionBaseUrl"] == ""
