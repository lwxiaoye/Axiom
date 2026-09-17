"""Administrator-configured Harness page, ACL and history-scope contracts."""

import inspect

import pytest
from fastapi import HTTPException

from app.core.auth import UserContext
from app.services.agents import recommendation_index
from app.services.chat import builtin_app_access as access


@pytest.mark.parametrize(
    ("pc_url", "h5_url", "expected"),
    [
        ("/center/chat/ppt", "/center/chat/ppt", "presentation"),
        ("/center/chat/campus/", "", "campus_services"),
        ("", "/center/chat/campus", "campus_services"),
        ("/center/chat/ppt?from=admin", "", None),
        ("https://example.test/center/chat/ppt", "", None),
        ("/center/chat/ppt", "/center/chat/campus", None),
        ("/center/chat/other", "", None),
    ],
)
def test_catalog_identity_is_external_route_not_name_or_generated_id(
    pc_url: str,
    h5_url: str,
    expected: str | None,
) -> None:
    assert access.builtin_preset_for_catalog_routes(pc_url, h5_url) == expected


def test_only_active_catalog_rows_configure_the_pages() -> None:
    rows = [
        {
            "id": "manual-ppt",
            "app_name": "管理员可自定义名称",
            "app_type": "external",
            "status": "1",
            "del_flag": "0",
            "pc_url": "/center/chat/ppt",
            "h5_url": "/center/chat/ppt",
        },
        {
            "id": "wrong-ai-row",
            "app_name": "演示文稿助手",
            "app_type": "ai",
            "status": "1",
            "del_flag": "0",
            "pc_url": "/center/chat/ppt",
            "h5_url": "/center/chat/ppt",
        },
        {
            "id": "disabled-campus",
            "app_name": "校园百事通",
            "app_type": "external",
            "status": "0",
            "del_flag": "0",
            "pc_url": "/center/chat/campus",
            "h5_url": "/center/chat/campus",
        },
        {
            "id": "custom-interview",
            "app_name": "面试助手",
            "app_type": "custom",
            "status": "1",
            "del_flag": "0",
            "pc_url": "/center/chat/interview",
            "h5_url": "/center/chat/interview",
        },
        {
            "id": "agent-campus",
            "app_name": "校园百事通",
            "app_type": "agent",
            "status": "1",
            "del_flag": "0",
            "pc_url": "/center/chat/campus",
            "h5_url": "/center/chat/campus",
        },
    ]

    configured = access._active_catalog_rows_by_preset(rows)
    assert configured == {"presentation": rows[0], "interview": rows[3]}


def test_duplicate_enabled_external_rows_fail_closed() -> None:
    row = {
        "app_name": "演示文稿助手",
        "app_type": "external",
        "status": "1",
        "del_flag": "0",
        "pc_url": "/center/chat/ppt",
        "h5_url": "/center/chat/ppt",
    }
    with pytest.raises(HTTPException) as exc_info:
        access._active_catalog_rows_by_preset([
            {"id": "one", **row},
            {"id": "two", **row},
        ])
    assert exc_info.value.status_code == 503
    assert "只保留一条" in str(exc_info.value.detail)


def test_access_service_never_creates_or_repairs_catalog_rows() -> None:
    source = inspect.getsource(access)
    assert not hasattr(access, "ensure_builtin_apps")
    assert not hasattr(access, "catalog_app_id")
    assert "INSERT INTO app_info" not in source
    assert "UPDATE app_info" not in source


@pytest.mark.parametrize(
    ("roles", "depts", "allowed_roles", "allowed_depts", "expected"),
    [
        (set(), set(), set(), set(), True),
        ({"teacher"}, set(), {"teacher"}, set(), True),
        (set(), {"dept-a"}, set(), {"dept-a"}, True),
        ({"student"}, {"dept-b"}, {"teacher"}, {"dept-a"}, False),
    ],
)
def test_catalog_acl_uses_empty_all_otherwise_role_or_department(
    roles: set[str],
    depts: set[str],
    allowed_roles: set[str],
    allowed_depts: set[str],
    expected: bool,
) -> None:
    assert access.catalog_acl_allows(
        user_roles=roles,
        user_depts=depts,
        allowed_roles=allowed_roles,
        allowed_depts=allowed_depts,
    ) is expected


def test_creator_fields_come_from_the_admin_catalog_creator() -> None:
    presentation = next(
        spec for spec in access.BUILTIN_APP_SPECS if spec.preset == "presentation"
    )
    item = access._row_to_marketplace(
        {
            "id": "manual-ppt",
            "app_name": "演示文稿助手",
            "app_type": "external",
            "status": "1",
            "order_num": 1,
            "create_by": "creator-login",
            "_creator_name": "创建者姓名",
            "_creator_avatar": "/avatar/creator.png",
        },
        presentation,
    )
    assert item["catalogAppType"] == "external"
    assert item["createBy"] == "creator-login"
    assert item["createByName"] == "创建者姓名"
    assert item["createByAvatar"] == "/avatar/creator.png"


def test_custom_catalog_source_is_preserved_on_marketplace_projection() -> None:
    interview = next(spec for spec in access.BUILTIN_APP_SPECS if spec.preset == "interview")
    item = access._row_to_marketplace(
        {
            "id": "custom-interview",
            "app_name": "面试助手",
            "app_type": "custom",
            "status": "1",
            "create_by": "admin",
            "_creator_name": "管理员",
        },
        interview,
    )
    assert item["catalogAppType"] == "custom"


def test_thread_scopes_keep_main_presentation_and_campus_separate() -> None:
    assert access.thread_scope_from_origin(None) == "ordinary"
    assert access.thread_scope_from_origin("presentation") == "presentation"
    assert access.thread_scope_from_origin("campus_services") == "campus_services"
    assert access.thread_scope_from_origin("side_chat") == "side_chat"
    assert access.normalize_thread_scope("ordinary") == "ordinary"
    assert access.normalize_thread_scope("presentation") == "presentation"
    assert access.normalize_thread_scope("campus_services") == "campus_services"

    with pytest.raises(HTTPException) as exc_info:
        access.normalize_thread_scope("side_chat")
    assert exc_info.value.status_code == 422


def test_catalog_contract_has_standalone_routes_and_external_type() -> None:
    by_preset = {spec.preset: spec for spec in access.BUILTIN_APP_SPECS}
    assert set(by_preset) == {"presentation", "campus_services", "interview"}
    assert by_preset["presentation"].route == "/center/chat/ppt"
    assert by_preset["campus_services"].route == "/center/chat/campus"
    assert by_preset["interview"].route == "/center/chat/interview"
    assert access.CATALOG_APP_TYPE == "external"
    assert access.CATALOG_APP_TYPES == frozenset({"external", "custom"})


def test_route_managed_apps_ignore_tenant_for_recommendation_visibility() -> None:
    user = UserContext(user_id="u1", username="u1", tenant_id="tenant-b")
    common = {
        "user": user,
        "tenant_id": "tenant-a",
        "owner_user_id": "creator",
        "role_ids": [],
        "dept_ids": [],
        "user_roles": set(),
        "user_depts": set(),
        "internal_workflow": False,
    }
    assert recommendation_index._visibility_allows(**common, global_catalog=False) is False
    assert recommendation_index._visibility_allows(**common, global_catalog=True) is True
