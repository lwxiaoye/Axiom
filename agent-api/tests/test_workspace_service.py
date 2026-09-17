"""Workspace File Service: source tree, working copy, overlay visibility."""
from __future__ import annotations

import io
import tarfile
from unittest.mock import AsyncMock

import pytest

from app.services.files.deliverable import WORKSPACE_SOURCE, is_deliverable
from app.services.agent_harness.workspace_service import (
    InMemoryWorkspaceRepo,
    clear_local_parse_cache_for_tests,
    compiler_inventory_from_manifest,
    commit_tree_snapshot,
    diff_visible,
    has_current_tree,
    ingest_user_files_into_workspace,
    inventory_for_compiler,
    is_overlay_visible,
    is_shared_sandbox_path,
    is_workspace_deliverable,
    list_workspace,
    parse_tree_manifest,
    pull_into_run,
    sandbox_asset_rel,
    save_asset,
    set_repo_for_tests,
    text_snippets,
    workspace_storage_key,
)
from app.services.skills.ppt_agentic_adapter import STAGING_ROOT
from app.services.agent_harness.workspace_service import WORK_ROOT


class _MemStorage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def exists(self, key: str) -> bool:
        return key in self.data

    async def read_bytes(self, key: str) -> bytes:
        return self.data[key]

    async def write_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.data[key] = data

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def delete_prefix(self, prefix: str) -> None:
        for item in [key for key in self.data if key.startswith(prefix)]:
            self.data.pop(item, None)


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def ws_env(monkeypatch):
    repo = InMemoryWorkspaceRepo()
    storage = _MemStorage()
    set_repo_for_tests(repo)
    clear_local_parse_cache_for_tests()
    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.get_file_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.assert_thread_owner",
        AsyncMock(),
    )
    yield repo, storage
    clear_local_parse_cache_for_tests()
    set_repo_for_tests(None)


def test_storage_key_is_not_sandbox_path():
    key = workspace_storage_key("u1", "thread-a", "trees", "abc.tgz")
    assert key.startswith("workspace/u1/thread-a/")
    assert not is_shared_sandbox_path(key)
    assert STAGING_ROOT not in key
    assert not key.startswith("/workspace")


def test_sandbox_asset_rel_maps_bare_images_into_media():
    assert sandbox_asset_rel("hero.jpg") == "media/hero.jpg"
    assert sandbox_asset_rel("media/hero.jpg") == "media/hero.jpg"
    assert sandbox_asset_rel("notes.md") == "notes.md"
    assert sandbox_asset_rel("pages/01.page") == "pages/01.page"


def test_overlay_hides_pages_scripts_and_tarballs():
    assert is_overlay_visible("media/kyrie.jpg") is True
    assert is_overlay_visible("draft.pptx") is True
    assert is_overlay_visible("notes.pdf") is True
    assert is_overlay_visible("pages/01-cover.page") is False
    assert is_overlay_visible("build.py") is False
    assert is_overlay_visible("trees/x.tgz") is False
    assert is_overlay_visible("photo.png", uploaded=True) is True
    assert is_overlay_visible("secret.page", uploaded=True) is False


def test_workspace_objects_are_not_my_files_deliverables():
    assert is_deliverable("draft.pptx", WORKSPACE_SOURCE) is False
    assert is_workspace_deliverable("draft.pptx") is False
    assert is_deliverable("photo.png", "generated") is True


@pytest.mark.asyncio
async def test_text_snippets_caches_local_no_ocr_parse_by_content_hash(ws_env, monkeypatch):
    calls = []

    async def _parse(filename, data, **kwargs):
        calls.append((filename, data, kwargs))
        return {"text": "cached local text"}

    monkeypatch.setattr(
        "app.services.files.document_parse_service.parse_upload",
        _parse,
    )
    await save_asset("u1", "t1", "guide.pdf", b"same-pdf-bytes")

    first = await text_snippets("t1", "u1", "")
    second = await text_snippets("t1", "u1", "")

    assert first == second == ["guide.pdf:\ncached local text"]
    assert len(calls) == 1
    assert calls[0][2] == {
        "ocr_embedded_images": False,
        "ocr_visual": False,
    }


def test_tree_manifest_and_visible_diff():
    blob = _tar({
        "pages/01.page": b"page",
        "media/a.jpg": b"img",
        "draft.pptx": b"ppt",
        "build.py": b"print(1)",
    })
    files = parse_tree_manifest(blob)
    names = {item["path"] for item in files}
    assert "pages/01.page" in names
    assert "media/a.jpg" in names
    added, removed = diff_visible(
        [{"path": "media/old.png"}],
        files,
    )
    assert added == 2  # a.jpg + draft.pptx
    assert removed == 1


@pytest.mark.asyncio
async def test_commit_overwrites_current_tree_pointer(ws_env):
    repo, storage = ws_env
    first = await commit_tree_snapshot(
        user_id="u1", thread_id="t1", blob=_tar({"media/a.jpg": b"1"}), run_id="r1",
    )
    second = await commit_tree_snapshot(
        user_id="u1", thread_id="t1", blob=_tar({"media/a.jpg": b"1", "draft.pptx": b"2"}), run_id="r2",
    )
    assert first and second
    assert first["id"] != second["id"]
    assert await has_current_tree("t1", "u1") is True
    ws = await repo.get_workspace("t1", "u1")
    assert ws["current_tree_id"] == second["id"]
    key = second["storage_key"]
    assert key in storage.data
    assert not is_shared_sandbox_path(key)
    listing = await list_workspace("u1", "t1")
    names = {item["name"] for item in listing["files"]}
    assert "a.jpg" in names
    assert "draft.pptx" in names
    assert "01.page" not in names
    assert listing["change"]["added"] == 1
    by_name = {item["name"]: item for item in listing["files"]}
    assert by_name["a.jpg"]["added"] == 0
    assert by_name["a.jpg"]["removed"] == 0
    assert by_name["draft.pptx"]["added"] == 1
    assert by_name["draft.pptx"]["removed"] == 0


@pytest.mark.asyncio
async def test_pull_empty_tree_returns_none(ws_env):
    result = await pull_into_run(thread_id="t-missing", run_id="r1", user_id="u1")
    assert result is None


@pytest.mark.asyncio
async def test_pull_copies_into_sandbox_not_shared_dir(ws_env, monkeypatch):
    _repo, _storage = ws_env
    await commit_tree_snapshot(
        user_id="u1", thread_id="t1", blob=_tar({"media/a.jpg": b"img"}), run_id="r1",
    )
    captured: dict = {}

    class _Result:
        error = ""
        ok = True
        stderr = ""

    async def _execute(script, **kwargs):
        captured.update(kwargs)
        captured["script"] = script
        return _Result()

    monkeypatch.setattr(
        "app.services.sandbox.sandbox_executor.execute_in_sandbox",
        _execute,
    )
    meta = await pull_into_run(thread_id="t1", run_id="run-99", user_id="u1")
    assert meta and meta["restored"] is True
    files = captured.get("input_files") or {}
    assert "ppt-project.tgz" in files
    assert all(not is_shared_sandbox_path(name) for name in files)
    assert captured.get("session_key") == "run-99"
    assert STAGING_ROOT in captured.get("script", "")
    assert "bind" not in captured.get("script", "")


@pytest.mark.asyncio
async def test_compiler_inventory_lists_names_not_pixels(ws_env):
    await commit_tree_snapshot(
        user_id="u1",
        thread_id="t1",
        blob=_tar({
            "pages/01-cover.page": b"secret-pixels",
            "media/kyrie.jpg": b"img",
            "DESIGN.md": b"gold",
        }),
        run_id="r1",
    )
    inventory = await inventory_for_compiler("t1", "u1")
    assert inventory["exists"] is True
    assert inventory["pages"][0]["name"] == "01-cover.page"
    assert inventory["media"][0]["name"] == "kyrie.jpg"
    block = compiler_inventory_from_manifest(
        [{"path": "media/kyrie.jpg", "bytes": 12}], exists=True,
    )
    assert "kyrie.jpg" in str(block)
    assert "secret-pixels" not in str(block)
    remapped = compiler_inventory_from_manifest(
        [{"path": "hero.jpg", "bytes": 8}], exists=True,
    )
    assert remapped["media"][0]["name"] == "hero.jpg"
    assert remapped["root_files"] == []


@pytest.mark.asyncio
async def test_pull_maps_bare_upload_into_media(ws_env, monkeypatch):
    await save_asset("u1", "t1", "hero.jpg", b"img")
    captured: dict = {}

    class _Result:
        error = ""
        ok = True
        stderr = ""

    async def _execute(script, **kwargs):
        captured.update(kwargs)
        captured["script"] = script
        return _Result()

    monkeypatch.setattr(
        "app.services.sandbox.sandbox_executor.execute_in_sandbox",
        _execute,
    )
    meta = await pull_into_run(thread_id="t1", run_id="run-media", user_id="u1")
    assert meta and meta["restored"] is True
    blob = (captured.get("input_files") or {}).get("workspace-assets.tgz")
    assert blob
    names = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = [member.name for member in tf.getmembers() if member.isfile()]
    assert "media/hero.jpg" in names
    assert "hero.jpg" not in names
    assert f"{STAGING_ROOT}/media" in captured.get("script", "")
    assert "ppt-project.tgz" not in (captured.get("input_files") or {})


@pytest.mark.asyncio
async def test_assets_only_pull_skips_committed_tree(ws_env, monkeypatch):
    await commit_tree_snapshot(
        user_id="u1", thread_id="t1", blob=_tar({"pages/01.page": b"x"}), run_id="r1",
    )
    await save_asset("u1", "t1", "hero.jpg", b"img")
    captured: dict = {}

    class _Result:
        error = ""
        ok = True
        stderr = ""

    async def _execute(script, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(
        "app.services.sandbox.sandbox_executor.execute_in_sandbox",
        _execute,
    )
    meta = await pull_into_run(
        thread_id="t1", run_id="run-assets", user_id="u1", include_tree=False,
    )
    assert meta and meta["include_tree"] is False
    files = captured.get("input_files") or {}
    assert "ppt-project.tgz" not in files
    assert "workspace-assets.tgz" in files


@pytest.mark.asyncio
async def test_non_ppt_pull_hydrates_into_work_root(ws_env, monkeypatch):
    await commit_tree_snapshot(
        user_id="u1", thread_id="t1", blob=_tar({"notes.md": b"hi"}), run_id="r1",
    )
    captured: dict = {}

    class _Result:
        error = ""
        ok = True
        stderr = ""

    async def _execute(script, **kwargs):
        captured["script"] = script
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(
        "app.services.sandbox.sandbox_executor.execute_in_sandbox",
        _execute,
    )
    meta = await pull_into_run(
        thread_id="t1", run_id="run-work", user_id="u1", project_root=WORK_ROOT,
    )
    assert meta and meta["restored"] is True
    script = captured.get("script") or ""
    assert f"{WORK_ROOT}/media" in script
    assert "ppt-project" not in script
    assert "work-project.tgz" in (captured.get("input_files") or {})
    assert "ppt-project.tgz" not in (captured.get("input_files") or {})


class _FileRow:
    def __init__(self, filename="hero.jpg", mime="image/jpeg"):
        self.filename = filename
        self.mime = mime


@pytest.mark.asyncio
async def test_ingest_maps_composer_image_into_media(ws_env, monkeypatch):
    async def _read(_user, file_id):
        assert file_id == "fid-1"
        return _FileRow(), b"img-bytes"

    monkeypatch.setattr("app.services.files.user_file_service.read_bytes", _read)
    monkeypatch.setattr(
        "app.services.sandbox.session_pool.has_live_session",
        lambda _rid: False,
    )
    pulled = []

    async def _pull(**kwargs):
        pulled.append(kwargs)
        return {"restored": True}

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.pull_into_run",
        _pull,
    )
    copied = await ingest_user_files_into_workspace(
        user_id="u1",
        thread_id="t1",
        attachments=[{"file_id": "fid-1", "filename": "hero.jpg", "kind": "image"}],
        run_id="run-new",
    )
    assert len(copied) == 1
    assert copied[0]["logical_path"] == "media/hero.jpg"
    assert pulled == []
    listing = await list_workspace("u1", "t1")
    names = {item["name"] for item in listing["files"]}
    paths = {item["path"] for item in listing["files"]}
    assert "hero.jpg" in names
    assert "media/hero.jpg" in paths

    copied_again = await ingest_user_files_into_workspace(
        user_id="u1",
        thread_id="t1",
        attachments=[{"file_id": "fid-1", "filename": "hero.jpg", "kind": "image"}],
        run_id="run-new",
    )
    assert copied_again == []
    assert copied[0]["current_version"] == 1


@pytest.mark.asyncio
async def test_ingest_skips_thread_ref(ws_env, monkeypatch):
    called = []

    async def _read(_user, file_id):
        called.append(file_id)
        return _FileRow(), b"x"

    monkeypatch.setattr("app.services.files.user_file_service.read_bytes", _read)
    monkeypatch.setattr(
        "app.services.sandbox.session_pool.has_live_session",
        lambda _rid: False,
    )
    copied = await ingest_user_files_into_workspace(
        user_id="u1",
        thread_id="t1",
        attachments=[{
            "file_id": "ref-1",
            "filename": "old chat",
            "kind": "thread_ref",
        }],
        run_id="run-new",
    )
    assert copied == []
    assert called == []


@pytest.mark.asyncio
async def test_ingest_live_session_overlays_assets_only(ws_env, monkeypatch):
    async def _read(_user, _file_id):
        return _FileRow(), b"img-bytes"

    monkeypatch.setattr("app.services.files.user_file_service.read_bytes", _read)
    monkeypatch.setattr(
        "app.services.sandbox.session_pool.has_live_session",
        lambda rid: rid == "run-live",
    )
    pulled = []

    async def _pull(**kwargs):
        pulled.append(kwargs)
        return {"restored": True, "include_tree": kwargs.get("include_tree")}

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.pull_into_run",
        _pull,
    )
    await ingest_user_files_into_workspace(
        user_id="u1",
        thread_id="t1",
        attachments=[{"file_id": "fid-1", "filename": "hero.jpg"}],
        run_id="run-live",
    )
    assert len(pulled) == 1
    assert pulled[0]["include_tree"] is False
    assert pulled[0]["run_id"] == "run-live"


@pytest.mark.asyncio
async def test_ingest_read_failure_does_not_raise(ws_env, monkeypatch):
    async def _boom(_user, _file_id):
        raise RuntimeError("missing")

    monkeypatch.setattr("app.services.files.user_file_service.read_bytes", _boom)
    monkeypatch.setattr(
        "app.services.sandbox.session_pool.has_live_session",
        lambda _rid: True,
    )
    pulled = []

    async def _pull(**kwargs):
        pulled.append(kwargs)
        return {}

    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.pull_into_run",
        _pull,
    )
    copied = await ingest_user_files_into_workspace(
        user_id="u1",
        thread_id="t1",
        attachments=[{"file_id": "gone", "filename": "x.jpg"}],
        run_id="run-live",
    )
    assert copied == []
    assert pulled == []


def test_orchestrator_ingests_composer_files_before_hydrate():
    from pathlib import Path

    text = Path(__file__).resolve().parents[1].joinpath(
        "app/services/agent_harness/orchestrator.py",
    ).read_text(encoding="utf-8")
    ingest_at = text.index("ingest_user_files_into_workspace")
    hydrate_at = text.index("_ckpt_meta, _ = await hydrate_ppt_staging")
    assert ingest_at < hydrate_at
