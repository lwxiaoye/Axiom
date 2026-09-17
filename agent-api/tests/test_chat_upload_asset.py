import hashlib
from types import SimpleNamespace

import pytest

from app.api.router import upload_chat_file
from app.services.files import document_parse_service, user_file_service
from app.services.platform.key_service import key_service


class _Request:
    headers = {}


class _Upload:
    filename = "same-name.xlsx"
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def __init__(self, data: bytes):
        self._data = data
        self._read = False

    async def read(self, _size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._data


@pytest.mark.asyncio
async def test_chat_upload_returns_exact_saved_file_identity(monkeypatch):
    raw = b"new workbook bytes"
    captured = {}

    async def fake_save(user_id, filename, data, **kwargs):
        captured.update(user_id=user_id, filename=filename, data=data, kwargs=kwargs)
        return {"id": "file-new-exact", "versionNo": 1}

    async def fake_parse(filename, data, **kwargs):
        assert filename == "same-name.xlsx"
        assert data == raw
        captured["parse_kwargs"] = kwargs
        return {
            "filename": filename,
            "kind": "xlsx",
            "text": "新上传文件的内容",
            "chars": 8,
            "truncated": False,
            "status": "ok",
        }

    monkeypatch.setattr(user_file_service, "save_file", fake_save)
    monkeypatch.setattr(document_parse_service, "parse_upload", fake_parse)
    monkeypatch.setattr(key_service, "get_user_key", pytest.fail)

    result = await upload_chat_file(
        request=_Request(),
        file=_Upload(raw),
        user=SimpleNamespace(user_id="u1"),
    )

    assert result["file_id"] == "file-new-exact"
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert captured["data"] == raw
    assert captured["kwargs"]["source"] == "workspace"
    assert captured["kwargs"]["source"] != "uploaded"
    assert captured["parse_kwargs"] == {
        "newapi_key": "",
        "ocr_embedded_images": False,
        "ocr_visual": False,
    }


@pytest.mark.asyncio
async def test_chat_upload_skips_visual_ocr_for_vision_models(monkeypatch):
    async def fake_save(user_id, filename, data, **kwargs):
        return {"id": "file-img", "versionNo": 1}

    monkeypatch.setattr(user_file_service, "save_file", fake_save)
    parse_upload = pytest.fail
    get_user_key = pytest.fail
    monkeypatch.setattr(document_parse_service, "parse_upload", parse_upload)
    monkeypatch.setattr(key_service, "get_user_key", get_user_key)

    photo = _Upload(b"\x89PNG")
    photo.filename = "shot.png"
    photo.content_type = "image/png"
    result = await upload_chat_file(
        request=_Request(),
        file=photo,
        model="deepseek-v4-flash-vision-exp",
        user=SimpleNamespace(user_id="u1"),
    )
    assert result["kind"] == "image"
    assert result["status"] == "ok"
    assert result["text"] == ""
    assert "回合执行阶段读取" in result["note"]


@pytest.mark.asyncio
async def test_chat_upload_defers_text_model_image_ocr_until_run_exists(monkeypatch):
    async def fake_save(user_id, filename, data, **kwargs):
        return {"id": "file-img", "versionNo": 1}

    monkeypatch.setattr(user_file_service, "save_file", fake_save)
    monkeypatch.setattr(document_parse_service, "parse_upload", pytest.fail)
    monkeypatch.setattr(key_service, "get_user_key", pytest.fail)

    photo = _Upload(b"\x89PNG")
    photo.filename = "shot.png"
    photo.content_type = "image/png"
    result = await upload_chat_file(
        request=_Request(),
        file=photo,
        model="deepseek-v4-flash",
        user=SimpleNamespace(user_id="u1"),
    )
    assert result["text"] == ""
    assert "回合执行阶段读取" in result["note"]


@pytest.mark.asyncio
async def test_chat_upload_rejects_unsupported_and_legacy_office():
    from fastapi import HTTPException

    zip_file = _Upload(b"PK")
    zip_file.filename = "payload.zip"
    zip_file.content_type = "application/zip"
    with pytest.raises(HTTPException) as rejected:
        await upload_chat_file(
            request=_Request(),
            file=zip_file,
            user=SimpleNamespace(user_id="u1"),
        )
    assert rejected.value.status_code == 415
    assert "不支持「payload.zip」" in str(rejected.value.detail)

    old_doc = _Upload(b"\xd0\xcf\x11\xe0")
    old_doc.filename = "旧稿.doc"
    old_doc.content_type = "application/msword"
    with pytest.raises(HTTPException) as legacy:
        await upload_chat_file(
            request=_Request(),
            file=old_doc,
            user=SimpleNamespace(user_id="u1"),
        )
    assert legacy.value.status_code == 415
    assert "另存为 .docx" in str(legacy.value.detail)
