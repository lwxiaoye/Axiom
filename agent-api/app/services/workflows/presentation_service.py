"""Global run-page presentation catalog and per-app assignments.

The database owns catalog and assignment facts.  Vue components and bitmap imports remain
code-owned and are addressed only by a stable preset key; neither customer data nor workflow JSON
may inject CSS, HTML, component names, or arbitrary asset URLs.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select

from app.core.auth import UserContext, is_platform_admin
from app.models import (
    AgentPresentationAssignment,
    AgentPresentationPreset,
    AgentPresentationPresetAsset,
    SubAgentSkin,
    WorkflowApp,
)
from app.services.workflows import sub_agent_skin_service

logger = logging.getLogger(__name__)

DEFAULT_PRESET_KEY = "default"
_PRESET_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PRESENTATION_KEYS = {"schemaVersion", "preset", "copy"}
_COPY_LIMITS = {"welcomeTitle": 80, "composerPlaceholder": 120}


@dataclass(frozen=True)
class BuiltinAssetSpec:
    resource_ref: str
    sha256: str


@dataclass(frozen=True)
class BuiltinPresetSpec:
    key: str
    version: int
    name: str
    description: str
    preview_key: str
    assets: dict[str, BuiltinAssetSpec]


BUILTIN_PRESENTATION_PRESETS: dict[str, BuiltinPresetSpec] = {
    "campus-welcome-v1": BuiltinPresetSpec(
        key="campus-welcome-v1",
        version=2,
        name="校园迎新",
        description="校园蓝图贯穿三栏，迎新插画使用透明抠图",
        preview_key="campus-welcome-v1",
        assets={
            "campus-background": BuiltinAssetSpec(
                resource_ref="bundle://presentation/campus-welcome-v1/campus-background",
                sha256="776717e1ae0d326d735bd0d7319f761323b060745425a9b31a37ebba9dc272b8",
            ),
            "backpack": BuiltinAssetSpec(
                resource_ref="bundle://presentation/campus-welcome-v1/backpack",
                sha256="5ae12ffe682761093fa29ff90392195434c5580af337e2c5d46e33c73102a977",
            ),
            "student-group": BuiltinAssetSpec(
                resource_ref="bundle://presentation/campus-welcome-v1/student-group",
                sha256="b575b77ef16f32dc9ed09c626a350c40802c17df52c187270651a40691f76c2b",
            ),
            "preview": BuiltinAssetSpec(
                resource_ref="bundle://presentation/campus-welcome-v1/preview",
                sha256="9293286683c1cd9e8dfd8ce92ad9aed8336f05f493cc559cd8b767cbab4981eb",
            ),
        },
    ),
}


def _chat_config(graph: dict[str, Any]) -> dict[str, Any]:
    value = graph.get("chatConfig") or (graph.get("fastgpt") or {}).get("chatConfig") or {}
    return value if isinstance(value, dict) else {}


def extract_presentation_config(workflow_json: Optional[str], *, strict: bool = True) -> dict[str, Any]:
    """Read the declarative preset request from a workflow definition.

    Strict mode is used on every write/publish boundary and rejects executable or remote-resource
    fields.  Runtime mode is fail-closed and returns the standard preset for malformed legacy data.
    """
    if not workflow_json:
        return {"schemaVersion": 1, "preset": DEFAULT_PRESET_KEY}
    try:
        graph = json.loads(workflow_json)
    except (json.JSONDecodeError, TypeError) as exc:
        if strict:
            raise HTTPException(400, "工作流 JSON 不合法，无法校验运行页外观") from exc
        return {"schemaVersion": 1, "preset": DEFAULT_PRESET_KEY}
    if not isinstance(graph, dict):
        if strict:
            raise HTTPException(400, "工作流 JSON 必须是对象")
        return {"schemaVersion": 1, "preset": DEFAULT_PRESET_KEY}

    source = _chat_config(graph).get("presentation")
    if source is None:
        return {"schemaVersion": 1, "preset": DEFAULT_PRESET_KEY}
    if not isinstance(source, dict):
        if strict:
            raise HTTPException(400, "运行页外观配置必须是对象")
        return {"schemaVersion": 1, "preset": DEFAULT_PRESET_KEY}

    if strict:
        unsupported = sorted(set(source) - _PRESENTATION_KEYS)
        if unsupported:
            raise HTTPException(400, f"运行页外观不允许字段：{','.join(unsupported)}")
        schema_version = source.get("schemaVersion", 1)
        if schema_version != 1:
            raise HTTPException(400, "不支持的运行页外观配置版本")

    preset_key = str(source.get("preset") or DEFAULT_PRESET_KEY).strip()
    if not _PRESET_KEY_RE.fullmatch(preset_key):
        if strict:
            raise HTTPException(400, "运行页外观 key 不合法")
        preset_key = DEFAULT_PRESET_KEY

    result: dict[str, Any] = {"schemaVersion": 1, "preset": preset_key}
    copy_source = source.get("copy")
    if copy_source is not None and not isinstance(copy_source, dict):
        if strict:
            raise HTTPException(400, "运行页外观文案必须是对象")
        copy_source = {}
    if isinstance(copy_source, dict):
        if strict:
            unsupported_copy = sorted(set(copy_source) - set(_COPY_LIMITS))
            if unsupported_copy:
                raise HTTPException(400, f"运行页外观文案不允许字段：{','.join(unsupported_copy)}")
        safe_copy: dict[str, str] = {}
        for key, max_length in _COPY_LIMITS.items():
            value = str(copy_source.get(key) or "").strip()
            if strict and len(value) > max_length:
                raise HTTPException(400, f"运行页外观文案 {key} 最长 {max_length} 字")
            if value:
                safe_copy[key] = value[:max_length]
        if safe_copy:
            result["copy"] = safe_copy
    return result


def _tenant_id(value: Any) -> str:
    return str(value or "0").strip() or "0"


def asset_inventory_problem(
    spec: BuiltinPresetSpec,
    preset: AgentPresentationPreset,
    assets: list[AgentPresentationPresetAsset],
) -> str:
    """Return an empty string when every code-declared global asset has a matching DB record."""
    active = {asset.asset_key: asset for asset in assets if str(asset.status or "") == "active"}
    for asset_key, expected in spec.assets.items():
        row = active.get(asset_key)
        if row is None:
            return f"缺少素材 {asset_key}"
        if str(row.resource_ref or "") != expected.resource_ref:
            return f"素材 {asset_key} 引用与代码包不一致"
        if str(row.sha256 or "").lower() != expected.sha256:
            return f"素材 {asset_key} 校验值与代码包不一致"
    return ""


async def _load_installation(
    session,
    preset_key: str,
    *,
    for_update: bool = False,
) -> AgentPresentationPreset | SubAgentSkin:
    spec = BUILTIN_PRESENTATION_PRESETS.get(preset_key)
    if spec is None:
        portable = await sub_agent_skin_service.find_active_by_assignment(
            session, preset_key, for_update=for_update,
        )
        if portable is None:
            raise HTTPException(400, "该运行页外观未安装")
        return portable
    preset = await session.get(AgentPresentationPreset, preset_key)
    if preset is None or str(preset.status or "") != "active":
        raise HTTPException(503, "运行页外观目录尚未初始化或已停用")
    if (
        int(preset.version_no or 0) != spec.version
        or str(preset.renderer_key or "") != spec.key
    ):
        raise HTTPException(503, "运行页外观前后端版本不一致")
    assets = (
        await session.execute(
            select(AgentPresentationPresetAsset).where(
                AgentPresentationPresetAsset.preset_key == preset_key
            )
        )
    ).scalars().all()
    problem = asset_inventory_problem(spec, preset, list(assets))
    if problem:
        raise HTTPException(503, f"运行页外观素材未就绪：{problem}")
    return preset


async def require_installed_preset(
    session,
    preset_key: str,
    *,
    for_update: bool = False,
) -> AgentPresentationPreset | SubAgentSkin:
    if preset_key == DEFAULT_PRESET_KEY:
        raise HTTPException(400, "标准外观不需要安装")
    return await _load_installation(session, preset_key, for_update=for_update)


def _preset_record(spec: BuiltinPresetSpec) -> dict:
    return {
        "key": spec.key,
        "version": spec.version,
        "name": spec.name,
        "description": spec.description,
        "previewKey": spec.preview_key,
        "sourceType": "builtin",
    }


def _default_record() -> dict:
    return {
        "key": DEFAULT_PRESET_KEY,
        "version": 1,
        "name": "标准外观",
        "description": "平台默认的简洁黑白运行页",
        "previewKey": DEFAULT_PRESET_KEY,
        "sourceType": "platform-default",
    }


async def list_available_presets(session) -> list[dict]:
    await sub_agent_skin_service.ensure_builtin_skins(session, "builtin")
    await session.commit()
    records = [_default_record()]
    # 试衣间与管理目录同一口径：只列当前 active 的皮肤包。
    # 代码预设仍可跑已保存的旧分配，但不能作为可选项出现；已删除的包也不出现。
    records.extend(
        sub_agent_skin_service.catalog_record(row)
        for row in await sub_agent_skin_service.list_skins(session)
    )
    return records


async def list_admin_presets(session) -> list[dict]:
    await sub_agent_skin_service.ensure_builtin_skins(session, "builtin")
    await session.commit()
    records = [_default_record()]
    # The management page owns portable packages. Historic code presets remain available at
    # runtime for existing assignments, but do not appear as duplicate, non-CRUD catalog rows.
    for row in await sub_agent_skin_service.list_skins(session):
        records.append(await sub_agent_skin_service.admin_catalog_record(session, row))
    return records


def _require_assignment_tenant(app: WorkflowApp, user: UserContext) -> None:
    if _tenant_id(app.tenant_id) == _tenant_id(user.tenant_id) or is_platform_admin(user):
        return
    raise HTTPException(403, "只能给当前客户所属的智能体分配运行页外观")


async def validate_definition_for_app(
    session,
    app: WorkflowApp,
    workflow_json: str,
    *,
    user: Optional[UserContext] = None,
) -> dict[str, Any]:
    if user is not None:
        _require_assignment_tenant(app, user)
    config = extract_presentation_config(workflow_json, strict=True)
    preset_key = config["preset"]
    if preset_key != DEFAULT_PRESET_KEY:
        await require_installed_preset(
            session,
            preset_key,
            for_update=True,
        )
    return config


async def _assignment_for_app(session, app_id: str) -> Optional[AgentPresentationAssignment]:
    return (
        await session.execute(
            select(AgentPresentationAssignment).where(AgentPresentationAssignment.app_id == app_id)
        )
    ).scalar_one_or_none()


async def sync_draft_assignment(
    session,
    app: WorkflowApp,
    workflow_json: str,
    user: UserContext,
) -> AgentPresentationAssignment:
    config = await validate_definition_for_app(session, app, workflow_json, user=user)
    assignment = await _assignment_for_app(session, app.id)
    if assignment is None:
        assignment = AgentPresentationAssignment(
            id=uuid.uuid4().hex,
            app_id=app.id,
            tenant_id=_tenant_id(app.tenant_id),
            published_preset_key=DEFAULT_PRESET_KEY,
            published_version=0,
        )
        session.add(assignment)
    if _tenant_id(assignment.tenant_id) != _tenant_id(app.tenant_id):
        raise HTTPException(409, "智能体外观分配记录的客户归属不一致")
    assignment.draft_preset_key = config["preset"]
    assignment.draft_updated_by = user.user_id
    return assignment


async def promote_published_assignment(
    session,
    app: WorkflowApp,
    workflow_json: str,
    *,
    version_no: int,
) -> AgentPresentationAssignment:
    config = await validate_definition_for_app(session, app, workflow_json)
    assignment = await _assignment_for_app(session, app.id)
    if assignment is None:
        assignment = AgentPresentationAssignment(
            id=uuid.uuid4().hex,
            app_id=app.id,
            tenant_id=_tenant_id(app.tenant_id),
        )
        session.add(assignment)
    if _tenant_id(assignment.tenant_id) != _tenant_id(app.tenant_id):
        raise HTTPException(409, "智能体外观分配记录的客户归属不一致")
    assignment.published_preset_key = config["preset"]
    assignment.published_version = int(version_no or 0)
    return assignment


async def resolve_runtime_presentation(
    session,
    app: WorkflowApp,
    workflow_json: Optional[str],
    *,
    scope: str,
) -> Optional[dict[str, Any]]:
    """Resolve a runtime preset after re-checking app, assignment, installation and assets.

    Missing or inconsistent custom styles fail closed to the platform default without blocking the
    assistant itself. The caller must remove any unverified presentation parsed directly from JSON.
    """
    config = extract_presentation_config(workflow_json, strict=False)
    preset_key = config["preset"]
    if preset_key == DEFAULT_PRESET_KEY:
        return None
    assignment = await _assignment_for_app(session, app.id)
    if scope == "draft":
        assigned_key = assignment.draft_preset_key if assignment else None
    elif scope == "published":
        assigned_key = assignment.published_preset_key if assignment else None
    else:
        assigned_key = preset_key  # immutable review/version snapshot; installation is still checked
    if (
        assignment is not None
        and _tenant_id(assignment.tenant_id) != _tenant_id(app.tenant_id)
    ) or assigned_key != preset_key:
        logger.warning(
            "presentation assignment mismatch app=%s tenant=%s scope=%s requested=%s assigned=%s",
            app.id,
            app.tenant_id,
            scope,
            preset_key,
            assigned_key,
        )
        return None
    try:
        installation = await require_installed_preset(session, preset_key)
    except HTTPException:
        logger.warning(
            "presentation installation rejected at runtime app=%s tenant=%s preset=%s",
            app.id,
            app.tenant_id,
            preset_key,
            exc_info=True,
        )
        return None
    if isinstance(installation, SubAgentSkin):
        # Server-only projection.  Strict workflow parsing never accepts this field from draft JSON.
        config["portableSkin"] = sub_agent_skin_service.serialize_skin(installation)
    return config


async def delete_assignment(session, app_id: str) -> None:
    await session.execute(
        sa_delete(AgentPresentationAssignment).where(AgentPresentationAssignment.app_id == app_id)
    )
