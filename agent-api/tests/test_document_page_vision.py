from io import BytesIO

import pytest
from PIL import Image

from app.services.files import document_parse_service, user_file_service
from app.services.chat.tools.workspace_sync import WorkspaceSync


def _png(size=8) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), (20, 40, 80)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_document_page_data_urls_injects_vision_pngs(monkeypatch):
    pngs = [_png(), _png()]

    async def fake_preview(user_id, file_id):
        return b"%PDF-fake"

    async def fake_read(user_id, file_id):
        return object(), b"%PDF-1"

    def fake_render(content, limit, *, scale=None):
        return pngs[:limit], 2

    monkeypatch.setattr(user_file_service, "get_preview_pdf", fake_preview)
    monkeypatch.setattr(user_file_service, "read_bytes", fake_read)
    monkeypatch.setattr(document_parse_service, "_render_pdf_pages", fake_render)

    urls, note = await user_file_service.document_page_data_urls(
        "u1",
        [{"filename": "库里.pptx", "file_id": "fid-1", "kind": "pptx"}],
    )
    assert len(urls) == 2
    assert all(u.startswith("data:image/") for u in urls)
    assert "文档页图已注入" in note
    assert "库里.pptx" in note
    assert "to-pdf" in note


def test_workspace_sync_skips_slide_render_junk():
    assert WorkspaceSync._is_intermediate("slide-1.png") is True
    assert WorkspaceSync._is_intermediate("page_2.jpg") is True
    assert WorkspaceSync._is_intermediate("封面.png") is False
    assert WorkspaceSync._is_intermediate("报告.pdf") is False
