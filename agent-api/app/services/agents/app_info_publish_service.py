"""Publish approved agent-api apps into Java's app_info catalog.

Java owns marketplace listing and filtering. agent-api only writes the approved
snapshot plus visibility metadata into the shared app_info row.
"""
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from app.models import WorkflowApp, WorkflowVersion

logger = logging.getLogger(__name__)


def normalize_visible_ids(value: Any) -> list[str]:
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


def visible_ids_csv(value: Any) -> str:
    return ",".join(normalize_visible_ids(value))


def merge_required_model_ids(*groups: Any) -> list[str]:
    from app.services.workflows.workflow_model_requirements import parse_required_model_ids

    result: list[str] = []
    for group in groups:
        for model_id in parse_required_model_ids(group):
            if model_id not in result:
                result.append(model_id)
    return result


def _runtime_url(app: "WorkflowApp") -> str:
    app_id = str(getattr(app, "id", "") or "")
    return f"/agent/run/{app_id}"


def _parse_json_object(value: Optional[str]) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_app_info_form_options(
    *,
    existing_options: Optional[str],
    ai_app_type: str,
    source_app_id: str,
    published_version: int,
    visible_role_ids: list[str],
    visible_dept_ids: list[str],
) -> str:
    options = _parse_json_object(existing_options)
    options.update(
        {
            "aiAppType": ai_app_type,
            "sourceAppId": source_app_id,
            "publishedVersion": int(published_version or 0),
            "visibleRoleIds": normalize_visible_ids(visible_role_ids),
            "visibleDeptIds": normalize_visible_ids(visible_dept_ids),
        }
    )
    return json.dumps(options, ensure_ascii=False, separators=(",", ":"))


def build_app_info_column_values(
    app: "WorkflowApp",
    version: "WorkflowVersion",
    form_options: str,
    workflow_json: Optional[str] = None,
    required_model_ids: Optional[list[str]] = None,
    existing_open_type: Optional[str] = None,
    catalog_enabled: bool = True,
) -> dict[str, Any]:
    from app.services.workflows.workflow_model_requirements import extract_required_model_ids

    selected_roles = visible_ids_csv(version.visible_role_ids)
    selected_departs = visible_ids_csv(version.visible_dept_ids)
    runtime_url = _runtime_url(app)
    open_type = existing_open_type if existing_open_type in {"iframe", "_blank"} else "_blank"
    owner_user_id = str(app.owner_user_id or "").strip()
    if not owner_user_id:
        raise ValueError("智能体应用缺少 owner_user_id，不能写入 app_info 审计字段")
    return {
        "app_name": app.name,
        "name": app.name,
        "app_desc": app.description or "",
        "description": app.description or "",
        "app_remark": app.description or "",
        "app_icon": app.app_icon or "",
        "icon": app.app_icon or "",
        "app_category": app.app_category or "",
        "terminal_type": "pc,h5",
        "open_type": open_type,
        "pc_url": runtime_url,
        "h5_url": runtime_url,
        "app_type": "agent",
        "ai_app_type": app.ai_app_type,
        "status": "1" if catalog_enabled else "0",
        "form_options": form_options,
        "selected_roles": selected_roles,
        "selected_departs": selected_departs,
        "create_by": owner_user_id,
        "update_by": version.reviewed_by or version.submitted_by or owner_user_id,
        "tenant_id": app.tenant_id,
        "del_flag": 0,
        "use_models": ",".join(
            merge_required_model_ids(
                required_model_ids if required_model_ids is not None else extract_required_model_ids(workflow_json)
            )
        ),
    }


def build_visibility_rows(app_id: str, tenant_id: str, ids: list[str], id_column: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item_id in ids:
        text_id = str(item_id or "").strip()
        if text_id:
            rows.append({"app_id": app_id, id_column: text_id, "tenant_id": tenant_id or "0"})
    return rows


async def _table_columns(session, table_name: str) -> set[str]:
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                """
                SELECT COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                """
            ),
            {"table_name": table_name},
        )
    ).mappings().all()
    return {str(row["COLUMN_NAME"]) for row in rows}


async def _existing_app_info_settings(
    session,
    app_info_id: str,
    columns: set[str],
) -> tuple[Optional[str], Optional[str]]:
    from sqlalchemy import text

    selected_columns = [column for column in ("form_options", "open_type") if column in columns]
    if not selected_columns:
        return None, None
    row = (
        await session.execute(
            text(f"SELECT {', '.join(selected_columns)} FROM app_info WHERE id = :id LIMIT 1"),
            {"id": app_info_id},
        )
    ).mappings().first()
    if not row:
        return None, None
    form_options = str(row["form_options"]) if row.get("form_options") is not None else None
    open_type = str(row["open_type"]) if row.get("open_type") is not None else None
    return form_options, open_type


async def _knowledge_embedding_models_for_workflow(session, workflow_json: Any) -> list[str]:
    from sqlalchemy import text

    from app.services.workflows.workflow_model_requirements import extract_required_knowledge_ids

    knowledge_ids = extract_required_knowledge_ids(workflow_json)
    if not knowledge_ids:
        return []

    columns = await _table_columns(session, "ai_knowledge_base")
    if "id" not in columns or "embedding_model" not in columns:
        return []

    params = {f"id_{index}": knowledge_id for index, knowledge_id in enumerate(knowledge_ids)}
    placeholders = ", ".join(f":id_{index}" for index in range(len(knowledge_ids)))
    where_parts = [f"id IN ({placeholders})"]
    if "del_flag" in columns:
        where_parts.append("(del_flag = 0 OR del_flag IS NULL)")
    rows = (
        await session.execute(
            text(
                "SELECT id, embedding_model FROM ai_knowledge_base "
                f"WHERE {' AND '.join(where_parts)}"
            ),
            params,
        )
    ).mappings().all()
    by_id = {
        str(row["id"]): str(row["embedding_model"]).strip()
        for row in rows
        if row.get("id") is not None and row.get("embedding_model") is not None and str(row["embedding_model"]).strip()
    }
    return merge_required_model_ids([by_id.get(knowledge_id) for knowledge_id in knowledge_ids])


async def _referenced_published_definitions(session, workflow_json: Any, visited_app_ids: set[str]) -> list[tuple[str, str]]:
    from sqlalchemy import text

    from app.services.workflows.workflow_model_requirements import extract_referenced_app_ids

    refs = [app_id for app_id in extract_referenced_app_ids(workflow_json) if app_id not in visited_app_ids]
    if not refs:
        return []

    params = {f"id_{index}": app_id for index, app_id in enumerate(refs)}
    placeholders = ", ".join(f":id_{index}" for index in range(len(refs)))
    rows = (
        await session.execute(
            text(
                "SELECT a.id, a.ai_app_type, d.published_json "
                "FROM agent_workflow_app a "
                "LEFT JOIN agent_workflow_definition d ON d.app_id = a.id "
                f"WHERE a.id IN ({placeholders})"
            ),
            params,
        )
    ).mappings().all()
    published_by_id = {
        str(row["id"]): str(row["published_json"])
        for row in rows
        if row.get("id") is not None
        and row.get("published_json") is not None
        and str(row.get("ai_app_type") or "") in {"chatAgent", "simple", "workflow", "workflowTool"}
    }
    return [(app_id, published_by_id[app_id]) for app_id in refs if app_id in published_by_id]


async def resolve_workflow_required_model_ids(
    session,
    workflow_json: Any,
    _visited_app_ids: Optional[set[str]] = None,
) -> list[str]:
    from app.services.workflows.workflow_model_requirements import extract_required_model_ids

    visited_app_ids = _visited_app_ids if _visited_app_ids is not None else set()
    child_model_groups: list[list[str]] = []
    for app_id, child_workflow_json in await _referenced_published_definitions(session, workflow_json, visited_app_ids):
        if app_id in visited_app_ids:
            continue
        visited_app_ids.add(app_id)
        child_model_groups.append(
            await resolve_workflow_required_model_ids(session, child_workflow_json, visited_app_ids)
        )
    return merge_required_model_ids(
        extract_required_model_ids(workflow_json),
        await _knowledge_embedding_models_for_workflow(session, workflow_json),
        *child_model_groups,
    )


async def _sync_visibility_table(
    session,
    *,
    table_name: str,
    app_id: str,
    id_column: str,
    ids: list[str],
    tenant_id: str,
) -> None:
    from sqlalchemy import text

    columns = await _table_columns(session, table_name)
    if not columns:
        logger.info("%s table not found; skip visibility sync for app %s", table_name, app_id)
        return
    if "app_id" not in columns or id_column not in columns:
        logger.warning("%s table misses app_id or %s; skip visibility sync for app %s", table_name, id_column, app_id)
        return

    await session.execute(text(f"DELETE FROM `{table_name}` WHERE app_id = :app_id"), {"app_id": app_id})
    for row in build_visibility_rows(app_id, tenant_id, ids, id_column):
        insert_columns = ["app_id", id_column]
        values: dict[str, Any] = {"app_id": row["app_id"], id_column: row[id_column]}
        if "id" in columns:
            insert_columns.insert(0, "id")
            values["id"] = uuid.uuid4().hex
        if "tenant_id" in columns:
            insert_columns.append("tenant_id")
            values["tenant_id"] = row["tenant_id"]

        placeholders = ", ".join(f":{column}" for column in insert_columns)
        await session.execute(
            text(
                f"INSERT INTO `{table_name}` ({', '.join(f'`{column}`' for column in insert_columns)}) "
                f"VALUES ({placeholders})"
            ),
            values,
        )


async def sync_app_visibility(
    session,
    *,
    app_id: str,
    tenant_id: str,
    visible_role_ids: Any,
    visible_dept_ids: Any,
) -> None:
    await _sync_visibility_table(
        session,
        table_name="app_role",
        app_id=app_id,
        id_column="role_id",
        ids=normalize_visible_ids(visible_role_ids),
        tenant_id=tenant_id,
    )
    await _sync_visibility_table(
        session,
        table_name="app_dept",
        app_id=app_id,
        id_column="dept_id",
        ids=normalize_visible_ids(visible_dept_ids),
        tenant_id=tenant_id,
    )


async def upsert_app_info_for_approved_version(
    session,
    app: "WorkflowApp",
    version: "WorkflowVersion",
    *,
    catalog_enabled: bool = True,
) -> None:
    """Write the approved app snapshot into Java app_info if the table exists.

    The target table has drifted across Java migrations, so this method writes
    only columns that exist in the current database. The stable marketplace ACL
    contract is carried in form_options.visibleRoleIds/visibleDeptIds.
    """
    try:
        from sqlalchemy import text

        columns = await _table_columns(session, "app_info")
        if not columns:
            logger.info("app_info table not found; skip Java marketplace sync for app %s", app.id)
            return

        existing_options, existing_open_type = await _existing_app_info_settings(session, app.id, columns)
        form_options = build_app_info_form_options(
            existing_options=existing_options,
            ai_app_type=app.ai_app_type,
            source_app_id=app.id,
            published_version=version.version_no,
            visible_role_ids=normalize_visible_ids(version.visible_role_ids),
            visible_dept_ids=normalize_visible_ids(version.visible_dept_ids),
        )

        values: dict[str, Any] = {"id": app.id}
        required_model_ids = await resolve_workflow_required_model_ids(session, version.definition_json)
        candidates = build_app_info_column_values(
            app,
            version,
            form_options,
            version.definition_json,
            required_model_ids=required_model_ids,
            existing_open_type=existing_open_type,
            catalog_enabled=catalog_enabled,
        )
        for column, value in candidates.items():
            if column in columns:
                values[column] = value

        if "create_time" in columns:
            values["create_time"] = text("NOW()")
        if "update_time" in columns:
            values["update_time"] = text("NOW()")

        insert_columns = list(values.keys())
        placeholders = []
        params: dict[str, Any] = {}
        for column in insert_columns:
            value = values[column]
            if hasattr(value, "text"):
                placeholders.append(str(value))
            else:
                placeholders.append(f":{column}")
                params[column] = value

        # AI 应用以 agent-api 的 owner_user_id 为唯一创建者事实源。历史版本曾把用户名
        # 写进 create_by，Java 广场按用户 ID 查询时会让创建者看不到自己的应用；重发时顺带纠正。
        update_columns = [c for c in insert_columns if c not in {"id", "create_time"}]
        update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_columns)
        sql = (
            f"INSERT INTO app_info ({', '.join(f'`{c}`' for c in insert_columns)}) "
            f"VALUES ({', '.join(placeholders)}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}"
        )
        await session.execute(text(sql), params)
        await sync_app_visibility(
            session,
            app_id=app.id,
            tenant_id=app.tenant_id or "0",
            visible_role_ids=version.visible_role_ids,
            visible_dept_ids=version.visible_dept_ids,
        )
    except Exception:
        logger.exception("Failed to sync approved app %s into app_info", app.id)
        raise


async def sync_app_info_for_approved_version(session, app: "WorkflowApp", version: "WorkflowVersion") -> str:
    """Every approved publish is a marketplace publish.

    Publisher API and iframe exposure are now controlled independently by the
    application-level public configuration, never by a version channel.
    """
    await upsert_app_info_for_approved_version(session, app, version, catalog_enabled=True)
    return "upserted"


async def set_app_info_catalog_status(session, app_id: str, *, enabled: bool) -> None:
    """同步 Java 应用目录的可见状态；目录表不存在或缺少状态列时安全跳过。"""
    from sqlalchemy import text

    columns = await _table_columns(session, "app_info")
    if "status" not in columns:
        logger.info("app_info.status 不存在，跳过目录状态同步 app=%s", app_id)
        return
    assignments = ["status = :status"]
    if "update_time" in columns:
        # Python 直连写库不会经过 Java 的自动填充；更新该字段让目录同步任务及时感知。
        assignments.append("update_time = NOW()")
    await session.execute(
        text(f"UPDATE app_info SET {', '.join(assignments)} WHERE id = :id"),
        {"id": app_id, "status": "1" if enabled else "0"},
    )


async def get_app_info_catalog_enabled(session, app_id: str) -> Optional[bool]:
    """返回 Java 目录启用态；目录记录不存在/结构不支持时返回 None。"""
    from sqlalchemy import text

    columns = await _table_columns(session, "app_info")
    if "status" not in columns:
        return None
    row = (
        await session.execute(
            text("SELECT status FROM app_info WHERE id = :id LIMIT 1"),
            {"id": app_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return str(row.get("status") or "0") == "1"


def _catalog_retire_assignments(columns: set[str], *, alias: str = "") -> tuple[list[str], dict[str, Any]]:
    prefix = f"{alias}." if alias else ""
    assignments: list[str] = []
    params: dict[str, Any] = {}
    if "status" in columns:
        assignments.append(f"{prefix}status = :status")
        params["status"] = "0"
    if "del_flag" in columns:
        assignments.append(f"{prefix}del_flag = :del_flag")
        # app_info.del_flag 是 varchar，写整数在部分驱动/比较路径下不会被当成已删除。
        params["del_flag"] = "1"
    return assignments, params


async def retire_app_info_catalog_entry(session, app_id: str) -> int:
    """删除运行时应用时，同步隐藏并逻辑删除 Java 目录记录。"""
    from sqlalchemy import text

    columns = await _table_columns(session, "app_info")
    if not columns:
        # information_schema 偶发空结果时仍按已知列更新，避免工作流已删、广场残留。
        logger.warning("app_info columns empty, still attempting catalog retire app=%s", app_id)
        columns = {"id", "status", "del_flag", "update_time", "form_options"}
    assignments, params = _catalog_retire_assignments(columns)
    if not assignments:
        logger.info("app_info 无 status/del_flag，跳过目录删除同步 app=%s", app_id)
        return 0
    if "update_time" in columns:
        assignments.append("update_time = NOW()")
    params["id"] = app_id
    conditions = ["id = :id"]
    if "form_options" in columns:
        conditions.append("JSON_UNQUOTE(JSON_EXTRACT(form_options, '$.sourceAppId')) = :id")
    result = await session.execute(
        text(f"UPDATE app_info SET {', '.join(assignments)} WHERE {' OR '.join(conditions)}"),
        params,
    )
    rowcount = int(getattr(result, "rowcount", 0) or 0)
    if rowcount <= 0:
        logger.warning("retire app_info matched 0 rows app=%s", app_id)
    else:
        logger.info("retired app_info catalog app=%s rowcount=%s", app_id, rowcount)
    return rowcount


async def retire_orphaned_agent_catalog_entries(session) -> int:
    """隐藏已经没有 agent_workflow_app 的运行时目录行（历史删除未同步的残留）。"""
    from sqlalchemy import text

    columns = await _table_columns(session, "app_info")
    workflow_columns = await _table_columns(session, "agent_workflow_app")
    if "id" not in workflow_columns:
        logger.info("agent_workflow_app 不存在，跳过目录孤儿清理")
        return 0
    if not columns:
        columns = {"id", "status", "del_flag", "update_time", "form_options", "pc_url"}
    assignments, params = _catalog_retire_assignments(columns, alias="a")
    if not assignments:
        return 0
    if "update_time" in columns:
        assignments.append("a.update_time = NOW()")

    runtime_url_pred = "a.pc_url LIKE '/agent/run/%'" if "pc_url" in columns else "0=1"
    has_source = "form_options" in columns
    source_expr = "JSON_UNQUOTE(JSON_EXTRACT(a.form_options, '$.sourceAppId'))" if has_source else "NULL"
    source_join = f"LEFT JOIN agent_workflow_app w_src ON w_src.id = {source_expr}" if has_source else ""
    source_missing = "w_src.id IS NULL" if has_source else "1=1"
    source_present = f"{source_expr} IS NOT NULL" if has_source else "0=1"
    del_pred = "(a.del_flag = '0' OR a.del_flag IS NULL OR a.del_flag = '')" if "del_flag" in columns else "1=1"
    result = await session.execute(
        text(
            f"""
            UPDATE app_info a
            LEFT JOIN agent_workflow_app w_id ON w_id.id = a.id
            {source_join}
            SET {', '.join(assignments)}
            WHERE w_id.id IS NULL
              AND {source_missing}
              AND ({runtime_url_pred} OR {source_present})
              AND {del_pred}
            """
        ),
        params,
    )
    rowcount = int(getattr(result, "rowcount", 0) or 0)
    if rowcount:
        logger.info("retired %s orphaned app_info catalog rows", rowcount)
    return rowcount
