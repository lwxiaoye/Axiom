"""Authorization boundary for administrator-configured Harness application routes.

The standalone pages are code-owned, but their catalogue entries are ordinary
administrator records. This module never seeds, repairs or rewrites ``app_info``.
An entry becomes a Harness application only when it is enabled, not deleted,
has ``app_type`` of ``external`` (外部接入应用) or ``custom`` (自建业务应用),
and points at one of the reserved routes. ``agent``/``ai`` records stay ordinary
apps even if they reuse the same path. ``app_role``/``app_dept`` remain the ACL.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import bindparam, select, text

from app.core.auth import UserContext
from app.core.database import async_session
from app.models import ChatMessage, ChatThread
from app.services.chat.builtin_assistants.registry import (
    BUILTIN_ASSISTANT_DEFINITIONS,
    get_builtin_assistant_by_preset,
    preset_from_thread_origin,
)
from app.services.chat.builtin_assistants.types import BuiltinAppSpec

logger = logging.getLogger(__name__)

ORDINARY_THREAD_SCOPE = "ordinary"
BUILTIN_RUNTIME_KIND = "harness_builtin"
CATALOG_APP_TYPE = "external"
CATALOG_APP_TYPES = frozenset({"external", "custom"})


def normalize_visible_ids(value: Any) -> list[str]:
    """把角色/部门 id 的各种形态（JSON 数组 / 逗号串 / dict 列表）归一成去重字符串列表。

    原属工作流发布可见性模块（app_info_publish_service）；编排下线后内置智能体的
    app_role/app_dept ACL 仍靠它归一 UserContext 与关系表里的 id。
    """
    if not value:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                items: Iterable[Any] = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                items = value.split(",")
        else:
            items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = [value]

    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("id") or item.get("value") or item.get("roleId") or item.get("departId")
        if item is None:
            continue
        text_value = str(item).strip()
        if text_value and text_value not in result:
            result.append(text_value)
    return result


async def load_user_relation_ids(session, user: UserContext) -> tuple[list[str], list[str]]:
    """从 sys_user_role / sys_user_depart 补齐用户的角色与部门 id（失败降级为空）。"""
    try:
        role_ids = (
            await session.execute(
                text("SELECT role_id FROM sys_user_role WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
        ).scalars().all()
        dept_ids = (
            await session.execute(
                text("SELECT dep_id FROM sys_user_depart WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
        ).scalars().all()
        return normalize_visible_ids(role_ids), normalize_visible_ids(dept_ids)
    except Exception:
        logger.warning("Failed to load user role/dept relations for user %s", user.user_id, exc_info=True)
        return [], []


BUILTIN_APP_SPECS: tuple[BuiltinAppSpec, ...] = tuple(
    definition.catalog for definition in BUILTIN_ASSISTANT_DEFINITIONS
)

_SPECS_BY_PRESET = {item.preset: item for item in BUILTIN_APP_SPECS}
_SPECS_BY_ROUTE = {item.route: item for item in BUILTIN_APP_SPECS}
_CATALOG_ROUTE_VARIANTS = sorted(
    {variant for spec in BUILTIN_APP_SPECS for variant in (spec.route, f"{spec.route}/")}
)


def normalize_thread_scope(value: object, *, allow_empty: bool = False) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return None
    if normalized in {"", ORDINARY_THREAD_SCOPE}:
        return ORDINARY_THREAD_SCOPE
    if normalized in _SPECS_BY_PRESET:
        return normalized
    raise HTTPException(status_code=422, detail="会话范围不合法")


def thread_scope_from_origin(origin: object) -> str:
    normalized_origin = str(origin or "").strip()
    if not normalized_origin:
        return ORDINARY_THREAD_SCOPE
    return preset_from_thread_origin(normalized_origin) or normalized_origin


def normalize_catalog_route(value: object) -> str:
    route = str(value or "").strip()
    if not route.startswith("/") or "?" in route or "#" in route:
        return ""
    return route.rstrip("/") or "/"


def builtin_spec_for_catalog_routes(
    pc_url: object = None,
    h5_url: object = None,
) -> Optional[BuiltinAppSpec]:
    """Resolve a reserved page from catalogue routes without using name or row id guesses."""
    matches = {
        spec
        for value in (pc_url, h5_url)
        if (spec := _SPECS_BY_ROUTE.get(normalize_catalog_route(value))) is not None
    }
    return next(iter(matches)) if len(matches) == 1 else None


def builtin_preset_for_catalog_routes(pc_url: object = None, h5_url: object = None) -> Optional[str]:
    spec = builtin_spec_for_catalog_routes(pc_url, h5_url)
    return spec.preset if spec else None


def _enabled(value: object) -> bool:
    return str(value or "0").strip().lower() in {"1", "true", "yes", "on"}


def _not_deleted(value: object) -> bool:
    return str(value if value is not None else "0").strip().lower() in {
        "",
        "0",
        "false",
        "none",
    }


def _is_catalog_app(row: dict[str, Any]) -> bool:
    return str(row.get("app_type") or "").strip().lower() in CATALOG_APP_TYPES


def _row_to_marketplace(row: dict[str, Any], spec: BuiltinAppSpec) -> dict[str, Any]:
    creator = str(row.get("create_by") or "").strip()
    creator_name = str(row.get("_creator_name") or creator).strip()
    created_at = row.get("create_time")
    return {
        "id": str(row.get("id") or ""),
        "appName": str(row.get("app_name") or spec.name),
        "appRemark": str(row.get("app_remark") or spec.description),
        "appIcon": str(row.get("app_icon") or spec.icon),
        "appCategory": str(row.get("app_category") or spec.category),
        "appCategory_dictText": spec.category_label,
        # Runtime-facing type. Persist the administrator record's source as-is.
        "appType": "builtin",
        "catalogAppType": str(row.get("app_type") or CATALOG_APP_TYPE).strip().lower() or CATALOG_APP_TYPE,
        "pcUrl": spec.route,
        "h5Url": spec.route,
        "openType": "_blank",
        "status": 1,
        "orderNum": int(row.get("order_num") or spec.order_num),
        "formOptions": row.get("form_options"),
        "builtinPreset": spec.preset,
        "runtimeKind": BUILTIN_RUNTIME_KIND,
        "createBy": creator,
        "createByName": creator_name or "未知",
        "createByAvatar": str(row.get("_creator_avatar") or ""),
        "createTime": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
    }


async def _load_catalog_rows(session) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, app_name, app_remark, app_type, app_icon, app_category,
                       pc_url, h5_url, form_options, status, order_num, open_type,
                       del_flag, create_by, create_time
                FROM app_info
                WHERE pc_url IN :pc_routes OR h5_url IN :h5_routes
                """
            ).bindparams(
                bindparam("pc_routes", expanding=True),
                bindparam("h5_routes", expanding=True),
            ),
            {
                "pc_routes": _CATALOG_ROUTE_VARIANTS,
                "h5_routes": _CATALOG_ROUTE_VARIANTS,
            },
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _load_catalog_acl(
    session,
    app_ids: list[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    if not app_ids:
        return {}, {}
    roles: dict[str, set[str]] = {}
    depts: dict[str, set[str]] = {}
    role_rows = (
        await session.execute(
            text("SELECT app_id, role_id FROM app_role WHERE app_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": app_ids},
        )
    ).all()
    dept_rows = (
        await session.execute(
            text("SELECT app_id, dept_id FROM app_dept WHERE app_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": app_ids},
        )
    ).all()
    for app_id, role_id in role_rows:
        normalized = str(role_id or "").strip()
        if normalized:
            roles.setdefault(str(app_id), set()).add(normalized)
    for app_id, dept_id in dept_rows:
        normalized = str(dept_id or "").strip()
        if normalized:
            depts.setdefault(str(app_id), set()).add(normalized)
    return roles, depts


async def _load_creator_profiles(
    session,
    creator_keys: list[str],
) -> dict[str, tuple[str, str]]:
    keys = sorted({str(value or "").strip() for value in creator_keys if str(value or "").strip()})
    if not keys:
        return {}
    profiles: dict[str, tuple[str, str]] = {}
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, username, realname, avatar
                    FROM sys_user
                    WHERE id IN :user_ids OR username IN :usernames
                    """
                ).bindparams(
                    bindparam("user_ids", expanding=True),
                    bindparam("usernames", expanding=True),
                ),
                {"user_ids": keys, "usernames": keys},
            )
        ).mappings().all()
        for raw in rows:
            row = dict(raw)
            user_id = str(row.get("id") or "").strip()
            username = str(row.get("username") or "").strip()
            name = str(row.get("realname") or username or user_id).strip()
            avatar = str(row.get("avatar") or "").strip()
            if user_id:
                profiles[user_id] = (name, avatar)
            if username:
                profiles[username] = (name, avatar)
    except Exception:  # noqa: BLE001
        # Creator enrichment must not turn an otherwise valid application into an outage.
        logger.warning("加载应用创建人头像和姓名失败，回退到 create_by", exc_info=True)
    return profiles


def catalog_acl_allows(
    *,
    user_roles: set[str],
    user_depts: set[str],
    allowed_roles: set[str],
    allowed_depts: set[str],
) -> bool:
    """Application-management ACL semantics: empty means all users, otherwise role OR dept."""
    if not allowed_roles and not allowed_depts:
        return True
    return bool(allowed_roles & user_roles or allowed_depts & user_depts)


def _active_catalog_rows_by_preset(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not _enabled(row.get("status")) or not _not_deleted(row.get("del_flag")):
            continue
        if not _is_catalog_app(row):
            continue
        spec = builtin_spec_for_catalog_routes(row.get("pc_url"), row.get("h5_url"))
        if spec is not None:
            grouped.setdefault(spec.preset, []).append(row)

    duplicates = [
        _SPECS_BY_PRESET[preset].name
        for preset, candidates in grouped.items()
        if len(candidates) > 1
    ]
    if duplicates:
        names = "、".join(sorted(duplicates))
        raise HTTPException(
            status_code=503,
            detail=f"{names}存在多条已启用的目录应用配置，请管理员只保留一条",
        )
    return {preset: candidates[0] for preset, candidates in grouped.items() if candidates}


async def _visible_rows_for_user(user: UserContext) -> dict[str, dict[str, Any]]:
    try:
        async with async_session() as session:
            rows_by_preset = _active_catalog_rows_by_preset(await _load_catalog_rows(session))
            rows = list(rows_by_preset.values())
            app_ids = [str(row.get("id") or "") for row in rows]
            allowed_roles, allowed_depts = await _load_catalog_acl(session, app_ids)
            relation_roles, relation_depts = await load_user_relation_ids(session, user)
            creator_profiles = await _load_creator_profiles(
                session,
                [str(row.get("create_by") or "") for row in rows],
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="应用目录暂时不可用") from exc

    user_roles = set(normalize_visible_ids(user.role_ids)) | set(relation_roles)
    user_depts = set(normalize_visible_ids(user.dept_ids)) | set(relation_depts)
    visible: dict[str, dict[str, Any]] = {}
    for preset, row in rows_by_preset.items():
        app_id = str(row.get("id") or "")
        if not catalog_acl_allows(
            user_roles=user_roles,
            user_depts=user_depts,
            allowed_roles=allowed_roles.get(app_id, set()),
            allowed_depts=allowed_depts.get(app_id, set()),
        ):
            continue
        enriched = dict(row)
        creator_key = str(row.get("create_by") or "").strip()
        creator_name, creator_avatar = creator_profiles.get(
            creator_key,
            (creator_key or "未知", ""),
        )
        enriched["_creator_name"] = creator_name
        enriched["_creator_avatar"] = creator_avatar
        visible[preset] = enriched
    return visible


async def require_builtin_app_access(user: UserContext, preset: object) -> dict[str, Any]:
    normalized_preset = str(preset or "").strip().lower()
    spec = _SPECS_BY_PRESET.get(normalized_preset)
    if spec is None or get_builtin_assistant_by_preset(normalized_preset) is None:
        raise HTTPException(status_code=404, detail="系统应用不存在")
    row = (await _visible_rows_for_user(user)).get(normalized_preset)
    if row is not None:
        return _row_to_marketplace(row, spec)
    raise HTTPException(
        status_code=403,
        detail=f"你没有使用{spec.name}的权限，或管理员尚未启用该应用",
    )


async def list_builtin_apps(user: UserContext) -> list[dict[str, Any]]:
    """当前用户可见的内置智能体（广场列表）。

    与 require_builtin_app_access 共用同一套可见性判定，区别只是返回全部而非单个。
    上架记录缺失或被管理员停用的条目自然不会出现，无需额外过滤。
    """
    rows = await _visible_rows_for_user(user)
    items: list[dict[str, Any]] = []
    for preset, row in rows.items():
        spec = _SPECS_BY_PRESET.get(preset)
        if spec is None:
            continue
        items.append(_row_to_marketplace(row, spec))
    items.sort(key=lambda item: (item.get("orderNum") or item.get("order_num") or 0, item.get("appName") or ""))
    return items


async def require_thread_access(
    user: UserContext,
    thread_id: object,
    *,
    expected_scope: object = None,
) -> ChatThread:
    normalized_id = str(thread_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    async with async_session() as session:
        thread = await session.get(ChatThread, normalized_id)
        if not thread or str(thread.user_id) != str(user.user_id) or thread.app_id is not None:
            raise HTTPException(status_code=404, detail="会话不存在")

    actual_scope = thread_scope_from_origin(thread.origin)
    normalized_expected = normalize_thread_scope(expected_scope, allow_empty=True)
    if normalized_expected is not None and actual_scope != normalized_expected:
        # Do not reveal that a thread exists in another application scope.
        raise HTTPException(status_code=404, detail="会话不存在")
    if actual_scope in _SPECS_BY_PRESET:
        await require_builtin_app_access(user, actual_scope)
    return thread


async def require_message_access(user: UserContext, message_id: int) -> ChatThread:
    async with async_session() as session:
        thread = (
            await session.execute(
                select(ChatThread)
                .join(ChatMessage, ChatMessage.thread_id == ChatThread.id)
                .where(ChatMessage.id == int(message_id))
                .limit(1)
            )
        ).scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="消息不存在")
    if str(thread.user_id) != str(user.user_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    # 独立智能体运行页的会话带 app_id，不属于主对话/内置应用的 scope；但消息和会话
    # 都已按用户归属校验，允许用户给自己的运行消息提交反馈。
    if thread.app_id is not None:
        return thread
    return await require_thread_access(user, thread.id)


def scope_for_preset(preset: object) -> str:
    normalized = str(preset or "").strip().lower()
    return normalized if normalized in _SPECS_BY_PRESET else ORDINARY_THREAD_SCOPE


__all__ = [
    "BUILTIN_APP_SPECS",
    "BUILTIN_RUNTIME_KIND",
    "CATALOG_APP_TYPE",
    "CATALOG_APP_TYPES",
    "ORDINARY_THREAD_SCOPE",
    "builtin_preset_for_catalog_routes",
    "builtin_spec_for_catalog_routes",
    "catalog_acl_allows",
    "load_user_relation_ids",
    "normalize_catalog_route",
    "normalize_thread_scope",
    "normalize_visible_ids",
    "require_builtin_app_access",
    "require_message_access",
    "require_thread_access",
    "scope_for_preset",
    "thread_scope_from_origin",
]
