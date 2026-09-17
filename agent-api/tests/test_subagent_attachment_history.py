import json
from types import SimpleNamespace

import pytest

from app.routers.workflow import RunMessageAppend, _run_message_attachments
from app.services.chat.subagent_turn import _resolve_delegation_files


def test_legacy_delegation_marker_restores_docx_card():
    row = SimpleNamespace(
        role="user",
        content=(
            "请审阅这份文档。\n\n"
            "📎 交付文件：《Agent = Model + Harness 原则下的企业经营管理智能体开发准则.docx》"
        ),
        attachments_json=None,
    )

    attachments = _run_message_attachments(row, "delegation")

    assert attachments == [{
        "filename": "Agent = Model + Harness 原则下的企业经营管理智能体开发准则.docx",
        "kind": "docx",
        "status": "ok",
    }]


def test_structured_attachment_history_wins_over_legacy_text_marker():
    stored = [{
        "filename": "report.pdf",
        "kind": "pdf",
        "status": "partial",
        "note": "仅识别前 15 页",
        "file_id": "file-1",
    }]
    row = SimpleNamespace(
        role="user",
        content="交付文件：《old.docx》",
        attachments_json=json.dumps(stored, ensure_ascii=False),
    )

    assert _run_message_attachments(row, "delegation") == stored


def test_run_message_append_accepts_attachment_metadata():
    payload = RunMessageAppend.model_validate({
        "sessionId": "run-1",
        "role": "user",
        "content": "请审阅",
        "attachments": [{"filename": "report.docx", "kind": "docx", "status": "ok"}],
    })

    assert payload.attachments == [{"filename": "report.docx", "kind": "docx", "status": "ok"}]


@pytest.mark.asyncio
async def test_delegation_carries_attachment_card_without_persisting_document_text():
    block, marker, attachments = await _resolve_delegation_files(
        None,
        "",
        [],
        [{
            "filename": "report.docx",
            "kind": "docx",
            "text": "这是只应进模型上下文的文档正文",
            "status": "ok",
        }],
    )

    assert "这是只应进模型上下文的文档正文" in block
    assert marker == "📎 交付文件：《report.docx》"
    assert attachments == [{"filename": "report.docx", "kind": "docx", "status": "ok"}]
    assert "text" not in attachments[0]
