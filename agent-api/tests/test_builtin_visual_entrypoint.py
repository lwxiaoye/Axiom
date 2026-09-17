"""Exercise each real Harness entry through image preparation, before generation."""
from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness import artifact_checkpoint, orchestrator, run_store, workspace_service
from app.services.agent_harness import goal_contract
from app.services.chat import turn_context_builder
from app.services.files import document_parse_service, user_file_service
from app.services.skills import ppt_style_reference


class _ReachedModelInput(Exception):
    pass


async def _prepare_through_real_entry(
    monkeypatch, *, preset: str, model: str, vision_output: str,
    inline_image: bool = True, file_available: bool = True,
    message: str = "请识别图片中的内容", enable_preamble: bool = False,
):
    thread_id, run_id = "visual-entry-thread", "visual-entry-run"
    raw = b"test-image-pixels"
    image_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    attachment = {
        "filename": "uploaded.png", "kind": "image", "file_id": "owned-image",
        "image_url": image_url if inline_image else "", "text": "", "status": "ok",
    }
    service = orchestrator.HarnessOrchestrator()
    monkeypatch.setattr(service, "_ensure_thread", AsyncMock(return_value=thread_id))
    monkeypatch.setattr(service, "get_thread_model_setting", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_await_pending_partial_persist", AsyncMock())
    for name in ("initialize_run_state", "patch_run_state"):
        monkeypatch.setattr(run_store, name, AsyncMock())
    monkeypatch.setattr(run_store, "get_run_state", AsyncMock(return_value={"state": {}}))
    monkeypatch.setattr(orchestrator.task_run_service, "save_run_state", AsyncMock())
    monkeypatch.setattr(turn_context_builder, "get_persisted_skill_state", AsyncMock(return_value=[]))
    monkeypatch.setattr(goal_contract, "seed_goal_contract_for_resume", AsyncMock(
        return_value=SimpleNamespace(to_state=lambda: {}),
    ))
    monkeypatch.setattr(orchestrator, "_requires_reasoning_first_preamble", lambda *_args: enable_preamble)
    preamble = AsyncMock(return_value="图片已收到，查看画面后再核对需要解释的内容。")
    monkeypatch.setattr(orchestrator, "generate_public_commentary", preamble)
    monkeypatch.setattr(orchestrator.thread_attachment_service, "recent_file_targets", AsyncMock(return_value=[]))
    monkeypatch.setattr(ppt_style_reference, "analyze_ppt_style_reference", AsyncMock(return_value=""))
    monkeypatch.setattr(workspace_service, "ingest_user_files_into_workspace", AsyncMock())
    monkeypatch.setattr(artifact_checkpoint, "hydrate_ppt_staging", AsyncMock(return_value=(None, None)))

    read_bytes = AsyncMock(return_value=(SimpleNamespace(id="owned-image", filename="uploaded.png", mime="image/png"), raw))
    if not file_available:
        read_bytes.side_effect = user_file_service.UserFileError("文件不存在或已过期", status_code=404)
    monkeypatch.setattr(user_file_service, "read_bytes", read_bytes)
    monkeypatch.setattr(document_parse_service.cfg, "get_ocr_config", AsyncMock(return_value={
        "enabled": True, "strategy": "multimodal_model", "model": "configured-vision-model",
        "visionBaseUrl": "https://vision.example/v1", "visionApiKey": "test-key",
    }))
    async def visual_response(*_args, **_kwargs):
        captured["commentary_before_vision"] = any(
            '"type":"message.commentary"' in frame or '"type": "message.commentary"' in frame
            for frame in frames
        )
        return None, {"choices": [{"message": {"content": vision_output}}]}

    provider = AsyncMock(side_effect=visual_response)
    monkeypatch.setattr(document_parse_service, "_audited_document_model_post", provider)

    captured = {"preamble": preamble}

    async def file_context(**kwargs):
        captured["text_attachments"] = kwargs["attachments"]
        return "\n".join(str(item.get("text") or "") for item in kwargs["attachments"])

    monkeypatch.setattr(orchestrator.thread_attachment_service, "build_file_context", file_context)
    as_content = orchestrator._as_turn_content

    def reach_model_input(text, urls):
        captured["model_content"] = as_content(text, urls)
        raise _ReachedModelInput

    monkeypatch.setattr(orchestrator, "_as_turn_content", reach_model_input)
    snapshot = {
        "code": "campus_services", "release_id": "release-1", "knowledge_ids": ["official-kb"],
        "model_id": model, "official_domains": [{"host": "school.example", "include_subdomains": True}],
    }
    frames = []
    with pytest.raises(_ReachedModelInput):
        async for frame in service.stream_chat(
            user_id="image-owner", thread_id=thread_id, run_id=run_id, precreated_run=True,
            message=message, newapi_key="user-test-key",
            # Campus must use its published model when deciding whether to delegate.
            resolved_model="unused-default-model" if preset == "campus_services" else model,
            assistant_preset=preset,
            assistant_preset_snapshot=snapshot if preset == "campus_services" else None,
            attachments=[attachment],
        ):
            frames.append(frame)
    return captured, frames, provider, read_bytes, image_url


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["", "presentation", "campus_services"])
async def test_image_only_turn_publishes_preamble_before_shared_vision(monkeypatch, preset):
    captured, _frames, provider, _read_bytes, _image_url = await _prepare_through_real_entry(
        monkeypatch, preset=preset, model="deepseek-v4-flash", vision_output="图片里有一栋教学楼。",
        message="", enable_preamble=True, inline_image=False,
    )
    captured["preamble"].assert_awaited_once()
    assert "尚未读取文件正文或图片内容" in captured["preamble"].await_args.kwargs["developer_prompt"]
    assert captured["commentary_before_vision"] is True
    provider.assert_awaited_once()
    assert "教学楼" in captured["model_content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["", "presentation", "campus_services"])
@pytest.mark.parametrize("model", ["text-only-model", "gpt-4o"])
@pytest.mark.parametrize("inline_image", [True, False], ids=["inline-legacy", "durable-reference"])
async def test_main_presentation_and_campus_share_real_image_entry(monkeypatch, preset, model, inline_image):
    captured, _frames, provider, read_bytes, image_url = await _prepare_through_real_entry(
        monkeypatch, preset=preset, model=model, vision_output="图片里有一栋教学楼和一棵树。",
        inline_image=inline_image,
    )
    if model == "gpt-4o":
        assert captured["model_content"][-1] == {"type": "image_url", "image_url": {"url": image_url}}
        assert captured["text_attachments"] == []
        provider.assert_not_awaited()
        if inline_image:
            read_bytes.assert_not_awaited()
        else:
            read_bytes.assert_awaited_once_with("image-owner", "owned-image")
    else:
        assert isinstance(captured["model_content"], str)
        assert "【图片内容（由视觉模型识别）】" in captured["model_content"]
        assert "教学楼和一棵树" in captured["model_content"]
        read_bytes.assert_awaited_once_with("image-owner", "owned-image")
        provider.assert_awaited_once()
        call = provider.await_args.kwargs
        assert call["model"] == "configured-vision-model"
        assert call["purpose_detail"] == "document_image_vision"
        assert call["audit_context"] == {"run_id": "visual-entry-run", "thread_id": "visual-entry-thread"}
        assert call["json_payload"]["messages"][0]["content"][-1]["image_url"]["url"] == image_url


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["", "presentation", "campus_services"])
async def test_visual_failure_is_exposed_to_student_and_answer_model(monkeypatch, preset):
    captured, frames, provider, _read_bytes, _image_url = await _prepare_through_real_entry(
        monkeypatch, preset=preset, model="text-only-model", vision_output="",
    )
    provider.assert_awaited_once()
    assert captured["text_attachments"][0]["status"] == "failed"
    assert "视觉模型未返回内容" in captured["model_content"]
    assert "严禁假装已读取全部内容" in captured["model_content"]
    assert any('"type":"attachments.status"' in frame or '"type": "attachments.status"' in frame for frame in frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["", "presentation", "campus_services"])
async def test_unreadable_native_image_is_exposed_through_all_real_entries(monkeypatch, preset):
    captured, frames, provider, read_bytes, _image_url = await _prepare_through_real_entry(
        monkeypatch, preset=preset, model="gpt-4o", vision_output="", inline_image=False,
        file_available=False,
    )
    provider.assert_not_awaited()
    read_bytes.assert_awaited_once_with("image-owner", "owned-image")
    assert captured["text_attachments"][0]["status"] == "failed"
    assert isinstance(captured["model_content"], str)
    assert "本轮未能查看图片" in captured["model_content"]
    assert "严禁假装已读取全部内容" in captured["model_content"]
    assert any('"type":"attachments.status"' in frame or '"type": "attachments.status"' in frame for frame in frames)
