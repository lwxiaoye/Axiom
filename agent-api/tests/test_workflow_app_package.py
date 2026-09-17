import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.services.workflows.app_package_service import (
    PACKAGE_FORMAT,
    PACKAGE_VERSION,
    build_export_package,
    make_import_name,
    make_copy_name,
    parse_import_package,
)


class WorkflowAppPackageTest(unittest.TestCase):
    def test_export_package_omits_identity_owner_and_publication_state(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="chatAgent",
            name="课程助手",
            description="帮学生查课",
            app_category="edu",
            app_icon="https://example.test/icon.png",
            config_json='{"model":"gpt"}',
            status="published",
            owner_user_id="u-1",
            owner_username="owner",
            published_by="u-1",
        )
        definition = SimpleNamespace(
            draft_json='{"nodes":[]}',
            published_json='{"nodes":[{"id":"live"}]}',
            published_version=3,
            status="published",
        )

        package = build_export_package(app, definition)

        self.assertEqual(package["format"], PACKAGE_FORMAT)
        self.assertEqual(package["version"], PACKAGE_VERSION)
        self.assertEqual(package["app"]["aiAppType"], "chatAgent")
        self.assertEqual(package["definition"]["publishedVersion"], 3)
        encoded = json.dumps(package, ensure_ascii=False)
        self.assertNotIn("app-1", encoded)
        self.assertNotIn("ownerUserId", encoded)
        self.assertNotIn("publishedBy", encoded)
        self.assertNotIn('"status": "published"', encoded)

    def test_parse_import_package_rejects_tool_packages(self):
        payload = {
            "format": PACKAGE_FORMAT,
            "version": PACKAGE_VERSION,
            "app": {"aiAppType": "workflowTool", "name": "工具", "configJson": "{}"},
            "definition": {},
        }

        with self.assertRaises(HTTPException) as ctx:
            parse_import_package(json.dumps(payload).encode("utf-8"))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("智能体", str(ctx.exception.detail))

    def test_parse_import_package_accepts_json_and_normalizes_definition(self):
        payload = {
            "format": PACKAGE_FORMAT,
            "version": PACKAGE_VERSION,
            "app": {"aiAppType": "workflow", "name": "流程助手", "description": "desc"},
            "definition": {"publishedJson": '{"nodes":[{"id":"live"}]}'},
        }

        parsed = parse_import_package(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(parsed["app"]["name"], "流程助手")
        self.assertEqual(parsed["app"]["configJson"], "{}")
        self.assertEqual(parsed["definition"]["draftJson"], '{"nodes":[{"id":"live"}]}')
        self.assertIsNone(parsed["definition"]["publishedJson"])

    def test_make_copy_name_uses_existing_name(self):
        self.assertEqual(make_copy_name("课程助手"), "课程助手 副本")
        self.assertEqual(make_copy_name(""), "未命名智能体 副本")

    def test_make_import_name_appends_import_timestamp(self):
        self.assertEqual(make_import_name("课程助手", "20260720114530"), "课程助手_导入_20260720114530")
        self.assertEqual(make_import_name("", "20260720114530"), "未命名智能体_导入_20260720114530")


if __name__ == "__main__":
    unittest.main()
