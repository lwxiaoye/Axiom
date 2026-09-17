import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agents.app_info_publish_service import build_visibility_rows


class AppInfoVisibilitySyncTest(unittest.TestCase):
    def test_build_visibility_rows_sets_app_id_target_id_and_tenant(self):
        rows = build_visibility_rows("app-1", "tenant-1", ["role-a", "role-b"], "role_id")

        self.assertEqual(
            rows,
            [
                {"app_id": "app-1", "role_id": "role-a", "tenant_id": "tenant-1"},
                {"app_id": "app-1", "role_id": "role-b", "tenant_id": "tenant-1"},
            ],
        )

    def test_build_visibility_rows_skips_empty_ids(self):
        self.assertEqual(
            build_visibility_rows("app-1", "0", ["", "dept-a"], "dept_id"),
            [{"app_id": "app-1", "dept_id": "dept-a", "tenant_id": "0"}],
        )


class AppInfoPublishChannelSyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_published_version_always_upserts_marketplace_catalog(self):
        from app.services.agents.app_info_publish_service import sync_app_info_for_approved_version

        app = SimpleNamespace(id="app-api-only")
        version = SimpleNamespace(publish_channels='["api"]')
        with (
            patch(
                "app.services.agents.app_info_publish_service.upsert_app_info_for_approved_version",
                new_callable=AsyncMock,
            ) as upsert,
            patch(
                "app.services.agents.app_info_publish_service.retire_app_info_catalog_entry",
                new_callable=AsyncMock,
            ) as retire,
        ):
            result = await sync_app_info_for_approved_version(object(), app, version)

        self.assertEqual(result, "upserted")
        upsert.assert_awaited_once()
        retire.assert_not_awaited()

    async def test_marketplace_version_upserts_catalog(self):
        from app.services.agents.app_info_publish_service import sync_app_info_for_approved_version

        app = SimpleNamespace(id="app-marketplace")
        version = SimpleNamespace(publish_channels='["marketplace", "api"]')
        with (
            patch(
                "app.services.agents.app_info_publish_service.upsert_app_info_for_approved_version",
                new_callable=AsyncMock,
            ) as upsert,
            patch(
                "app.services.agents.app_info_publish_service.retire_app_info_catalog_entry",
                new_callable=AsyncMock,
            ) as retire,
        ):
            result = await sync_app_info_for_approved_version(object(), app, version)

        self.assertEqual(result, "upserted")
        upsert.assert_awaited_once()
        retire.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
