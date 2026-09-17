import json
import importlib.util
import unittest

from app.services.agents.app_info_publish_service import (
    build_app_info_form_options,
    normalize_visible_ids,
)


class AppInfoPublishPayloadTest(unittest.TestCase):
    def test_normalize_visible_ids_accepts_lists_strings_and_objects(self):
        self.assertEqual(
            normalize_visible_ids([" role-a ", {"id": "role-b"}, {"value": "role-c"}, "", None]),
            ["role-a", "role-b", "role-c"],
        )
        self.assertEqual(normalize_visible_ids("dept-a, dept-b,,"), ["dept-a", "dept-b"])
        self.assertEqual(normalize_visible_ids('["json-a", "json-b"]'), ["json-a", "json-b"])

    def test_build_app_info_form_options_preserves_existing_json_and_visibility(self):
        existing = json.dumps({"legacy": True, "aiAppType": "old"}, ensure_ascii=False)

        result = build_app_info_form_options(
            existing_options=existing,
            ai_app_type="workflow",
            source_app_id="app-1",
            published_version=3,
            visible_role_ids=["role-a", "role-b"],
            visible_dept_ids=["dept-a"],
        )

        self.assertEqual(
            json.loads(result),
            {
                "legacy": True,
                "aiAppType": "workflow",
                "sourceAppId": "app-1",
                "publishedVersion": 3,
                "visibleRoleIds": ["role-a", "role-b"],
                "visibleDeptIds": ["dept-a"],
            },
        )

    def test_visible_ids_csv_dedupes_and_joins_values(self):
        from app.services.agents.app_info_publish_service import merge_required_model_ids, visible_ids_csv

        self.assertEqual(
            visible_ids_csv([" role-a ", {"id": "role-b"}, "role-a", "", None]),
            "role-a,role-b",
        )
        self.assertEqual(visible_ids_csv("dept-a, dept-b, dept-a"), "dept-a,dept-b")
        self.assertEqual(visible_ids_csv([]), "")
        self.assertEqual(
            merge_required_model_ids(["gpt-4o", "default"], ["text-embedding-v3", "gpt-4o"], ""),
            ["gpt-4o", "text-embedding-v3"],
        )

    def test_build_app_info_column_values_contains_portal_fields(self):
        from types import SimpleNamespace

        from app.services.agents.app_info_publish_service import build_app_info_column_values

        app = SimpleNamespace(
            id="app-1",
            name="审批助手",
            description="处理审批问题",
            app_icon="https://icon.test/a.png",
            app_category="office",
            ai_app_type="workflow",
            owner_username="zhangsan",
            owner_user_id="u1",
            tenant_id="tenant-1",
        )
        version = SimpleNamespace(
            version_no=7,
            visible_role_ids='["role-a","role-b"]',
            visible_dept_ids="dept-a,dept-b",
            reviewed_by_name="审核员",
            reviewed_by="reviewer-1",
            submitted_by_name="张三",
        )

        workflow_json = '{"nodes":[{"inputs":[{"key":"model","value":"gpt-4o"},{"key":"model","value":"default"}]}]}'
        values = build_app_info_column_values(
            app,
            version,
            '{"publishedVersion":7}',
            workflow_json,
            required_model_ids=["gpt-4o", "text-embedding-v3"],
        )

        self.assertEqual(values["app_name"], "审批助手")
        self.assertEqual(values["app_remark"], "处理审批问题")
        self.assertEqual(values["app_type"], "agent")
        self.assertEqual(values["ai_app_type"], "workflow")
        self.assertEqual(values["status"], "1")
        self.assertEqual(values["terminal_type"], "pc,h5")
        self.assertEqual(values["open_type"], "_blank")
        self.assertEqual(values["pc_url"], "/agent/run/app-1")
        self.assertEqual(values["selected_roles"], "role-a,role-b")
        self.assertEqual(values["selected_departs"], "dept-a,dept-b")
        self.assertEqual(values["form_options"], '{"publishedVersion":7}')
        self.assertEqual(values["create_by"], "u1")
        self.assertEqual(values["update_by"], "reviewer-1")
        self.assertEqual(values["use_models"], "gpt-4o,text-embedding-v3")

        version.reviewed_by = ""
        version.submitted_by = ""
        fallback_values = build_app_info_column_values(app, version, "{}", workflow_json)
        self.assertEqual(fallback_values["update_by"], "u1")

        app.owner_user_id = ""
        with self.assertRaisesRegex(ValueError, "owner_user_id"):
            build_app_info_column_values(app, version, "{}", workflow_json)

    def test_runtime_url_uses_agent_run_for_tool_apps_too(self):
        from types import SimpleNamespace

        from app.services.agents.app_info_publish_service import _runtime_url

        app = SimpleNamespace(id="tool-1", ai_app_type="workflowTool")

        self.assertEqual(_runtime_url(app), "/agent/run/tool-1")

    def test_build_app_info_column_values_keeps_a_disabled_catalog_disabled(self):
        from types import SimpleNamespace

        from app.services.agents.app_info_publish_service import build_app_info_column_values

        app = SimpleNamespace(
            id="app-1", name="审批助手", description="", app_icon="", app_category="",
            ai_app_type="workflow", owner_username="zhangsan", owner_user_id="u1", tenant_id="0",
        )
        version = SimpleNamespace(
            version_no=1, visible_role_ids=[], visible_dept_ids=[], reviewed_by="reviewer-1",
            submitted_by="u1",
        )

        values = build_app_info_column_values(app, version, "{}", "{\"nodes\":[]}", catalog_enabled=False)

        self.assertEqual(values["status"], "0")


class AppInfoPublishSyncTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_upsert_preserves_the_open_type_edited_in_app_management(self):
        from types import SimpleNamespace

        from app.services.agents.app_info_publish_service import upsert_app_info_for_approved_version

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return self.rows

            def first(self):
                return self.rows[0] if self.rows else None

        class FakeSession:
            app_info_columns = {
                "id",
                "app_name",
                "app_remark",
                "app_icon",
                "app_category",
                "terminal_type",
                "open_type",
                "pc_url",
                "h5_url",
                "app_type",
                "ai_app_type",
                "status",
                "form_options",
                "selected_roles",
                "selected_departs",
                "create_by",
                "update_by",
                "tenant_id",
                "del_flag",
                "use_models",
                "create_time",
                "update_time",
            }

            def __init__(self):
                self.upsert_values = None

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "information_schema.COLUMNS" in sql:
                    columns = self.app_info_columns if params["table_name"] == "app_info" else set()
                    return FakeResult([{"COLUMN_NAME": column} for column in columns])
                if "SELECT form_options" in sql:
                    return FakeResult([{"form_options": None, "open_type": "iframe"}])
                if "INSERT INTO app_info" in sql:
                    self.upsert_values = params
                return FakeResult([])

        app = SimpleNamespace(
            id="app-1",
            name="审批助手",
            description="处理审批问题",
            app_icon="",
            app_category="office",
            ai_app_type="workflow",
            owner_username="zhangsan",
            owner_user_id="u1",
            tenant_id="tenant-1",
        )
        version = SimpleNamespace(
            version_no=7,
            visible_role_ids=[],
            visible_dept_ids=[],
            reviewed_by="reviewer-1",
            submitted_by="submitter-1",
            definition_json='{"nodes":[]}',
        )
        session = FakeSession()

        await upsert_app_info_for_approved_version(session, app, version)

        self.assertEqual(session.upsert_values["open_type"], "iframe")

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_approved_publish_projects_version_visibility_into_marketplace_relations(self):
        """广场按 app_role/app_dept 过滤，审核版本的可见范围必须落到这两张关联表。"""
        from types import SimpleNamespace

        from app.services.agents.app_info_publish_service import upsert_app_info_for_approved_version

        class FakeResult:
            def __init__(self, rows=None):
                self.rows = rows or []

            def mappings(self):
                return self

            def all(self):
                return self.rows

            def first(self):
                return self.rows[0] if self.rows else None

        class FakeSession:
            columns_by_table = {
                "app_info": {"id", "form_options", "open_type", "status", "app_name"},
                "app_role": {"id", "app_id", "role_id", "tenant_id"},
                "app_dept": {"id", "app_id", "dept_id", "tenant_id"},
            }

            def __init__(self):
                self.visibility_writes = []

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "information_schema.COLUMNS" in sql:
                    columns = self.columns_by_table.get(params["table_name"], set())
                    return FakeResult([{"COLUMN_NAME": column} for column in columns])
                if "SELECT form_options" in sql:
                    return FakeResult([])
                if "app_role" in sql or "app_dept" in sql:
                    self.visibility_writes.append((sql, params))
                return FakeResult()

        app = SimpleNamespace(
            id="app-1", name="审批助手", description="", app_icon="", app_category="",
            ai_app_type="workflow", owner_username="owner", owner_user_id="u-owner", tenant_id="tenant-1",
        )
        version = SimpleNamespace(
            version_no=4, visible_role_ids=["role-a", "role-b"], visible_dept_ids=["dept-a"],
            reviewed_by="reviewer", submitted_by="u-owner", definition_json='{"nodes":[]}',
        )
        session = FakeSession()

        await upsert_app_info_for_approved_version(session, app, version)

        relation_inserts = [params for sql, params in session.visibility_writes if "INSERT INTO" in sql]
        self.assertEqual(
            {(row.get("role_id"), row.get("dept_id"), row["tenant_id"]) for row in relation_inserts},
            {("role-a", None, "tenant-1"), ("role-b", None, "tenant-1"), (None, "dept-a", "tenant-1")},
        )

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_unpublish_marks_the_java_catalog_entry_disabled(self):
        from app.services.agents.app_info_publish_service import set_app_info_catalog_status

        class FakeResult:
            def mappings(self):
                return self

            def all(self):
                return [{"COLUMN_NAME": "status"}, {"COLUMN_NAME": "update_time"}]

        class FakeSession:
            def __init__(self):
                self.status_params = None
                self.status_sql = ""

            async def execute(self, statement, params=None):
                if "information_schema.COLUMNS" in str(statement):
                    return FakeResult()
                if "UPDATE app_info" in str(statement):
                    self.status_params = params
                    self.status_sql = str(statement)
                return FakeResult()

        session = FakeSession()
        await set_app_info_catalog_status(session, "app-1", enabled=False)

        self.assertEqual(session.status_params, {"id": "app-1", "status": "0"})
        self.assertIn("update_time = NOW()", session.status_sql)

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_catalog_enabled_check_distinguishes_absent_and_disabled_rows(self):
        from app.services.agents.app_info_publish_service import get_app_info_catalog_enabled

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return [{"COLUMN_NAME": "status"}]

            def first(self):
                return self.rows[0] if self.rows else None

        class FakeSession:
            def __init__(self, row):
                self.row = row

            async def execute(self, statement, params=None):
                if "information_schema.COLUMNS" in str(statement):
                    return FakeResult([])
                return FakeResult([self.row] if self.row else [])

        self.assertIsNone(await get_app_info_catalog_enabled(FakeSession(None), "missing"))
        self.assertFalse(await get_app_info_catalog_enabled(FakeSession({"status": "0"}), "disabled"))
        self.assertTrue(await get_app_info_catalog_enabled(FakeSession({"status": "1"}), "enabled"))

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_retire_hides_catalog_entry_and_marks_it_deleted(self):
        from app.services.agents.app_info_publish_service import retire_app_info_catalog_entry

        class FakeResult:
            rowcount = 1

            def mappings(self):
                return self

            def all(self):
                return [
                    {"COLUMN_NAME": "status"},
                    {"COLUMN_NAME": "del_flag"},
                    {"COLUMN_NAME": "update_time"},
                    {"COLUMN_NAME": "form_options"},
                ]

        class FakeSession:
            def __init__(self):
                self.params = None
                self.sql = ""

            async def execute(self, statement, params=None):
                if "UPDATE app_info" in str(statement):
                    self.params = params
                    self.sql = str(statement)
                return FakeResult()

        session = FakeSession()
        await retire_app_info_catalog_entry(session, "app-1")

        self.assertEqual(session.params, {"id": "app-1", "status": "0", "del_flag": "1"})
        self.assertIn("update_time = NOW()", session.sql)
        self.assertIn("sourceAppId", session.sql)

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_orphan_catalog_retire_joins_workflow_app_and_hides_missing_rows(self):
        from app.services.agents.app_info_publish_service import retire_orphaned_agent_catalog_entries

        class FakeResult:
            def __init__(self, kind="app_info"):
                self.kind = kind
                self.rowcount = 2

            def mappings(self):
                return self

            def all(self):
                if self.kind == "app_info":
                    return [
                        {"COLUMN_NAME": "id"},
                        {"COLUMN_NAME": "status"},
                        {"COLUMN_NAME": "del_flag"},
                        {"COLUMN_NAME": "update_time"},
                        {"COLUMN_NAME": "form_options"},
                        {"COLUMN_NAME": "pc_url"},
                    ]
                return [{"COLUMN_NAME": "id"}]

        class FakeSession:
            def __init__(self):
                self.sql = ""
                self.params = None

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "information_schema.COLUMNS" in sql:
                    kind = "workflow" if params and params.get("table_name") == "agent_workflow_app" else "app_info"
                    return FakeResult(kind)
                if "UPDATE app_info" in sql:
                    self.sql = sql
                    self.params = params
                    return FakeResult()
                return FakeResult()

        session = FakeSession()
        self.assertEqual(await retire_orphaned_agent_catalog_entries(session), 2)
        self.assertEqual(session.params, {"status": "0", "del_flag": "1"})
        self.assertIn("LEFT JOIN agent_workflow_app w_id", session.sql)
        self.assertIn("a.pc_url LIKE '/agent/run/%'", session.sql)
        self.assertIn("sourceAppId", session.sql)


class AppInfoPublishModelResolutionTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_resolves_embedding_models_from_selected_knowledge_in_first_seen_order(self):
        from app.services.agents.app_info_publish_service import resolve_workflow_required_model_ids

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return self.rows

        class FakeSession:
            async def execute(self, statement, params=None):
                sql = str(statement)
                if "information_schema.COLUMNS" in sql:
                    return FakeResult(
                        [
                            {"COLUMN_NAME": "id"},
                            {"COLUMN_NAME": "embedding_model"},
                            {"COLUMN_NAME": "del_flag"},
                        ]
                    )
                return FakeResult(
                    [
                        {"id": "kb-search", "embedding_model": "text-embedding-v3"},
                        {"id": "kb-chat", "embedding_model": "bge-m3"},
                    ]
                )

        workflow = {
            "fastgpt": {
                "nodes": [
                    {
                        "inputs": [
                            {"key": "model", "value": "gpt-4o"},
                            {"key": "aiChatDatasets", "value": [{"datasetId": "kb-chat"}]},
                        ]
                    },
                    {
                        "inputs": [
                            {"key": "datasets", "value": [{"datasetId": "kb-search"}, {"datasetId": "kb-chat"}]}
                        ]
                    },
                ]
            }
        }

        self.assertEqual(
            await resolve_workflow_required_model_ids(FakeSession(), json.dumps(workflow)),
            ["gpt-4o", "bge-m3", "text-embedding-v3"],
        )

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
    async def test_resolves_models_from_referenced_agent_and_workflow(self):
        from app.services.agents.app_info_publish_service import resolve_workflow_required_model_ids

        child_agent_json = json.dumps(
            {
                "nodes": [
                    {"inputs": [{"key": "model", "value": "child-chat"}]},
                    {"flowNodeType": "appModule", "pluginId": "child-workflow"},
                ]
            }
        )
        child_workflow_json = json.dumps({"nodes": [{"inputs": [{"key": "model", "value": "nested-chat"}]}]})

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return self.rows

        class FakeSession:
            definitions = {
                "child-agent": ("chatAgent", child_agent_json),
                "child-workflow": ("workflow", child_workflow_json),
            }

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "agent_workflow_app" not in sql:
                    return FakeResult([])
                rows = []
                for app_id in (params or {}).values():
                    if app_id in self.definitions:
                        ai_app_type, published_json = self.definitions[app_id]
                        rows.append({"id": app_id, "ai_app_type": ai_app_type, "published_json": published_json})
                return FakeResult(rows)

        workflow = {
            "nodes": [
                {"inputs": [{"key": "model", "value": "root-chat"}]},
                {"flowNodeType": "appModule", "pluginId": "child-agent"},
            ]
        }

        self.assertEqual(
            await resolve_workflow_required_model_ids(FakeSession(), json.dumps(workflow)),
            ["root-chat", "child-chat", "nested-chat"],
        )


if __name__ == "__main__":
    unittest.main()
