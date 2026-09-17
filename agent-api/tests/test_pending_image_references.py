"""Recoverable image inputs must not amplify every RunState/event write."""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness import run_store
from app.services.files import user_file_service


@pytest.mark.asyncio
async def test_pending_input_stores_durable_image_reference_without_mutating_request(monkeypatch):
    image = {
        "filename": "photo.png", "kind": "image", "file_id": "owned-file",
        "image_url": "data:image/png;base64," + "A" * 2_400_000,
        "preview_url": "data:image/png;base64,preview", "sha256": "image-hash",
        "text": "", "status": "ok", "note": "原图已保存",
    }
    payload = {"user_id": "owner", "model": "text-only", "attachments": [image]}
    original = copy.deepcopy(payload)
    persist = AsyncMock(return_value={"state": {}})
    monkeypatch.setattr(run_store, "patch_run_state", persist)
    from app.services.connectors import crypto
    monkeypatch.setattr(crypto, "encrypt_secret", lambda _token: "encrypted-test-token")

    await run_store.store_pending_input("run-1", payload, access_token="test-token")

    stored = persist.await_args.args[1]["pending_input"]
    assert stored["attachments"] == [{**image, "image_url": ""}]
    assert stored["access_token_cipher"] == "encrypted-test-token"
    assert "access_token" not in stored
    assert len(json.dumps(stored)) < 1000
    assert payload == original


@pytest.mark.asyncio
async def test_inline_only_legacy_images_and_other_attachments_are_not_dropped(monkeypatch):
    attachments = [
        {"kind": "image", "image_url": "data:image/png;base64,legacy"},
        {"kind": "image", "file_id": "", "image_url": "data:image/png;base64,no-id"},
        {"kind": "image", "file_id": "  ", "image_url": "data:image/png;base64,empty-id"},
        {"kind": "image", "file_id": "file", "image_url": "https://example.test/photo.png"},
        {"kind": "pdf", "file_id": "doc", "text": "complete document text"},
    ]
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(run_store, "patch_run_state", persist)
    await run_store.store_pending_input("run-1", {"attachments": attachments})
    assert persist.await_args.args[1]["pending_input"]["attachments"] == attachments


@pytest.mark.asyncio
async def test_native_vision_restores_exact_bytes_after_pending_input_reload(monkeypatch):
    raw = b"original-pixels" * 140_000
    image_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    attachment = {
        "kind": "image", "filename": "photo.png", "file_id": "owned-file",
        "image_url": image_url, "sha256": hashlib.sha256(raw).hexdigest(), "preview_url": "preview",
    }
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(run_store, "patch_run_state", persist)
    await run_store.store_pending_input("run-1", {"attachments": [attachment]})
    state = persist.await_args.args[1]
    monkeypatch.setattr(run_store, "get_run_state", AsyncMock(return_value={"state": state}))
    payload = await run_store.load_pending_input("run-1")
    read = AsyncMock(return_value=(SimpleNamespace(filename="photo.png", mime="image/png"), raw))
    monkeypatch.setattr(user_file_service, "read_bytes", read)

    restored = await user_file_service.restore_vision_image_attachments("owner", payload["attachments"])

    read.assert_awaited_once_with("owner", "owned-file")
    assert restored == [attachment]
    # Rehydration belongs only to invocation memory, never the recoverable snapshot.
    assert state["pending_input"]["attachments"][0]["image_url"] == ""


@pytest.mark.asyncio
async def test_native_vision_does_not_reread_inline_images_or_non_images(monkeypatch):
    read = AsyncMock(side_effect=AssertionError("unnecessary file read"))
    monkeypatch.setattr(user_file_service, "read_bytes", read)
    attachments = [
        {"kind": "image", "file_id": "file", "image_url": "data:image/png;base64,original"},
        {"kind": "image", "image_url": "data:image/png;base64,legacy"},
        {"kind": "text", "file_id": "doc", "text": "contents"},
    ]
    assert await user_file_service.restore_vision_image_attachments("owner", attachments) == attachments
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_unauthorized_original_is_reported_as_unreadable(monkeypatch):
    read = AsyncMock(side_effect=user_file_service.UserFileError("文件不存在或已过期", status_code=404))
    monkeypatch.setattr(user_file_service, "read_bytes", read)
    attachment = {"kind": "image", "file_id": "not-owned", "filename": "photo.png", "status": "ok"}

    restored = await user_file_service.restore_vision_image_attachments("owner", [attachment])

    read.assert_awaited_once_with("owner", "not-owned")
    assert restored[0]["status"] == "failed"
    assert restored[0]["image_url"] == ""
    assert "本轮未能查看图片" in restored[0]["text"]
    assert "重新上传" in restored[0]["note"]
    assert attachment["status"] == "ok"


@pytest.mark.asyncio
async def test_changed_file_cannot_silently_replace_accepted_image(monkeypatch):
    original = b"accepted pixels"
    read = AsyncMock(return_value=(SimpleNamespace(filename="photo.png", mime="image/png"), b"changed pixels"))
    monkeypatch.setattr(user_file_service, "read_bytes", read)
    attachment = {
        "kind": "image", "file_id": "owned-file", "filename": "photo.png",
        "sha256": hashlib.sha256(original).hexdigest(),
    }

    restored = await user_file_service.restore_vision_image_attachments("owner", [attachment])

    assert restored[0]["status"] == "failed"
    assert restored[0]["image_url"] == ""
    assert "本轮未能查看图片" in restored[0]["text"]
