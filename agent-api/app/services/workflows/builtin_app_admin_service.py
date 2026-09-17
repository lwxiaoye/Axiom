"""Project existing catalogue assistants into the agent management directory."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.chat import builtin_app_access as catalog
from app.services.chat.builtin_assistants.registry import get_builtin_assistant_by_preset


MANAGED_BUILTIN_PREFIX = "builtin-catalog:"


def is_managed_builtin_id(app_id: str) -> bool:
    return app_id.startswith(MANAGED_BUILTIN_PREFIX)


async def list_managed_builtins(
    session: Any,
    *,
    keyword: str | None = None,
    status: str | None = None,
    owner_user_id: str | None = None,
) -> list[dict[str, Any]]:
    # Keep disabled records manageable, and surface duplicates for administrators to fix.
    # Runtime access still uses the catalogue's fail-closed duplicate/ACL checks.
    rows = [
        row for row in await catalog._load_catalog_rows(session)
        if catalog._not_deleted(row.get("del_flag")) and catalog._is_catalog_app(row)
    ]
    profiles = await catalog._load_creator_profiles(session, [str(row.get("create_by") or "") for row in rows])
    records = []
    for row in rows:
        spec = catalog.builtin_spec_for_catalog_routes(row.get("pc_url"), row.get("h5_url"))
        if spec is None:
            continue
        creator = str(row.get("create_by") or "")
        enabled = catalog._enabled(row.get("status"))
        record = {
            "id": f"{MANAGED_BUILTIN_PREFIX}{row['id']}",
            "catalogAppId": str(row["id"]),
            "aiAppType": "builtin",
            "builtinPreset": spec.preset,
            "name": str(row.get("app_name") or spec.name),
            "description": str(row.get("app_remark") or spec.description),
            "appIcon": str(row.get("app_icon") or spec.icon),
            "appCategory": str(row.get("app_category") or spec.category),
            "ownerUserId": creator,
            "ownerUsername": profiles.get(creator, (creator, ""))[0],
            "status": "published" if enabled else "unpublished",
            "createdAt": row["create_time"].isoformat() if row.get("create_time") else None,
            "entryPath": spec.route,
        }
        if status and record["status"] != status:
            continue
        if owner_user_id and creator != owner_user_id:
            continue
        if keyword and keyword.casefold() not in " ".join(
            str(record[key]) for key in ("name", "description", "ownerUsername", "ownerUserId")
        ).casefold():
            continue
        records.append((spec.order_num, record))
    return [record for _, record in sorted(records, key=lambda item: (item[0], item[1]["id"]))]


async def get_managed_builtin(session: Any, app_id: str) -> dict[str, Any]:
    records = await list_managed_builtins(session)
    record = next((item for item in records if item["id"] == app_id), None)
    if record is None:
        raise HTTPException(404, "定制智能体不存在")
    return record


def managed_builtin_origin(record: dict[str, Any]) -> str:
    definition = get_builtin_assistant_by_preset(record["builtinPreset"])
    if definition is None:
        raise HTTPException(404, "定制智能体不存在")
    return definition["origin"]


def build_builtin_admin_detail(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "app": record,
        "summary": {"model": "", "nodeTypes": [], "dependencyIds": [], "publishedVersion": 0},
        "versions": [],
        "audits": [],
    }
