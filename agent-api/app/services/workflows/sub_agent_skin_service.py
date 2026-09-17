"""Persistence and runtime projection for portable `/agent/run/:appId` skins.

Imported packages remain declarative: the database stores a normalized manifest and verified
raster bytes, while the frontend owns the single audited generic renderer.  Imported HTML, CSS,
JavaScript, Vue components and remote asset URLs are never accepted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import is_platform_admin
from app.models import (
    AgentPresentationAssignment,
    SubAgentSkin,
    SubAgentSkinAsset,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.services.workflows.sub_agent_skin_package import (
    PACKAGE_SCOPE,
    ParsedMainChatSkinPackage,
    build_sub_agent_skin_package,
    build_sub_agent_skin_package_from_directory,
    parse_sub_agent_skin_package,
)


logger = logging.getLogger(__name__)
ACTIVE_STATUS = "active"
REMOVED_STATUS = "removed"
DEFAULT_PRESET_KEY = "default"
BUILTIN_ROOT = Path(__file__).resolve().parents[3] / "skin_packages" / "sub_agent"
ASSET_URL_PREFIX = "/agent-api/workflow/presentation/skins"


class SubAgentSkinError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _user_id(user: Any) -> str:
    return str(getattr(user, "user_id", None) or "")


def assignment_key(package_key: str, version: str) -> str:
    """Return a deterministic workflow-safe key that fits the historic VARCHAR(64) contract."""
    candidate = f"skin-{package_key}-v{version.replace('.', '-')}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(f"{package_key}@{version}".encode("utf-8")).hexdigest()[:12]
    prefix = package_key[:44].rstrip("-")
    return f"skin-{prefix}-{digest}"


def _manifest(row: SubAgentSkin) -> dict[str, Any]:
    try:
        value = json.loads(row.manifest_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _asset_url(skin_id: str, asset_key: str) -> str:
    return f"{ASSET_URL_PREFIX}/{skin_id}/assets/{asset_key}"


def serialize_skin(row: SubAgentSkin, *, reference_count: Optional[int] = None) -> dict[str, Any]:
    manifest = json.loads(json.dumps(_manifest(row), ensure_ascii=False))
    for asset in manifest.get("assets") or []:
        if isinstance(asset, dict):
            asset["url"] = _asset_url(row.id, str(asset.get("key") or ""))
    data = {
        "id": row.id,
        "scope": PACKAGE_SCOPE,
        "key": row.skin_key,
        "assignmentKey": row.assignment_key,
        "version": row.version,
        "schemaVersion": row.schema_version,
        "name": row.name,
        "description": row.description or "",
        "renderer": row.renderer_key,
        "contentHash": row.content_hash,
        "sourceType": row.source_type,
        "status": row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "manifest": manifest,
    }
    if reference_count is not None:
        refs = max(int(reference_count), 0)
        data.update({
            "referenceCount": refs,
            "deletable": True,
        })
    return data


def catalog_record(
    row: SubAgentSkin,
    *,
    reference_count: Optional[int] = None,
) -> dict[str, Any]:
    skin = serialize_skin(row, reference_count=reference_count)
    return {
        "key": row.assignment_key,
        "packageKey": row.skin_key,
        "version": row.version,
        "name": row.name,
        "description": row.description or "",
        "previewKey": row.assignment_key,
        "sourceType": row.source_type,
        "materialReady": True,
        "materialProblem": "",
        "portableSkin": skin,
    }


async def _assets_for_skin(session: AsyncSession, skin_id: str) -> list[SubAgentSkinAsset]:
    return list((await session.execute(
        select(SubAgentSkinAsset)
        .where(SubAgentSkinAsset.skin_id == skin_id)
        .order_by(SubAgentSkinAsset.asset_key.asc())
    )).scalars().all())


async def _install_parsed(
    session: AsyncSession,
    parsed: ParsedMainChatSkinPackage,
    *,
    installed_by: str,
    source_type: str,
    reactivate_existing: bool = False,
) -> tuple[SubAgentSkin, bool]:
    same_content = (await session.execute(
        select(SubAgentSkin)
        .where(SubAgentSkin.content_hash == parsed.content_hash)
    )).scalar_one_or_none()
    if same_content is not None:
        if reactivate_existing and str(same_content.status or "") != ACTIVE_STATUS:
            same_content.status = ACTIVE_STATUS
            same_content.installed_by = installed_by
            return same_content, True
        return same_content, False

    key = parsed.manifest["key"]
    version = parsed.manifest["version"]
    conflicting = (await session.execute(
        select(SubAgentSkin)
        .where(SubAgentSkin.skin_key == key)
        .where(SubAgentSkin.version == version)
    )).scalar_one_or_none()
    if conflicting is not None:
        if str(conflicting.status or "") == REMOVED_STATUS and not reactivate_existing:
            return conflicting, False
        raise SubAgentSkinError(409, f"子智能体皮肤 {key} {version} 已存在，但内容不同；请升级版本号")

    lookup_key = assignment_key(key, version)
    lookup_conflict = (await session.execute(
        select(SubAgentSkin)
        .where(SubAgentSkin.assignment_key == lookup_key)
    )).scalar_one_or_none()
    if lookup_conflict is not None:
        raise SubAgentSkinError(409, "皮肤分配标识发生冲突，请修改包 key 或版本号")

    row = SubAgentSkin(
        id=_new_id("sas"),
        skin_key=key,
        version=version,
        assignment_key=lookup_key,
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
        session.add(SubAgentSkinAsset(
            id=_new_id("sasa"),
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


async def _rows_for_skin_key(session: AsyncSession, skin_key: str) -> list[SubAgentSkin]:
    return list((await session.execute(
        select(SubAgentSkin).where(SubAgentSkin.skin_key == str(skin_key or ""))
    )).scalars().all())


def should_skip_builtin_install(existing: list[SubAgentSkin]) -> bool:
    """Directory bootstrap must not resurrect a shipped package the admin removed."""
    return any(str(row.status or "") == REMOVED_STATUS for row in existing)


async def ensure_builtin_skins(
    session: AsyncSession,
    installed_by: str,
) -> None:
    """Install developer-shipped packages once into the global skin catalog."""
    if not BUILTIN_ROOT.is_dir():
        logger.warning("sub-agent builtin skin directory is missing: %s", BUILTIN_ROOT)
        return
    for child in sorted(BUILTIN_ROOT.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        try:
            package = build_sub_agent_skin_package_from_directory(child)
            parsed = parse_sub_agent_skin_package(package)
            existing = await _rows_for_skin_key(session, parsed.manifest["key"])
            if should_skip_builtin_install(existing):
                continue
            await _install_parsed(
                session,
                parsed,
                installed_by=installed_by or "builtin",
                source_type="builtin-package",
            )
        except SubAgentSkinError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("failed to install builtin sub-agent skin: %s", child)


async def list_skins(session: AsyncSession) -> list[SubAgentSkin]:
    return list((await session.execute(
        select(SubAgentSkin)
        .where(SubAgentSkin.status == ACTIVE_STATUS)
        .order_by(SubAgentSkin.created_at.asc(), SubAgentSkin.skin_key.asc())
    )).scalars().all())


async def import_skin_package(
    session: AsyncSession,
    user: Any,
    package_bytes: bytes,
) -> tuple[SubAgentSkin, bool]:
    if not is_platform_admin(user):
        raise SubAgentSkinError(403, "只有平台管理员可以导入子智能体皮肤")
    try:
        parsed = parse_sub_agent_skin_package(package_bytes)
    except ValueError as exc:
        raise SubAgentSkinError(422, str(exc)) from exc
    row, installed = await _install_parsed(
        session,
        parsed,
        installed_by=_user_id(user),
        source_type="imported",
        reactivate_existing=True,
    )
    await _retire_other_active_versions(session, row)
    return row, installed


def _retarget_workflow_presentation(raw: Any, old_key: str, new_key: str) -> tuple[Any, bool]:
    if not raw or old_key == new_key:
        return raw, False
    try:
        graph = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return raw, False
    if not isinstance(graph, dict):
        return raw, False
    changed = False
    for chat_config in _workflow_chat_configs(graph):
        presentation = chat_config.get("presentation")
        if (
            isinstance(presentation, dict)
            and str(presentation.get("preset") or "").strip() == old_key
        ):
            presentation["preset"] = new_key
            changed = True
    if not changed:
        return raw, False
    if isinstance(raw, str):
        return json.dumps(graph, ensure_ascii=False, separators=(",", ":")), True
    return graph, True


async def _retarget_current_references(
    session: AsyncSession,
    old_row: SubAgentSkin,
    new_row: SubAgentSkin,
    *,
    updated_by: str,
) -> None:
    assignments = (await session.execute(
        select(AgentPresentationAssignment)
        .where(
            or_(
                AgentPresentationAssignment.draft_preset_key == old_row.assignment_key,
                AgentPresentationAssignment.published_preset_key == old_row.assignment_key,
            )
        )
        .with_for_update()
    )).scalars().all()
    for assignment in assignments:
        if assignment.draft_preset_key == old_row.assignment_key:
            assignment.draft_preset_key = new_row.assignment_key
            assignment.draft_updated_by = updated_by
        if assignment.published_preset_key == old_row.assignment_key:
            assignment.published_preset_key = new_row.assignment_key

    definitions = (await session.execute(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.draft_json.contains(old_row.assignment_key))
        .with_for_update()
    )).scalars().all()
    for definition in definitions:
        draft_json, draft_changed = _retarget_workflow_presentation(
            definition.draft_json,
            old_row.assignment_key,
            new_row.assignment_key,
        )
        if draft_changed:
            definition.draft_json = draft_json


async def _retire_other_active_versions(session: AsyncSession, kept: SubAgentSkin) -> None:
    """An explicit import replaces other live versions of the same package key."""
    siblings = await _rows_for_skin_key(session, kept.skin_key)
    for sibling in siblings:
        if sibling.id == kept.id or str(sibling.status or "") != ACTIVE_STATUS:
            continue
        await _retarget_current_references(
            session,
            sibling,
            kept,
            updated_by=str(kept.installed_by or "import"),
        )
        sibling.status = REMOVED_STATUS


async def _require_skin(
    session: AsyncSession,
    skin_id: str,
    *,
    for_update: bool = False,
) -> SubAgentSkin:
    query = select(SubAgentSkin).where(SubAgentSkin.id == str(skin_id or ""))
    if for_update:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise SubAgentSkinError(404, "子智能体皮肤不存在")
    return row


def _workflow_chat_configs(graph: dict[str, Any]) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    direct = graph.get("chatConfig")
    if isinstance(direct, dict):
        configs.append(direct)
    fastgpt = graph.get("fastgpt")
    nested = fastgpt.get("chatConfig") if isinstance(fastgpt, dict) else None
    if isinstance(nested, dict) and all(nested is not item for item in configs):
        configs.append(nested)
    return configs


def _workflow_references_preset(raw: Any, assignment_key: str) -> bool:
    if not raw:
        return False
    try:
        graph = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(graph, dict):
        return False
    return any(
        isinstance(presentation := chat_config.get("presentation"), dict)
        and str(presentation.get("preset") or "").strip() == assignment_key
        for chat_config in _workflow_chat_configs(graph)
    )


def _fallback_workflow_presentation(raw: Any, assignment_key: str) -> tuple[Any, bool]:
    """Replace one current workflow presentation with the explicit platform default."""
    if not raw:
        return raw, False
    try:
        graph = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return raw, False
    if not isinstance(graph, dict):
        return raw, False

    changed = False
    for chat_config in _workflow_chat_configs(graph):
        presentation = chat_config.get("presentation")
        if (
            isinstance(presentation, dict)
            and str(presentation.get("preset") or "").strip() == assignment_key
        ):
            chat_config["presentation"] = {
                "schemaVersion": 1,
                "preset": DEFAULT_PRESET_KEY,
            }
            changed = True
    if not changed:
        return raw, False
    if isinstance(raw, str):
        return json.dumps(graph, ensure_ascii=False, separators=(",", ":")), True
    return graph, True


async def _fallback_current_references(
    session: AsyncSession,
    row: SubAgentSkin,
    *,
    updated_by: str,
) -> int:
    """Move every current draft/published consumer to the default without rewriting history."""
    affected_app_ids: set[str] = set()
    assignments = (await session.execute(
        select(AgentPresentationAssignment)
        .where(
            or_(
                AgentPresentationAssignment.draft_preset_key == row.assignment_key,
                AgentPresentationAssignment.published_preset_key == row.assignment_key,
            )
        )
        .with_for_update()
    )).scalars().all()
    for assignment in assignments:
        changed = False
        if assignment.draft_preset_key == row.assignment_key:
            assignment.draft_preset_key = DEFAULT_PRESET_KEY
            assignment.draft_updated_by = updated_by
            changed = True
        if assignment.published_preset_key == row.assignment_key:
            assignment.published_preset_key = DEFAULT_PRESET_KEY
            changed = True
        if changed:
            affected_app_ids.add(str(assignment.app_id))

    definitions = (await session.execute(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.draft_json.contains(row.assignment_key))
        .with_for_update()
    )).scalars().all()
    for definition in definitions:
        draft_json, draft_changed = _fallback_workflow_presentation(
            definition.draft_json,
            row.assignment_key,
        )
        if draft_changed:
            definition.draft_json = draft_json
            affected_app_ids.add(str(definition.app_id))
    # published_json belongs to the immutable release snapshot identified by published_version.
    # The published assignment above changes the effective skin without corrupting that audit link.
    return len(affected_app_ids)


async def _reference_count(session: AsyncSession, row: SubAgentSkin) -> int:
    current_count = int((await session.execute(
        select(func.count(AgentPresentationAssignment.id)).where(
            or_(
                AgentPresentationAssignment.draft_preset_key == row.assignment_key,
                AgentPresentationAssignment.published_preset_key == row.assignment_key,
            )
        )
    )).scalar_one() or 0)

    needle = row.assignment_key
    definitions = (await session.execute(
        select(WorkflowDefinition.draft_json, WorkflowDefinition.published_json).where(
            or_(
                WorkflowDefinition.draft_json.contains(needle),
                WorkflowDefinition.published_json.contains(needle),
            )
        )
    )).all()
    definition_count = sum(
        1
        for draft_json, published_json in definitions
        if _workflow_references_preset(draft_json, needle)
        or _workflow_references_preset(published_json, needle)
    )

    versions = (await session.execute(
        select(WorkflowVersion.definition_json).where(WorkflowVersion.definition_json.contains(needle))
    )).scalars().all()
    version_count = sum(1 for raw in versions if _workflow_references_preset(raw, needle))
    return current_count + definition_count + version_count


async def admin_catalog_record(session: AsyncSession, row: SubAgentSkin) -> dict[str, Any]:
    """Project one catalog row with the reference-aware CRUD state used by administrators."""
    return catalog_record(row, reference_count=await _reference_count(session, row))


async def get_skin_detail(
    session: AsyncSession,
    user: Any,
    skin_id: str,
) -> dict[str, Any]:
    if not is_platform_admin(user):
        raise SubAgentSkinError(403, "只有平台管理员可以查看子智能体皮肤详情")
    row = await _require_skin(session, skin_id)
    return serialize_skin(row, reference_count=await _reference_count(session, row))


async def update_skin_metadata(
    session: AsyncSession,
    user: Any,
    skin_id: str,
    *,
    name: str,
    description: str,
) -> dict[str, Any]:
    if not is_platform_admin(user):
        raise SubAgentSkinError(403, "只有平台管理员可以修改子智能体皮肤")
    cleaned_name = str(name or "").strip()
    cleaned_description = str(description or "").strip()
    if not cleaned_name:
        raise SubAgentSkinError(422, "皮肤显示名称不能为空")
    if len(cleaned_name) > 128 or len(cleaned_description) > 512:
        raise SubAgentSkinError(422, "皮肤显示名称或备注超出长度限制")
    row = await _require_skin(session, skin_id, for_update=True)
    # Local catalog metadata is mutable; portable package bytes and manifest stay immutable.
    row.name = cleaned_name
    row.description = cleaned_description
    reference_count = await _reference_count(session, row)
    # The UPDATE (autoflushed by the query above) runs the server-side `updated_at = now()`, which
    # expires that column on the instance.  Reload it explicitly: reading an expired attribute in
    # serialize_skin() would lazy-load synchronously inside the async session (MissingGreenlet).
    await session.flush()
    await session.refresh(row)
    return serialize_skin(row, reference_count=reference_count)


async def delete_skin(
    session: AsyncSession,
    user: Any,
    skin_id: str,
) -> dict[str, Any]:
    if not is_platform_admin(user):
        raise SubAgentSkinError(403, "只有平台管理员可以删除子智能体皮肤")
    row = await _require_skin(session, skin_id, for_update=True)
    fallback_count = await _fallback_current_references(
        session,
        row,
        updated_by=_user_id(user),
    )
    deleted_id = row.id
    if str(row.source_type or "").startswith("builtin"):
        # The package bytes ship with the application, so removing the database row would make
        # the next catalog bootstrap install it again. Keep a tombstone that an explicit import
        # can reactivate, while ordinary catalog bootstrap continues to respect the deletion.
        row.status = REMOVED_STATUS
        return {
            "deleted": True,
            "id": deleted_id,
            "fallbackAgentCount": fallback_count,
        }
    await session.execute(delete(SubAgentSkinAsset).where(SubAgentSkinAsset.skin_id == row.id))
    await session.delete(row)
    return {
        "deleted": True,
        "id": deleted_id,
        "fallbackAgentCount": fallback_count,
    }


async def export_skin_package(
    session: AsyncSession,
    user: Any,
    skin_id: str,
) -> tuple[SubAgentSkin, bytes]:
    if not is_platform_admin(user):
        raise SubAgentSkinError(403, "只有平台管理员可以导出子智能体皮肤")
    row = await _require_skin(session, skin_id)
    assets = await _assets_for_skin(session, row.id)
    try:
        package = build_sub_agent_skin_package(
            _manifest(row),
            {asset.asset_key: bytes(asset.content) for asset in assets},
        )
        parsed = parse_sub_agent_skin_package(package)
    except ValueError as exc:
        raise SubAgentSkinError(409, f"已安装皮肤素材不完整，无法导出：{exc}") from exc
    if parsed.content_hash != row.content_hash:
        raise SubAgentSkinError(409, "已安装皮肤内容哈希异常，无法导出")
    return row, package


async def read_skin_asset(
    session: AsyncSession,
    skin_id: str,
    asset_key: str,
) -> SubAgentSkinAsset:
    row = await _require_skin(session, skin_id)
    if row.status != ACTIVE_STATUS:
        raise SubAgentSkinError(404, "子智能体皮肤不可用")
    asset = (await session.execute(
        select(SubAgentSkinAsset)
        .where(SubAgentSkinAsset.skin_id == row.id)
        .where(SubAgentSkinAsset.asset_key == str(asset_key or ""))
    )).scalar_one_or_none()
    if asset is None:
        raise SubAgentSkinError(404, "皮肤素材不存在")
    asset.content = bytes(asset.content)
    return asset


async def find_active_by_assignment(
    session: AsyncSession,
    lookup_key: str,
    *,
    for_update: bool = False,
) -> Optional[SubAgentSkin]:
    query = (
        select(SubAgentSkin)
        .where(SubAgentSkin.assignment_key == str(lookup_key or ""))
        .where(SubAgentSkin.status == ACTIVE_STATUS)
    )
    if for_update:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()
