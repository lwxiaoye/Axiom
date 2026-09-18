"""广场目录（自建已发布智能体）的离线回归：目录行结构与空集短路。"""
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from app.core.auth import UserContext
from app.services.workflows import marketplace_catalog_service as catalog

MEMBER = UserContext(user_id="u-member", username="zhangsan", real_name="张三")


class MarketplaceCatalogRowTests(IsolatedAsyncioTestCase):
    def test_row_matches_app_info_marketplace_contract(self):
        app = SimpleNamespace(
            id="app-9", name="选课助手", description="帮你选课", app_icon="", app_category="study",
            ai_app_type="chatAgent", owner_user_id="u-owner", owner_username="owner", create_time=None, published_at=None,
        )
        row = catalog._to_marketplace_row(app, 3, ("王五", "/avatar.png"))
        self.assertEqual(row["pcUrl"], "/agent/run/app-9")
        self.assertEqual(row["appName"], "选课助手")
        self.assertEqual(row["createBy"], "u-owner")
        self.assertEqual(row["createByName"], "王五")
        self.assertEqual(row["createByAvatar"], "/avatar.png")
        self.assertEqual(json.loads(row["formOptions"]), {"aiAppType": "chatAgent", "sourceAppId": "app-9", "publishedVersion": 3})
        self.assertEqual(row["runtimeKind"], "workflow_app")

    async def test_empty_allowed_set_short_circuits_without_db(self):
        with mock.patch.object(catalog, "list_callable_subagent_ids", new=mock.AsyncMock(return_value=set())):
            self.assertEqual(await catalog.list_published_marketplace_apps(MEMBER), [])
