from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.chat.builtin_assistants.registry import (
    is_campus_preset,
    is_supported_preset,
    origin_for_preset,
    preset_from_thread_origin,
)
from app.services.chat.builtin_assistants.campus_services.policy import (
    campus_answer_presentation,
    campus_turn_guard,
    reject_campus_request_overrides,
)
from app.services.chat.turn_context_builder import _split_attachments, model_supports_vision
from app.services.chat.builtin_assistants.campus_services.config_service import compute_config_hash
from app.services.files import document_parse_service, user_file_service


def test_identity_registry_maps_campus_origin():
    assert is_supported_preset("campus_services")
    assert is_campus_preset("campus_services")
    assert origin_for_preset("campus_services") == "campus_services"
    assert preset_from_thread_origin("campus_services") == "campus_services"
    assert preset_from_thread_origin("presentation") == "presentation"


def test_campus_answer_style_is_student_scannable_and_keeps_evidence_boundaries():
    style = campus_answer_presentation()
    assert style in campus_turn_guard()
    assert "先用一两句直接回答当前问题" in style
    assert "简单问题用短句或少量要点答完" in style
    assert "时间与地点" in style and "需要带什么" in style
    assert "每步独占一行" in style and "分支分别列出" in style
    assert "截止时间的关键条件和例外必须保留" in style
    assert "缺少决定办理地点的必要信息" in style
    assert "单独一段写 [图N]" in style
    assert "一律不补" in style
    assert "不同问题时，分成对应主题分别回答" in style


@pytest.mark.parametrize("field,value", [
    ("knowledge_ids", ["kb-1"]),
    ("skill_ids", ["s1"]),
    ("file_ids", ["f1"]),
    ("thread_ids", ["t1"]),
])
def test_campus_request_overrides_are_rejected(field, value):
    with pytest.raises(HTTPException) as exc:
        reject_campus_request_overrides({"assistant_preset": "campus_services", field: value})
    assert exc.value.status_code == 422


def test_campus_allows_shared_main_chat_image_attachment():
    reject_campus_request_overrides({
        "assistant_preset": "campus_services",
        "attachments": [{
            "filename": "campus.png",
            "kind": "image",
            "file_id": "uploaded-image-1",
            "image_url": "data:image/png;base64,AA==",
        }],
    })


def test_campus_image_uses_direct_multimodal_path_when_pinned_model_supports_vision():
    attachment = {
        "filename": "campus.png",
        "kind": "image",
        "file_id": "uploaded-image-1",
        "image_url": "data:image/png;base64,AA==",
    }

    image_urls, text_attachments = _split_attachments(
        [attachment],
        model_supports_vision("deepseek-v4-flash-vision-exp"),
    )

    assert image_urls == ["data:image/png;base64,AA=="]
    assert text_attachments == []


@pytest.mark.asyncio
async def test_campus_image_delegates_to_visual_model_when_pinned_model_is_text(monkeypatch):
    attachment = {
        "filename": "campus.png",
        "kind": "image",
        "file_id": "uploaded-image-1",
        "image_url": "data:image/png;base64,AA==",
        "text": "",
        "status": "ok",
    }
    image_urls, text_attachments = _split_attachments(
        [attachment],
        model_supports_vision("deepseek-v4-flash"),
    )
    assert image_urls == []
    assert text_attachments == [attachment]

    read_bytes = AsyncMock(return_value=(SimpleNamespace(id="uploaded-image-1"), b"image-bytes"))
    parse_upload = AsyncMock(return_value={
        "text": "【图片内容（由视觉模型识别）】\n图中是校园主楼。",
        "status": "ok",
        "note": None,
    })
    monkeypatch.setattr(user_file_service, "read_bytes", read_bytes)
    monkeypatch.setattr(document_parse_service, "parse_upload", parse_upload)

    enriched = await user_file_service.ensure_text_model_image_ocr(
        "user-1",
        text_attachments,
        newapi_key="user-key",
        audit_context={"run_id": "run-campus", "thread_id": "thread-campus"},
    )

    assert "【图片内容（由视觉模型识别）】" in enriched[0]["text"]
    assert "校园主楼" in enriched[0]["text"]
    read_bytes.assert_awaited_once_with("user-1", "uploaded-image-1")
    assert parse_upload.await_args.kwargs == {
        "newapi_key": "user-key",
        "ocr_embedded_images": True,
        "ocr_visual": True,
        "audit_context": {"run_id": "run-campus", "thread_id": "thread-campus"},
    }


@pytest.mark.parametrize("attachment", [
    {"filename": "rules.pdf", "kind": "pdf", "file_id": "doc-1"},
    {"filename": "fake.png", "kind": "image", "file_id": ""},
    {"filename": "fake.svg", "kind": "image", "file_id": "image-2"},
    {
        "filename": "remote.png",
        "kind": "image",
        "file_id": "image-3",
        "image_url": "https://untrusted.example/remote.png",
    },
])
def test_campus_rejects_non_uploaded_image_attachments(attachment):
    with pytest.raises(HTTPException) as exc:
        reject_campus_request_overrides({
            "assistant_preset": "campus_services",
            "attachments": [attachment],
        })
    assert exc.value.status_code == 422
    assert "只接受" in str(exc.value.detail)


def test_campus_rejects_plan_and_research_modes():
    with pytest.raises(HTTPException) as exc:
        reject_campus_request_overrides({"assistant_preset": "campus_services", "agent_mode": "research"})
    assert exc.value.status_code == 422


def test_config_hash_is_stable_after_normalization():
    domains = [{"host": "example.edu.cn", "include_subdomains": True}]
    bindings = [
        {"knowledge_id": "b", "category": "教务", "department": "A", "priority": 2, "enabled": True},
        {"knowledge_id": "a", "category": "学工", "department": "B", "priority": 1, "enabled": True},
    ]
    first = compute_config_hash(
        model_id="m1", official_domains=domains, knowledge_bindings=bindings, policy_version="campus-policy-v1",
    )
    second = compute_config_hash(
        model_id="m1",
        official_domains=list(domains),
        knowledge_bindings=list(reversed(bindings)),
        policy_version="campus-policy-v1",
    )
    assert first == second
    assert len(first) == 64
