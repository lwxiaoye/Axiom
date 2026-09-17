"""Workspace File Service: Source of Truth for a thread's project tree.

Workspace objects live under `workspace/{user_id}/{thread_id}/` in the existing
file storage (MinIO or local). The sandbox only ever sees a working copy via
Pull/Commit. This module must not bind-mount storage keys into `/workspace`.
"""
from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import tarfile
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.core.database import async_session
from app.core.runtime_db import runtime_session
from app.models import ChatThread
from app.services.files.deliverable import WORKSPACE_SOURCE, is_deliverable
from app.services.files.storage.factory import get_file_storage
from app.services.skills.ppt_agentic_adapter import STAGING_ROOT

WORK_ROOT = "/workspace/tmp"

logger = logging.getLogger(__name__)

KIND_ASSET = "asset"
KIND_TREE = "tree_snapshot"
TREE_KEEP = 8
TEXT_SNIPPET_EXTS = {".md", ".txt", ".csv", ".markdown"}
PARSE_SNIPPET_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
LOCAL_PARSE_POLICY = "workspace-local-no-ocr-v1"
LOCAL_PARSE_CACHE_MAX_ENTRIES = 16
OVERLAY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
}
IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
)
INTERNAL_EXTS = {
    ".page", ".py", ".pyc", ".pyo", ".sh", ".bash", ".tgz", ".tar", ".gz",
    ".wasm", ".json",
}
_repo_override: Any = None
_local_parse_cache: OrderedDict[str, str] = OrderedDict()


class WorkspaceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def set_repo_for_tests(repo: Any) -> None:
    global _repo_override
    _repo_override = repo


def clear_local_parse_cache_for_tests() -> None:
    _local_parse_cache.clear()


def _local_parse_cache_key(path: str, data: bytes) -> str:
    suffix = "." + str(path or "").lower().rsplit(".", 1)[-1] if "." in str(path or "") else ""
    content_hash = hashlib.sha256(data).hexdigest()
    return f"{LOCAL_PARSE_POLICY}:{suffix}:{content_hash}"


def _remember_local_parse(key: str, text: str) -> None:
    _local_parse_cache[key] = text
    _local_parse_cache.move_to_end(key)
    while len(_local_parse_cache) > LOCAL_PARSE_CACHE_MAX_ENTRIES:
        _local_parse_cache.popitem(last=False)


def workspace_prefix(user_id: str, thread_id: str) -> str:
    return f"workspace/{_token(user_id)}/{_token(thread_id)}"


def workspace_storage_key(user_id: str, thread_id: str, *parts: str) -> str:
    tail = "/".join(_token(part) for part in parts if str(part or "").strip())
    key = f"{workspace_prefix(user_id, thread_id)}/{tail}".replace("//", "/")
    return key.strip("/")


def is_shared_sandbox_path(storage_key: str) -> bool:
    """True if a storage key would collide with the sandbox working copy."""
    key = str(storage_key or "").replace("\\", "/")
    return key.startswith("/workspace") or "tmp/ppt-project" in key


def safe_logical_path(raw: str, *, default: str = "untitled") -> str:
    value = str(raw or "").replace("\\", "/").lstrip("/")
    parts = [part for part in value.split("/") if part and part not in (".", "..")]
    path = "/".join(parts)[:240]
    return path or default


def sandbox_asset_rel(path: str) -> str:
    """Where a workspace asset should live in the PPT project working copy.

    Bare image uploads (`hero.jpg`) become `media/hero.jpg` so SKILL.md and
    `fetch_ppt_asset` agree. Paths already under `media/` and non-images stay.
    """
    rel = safe_logical_path(path, default="")
    if not rel:
        return ""
    lower = rel.lower()
    name = rel.rsplit("/", 1)[-1]
    is_image = any(lower.endswith(ext) for ext in IMAGE_EXTS)
    if is_image and not lower.startswith("media/") and "/" not in rel:
        return f"media/{name}"
    return rel


def is_internal_workspace_path(path: str) -> bool:
    norm = safe_logical_path(path, default="")
    if not norm:
        return True
    lower = norm.lower()
    name = lower.rsplit("/", 1)[-1]
    if any(lower.endswith(ext) for ext in INTERNAL_EXTS):
        return True
    if lower.startswith("pages/") or "/pages/" in f"/{lower}":
        return True
    if name in {"design.md", "spec.json", "deck.json", "manifest.json"}:
        return True
    return False


def is_overlay_visible(path: str, *, uploaded: bool = False) -> bool:
    lower = safe_logical_path(path).lower()
    if lower.endswith((".page", ".py", ".pyc", ".sh", ".bash", ".tgz", ".tar", ".gz", ".wasm")):
        return False
    if lower.startswith("pages/") or "/pages/" in f"/{lower}":
        return False
    if uploaded:
        return bool(lower)
    if is_internal_workspace_path(path):
        return False
    return any(lower.endswith(ext) for ext in OVERLAY_EXTS)


def is_workspace_deliverable(filename: str) -> bool:
    """Workspace drafts must never appear as 「我的文件」 deliverables."""
    return is_deliverable(filename, WORKSPACE_SOURCE)


def parse_tree_manifest(blob: bytes) -> list[dict[str, Any]]:
    """File-level inventory from a whole-tree snapshot. No binary diffs."""
    files: list[dict[str, Any]] = []
    if not blob:
        return files
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                path = safe_logical_path(member.name, default="")
                if not path:
                    continue
                files.append({
                    "path": path,
                    "name": path.rsplit("/", 1)[-1],
                    "bytes": int(member.size or 0),
                })
    except tarfile.TarError:
        return []
    return files


def visible_manifest(files: list[dict[str, Any]], *, uploaded: bool = False) -> list[dict[str, Any]]:
    out = []
    for item in files or []:
        path = str(item.get("path") or item.get("name") or "")
        if is_overlay_visible(path, uploaded=uploaded):
            out.append(item)
    return out


def diff_visible(old_files: list[dict[str, Any]], new_files: list[dict[str, Any]]) -> tuple[int, int]:
    old_paths = {
        str(item.get("path") or item.get("name") or "")
        for item in visible_manifest(old_files)
    }
    new_paths = {
        str(item.get("path") or item.get("name") or "")
        for item in visible_manifest(new_files)
    }
    old_paths.discard("")
    new_paths.discard("")
    return len(new_paths - old_paths), len(old_paths - new_paths)


def compiler_inventory_from_manifest(
    files: list[dict[str, Any]],
    *,
    exists: bool,
) -> dict[str, Any]:
    pages = []
    media = []
    root_files = []
    seen: set[str] = set()
    for item in files or []:
        path = sandbox_asset_rel(str(item.get("path") or "")) or str(item.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        name = path.rsplit("/", 1)[-1]
        row = {"name": name, "bytes": int(item.get("bytes") or 0)}
        lower = path.lower()
        if lower.startswith("pages/") or lower.endswith(".page"):
            pages.append(row)
        elif lower.startswith("media/") or any(lower.endswith(ext) for ext in IMAGE_EXTS):
            media.append(row)
        else:
            root_files.append(row)
    return {
        "exists": bool(exists),
        "pages": pages,
        "media": media,
        "root_files": root_files,
        "checkpoint_present": bool(exists),
    }


def _token(value: str) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    return cleaned[:80] or "x"


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _guess_mime(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


class InMemoryWorkspaceRepo:
    """Test double. Production uses the Runtime PG tables via create_all."""

    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}

    async def get_workspace(self, thread_id: str, user_id: str) -> Optional[dict[str, Any]]:
        row = self.workspaces.get(thread_id)
        if not row or row.get("user_id") != user_id:
            return None
        return dict(row)

    async def upsert_workspace(self, thread_id: str, user_id: str, current_tree_id: Optional[str]) -> dict[str, Any]:
        now = _now()
        row = self.workspaces.get(thread_id) or {
            "thread_id": thread_id,
            "user_id": user_id,
            "created_at": now,
        }
        if row.get("user_id") != user_id:
            raise WorkspaceError("无权访问此工作区", 403)
        row["current_tree_id"] = current_tree_id
        row["updated_at"] = now
        self.workspaces[thread_id] = row
        return dict(row)

    async def insert_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        row.setdefault("id", _new_id())
        row.setdefault("created_at", _now())
        row["updated_at"] = _now()
        self.objects[row["id"]] = row
        return dict(row)

    async def update_object(self, object_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
        row = self.objects.get(object_id)
        if not row:
            return None
        row.update(patch)
        row["updated_at"] = _now()
        return dict(row)

    async def get_object(self, object_id: str, user_id: str) -> Optional[dict[str, Any]]:
        row = self.objects.get(object_id)
        if not row or row.get("user_id") != user_id:
            return None
        return dict(row)

    async def list_objects(self, thread_id: str, user_id: str, kind: str = "") -> list[dict[str, Any]]:
        rows = [
            dict(item) for item in self.objects.values()
            if item.get("thread_id") == thread_id and item.get("user_id") == user_id
            and (not kind or item.get("kind") == kind)
        ]
        rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return rows

    async def delete_object(self, object_id: str) -> None:
        self.objects.pop(object_id, None)
        for vid in [key for key, row in self.versions.items() if row.get("object_id") == object_id]:
            self.versions.pop(vid, None)

    async def delete_thread(self, thread_id: str, user_id: str) -> list[str]:
        keys = []
        row = self.workspaces.get(thread_id)
        if row and row.get("user_id") != user_id:
            raise WorkspaceError("无权访问此工作区", 403)
        self.workspaces.pop(thread_id, None)
        for oid in [key for key, item in self.objects.items() if item.get("thread_id") == thread_id]:
            keys.append(str(self.objects[oid].get("storage_key") or ""))
            await self.delete_object(oid)
        return [key for key in keys if key]

    async def insert_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        row.setdefault("id", _new_id())
        row.setdefault("created_at", _now())
        self.versions[row["id"]] = row
        return dict(row)

    async def list_versions(self, object_id: str, user_id: str) -> list[dict[str, Any]]:
        rows = [
            dict(item) for item in self.versions.values()
            if item.get("object_id") == object_id and item.get("user_id") == user_id
        ]
        rows.sort(key=lambda item: int(item.get("version_no") or 0), reverse=True)
        return rows


class PgWorkspaceRepo:
    async def get_workspace(self, thread_id: str, user_id: str) -> Optional[dict[str, Any]]:
        from app.runtime_models import AgentThreadWorkspace

        factory = runtime_session()
        if factory is None:
            return None
        async with factory() as session:
            row = await session.get(AgentThreadWorkspace, thread_id)
            if row is None or str(row.user_id) != user_id:
                return None
            return _workspace_row(row)

    async def upsert_workspace(self, thread_id: str, user_id: str, current_tree_id: Optional[str]) -> dict[str, Any]:
        from app.runtime_models import AgentThreadWorkspace

        factory = runtime_session()
        if factory is None:
            raise WorkspaceError("工作区存储未启用", 503)
        async with factory() as session:
            row = await session.get(AgentThreadWorkspace, thread_id)
            if row is None:
                row = AgentThreadWorkspace(
                    thread_id=thread_id, user_id=user_id, current_tree_id=current_tree_id,
                )
                session.add(row)
            else:
                if str(row.user_id) != user_id:
                    raise WorkspaceError("无权访问此工作区", 403)
                row.current_tree_id = current_tree_id
            await session.commit()
            await session.refresh(row)
            return _workspace_row(row)

    async def insert_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.runtime_models import AgentWorkspaceObject

        factory = runtime_session()
        if factory is None:
            raise WorkspaceError("工作区存储未启用", 503)
        async with factory() as session:
            row = AgentWorkspaceObject(**payload)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _object_row(row)

    async def update_object(self, object_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
        from app.runtime_models import AgentWorkspaceObject

        factory = runtime_session()
        if factory is None:
            return None
        async with factory() as session:
            row = await session.get(AgentWorkspaceObject, object_id)
            if row is None:
                return None
            for key, value in patch.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _object_row(row)

    async def get_object(self, object_id: str, user_id: str) -> Optional[dict[str, Any]]:
        from app.runtime_models import AgentWorkspaceObject

        factory = runtime_session()
        if factory is None:
            return None
        async with factory() as session:
            row = await session.get(AgentWorkspaceObject, object_id)
            if row is None or str(row.user_id) != user_id:
                return None
            return _object_row(row)

    async def list_objects(self, thread_id: str, user_id: str, kind: str = "") -> list[dict[str, Any]]:
        from app.runtime_models import AgentWorkspaceObject

        factory = runtime_session()
        if factory is None:
            return []
        async with factory() as session:
            stmt = select(AgentWorkspaceObject).where(
                AgentWorkspaceObject.thread_id == thread_id,
                AgentWorkspaceObject.user_id == user_id,
            )
            if kind:
                stmt = stmt.where(AgentWorkspaceObject.kind == kind)
            stmt = stmt.order_by(AgentWorkspaceObject.updated_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            return [_object_row(row) for row in rows]

    async def delete_object(self, object_id: str) -> None:
        from app.runtime_models import AgentWorkspaceObject, AgentWorkspaceObjectVersion

        factory = runtime_session()
        if factory is None:
            return
        async with factory() as session:
            versions = (
                await session.execute(
                    select(AgentWorkspaceObjectVersion).where(
                        AgentWorkspaceObjectVersion.object_id == object_id,
                    )
                )
            ).scalars().all()
            for item in versions:
                await session.delete(item)
            row = await session.get(AgentWorkspaceObject, object_id)
            if row is not None:
                await session.delete(row)
            await session.commit()

    async def delete_thread(self, thread_id: str, user_id: str) -> list[str]:
        from app.runtime_models import AgentThreadWorkspace, AgentWorkspaceObject

        factory = runtime_session()
        if factory is None:
            return []
        keys: list[str] = []
        async with factory() as session:
            ws = await session.get(AgentThreadWorkspace, thread_id)
            if ws is not None:
                if str(ws.user_id) != user_id:
                    raise WorkspaceError("无权访问此工作区", 403)
                await session.delete(ws)
            rows = (
                await session.execute(
                    select(AgentWorkspaceObject).where(
                        AgentWorkspaceObject.thread_id == thread_id,
                        AgentWorkspaceObject.user_id == user_id,
                    )
                )
            ).scalars().all()
            ids = [str(row.id) for row in rows]
            keys = [str(row.storage_key or "") for row in rows if row.storage_key]
            await session.commit()
        for oid in ids:
            await self.delete_object(oid)
        return [key for key in keys if key]

    async def insert_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.runtime_models import AgentWorkspaceObjectVersion

        factory = runtime_session()
        if factory is None:
            raise WorkspaceError("工作区存储未启用", 503)
        async with factory() as session:
            row = AgentWorkspaceObjectVersion(**payload)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return {
                "id": row.id,
                "object_id": row.object_id,
                "user_id": row.user_id,
                "version_no": row.version_no,
                "storage_key": row.storage_key,
                "size_bytes": int(row.size_bytes or 0),
                "sha256": row.sha256 or "",
                "created_at": row.created_at,
            }

    async def list_versions(self, object_id: str, user_id: str) -> list[dict[str, Any]]:
        from app.runtime_models import AgentWorkspaceObjectVersion

        factory = runtime_session()
        if factory is None:
            return []
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentWorkspaceObjectVersion)
                    .where(
                        AgentWorkspaceObjectVersion.object_id == object_id,
                        AgentWorkspaceObjectVersion.user_id == user_id,
                    )
                    .order_by(AgentWorkspaceObjectVersion.version_no.desc())
                )
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "object_id": row.object_id,
                    "version_no": row.version_no,
                    "size_bytes": int(row.size_bytes or 0),
                    "sha256": row.sha256 or "",
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
                for row in rows
            ]


def _workspace_row(row: Any) -> dict[str, Any]:
    return {
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "current_tree_id": row.current_tree_id,
        "updated_at": row.updated_at,
    }


def _object_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "kind": row.kind,
        "logical_path": row.logical_path or "",
        "storage_key": row.storage_key,
        "mime": row.mime or "",
        "size_bytes": int(row.size_bytes or 0),
        "sha256": row.sha256 or "",
        "run_id": row.run_id,
        "parent_id": row.parent_id,
        "added_count": int(row.added_count or 0),
        "removed_count": int(row.removed_count or 0),
        "current_version": int(row.current_version or 1),
        "manifest_json": row.manifest_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _repo() -> Any:
    if _repo_override is not None:
        return _repo_override
    return PgWorkspaceRepo()


async def assert_thread_owner(user_id: str, thread_id: str) -> None:
    if not user_id or not thread_id:
        raise WorkspaceError("缺少会话", 400)
    async with async_session() as session:
        thread = await session.get(ChatThread, thread_id)
        if thread is None:
            raise WorkspaceError("会话不存在", 404)
        if str(thread.user_id) != user_id:
            raise WorkspaceError("无权访问此工作区", 403)


async def has_current_tree(thread_id: str, user_id: str) -> bool:
    if not thread_id or not user_id:
        return False
    ws = await _repo().get_workspace(thread_id, user_id)
    return bool(ws and ws.get("current_tree_id"))


async def commit_tree_snapshot(
    *,
    user_id: str,
    thread_id: str,
    blob: bytes,
    run_id: str = "",
    media_count: int = 0,
    pack: str = "full",
) -> Optional[dict[str, Any]]:
    """Write a whole-tree snapshot as the thread's current Source of Truth."""
    if not user_id or not thread_id or not blob:
        return None
    repo = _repo()
    object_id = _new_id()
    key = workspace_storage_key(user_id, thread_id, "trees", f"{object_id}.tgz")
    if is_shared_sandbox_path(key):
        raise WorkspaceError("工作区存储路径非法", 500)
    await get_file_storage().write_bytes(key, blob, content_type="application/gzip")
    files = parse_tree_manifest(blob)
    previous = await repo.get_workspace(thread_id, user_id)
    parent_id = str((previous or {}).get("current_tree_id") or "") or None
    old_files: list[dict[str, Any]] = []
    if parent_id:
        parent = await repo.get_object(parent_id, user_id)
        if parent and isinstance(parent.get("manifest_json"), list):
            old_files = list(parent.get("manifest_json") or [])
    added, removed = diff_visible(old_files, files)
    row = await repo.insert_object({
        "id": object_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "kind": KIND_TREE,
        "logical_path": f"trees/{object_id}.tgz",
        "storage_key": key,
        "mime": "application/gzip",
        "size_bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "run_id": run_id or None,
        "parent_id": parent_id,
        "added_count": added,
        "removed_count": removed,
        "current_version": 1,
        "manifest_json": files,
    })
    await repo.upsert_workspace(thread_id, user_id, object_id)
    await _trim_old_trees(repo, thread_id, user_id)
    logger.info(
        "workspace tree committed thread=%s run=%s bytes=%s added=%s removed=%s media=%s pack=%s",
        thread_id, run_id, len(blob), added, removed, media_count, pack,
    )
    return {
        **row,
        "workspace_object_id": object_id,
        "media_count": media_count,
        "pack": pack,
        "source": WORKSPACE_SOURCE,
    }


async def pull_into_run(
    *,
    thread_id: str,
    run_id: str,
    user_id: str,
    include_tree: bool = True,
    project_root: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Copy the current tree (and pending assets) into this Run's sandbox working copy.

    ``include_tree=False`` overlays user-uploaded assets only. Use that after a
    staging tar restore so a newer working copy is not overwritten by an older
    committed tree.
    """
    if not thread_id or not run_id or not user_id:
        return None
    repo = _repo()
    ws = await repo.get_workspace(thread_id, user_id)
    tree_id = str((ws or {}).get("current_tree_id") or "")
    blob = b""
    tree_row = None
    if include_tree and tree_id:
        tree_row = await repo.get_object(tree_id, user_id)
        key = str((tree_row or {}).get("storage_key") or "")
        if key:
            if is_shared_sandbox_path(key):
                logger.warning("workspace pull refused shared path thread=%s key=%s", thread_id, key)
                return None
            try:
                blob = await get_file_storage().read_bytes(key)
            except Exception:  # noqa: BLE001
                logger.warning("workspace pull read failed thread=%s", thread_id, exc_info=True)
                blob = b""
    assets = await repo.list_objects(thread_id, user_id, KIND_ASSET)
    asset_files = await _read_assets(assets)
    if not blob and not asset_files:
        return None
    restored = await _hydrate_sandbox(
        run_id, blob, asset_files, project_root=project_root or STAGING_ROOT,
    )
    if not restored:
        return None
    media_count = 0
    manifest = list((tree_row or {}).get("manifest_json") or []) if tree_row else []
    for item in manifest:
        path = sandbox_asset_rel(str(item.get("path") or "")).lower()
        if path.startswith("media/"):
            media_count += 1
    for path in asset_files:
        if str(path).lower().startswith("media/"):
            media_count += 1
    return {
        "workspace_object_id": tree_id,
        "restored": True,
        "bytes": len(blob),
        "media_count": media_count,
        "source": WORKSPACE_SOURCE,
        "file_id": "",
        "include_tree": include_tree,
    }


async def inventory_for_compiler(thread_id: str, user_id: str) -> dict[str, Any]:
    if not thread_id or not user_id:
        return compiler_inventory_from_manifest([], exists=False)
    repo = _repo()
    ws = await repo.get_workspace(thread_id, user_id)
    files: list[dict[str, Any]] = []
    exists = False
    if ws and ws.get("current_tree_id"):
        tree = await repo.get_object(str(ws["current_tree_id"]), user_id)
        if tree and isinstance(tree.get("manifest_json"), list):
            files = list(tree.get("manifest_json") or [])
            exists = True
    for asset in await repo.list_objects(thread_id, user_id, KIND_ASSET):
        logical = str(asset.get("logical_path") or asset.get("id") or "")
        path = sandbox_asset_rel(logical) or logical
        files.append({
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "bytes": int(asset.get("size_bytes") or 0),
        })
        exists = True
    inventory = compiler_inventory_from_manifest(files, exists=exists)
    inventory["checkpoint_present"] = exists
    return inventory


async def list_workspace(
    user_id: str,
    thread_id: str,
    *,
    query: str = "",
    kind: str = "",
) -> dict[str, Any]:
    await assert_thread_owner(user_id, thread_id)
    repo = _repo()
    ws = await repo.get_workspace(thread_id, user_id)
    files: list[dict[str, Any]] = []
    change = {"added": 0, "removed": 0}
    tree_id = str((ws or {}).get("current_tree_id") or "")
    tree_updated = ""
    previous_index: dict[str, dict[str, Any]] = {}
    if tree_id:
        tree = await repo.get_object(tree_id, user_id)
        if tree:
            change = {
                "added": int(tree.get("added_count") or 0),
                "removed": int(tree.get("removed_count") or 0),
            }
            tree_updated = _iso(tree.get("updated_at") or tree.get("created_at"))
            parent_id = str(tree.get("parent_id") or "")
            if parent_id:
                parent = await repo.get_object(parent_id, user_id)
                if parent:
                    previous_index = _path_index(list(parent.get("manifest_json") or []))
            for item in visible_manifest(list(tree.get("manifest_json") or [])):
                added, removed = _overlay_delta(item, previous_index, uploaded=False)
                files.append(_overlay_file(
                    item,
                    file_id=f"tree:{tree_id}:{item.get('path')}",
                    kind="tree_file",
                    updated_at=tree_updated,
                    uploaded=False,
                    added=added,
                    removed=removed,
                ))
    for asset in await repo.list_objects(thread_id, user_id, KIND_ASSET):
        path = str(asset.get("logical_path") or "")
        if not is_overlay_visible(path, uploaded=True):
            continue
        payload = {
            "path": path,
            "name": path.rsplit("/", 1)[-1] or path,
            "bytes": int(asset.get("size_bytes") or 0),
        }
        added, removed = _overlay_delta(payload, previous_index, uploaded=True)
        files.append(_overlay_file(
            payload,
            file_id=str(asset.get("id") or ""),
            kind="asset",
            updated_at=_iso(asset.get("updated_at") or asset.get("created_at")),
            mime=str(asset.get("mime") or ""),
            uploaded=True,
            added=added,
            removed=removed,
        ))
    needle = str(query or "").strip().lower()
    kind_filter = str(kind or "").strip().lower()
    if needle:
        files = [item for item in files if needle in str(item.get("name") or "").lower()]
    if kind_filter and kind_filter != "all":
        files = [item for item in files if _kind_match(item, kind_filter)]
    files.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in files:
        key = str(item.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    files = deduped
    return {
        "files": files,
        "change": change,
        "empty": not files,
        "current_tree_id": tree_id,
    }


async def save_asset(
    user_id: str,
    thread_id: str,
    filename: str,
    data: bytes,
    *,
    mime: str = "",
) -> dict[str, Any]:
    await assert_thread_owner(user_id, thread_id)
    path = sandbox_asset_rel(filename) or safe_logical_path(filename)
    repo = _repo()
    existing = None
    for item in await repo.list_objects(thread_id, user_id, KIND_ASSET):
        if str(item.get("logical_path") or "") == path:
            existing = item
            break
    object_id = str((existing or {}).get("id") or _new_id())
    version_no = int((existing or {}).get("current_version") or 0) + 1
    key = workspace_storage_key(user_id, thread_id, "assets", object_id, f"v{version_no}")
    if is_shared_sandbox_path(key):
        raise WorkspaceError("工作区存储路径非法", 500)
    await get_file_storage().write_bytes(key, data, content_type=mime or _guess_mime(path))
    digest = hashlib.sha256(data).hexdigest()
    if existing:
        await repo.insert_version({
            "id": _new_id(),
            "object_id": object_id,
            "user_id": user_id,
            "version_no": int(existing.get("current_version") or 1),
            "storage_key": existing.get("storage_key"),
            "size_bytes": int(existing.get("size_bytes") or 0),
            "sha256": existing.get("sha256") or "",
        })
        row = await repo.update_object(object_id, {
            "storage_key": key,
            "size_bytes": len(data),
            "sha256": digest,
            "mime": mime or existing.get("mime") or _guess_mime(path),
            "current_version": version_no,
        })
        return row or existing
    await repo.upsert_workspace(thread_id, user_id, (await repo.get_workspace(thread_id, user_id) or {}).get("current_tree_id"))
    return await repo.insert_object({
        "id": object_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "kind": KIND_ASSET,
        "logical_path": path,
        "storage_key": key,
        "mime": mime or _guess_mime(path),
        "size_bytes": len(data),
        "sha256": digest,
        "current_version": 1,
        "manifest_json": None,
    })


async def create_empty_asset(user_id: str, thread_id: str, filename: str) -> dict[str, Any]:
    return await save_asset(user_id, thread_id, filename, b"", mime=_guess_mime(filename))


def _attachment_scalar(att: Any, key: str, default: str = "") -> str:
    if isinstance(att, dict):
        return str(att.get(key) or default)
    return str(getattr(att, key, default) or default)


async def ingest_user_files_into_workspace(
    *,
    user_id: str,
    thread_id: str,
    attachments: Optional[list[Any]] = None,
    run_id: str = "",
) -> list[dict[str, Any]]:
    """Copy this-turn composer / 「我的文件」 selections into the session workspace.

    `/chat/upload` stores bytes with source=workspace (hidden from 我的文件) plus
    a file_id for parse/ingest. The session workspace is the working folder; without
    this copy, Pull never sees the photos the user just dropped into the input box.

    If a sandbox for this Run is already live, overlay assets only (`include_tree=False`).
    Otherwise only `save_asset`; `hydrate_ppt_staging` Pulls afterwards and must still
    be able to restore a staging tar on a fresh container.
    """
    if not user_id or not thread_id:
        return []
    from app.services.files import user_file_service
    from app.services.files.thread_attachment_service import THREAD_REF_KIND
    from app.services.sandbox import session_pool

    copied: list[dict[str, Any]] = []
    seen: set[str] = set()
    for att in attachments or []:
        if _attachment_scalar(att, "kind") in {
            THREAD_REF_KIND, "skill", "knowledge", "subagent", "web",
        }:
            continue
        file_id = _attachment_scalar(att, "file_id").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        filename = _attachment_scalar(att, "filename") or "file"
        try:
            row, data = await user_file_service.read_bytes(user_id, file_id)
            if not data:
                continue
            mime = _attachment_scalar(att, "mime") or str(getattr(row, "mime", "") or "")
            name = filename or str(getattr(row, "filename", "") or "file")
            path = sandbox_asset_rel(name) or safe_logical_path(name)
            digest = hashlib.sha256(data).hexdigest()
            already = False
            for item in await _repo().list_objects(thread_id, user_id, KIND_ASSET):
                if (
                    str(item.get("logical_path") or "") == path
                    and str(item.get("sha256") or "") == digest
                ):
                    already = True
                    break
            if already:
                continue
            saved = await save_asset(user_id, thread_id, name, data, mime=mime)
            copied.append(saved)
        except Exception:  # noqa: BLE001
            logger.warning(
                "workspace ingest skipped file_id=%s thread=%s", file_id, thread_id,
                exc_info=True,
            )
    if copied and run_id and session_pool.has_live_session(run_id):
        try:
            await pull_into_run(
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                include_tree=False,
            )
        except Exception:  # noqa: BLE001
            logger.warning("workspace live overlay after ingest failed run=%s", run_id, exc_info=True)
    return copied


async def delete_asset(user_id: str, thread_id: str, object_id: str) -> None:
    await assert_thread_owner(user_id, thread_id)
    repo = _repo()
    row = await repo.get_object(object_id, user_id)
    if row is None or str(row.get("thread_id") or "") != thread_id:
        raise WorkspaceError("文件不存在", 404)
    if str(row.get("kind") or "") != KIND_ASSET:
        raise WorkspaceError("只能删除工作区上传文件", 400)
    key = str(row.get("storage_key") or "")
    if key:
        try:
            await get_file_storage().delete(key)
        except Exception:  # noqa: BLE001
            logger.debug("workspace asset delete storage skipped", exc_info=True)
    for version in await repo.list_versions(object_id, user_id):
        pass
    await repo.delete_object(object_id)


async def list_versions(user_id: str, thread_id: str, object_id: str) -> list[dict[str, Any]]:
    await assert_thread_owner(user_id, thread_id)
    row = await _repo().get_object(object_id, user_id)
    if row is None or str(row.get("thread_id") or "") != thread_id:
        raise WorkspaceError("文件不存在", 404)
    if str(row.get("kind") or "") == KIND_TREE:
        trees = await _repo().list_objects(thread_id, user_id, KIND_TREE)
        return [
            {
                "id": item.get("id"),
                "version_no": idx,
                "size_bytes": int(item.get("size_bytes") or 0),
                "created_at": _iso(item.get("created_at")),
                "current": item.get("id") == (await _repo().get_workspace(thread_id, user_id) or {}).get("current_tree_id"),
            }
            for idx, item in enumerate(reversed(trees), start=1)
        ]
    current = {
        "id": row.get("id"),
        "version_no": int(row.get("current_version") or 1),
        "size_bytes": int(row.get("size_bytes") or 0),
        "created_at": _iso(row.get("updated_at") or row.get("created_at")),
        "current": True,
    }
    history = await _repo().list_versions(object_id, user_id)
    return [current, *history]


async def read_workspace_bytes(user_id: str, thread_id: str, object_id: str) -> tuple[str, bytes, str]:
    await assert_thread_owner(user_id, thread_id)
    if object_id.startswith("tree:"):
        _prefix, tree_id, path = object_id.split(":", 2)
        tree = await _repo().get_object(tree_id, user_id)
        if not tree or str(tree.get("thread_id") or "") != thread_id:
            raise WorkspaceError("文件不存在", 404)
        blob = await get_file_storage().read_bytes(str(tree.get("storage_key") or ""))
        data = _extract_member(blob, path)
        return path.rsplit("/", 1)[-1], data, _guess_mime(path)
    row = await _repo().get_object(object_id, user_id)
    if row is None or str(row.get("thread_id") or "") != thread_id:
        raise WorkspaceError("文件不存在", 404)
    data = await get_file_storage().read_bytes(str(row.get("storage_key") or ""))
    name = str(row.get("logical_path") or "file").rsplit("/", 1)[-1]
    return name, data, str(row.get("mime") or _guess_mime(name))


async def clear_workspace(user_id: str, thread_id: str, *, confirm: bool = False) -> None:
    await assert_thread_owner(user_id, thread_id)
    if not confirm:
        raise WorkspaceError("清空工作区需要二次确认", 400)
    keys = await _repo().delete_thread(thread_id, user_id)
    storage = get_file_storage()
    try:
        await storage.delete_prefix(workspace_prefix(user_id, thread_id))
    except Exception:  # noqa: BLE001
        for key in keys:
            try:
                await storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.debug("workspace clear key skipped", exc_info=True)


async def delete_for_thread(thread_id: str, user_id: str) -> None:
    """Best-effort cleanup when a chat thread is deleted."""
    try:
        keys = await _repo().delete_thread(thread_id, user_id)
        storage = get_file_storage()
        try:
            await storage.delete_prefix(workspace_prefix(user_id, thread_id))
        except Exception:  # noqa: BLE001
            for key in keys:
                await storage.delete(key)
    except WorkspaceError:
        return
    except Exception:  # noqa: BLE001
        logger.debug("workspace thread cleanup skipped thread=%s", thread_id, exc_info=True)


async def text_snippets(
    thread_id: str,
    user_id: str,
    query: str,
    *,
    run_id: str = "",
) -> list[str]:
    """On-demand text retrieval for compiler; never dumps pixels."""
    if not thread_id or not user_id:
        return []
    snippets: list[str] = []
    for asset in await _repo().list_objects(thread_id, user_id, KIND_ASSET):
        path = str(asset.get("logical_path") or "")
        lower = path.lower()
        key = str(asset.get("storage_key") or "")
        try:
            data = await get_file_storage().read_bytes(key)
        except Exception:  # noqa: BLE001
            continue
        text = ""
        if any(lower.endswith(ext) for ext in TEXT_SNIPPET_EXTS):
            text = data.decode("utf-8", "ignore")
        elif any(lower.endswith(ext) for ext in PARSE_SNIPPET_EXTS):
            try:
                cache_key = _local_parse_cache_key(path, data)
                if cache_key in _local_parse_cache:
                    text = _local_parse_cache[cache_key]
                    _local_parse_cache.move_to_end(cache_key)
                else:
                    from app.services.files.document_parse_service import parse_upload
                    parsed = await parse_upload(
                        path.rsplit("/", 1)[-1],
                        data,
                        ocr_embedded_images=False,
                        ocr_visual=False,
                    )
                    text = str((parsed or {}).get("text") or "")
                    _remember_local_parse(cache_key, text)
            except Exception:  # noqa: BLE001
                continue
        else:
            continue
        if not str(text).strip():
            continue
        if query:
            from app.services.files.session_file_service import retrieve_relevant
            text = await retrieve_relevant(
                query,
                text,
                audit_context={
                    "run_id": run_id,
                    "thread_id": thread_id,
                },
            )
        snippets.append(f"{path}:\n{text[:800]}")
        if len(snippets) >= 3:
            break
    return snippets


async def _trim_old_trees(repo: Any, thread_id: str, user_id: str) -> None:
    trees = await repo.list_objects(thread_id, user_id, KIND_TREE)
    if len(trees) <= TREE_KEEP:
        return
    storage = get_file_storage()
    for stale in trees[TREE_KEEP:]:
        key = str(stale.get("storage_key") or "")
        if key:
            try:
                await storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.debug("workspace trim skip", exc_info=True)
        await repo.delete_object(str(stale.get("id") or ""))


async def _read_assets(assets: list[dict[str, Any]]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    storage = get_file_storage()
    for asset in assets or []:
        path = sandbox_asset_rel(str(asset.get("logical_path") or ""))
        key = str(asset.get("storage_key") or "")
        if not path or not key or is_shared_sandbox_path(key):
            continue
        try:
            out[path] = await storage.read_bytes(key)
        except Exception:  # noqa: BLE001
            logger.debug("workspace asset read skipped path=%s", path, exc_info=True)
    return out


async def asset_bytes_for_publish(thread_id: str, user_id: str) -> dict[str, bytes]:
    """Return explicit conversation assets for deterministic artifact publishing.

    This is intentionally narrower than the whole workspace tree: composer uploads
    and explicitly selected user files are stored as ``KIND_ASSET`` objects, while
    generated project internals must not become ambient HTML dependencies.
    """
    if not thread_id or not user_id:
        return {}
    assets = await _repo().list_objects(thread_id, user_id, KIND_ASSET)
    return await _read_assets(assets)


def _pack_assets_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _hydrate_sandbox(
    run_id: str,
    tree_blob: bytes,
    assets: dict[str, bytes],
    *,
    project_root: str = STAGING_ROOT,
) -> bool:
    from app.services.sandbox import sandbox_executor

    root = str(project_root or STAGING_ROOT).rstrip("/") or STAGING_ROOT
    tree_name = "ppt-project.tgz" if root == STAGING_ROOT else "work-project.tgz"
    input_files: dict[str, bytes] = {}
    if tree_blob:
        input_files[tree_name] = tree_blob
    if assets:
        input_files["workspace-assets.tgz"] = _pack_assets_tar(assets)
    if not input_files:
        return False
    script = f"""
set -eu
mkdir -p {root}/media
if [ -f /workspace/inputs/{tree_name} ]; then
  tar -xzf /workspace/inputs/{tree_name} -C {root}
fi
if [ -f /workspace/inputs/workspace-assets.tgz ]; then
  tar -xzf /workspace/inputs/workspace-assets.tgz -C {root}
fi
echo WORKSPACE_PULLED
"""
    result = await sandbox_executor.execute_in_sandbox(
        script,
        language="bash",
        input_files=input_files,
        collect_outputs=False,
        collect_workspace=False,
        migrate_outputs=False,
        timeout_ms=25_000,
        session_key=run_id,
    )
    if result.error or not result.ok:
        logger.info("workspace pull sandbox failed run=%s err=%s", run_id, result.error or result.stderr)
        return False
    return True


def _extract_member(blob: bytes, path: str) -> bytes:
    want = safe_logical_path(path)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.isfile() and safe_logical_path(member.name) == want:
                extracted = tf.extractfile(member)
                if extracted is None:
                    break
                return extracted.read()
    raise WorkspaceError("文件不存在", 404)


def _path_index(files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in visible_manifest(files):
        path = safe_logical_path(str(item.get("path") or item.get("name") or ""))
        if path:
            index[path] = item
    return index


def _overlay_delta(
    item: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    *,
    uploaded: bool,
) -> tuple[int, int]:
    if uploaded:
        return 1, 0
    path = safe_logical_path(str(item.get("path") or item.get("name") or ""))
    prev = previous.get(path)
    if not prev:
        return 1, 0
    if int(prev.get("bytes") or 0) != int(item.get("bytes") or 0):
        return 1, 1
    return 0, 0


def _overlay_file(
    item: dict[str, Any],
    *,
    file_id: str,
    kind: str,
    updated_at: str,
    mime: str = "",
    uploaded: bool = False,
    added: int = 0,
    removed: int = 0,
) -> dict[str, Any]:
    name = str(item.get("name") or item.get("path") or "file")
    return {
        "id": file_id,
        "name": name,
        "path": item.get("path") or name,
        "kind": kind,
        "mime": mime or _guess_mime(name),
        "size_bytes": int(item.get("bytes") or 0),
        "updated_at": updated_at,
        "uploaded": uploaded,
        "added": int(added or 0),
        "removed": int(removed or 0),
    }


def _kind_match(item: dict[str, Any], kind: str) -> bool:
    name = str(item.get("name") or "").lower()
    mime = str(item.get("mime") or "").lower()
    if kind == "image":
        return mime.startswith("image/") or any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
    if kind == "pdf":
        return name.endswith(".pdf") or mime == "application/pdf"
    if kind == "office":
        return any(name.endswith(ext) for ext in (".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls"))
    if kind == "uploaded":
        return bool(item.get("uploaded"))
    return True


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")
