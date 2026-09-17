"""Persistence, import/export and runtime projection for portable main-chat skins."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models import (
    CampusAssistantConfig,
    CampusAssistantRelease,
    MainChatSkin,
    MainChatSkinAsset,
)
from app.services.campus_assistant.main_chat_skin_package import (
    PACKAGE_SCOPE,
    ParsedMainChatSkinPackage,
    build_main_chat_skin_package,
    build_main_chat_skin_package_from_directory,
    parse_main_chat_skin_package,
)


logger = logging.getLogger(__name__)
ACTIVE_STATUS = "active"
BUILTIN_ROOT = Path(__file__).resolve().parents[3] / "skin_packages" / "main_chat"
ASSET_URL_PREFIX = "/agent-api/campus-assistant/skins"


class MainChatSkinError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _tenant_id(user) -> str:
    return str(getattr(user, "tenant_id", None) or "0")


def _user_id(user) -> str:
    return str(getattr(user, "user_id", None) or "")


def _manifest(row: MainChatSkin) -> dict[str, Any]:
    try:
        value = json.loads(row.manifest_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _asset_url(skin_id: str, asset_key: str) -> str:
    return f"{ASSET_URL_PREFIX}/{skin_id}/assets/{asset_key}"


def serialize_skin(
    row: MainChatSkin,
    *,
    include_manifest: bool = True,
    reference_count: Optional[int] = None,
) -> dict[str, Any]:
    manifest = _manifest(row)
    runtime_manifest: Optional[dict[str, Any]] = None
    if include_manifest:
        runtime_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
        for asset in runtime_manifest.get("assets") or []:
            if isinstance(asset, dict):
                asset["url"] = _asset_url(row.id, str(asset.get("key") or ""))
    data = {
        "id": row.id,
        "scope": PACKAGE_SCOPE,
        "key": row.skin_key,
        "version": row.version,
        "schemaVersion": row.schema_version,
        "name": row.name,
        "description": row.description or "",
        "renderer": row.renderer_key,
        "contentHash": row.content_hash,
        "sourceType": row.source_type,
        "status": row.status,
        "installedBy": row.installed_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "manifest": runtime_manifest,
    }
    if reference_count is not None:
        refs = max(int(reference_count), 0)
        data.update({
            "referenceCount": refs,
            "deletable": refs == 0 and not str(row.source_type or "").startswith("builtin"),
        })
    return data


async def _assets_for_skin(session: AsyncSession, skin_id: str) -> list[MainChatSkinAsset]:
    return list((await session.execute(
        select(MainChatSkinAsset)
        .where(MainChatSkinAsset.skin_id == skin_id)
        .order_by(MainChatSkinAsset.asset_key.asc())
    )).scalars().all())


async def _install_parsed(
    session: AsyncSession,
    parsed: ParsedMainChatSkinPackage,
    *,
    tenant_id: str,
    installed_by: str,
    source_type: str,
) -> tuple[MainChatSkin, bool]:
    same_content = (await session.execute(
        select(MainChatSkin)
        .where(MainChatSkin.tenant_id == tenant_id)
        .where(MainChatSkin.content_hash == parsed.content_hash)
    )).scalar_one_or_none()
    if same_content is not None:
        return same_content, False

    key = parsed.manifest["key"]
    version = parsed.manifest["version"]
    conflicting = (await session.execute(
        select(MainChatSkin)
        .where(MainChatSkin.tenant_id == tenant_id)
        .where(MainChatSkin.skin_key == key)
        .where(MainChatSkin.version == version)
    )).scalar_one_or_none()
    if conflicting is not None:
        raise MainChatSkinError(409, f"皮肤 {key} {version} 已存在，但内容不同；请升级版本号")

    row = MainChatSkin(
        id=_new_id("mcs"),
        tenant_id=tenant_id,
        skin_key=key,
        version=version,
        schema_version=int(parsed.manifest["schemaVersion"]),
        name=parsed.manifest["name"],
        description=parsed.manifest.get("description") or "",
        renderer_key=parsed.manifest["renderer"],
        manifest_json=json.dumps(parsed.manifest, ensure_ascii=False, separators=(",", ":")),
        content_hash=parsed.content_hash,
        source_type=source_type,
        status=ACTIVE_STATUS,
        installed_by=installed_by,
    )
    session.add(row)
    await session.flush()
    descriptors = {item["key"]: item for item in parsed.manifest["assets"]}
    for asset_key, content in parsed.assets.items():
        descriptor = descriptors[asset_key]
        session.add(MainChatSkinAsset(
            id=_new_id("mcsa"),
            skin_id=row.id,
            asset_key=asset_key,
            asset_path=descriptor["path"],
            mime_type=descriptor["mime"],
            sha256=descriptor["sha256"],
            byte_size=len(content),
            content=content,
        ))
    await session.flush()
    return row, True


async def _ensure_builtin_skins(session: AsyncSession, tenant_id: str, installed_by: str) -> None:
    if not BUILTIN_ROOT.is_dir():
        logger.warning("main-chat builtin skin directory is missing: %s", BUILTIN_ROOT)
        return
    for child in sorted(BUILTIN_ROOT.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        try:
            package = build_main_chat_skin_package_from_directory(child)
            parsed = parse_main_chat_skin_package(package)
            await _install_parsed(
                session,
                parsed,
                tenant_id=tenant_id,
                installed_by=installed_by or "builtin",
                source_type="builtin",
            )
        except MainChatSkinError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("failed to install builtin main-chat skin: %s", child)


async def list_installed_skins(user) -> dict[str, Any]:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        await _ensure_builtin_skins(session, tenant_id, _user_id(user))
        await session.commit()
        rows = (await session.execute(
            select(MainChatSkin)
            .where(MainChatSkin.tenant_id == tenant_id)
            .where(MainChatSkin.status == ACTIVE_STATUS)
            .order_by(MainChatSkin.created_at.asc(), MainChatSkin.skin_key.asc())
        )).scalars().all()
        skin_ids = [row.id for row in rows]
        reference_counts: dict[str, int] = {}
        if skin_ids:
            references = (await session.execute(
                select(
                    CampusAssistantRelease.main_chat_skin_id,
                    func.count(CampusAssistantRelease.id),
                )
                .where(CampusAssistantRelease.main_chat_skin_id.in_(skin_ids))
                .group_by(CampusAssistantRelease.main_chat_skin_id)
            )).all()
            reference_counts = {
                str(skin_id): int(count or 0)
                for skin_id, count in references
                if skin_id
            }
        return {
            "scope": PACKAGE_SCOPE,
            "records": [
                serialize_skin(row, reference_count=reference_counts.get(row.id, 0))
                for row in rows
            ],
        }


async def import_skin_package(user, package_bytes: bytes) -> dict[str, Any]:
    try:
        parsed = parse_main_chat_skin_package(package_bytes)
    except ValueError as exc:
        raise MainChatSkinError(422, str(exc)) from exc
    async with async_session() as session:
        row, installed = await _install_parsed(
            session,
            parsed,
            tenant_id=_tenant_id(user),
            installed_by=_user_id(user),
            source_type="imported",
        )
        await session.commit()
        return {"installed": installed, "skin": serialize_skin(row)}


async def _require_skin(
    session: AsyncSession,
    tenant_id: str,
    skin_id: str,
    *,
    for_update: bool = False,
) -> MainChatSkin:
    query = select(MainChatSkin).where(MainChatSkin.id == str(skin_id or ""))
    if for_update:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None or row.tenant_id != tenant_id:
        raise MainChatSkinError(404, "主对话皮肤不存在")
    return row


async def _reference_count(session: AsyncSession, skin_id: str) -> int:
    return int((await session.execute(
        select(func.count(CampusAssistantRelease.id))
        .where(CampusAssistantRelease.main_chat_skin_id == skin_id)
    )).scalar_one() or 0)


async def get_skin_detail(user, skin_id: str) -> dict[str, Any]:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        row = await _require_skin(session, tenant_id, skin_id)
        return serialize_skin(row, reference_count=await _reference_count(session, row.id))


async def update_skin_metadata(
    user,
    skin_id: str,
    *,
    name: str,
    description: str,
) -> dict[str, Any]:
    cleaned_name = str(name or "").strip()
    cleaned_description = str(description or "").strip()
    if not cleaned_name:
        raise MainChatSkinError(422, "皮肤显示名称不能为空")
    if len(cleaned_name) > 128 or len(cleaned_description) > 512:
        raise MainChatSkinError(422, "皮肤显示名称或备注超出长度限制")
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        row = await _require_skin(session, tenant_id, skin_id, for_update=True)
        # Package manifest/assets/hash stay immutable.  These columns are local catalog metadata.
        row.name = cleaned_name
        row.description = cleaned_description
        await session.commit()
        # The UPDATE ran the server-side `updated_at = now()`, which expires that column even with
        # expire_on_commit=False.  Reload it explicitly: reading an expired attribute in
        # serialize_skin() would lazy-load synchronously inside the async session (MissingGreenlet).
        await session.refresh(row)
        return serialize_skin(row, reference_count=await _reference_count(session, row.id))


async def delete_skin(user, skin_id: str) -> dict[str, Any]:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        row = await _require_skin(session, tenant_id, skin_id, for_update=True)
        if str(row.source_type or "").startswith("builtin"):
            raise MainChatSkinError(409, "内置主对话皮肤不能删除")
        references = await _reference_count(session, row.id)
        if references:
            raise MainChatSkinError(
                409,
                f"该皮肤仍被 {references} 个校园百事通草稿或发布版本引用，请先更换引用后再删除",
            )
        deleted_id = row.id
        await session.execute(delete(MainChatSkinAsset).where(MainChatSkinAsset.skin_id == row.id))
        await session.delete(row)
        await session.commit()
        return {"deleted": True, "id": deleted_id}


async def export_skin_package(user, skin_id: str) -> tuple[MainChatSkin, bytes]:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        row = await _require_skin(session, tenant_id, skin_id)
        assets = await _assets_for_skin(session, row.id)
        manifest = _manifest(row)
        try:
            package = build_main_chat_skin_package(
                manifest,
                {asset.asset_key: bytes(asset.content) for asset in assets},
            )
        except ValueError as exc:
            raise MainChatSkinError(409, f"已安装皮肤素材不完整，无法导出：{exc}") from exc
        parsed = parse_main_chat_skin_package(package)
        if parsed.content_hash != row.content_hash:
            raise MainChatSkinError(409, "已安装皮肤内容哈希异常，无法导出")
        return row, package


async def read_skin_asset(user, skin_id: str, asset_key: str) -> MainChatSkinAsset:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        row = await _require_skin(session, tenant_id, skin_id)
        if row.status != ACTIVE_STATUS:
            raise MainChatSkinError(404, "主对话皮肤不可用")
        asset = (await session.execute(
            select(MainChatSkinAsset)
            .where(MainChatSkinAsset.skin_id == row.id)
            .where(MainChatSkinAsset.asset_key == str(asset_key or ""))
        )).scalar_one_or_none()
        if asset is None:
            raise MainChatSkinError(404, "皮肤素材不存在")
        # Detach the immutable bytes before the session closes.
        asset.content = bytes(asset.content)
        return asset


async def validate_skin_selection(
    session: AsyncSession,
    tenant_id: str,
    skin_id: Optional[str],
    *,
    for_update: bool = False,
) -> Optional[MainChatSkin]:
    skin_id = str(skin_id or "").strip()
    if not skin_id:
        return None
    query = select(MainChatSkin).where(MainChatSkin.id == skin_id)
    if for_update:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None or row.tenant_id != str(tenant_id or "0"):
        raise MainChatSkinError(422, "所选主对话皮肤不存在或不属于当前客户")
    if row.status != ACTIVE_STATUS:
        raise MainChatSkinError(422, "所选主对话皮肤尚未完成三端校验，不能发布")
    return row


async def resolve_published_skin(user) -> dict[str, Any]:
    """Return only safe display data; model/KB/policy fields never leave this endpoint."""
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        config = (await session.execute(
            select(CampusAssistantConfig).where(CampusAssistantConfig.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if config is None or not config.enabled or not config.current_release_id:
            return {"scope": PACKAGE_SCOPE, "skin": None}
        release = await session.get(CampusAssistantRelease, config.current_release_id)
        if release is None or release.status != "PUBLISHED" or not release.main_chat_skin_id:
            return {"scope": PACKAGE_SCOPE, "skin": None}
        try:
            skin = await validate_skin_selection(session, tenant_id, release.main_chat_skin_id)
        except MainChatSkinError:
            logger.warning(
                "published campus skin is unavailable tenant=%s release=%s skin=%s",
                tenant_id,
                release.id,
                release.main_chat_skin_id,
                exc_info=True,
            )
            return {"scope": PACKAGE_SCOPE, "skin": None}
        return {"scope": PACKAGE_SCOPE, "skin": serialize_skin(skin)}
