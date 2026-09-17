"""Validate and migrate the 23 marketplace applications to capability categories.

Dry-run is the default. Use ``--apply`` only after reviewing the resolved IDs and
the generated JSON backup. The transaction aborts on missing or duplicate names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session, engine


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "agent_market_category_manifest.json"
CAPABILITY_LABELS = {
    "communication": "沟通交互",
    "document_knowledge": "文档与知识",
    "data_table": "数据与表格",
    "content_creation": "内容创作",
    "planning_structure": "规划与结构",
    "developer_automation": "开发与自动化",
    "image_multimedia": "图像与多媒体",
    "other": "综合/其他",
}


def load_manifest() -> dict[str, list[str]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if list(manifest) != list(CAPABILITY_LABELS):
        raise RuntimeError("manifest category order differs from the canonical capability order")
    names = [name for category_names in manifest.values() for name in category_names]
    duplicates = [name for name, count in Counter(names).items() if count != 1]
    if len(names) != 23 or duplicates:
        raise RuntimeError(f"manifest must contain 23 unique names, got {len(names)}; duplicates={duplicates}")
    return manifest


async def table_columns(session: Any, table_name: str) -> set[str]:
    result = await session.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
        ),
        {"table_name": table_name},
    )
    return {str(row[0]) for row in result.fetchall()}


async def resolve_apps(session: Any, manifest: dict[str, list[str]]) -> list[dict[str, Any]]:
    target_by_name = {
        name: category
        for category, names in manifest.items()
        for name in names
    }
    statement = text(
        "SELECT id, app_name, app_category FROM app_info "
        "WHERE app_name IN :names AND (del_flag IS NULL OR CAST(del_flag AS CHAR) = '0')"
    ).bindparams(bindparam("names", expanding=True))
    rows = (await session.execute(statement, {"names": list(target_by_name)})).mappings().all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["app_name"]), []).append(dict(row))

    missing = sorted(set(target_by_name) - set(grouped))
    duplicates = sorted(name for name, matched in grouped.items() if len(matched) != 1)
    if missing or duplicates:
        raise RuntimeError(f"migration aborted: missing={missing}; duplicate_names={duplicates}")

    return [
        {
            "id": grouped[name][0]["id"],
            "app_name": name,
            "old_category": grouped[name][0].get("app_category"),
            "new_category": category,
        }
        for category, names in manifest.items()
        for name in names
    ]


async def sync_dictionary(session: Any) -> None:
    dict_columns = await table_columns(session, "sys_dict")
    item_columns = await table_columns(session, "sys_dict_item")
    required = {"id", "dict_code"}
    required_items = {"id", "dict_id", "item_text", "item_value"}
    if not required.issubset(dict_columns) or not required_items.issubset(item_columns):
        raise RuntimeError("sys_dict/sys_dict_item schema is missing required columns")

    dict_row = (
        await session.execute(
            text("SELECT id FROM sys_dict WHERE dict_code = 'app_category' LIMIT 1")
        )
    ).mappings().first()
    if not dict_row:
        raise RuntimeError("app_category dictionary not found")
    dict_id = str(dict_row["id"])

    for sort_order, (value, label) in enumerate(CAPABILITY_LABELS.items(), start=1):
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM sys_dict_item "
                    "WHERE dict_id = :dict_id AND item_value = :item_value LIMIT 1"
                ),
                {"dict_id": dict_id, "item_value": value},
            )
        ).mappings().first()
        assignments = ["item_text = :item_text"]
        params: dict[str, Any] = {"dict_id": dict_id, "item_value": value, "item_text": label}
        if "sort_order" in item_columns:
            assignments.append("sort_order = :sort_order")
            params["sort_order"] = sort_order
        if "status" in item_columns:
            assignments.append("status = '1'")
        if "del_flag" in item_columns:
            assignments.append("del_flag = '0'")
        if "update_time" in item_columns:
            assignments.append("update_time = NOW()")

        if existing:
            params["id"] = existing["id"]
            await session.execute(
                text(f"UPDATE sys_dict_item SET {', '.join(assignments)} WHERE id = :id"),
                params,
            )
            continue

        columns = ["id", "dict_id", "item_text", "item_value"]
        values = [":id", ":dict_id", ":item_text", ":item_value"]
        params["id"] = uuid4().hex
        optional_values = {
            "sort_order": ":sort_order",
            "status": "'1'",
            "del_flag": "'0'",
            "create_by": "'capability_migration'",
            "create_time": "NOW()",
        }
        for column, value_sql in optional_values.items():
            if column in item_columns:
                columns.append(column)
                values.append(value_sql)
        await session.execute(
            text(
                f"INSERT INTO sys_dict_item ({', '.join(f'`{column}`' for column in columns)}) "
                f"VALUES ({', '.join(values)})"
            ),
            params,
        )

    canonical = list(CAPABILITY_LABELS)
    active_categories = (
        await session.execute(
            text(
                "SELECT DISTINCT app_category FROM app_info "
                "WHERE (del_flag IS NULL OR CAST(del_flag AS CHAR) = '0') "
                "AND (status IS NULL OR CAST(status AS CHAR) = '1')"
            )
        )
    ).scalars().all()
    legacy_active = sorted(
        str(value).strip()
        for value in active_categories
        if str(value or "").strip() not in CAPABILITY_LABELS
    )
    if legacy_active:
        raise RuntimeError(
            f"active applications still use legacy categories {legacy_active}; old dictionary items were not disabled"
        )

    if "status" in item_columns:
        deactivate = text(
            "UPDATE sys_dict_item SET status = '0' "
            "WHERE dict_id = :dict_id AND item_value NOT IN :canonical"
        ).bindparams(bindparam("canonical", expanding=True))
        await session.execute(deactivate, {"dict_id": dict_id, "canonical": canonical})


async def run(*, apply: bool, backup_path: Path | None) -> None:
    manifest = load_manifest()
    async with async_session() as session:
        resolved = await resolve_apps(session, manifest)
        print(json.dumps(resolved, ensure_ascii=False, indent=2, default=str))
        print("counts:", dict(Counter(item["new_category"] for item in resolved)))
        if not apply:
            print("dry-run complete; no database changes were made")
            return

        backup = backup_path or (
            SCRIPT_DIR / "backups" / f"agent-market-categories-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(resolved, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        await session.rollback()
        async with session.begin():
            resolved = await resolve_apps(session, manifest)
            for item in resolved:
                await session.execute(
                    text("UPDATE app_info SET app_category = :category WHERE id = :id"),
                    {"category": item["new_category"], "id": item["id"]},
                )
            await sync_dictionary(session)

            readback = await resolve_apps(session, manifest)
            mismatches = [item for item in readback if item["old_category"] != item["new_category"]]
            if mismatches:
                raise RuntimeError(f"readback mismatch; transaction rolled back: {mismatches}")

        print(f"migration committed; backup={backup}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the validated migration")
    parser.add_argument("--backup-file", type=Path, help="write the pre-migration values to this JSON file")
    args = parser.parse_args()
    async def execute() -> None:
        try:
            await run(apply=args.apply, backup_path=args.backup_file)
        finally:
            await engine.dispose()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
