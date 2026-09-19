# -*- coding: utf-8 -*-
import base64

import pytest

from app.services.files.html_artifact_service import bundle_html_images


def test_bundles_relative_workspace_and_css_images_into_one_html():
    photo = b"\xff\xd8\xffuser-photo"
    logo = b"\x89PNG\r\nlogo"
    html = """<!doctype html>
    <style>.hero { background-image: url('../media/hero.jpg'); }</style>
    <img src="/workspace/files/logo.png?version=2#crop" alt="logo">
    """

    result = bundle_html_images(
        html,
        {"media/hero.jpg": photo, "logo.png": logo},
        html_path="pages/index.html",
    )
    text = result.data.decode("utf-8")

    assert result.missing == ()
    assert set(result.embedded) == {"media/hero.jpg", "logo.png"}
    assert f"data:image/jpeg;base64,{base64.b64encode(photo).decode()}" in text
    assert f"data:image/png;base64,{base64.b64encode(logo).decode()}" in text
    assert "/workspace/files/" not in text
    assert "../media/hero.jpg" not in text


def test_preserves_remote_data_and_non_image_dependencies():
    html = """<link rel="stylesheet" href="styles.css">
    <script src="app.js"></script>
    <img src="https://cdn.example.test/a.jpg">
    <img src="data:image/png;base64,AAAA">
    <source src="movie.mp4" type="video/mp4">
    """

    result = bundle_html_images(html, {}, html_path="index.html")

    assert result.missing == ()
    assert result.data.decode("utf-8") == html


def test_reports_missing_local_image_instead_of_silently_publishing_it():
    result = bundle_html_images(
        '<img src="missing.jpg"><link rel="icon" href="favicon.png">',
        {},
        html_path="index.html",
    )

    assert result.missing == ("missing.jpg", "favicon.png")
    assert result.embedded == ()


def test_srcset_images_are_all_embedded():
    result = bundle_html_images(
        '<picture><source srcset="small.webp 1x, large.webp 2x"></picture>',
        {"small.webp": b"small", "large.webp": b"large"},
    )
    text = result.data.decode("utf-8")

    assert result.missing == ()
    assert text.count("data:image/webp;base64,") == 2


def test_image_mime_does_not_depend_on_system_mimetypes_table(monkeypatch):
    """容器里没有 /etc/mime.types、Python 3.11 也不内置 .webp/.apng/.jfif：不能把图片内嵌成
    application/octet-stream（<source>/srcset 不会对 octet-stream 做图片嗅探）。"""
    import mimetypes

    from app.services.files import html_artifact_service

    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: (None, None))
    html = (
        '<img src="a.webp"><img src="b.apng"><img src="c.jfif"><img src="d.svg">'
        '<img src="e.png?v=1#x">'
    )
    files = {"a.webp": b"1", "b.apng": b"2", "c.jfif": b"3", "d.svg": b"4", "e.png": b"5"}
    text = html_artifact_service.bundle_html_images(html, files).data.decode("utf-8")

    assert "application/octet-stream" not in text
    for mime in ("image/webp", "image/apng", "image/jpeg", "image/svg+xml", "image/png"):
        assert f"data:{mime};base64," in text


def test_handles_unquoted_img_src_but_does_not_rewrite_script_text():
    html = """<img src=photo.jpg>
    <div style="background:url(photo.jpg)"></div>
    <script>const sample = 'url(not-a-real-image.png)';</script>
    """
    result = bundle_html_images(html, {"photo.jpg": b"photo"})
    text = result.data.decode("utf-8")

    assert result.missing == ()
    assert "src=data:image/jpeg;base64," in text
    assert 'style="background:url(data:image/jpeg;base64,' in text
    assert "url(not-a-real-image.png)" in text


@pytest.mark.asyncio
async def test_streamed_html_artifact_is_bundled_before_new_file_is_saved(monkeypatch):
    from app.services.agent_harness import workspace_service
    from app.services.files import user_file_service

    saved: dict[str, object] = {}

    async def workspace_assets(_thread_id, _user_id):
        return {"media/portrait.jpg": b"selected-user-photo"}

    async def no_existing(_user_id, _filename, _thread_id):
        return None

    async def fake_save(user_id, filename, data, **kwargs):
        saved.update(user_id=user_id, filename=filename, data=bytes(data), kwargs=kwargs)
        return {"id": "html-1", "filename": filename, "size": len(data)}

    monkeypatch.setattr(workspace_service, "asset_bytes_for_publish", workspace_assets)
    monkeypatch.setattr(user_file_service, "find_generated_file", no_existing)
    monkeypatch.setattr(user_file_service, "save_file", fake_save)

    row = await user_file_service.save_generated_artifact(
        "u1",
        "portrait.html",
        '<img src="/workspace/tmp/ppt-project/media/portrait.jpg">',
        thread_id="t1",
    )

    assert row["id"] == "html-1"
    assert b"data:image/jpeg;base64," in saved["data"]
    assert b"/workspace/" not in saved["data"]


@pytest.mark.asyncio
async def test_streamed_html_artifact_rejects_unresolved_local_image(monkeypatch):
    from app.services.agent_harness import workspace_service
    from app.services.files import user_file_service

    async def no_assets(_thread_id, _user_id):
        return {}

    monkeypatch.setattr(workspace_service, "asset_bytes_for_publish", no_assets)

    with pytest.raises(user_file_service.UserFileError) as caught:
        await user_file_service.save_generated_artifact(
            "u1", "broken.html", '<img src="missing.jpg">', thread_id="t1",
        )

    assert caught.value.status_code == 422
    assert "missing.jpg" in str(caught.value)
