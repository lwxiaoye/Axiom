from datetime import datetime

from app.services.audit.audit_service import AUDIT_CATEGORIES, _date_boundary, _record, _tool_category


def test_audit_categories_cover_required_operations():
    assert {
        "login",
        "knowledge_access",
        "model_call",
        "plugin_call",
        "database_query",
        "export",
    } <= AUDIT_CATEGORIES


def test_runtime_tool_projection_separates_database_queries():
    assert _tool_category("query_database") == "database_query"
    assert _tool_category("execute_in_sandbox") == "plugin_call"


def test_date_only_end_boundary_includes_the_whole_day():
    assert _date_boundary("2026-09-10", end=False).isoformat().endswith("T00:00:00")
    assert _date_boundary("2026-09-10", end=True).isoformat().endswith("T23:59:59.999999")


def test_audit_record_uses_monitor_log_field_names_and_time_format():
    record = _record(
        event_id="event-1", category="model_call", action="模型调用", resource="model-a",
        status="succeeded", actor_user_id="user-1", actor_username="张三", ip="127.0.0.1",
        detail="", source="runtime", create_time=datetime(2026, 9, 11, 8, 9, 10),
    )

    assert record["userid"] == "user-1"
    assert record["username"] == "张三"
    assert record["createTime"] == "2026-09-11 08:09:10"
