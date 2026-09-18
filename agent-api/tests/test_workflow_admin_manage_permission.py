import asyncio
import unittest
from unittest import mock

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import workflow as wf


def _run(coro):
    return asyncio.run(coro)


class WorkflowAdminManagePermissionTest(unittest.TestCase):
    def test_nested_menu_path_grants_agent_manage_page(self):
        menus = [
            {
                "path": "/root",
                "children": [
                    {"path": "/workflow/review"},
                    {"path": "/workflow/manage"},
                ],
            }
        ]

        self.assertTrue(wf._menu_tree_contains_path(menus, "/workflow/manage"))

    def test_non_platform_admin_with_manage_page_permission_is_allowed(self):
        user = UserContext(user_id="u1", username="lisi", access_token="token-1")

        with mock.patch.object(wf, "_auth_api_user_has_menu_path", new=mock.AsyncMock(return_value=True)):
            _run(wf._require_agent_manage_permission(user))

    def test_non_platform_admin_without_manage_page_permission_is_rejected(self):
        user = UserContext(user_id="u1", username="lisi", access_token="token-1")

        with mock.patch.object(wf, "_auth_api_user_has_menu_path", new=mock.AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as ctx:
                _run(wf._require_agent_manage_permission(user))

        self.assertEqual(ctx.exception.status_code, 403)

    def test_platform_admin_does_not_call_auth_api_permission_service(self):
        user = UserContext(user_id="admin-id", username="admin")

        auth_api_check = mock.AsyncMock(return_value=False)
        with mock.patch.object(wf, "_auth_api_user_has_menu_path", new=auth_api_check):
            _run(wf._require_agent_manage_permission(user))

        auth_api_check.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
