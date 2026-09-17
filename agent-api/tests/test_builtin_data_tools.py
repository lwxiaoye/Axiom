import asyncio
import base64
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("httpx", types.SimpleNamespace(AsyncClient=object))

from app.services.skills.builtin_data_tools import (
    _referenced_sql_tables,
    _schema_table_names,
    validate_sql_statement,
    validate_readonly_sql,
)
from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, execute_builtin_tool


class BuiltinDataToolsTest(unittest.TestCase):
    def test_catalog_contains_data_tools(self):
        for tool_id in (
            "builtin.base64",
            "builtin.text_to_sql",
            "builtin.sql_query",
            "builtin.table_transform",
            "builtin.text_extract",
            "builtin.url_fetch",
            "builtin.crypto_digest",
            "builtin.date_range",
            "builtin.chart",
        ):
            self.assertIn(tool_id, BUILTIN_TOOL_MAP)

        text_to_sql = BUILTIN_TOOL_MAP["builtin.text_to_sql"]
        self.assertEqual(text_to_sql["inputKeys"], ["model", "query_text", "database_schema", "limit_rows"])
        self.assertEqual(
            text_to_sql["outputKeys"],
            ["sql", "table_fields", "confidence", "syntax_error"],
        )
        sql_query = BUILTIN_TOOL_MAP["builtin.sql_query"]
        self.assertEqual(sql_query["name"], "SQL 执行")
        self.assertEqual(sql_query["inputKeys"], ["sql", "db_uri"])
        self.assertIn("affected_rows", sql_query["outputKeys"])
        self.assertNotIn("limit_rows", sql_query["parameters"]["properties"])

        table_transform = BUILTIN_TOOL_MAP["builtin.table_transform"]
        self.assertIn("text", table_transform["outputKeys"])
        self.assertIn("rows", table_transform["outputKeys"])

    def test_base64_text_round_trip(self):
        encoded = asyncio.run(
            execute_builtin_tool("builtin.base64", {"content": "AXIOM AI", "operation": "编码"})
        )
        decoded = asyncio.run(
            execute_builtin_tool("builtin.base64", {"content": encoded["base64"], "operation": "解码"})
        )
        self.assertEqual(decoded["text"], "AXIOM AI")

    def test_base64_file_returns_transport_safe_file_object(self):
        payload = base64.b64encode(b"\x00\xffPNG").decode()
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.base64",
                {
                    "content": payload,
                    "operation": "解码",
                    "is_file": True,
                    "filename": "sample.bin",
                },
            )
        )
        self.assertEqual(result["file"]["size"], 5)
        self.assertEqual(result["file"]["content_base64"], payload)

    def test_readonly_validator_blocks_writes_and_multiple_statements(self):
        self.assertEqual(validate_readonly_sql("SELECT * FROM users;"), "SELECT * FROM users")
        self.assertEqual(
            validate_readonly_sql("WITH active AS (SELECT * FROM users) SELECT * FROM active"),
            "WITH active AS (SELECT * FROM users) SELECT * FROM active",
        )
        with self.assertRaises(ValueError):
            validate_readonly_sql("DELETE FROM users")
        with self.assertRaises(ValueError):
            validate_readonly_sql("SELECT 1; DROP TABLE users")
        self.assertEqual(validate_readonly_sql("SELECT 'drop table x' AS note"), "SELECT 'drop table x' AS note")

    def test_sql_executor_accepts_writes_but_keeps_single_statement_boundary(self):
        self.assertEqual(validate_sql_statement("UPDATE users SET active = 1;"), "UPDATE users SET active = 1")
        with self.assertRaises(ValueError):
            validate_sql_statement("SELECT 1; DELETE FROM users")
        with self.assertRaises(ValueError):
            validate_sql_statement("COMMIT")

    def test_sql_query_returns_structured_rows_without_max_row_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sample.sqlite"
            import sqlite3

            connection = sqlite3.connect(db_path)
            connection.execute("create table metrics(name text, value integer)")
            connection.executemany("insert into metrics values(?, ?)", [("A", 1), ("B", 2), ("C", 3)])
            connection.commit()
            connection.close()
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.sql_query",
                    {
                        "sql": "select name, value from metrics order by value",
                        "db_uri": f"sqlite:///{db_path.as_posix()}",
                        "limit_rows": 2,
                    },
                )
            )
        self.assertEqual(result["columns"], ["name", "value"])
        self.assertEqual(result["rows"][0], {"name": "A", "value": 1})
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["affected_rows"], 0)
        self.assertFalse(result["truncated"])

    def test_sql_executor_commits_dml_and_returns_affected_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sample.sqlite"
            db_uri = f"sqlite:///{db_path.as_posix()}"
            asyncio.run(execute_builtin_tool("builtin.sql_query", {"sql": "CREATE TABLE metrics(value integer)", "db_uri": db_uri}))
            inserted = asyncio.run(
                execute_builtin_tool("builtin.sql_query", {"sql": "INSERT INTO metrics(value) VALUES (7)", "db_uri": db_uri})
            )
            result = asyncio.run(
                execute_builtin_tool("builtin.sql_query", {"sql": "SELECT value FROM metrics", "db_uri": db_uri})
            )
        self.assertEqual(inserted["affected_rows"], 1)
        self.assertEqual(result["rows"], [{"value": 7}])

    def test_chart_returns_svg_base64_and_echarts_option(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": [{"month": "1月", "sales": 12}, {"month": "2月", "sales": 18}],
                    "chart_type": "bar",
                    "title": "月销售额",
                },
            )
        )
        self.assertEqual(result["image_mime_type"], "image/svg+xml")
        self.assertIn(b"<svg", base64.b64decode(result["image_base64"]))
        self.assertEqual(result["echarts_option"]["series"][0]["type"], "bar")
        self.assertIn("echarts.init", result["html"])
        self.assertIn("| month | sales |", result["data_markdown"])
        self.assertIn("| 1月 | 12 |", result["data_markdown"])

    def test_chart_accepts_dataset_as_json_text(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": """
                    [
                      { "month": "1月", "sales": 12 },
                      { "month": "2月", "sales": 18 },
                      { "month": "3月", "sales": 15 }
                    ]
                    """,
                    "chart_type": "bar",
                },
            )
        )

        self.assertEqual(result["echarts_option"]["xAxis"]["data"], ["1月", "2月", "3月"])

    def test_chart_accepts_dataset_pasted_as_markdown_json_block(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": """```json
                    [
                      { "month": "1月", "sales": 12 },
                      { "month": "2月", "sales": 18 },
                      { "month": "3月", "sales": 15 }
                    ]
                    ```""",
                    "chart_type": "bar",
                },
            )
        )

        self.assertEqual(result["echarts_option"]["xAxis"]["data"], ["1月", "2月", "3月"])

    def test_chart_accepts_dataset_with_common_rich_text_punctuation(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": "[{ “month”: “1月”, “sales”: 12 },{ “month”: “2月”, “sales”: 18 },]",
                    "chart_type": "bar",
                },
            )
        )

        self.assertEqual(result["echarts_option"]["xAxis"]["data"], ["1月", "2月"])

    def test_chart_limits_render_rows_and_reports_warning(self):
        dataset = [{"index": index, "value": index * 2} for index in range(1005)]

        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": dataset,
                    "chart_type": "line",
                    "title": "大数据图表",
                },
            )
        )

        self.assertEqual(len(result["echarts_option"]["xAxis"]["data"]), 1000)
        self.assertIn("前 1000 条", result["render_warning"])

    def test_chart_auto_uses_line_for_time_series(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": [
                        {"date": "2026-07-22", "sales": 12},
                        {"date": "2026-07-23", "sales": 18},
                        {"date": "2026-07-24", "sales": 16},
                    ],
                    "chart_type": "auto",
                },
            )
        )

        self.assertEqual(result["chart_type"], "line")
        self.assertEqual(result["echarts_option"]["series"][0]["type"], "line")

    def test_chart_accepts_chinese_chart_type_values(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.chart",
                {
                    "dataset": [{"month": "1月", "sales": 12}, {"month": "2月", "sales": 18}],
                    "chart_type": "柱状图",
                },
            )
        )

        self.assertEqual(result["chart_type"], "bar")
        self.assertEqual(result["echarts_option"]["series"][0]["type"], "bar")

    def test_table_transform_filters_groups_sorts_and_outputs_json(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.table_transform",
                {
                    "source": "dept,name,score\nA,Alice,90\nA,Bob,80\nB,Cara,70\nB,Dan,60\n",
                    "source_format": "csv",
                    "output_format": "json",
                    "filters": [{"column": "score", "op": ">=", "value": 70}],
                    "group_by": ["dept"],
                    "aggregations": [
                        {"column": "score", "op": "sum", "as": "total_score"},
                        {"column": "score", "op": "count", "as": "count"},
                    ],
                    "sort_by": [{"column": "dept", "direction": "asc"}],
                },
            )
        )

        self.assertEqual(result["columns"], ["dept", "total_score", "count"])
        self.assertEqual(
            result["rows"],
            [
                {"dept": "A", "total_score": 170.0, "count": 2},
                {"dept": "B", "total_score": 70.0, "count": 1},
            ],
        )
        self.assertEqual(json.loads(result["text"]), result["rows"])

    def test_table_transform_deduplicates_selects_and_outputs_tsv(self):
        result = asyncio.run(
            execute_builtin_tool(
                "builtin.table_transform",
                {
                    "source": [
                        {"id": 1, "name": "Alice", "extra": "x"},
                        {"id": 1, "name": "Alice duplicate", "extra": "y"},
                        {"id": 2, "name": "Bob", "extra": "z"},
                    ],
                    "output_format": "tsv",
                    "select_columns": ["id", "name"],
                    "dedupe_columns": ["id"],
                    "sort_by": [{"column": "id", "direction": "desc"}],
                },
            )
        )

        self.assertEqual(result["columns"], ["id", "name"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["text"], "id\tname\r\n2\tBob\r\n1\tAlice\r\n")

    def test_text_extract_supports_regex_delimiter_and_line_range(self):
        regex_result = asyncio.run(
            execute_builtin_tool(
                "builtin.text_extract",
                {
                    "text": "姓名：张三\n学号：20260820\n手机号：13800138000",
                    "mode": "regex",
                    "pattern": r"学号[:：]\s*(\d+)",
                },
            )
        )
        self.assertEqual(regex_result["matches"][0]["groups"], ["20260820"])
        self.assertEqual(regex_result["count"], 1)

        delimiter_result = asyncio.run(
            execute_builtin_tool(
                "builtin.text_extract",
                {"text": "A|B|C", "mode": "delimiter", "delimiter": "|"},
            )
        )
        self.assertEqual([item["value"] for item in delimiter_result["matches"]], ["A", "B", "C"])

        line_result = asyncio.run(
            execute_builtin_tool(
                "builtin.text_extract",
                {"text": "line1\nline2\nline3", "mode": "line_range", "start_line": 2, "end_line": 3},
            )
        )
        self.assertEqual(line_result["text"], "line2\nline3")
        self.assertEqual([item["line_number"] for item in line_result["matches"]], [2, 3])

    def test_url_fetch_blocks_private_hosts_before_network_request(self):
        with self.assertRaises(ValueError):
            asyncio.run(
                execute_builtin_tool(
                    "builtin.url_fetch",
                    {"url": "http://127.0.0.1:8000/health"},
                )
            )

    def test_crypto_digest_hash_hmac_and_uuid(self):
        md5_result = asyncio.run(
            execute_builtin_tool("builtin.crypto_digest", {"operation": "md5", "content": "abc"})
        )
        self.assertEqual(md5_result["result"], "900150983cd24fb0d6963f7d28e17f72")

        hmac_result = asyncio.run(
            execute_builtin_tool(
                "builtin.crypto_digest",
                {"operation": "hmac_sha256", "content": "abc", "secret": "key"},
            )
        )
        self.assertEqual(
            hmac_result["result"],
            "9c196e32dc0175f86f4b1cb89289d6619de6bee699e4c378e68309ed97a1a6ab",
        )

        uuid_result = asyncio.run(execute_builtin_tool("builtin.crypto_digest", {"operation": "uuid4"}))
        self.assertEqual(uuid_result["algorithm"], "uuid4")
        self.assertEqual(len(uuid_result["result"]), 36)

    def test_date_range_calculates_differences_workdays_and_boundaries(self):
        diff_result = asyncio.run(
            execute_builtin_tool(
                "builtin.date_range",
                {"start_date": "2026-08-17", "end_date": "2026-08-21"},
            )
        )
        self.assertEqual(diff_result["days_between"], 4)
        self.assertEqual(diff_result["workdays"], 5)
        self.assertEqual(diff_result["week_start"], "2026-08-17")
        self.assertEqual(diff_result["week_end"], "2026-08-23")
        self.assertEqual(diff_result["month_start"], "2026-08-01")
        self.assertEqual(diff_result["month_end"], "2026-08-31")

        add_result = asyncio.run(
            execute_builtin_tool(
                "builtin.date_range",
                {"operation": "add", "start_date": "2026-01-31", "amount": 1, "unit": "months"},
            )
        )
        self.assertEqual(add_result["result_date"], "2026-02-28")

    def test_text_to_sql_uses_runtime_model_and_revalidates_output(self):
        response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": "SELECT id, name FROM users LIMIT 20",
                            "table_fields": [{"table": "users", "field": "id", "description": "主键"}],
                            "confidence": 0.93,
                            "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False
        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "query_text": "列出用户",
                        "database_schema": "users(id bigint, name varchar)",
                    },
                    {"api_key": "test", "default_model": "model-a", "base_url": "http://llm/v1"},
                )
            )
        self.assertEqual(result["sql"], "SELECT id, name FROM users LIMIT 20")
        self.assertEqual(result["confidence"], 0.93)
        self.assertEqual(client.post.call_args.kwargs["json"]["model"], "model-a")

    def test_text_to_sql_default_model_value_falls_back_to_runtime_model(self):
        response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": "SELECT id FROM users",
                            "table_fields": [],
                            "confidence": 0.9,
                            "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False
        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "model": "default",
                        "query_text": "列出用户",
                        "database_schema": "users(id bigint)",
                    },
                    {"api_key": "test", "default_model": "model-a", "base_url": "http://llm/v1"},
                )
            )

        self.assertEqual(client.post.call_args.kwargs["json"]["model"], "model-a")

    def test_text_to_sql_falls_back_to_workflow_user_input_for_legacy_nodes(self):
        response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": "SELECT id, name FROM users LIMIT 20",
                            "table_fields": [],
                            "confidence": 0.9,
                            "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "query_text": "",
                        "database_schema": "users(id bigint, name varchar)",
                    },
                    {
                        "api_key": "test",
                        "default_model": "model-a",
                        "base_url": "http://llm/v1",
                        "user_input": "列出用户",
                    },
                )
            )

        self.assertEqual(result["sql"], "SELECT id, name FROM users LIMIT 20")
        sent_messages = client.post.call_args.kwargs["json"]["messages"]
        self.assertIn("用户问题：\n列出用户", sent_messages[1]["content"])

    def test_text_to_sql_returns_model_sql_without_semantic_retry(self):
        model_response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": "SELECT COUNT(*) AS total_users FROM sys_user WHERE del_flag = 0",
                            "table_fields": [],
                            "confidence": 0.95,
                            "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = model_response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "query_text": "姓李的用户有哪些",
                        "database_schema": (
                            "sys_user(id bigint, username varchar, realname varchar, work_no varchar, "
                            "phone varchar, email varchar, status int, del_flag int, create_time datetime)"
                        ),
                    },
                    {"api_key": "test", "default_model": "model-a", "base_url": "http://llm/v1"},
                )
            )

        self.assertEqual(
            result["sql"],
            "SELECT COUNT(*) AS total_users FROM sys_user WHERE del_flag = 0",
        )
        self.assertEqual(client.post.await_count, 1)

    def test_text_to_sql_accepts_model_generated_multi_table_join(self):
        response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                        "sql": (
                            "SELECT u.username, d.depart_name "
                            "FROM sys_user u "
                                "LEFT JOIN sys_depart d ON u.depart_id = d.id "
                                "WHERE u.del_flag = 0"
                            ),
                            "table_fields": [
                            {"table": "sys_user", "field": "username", "description": "用户名"},
                            {"table": "sys_depart", "field": "depart_name", "description": "部门名称"},
                        ],
                        "confidence": 0.97,
                        "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False
        schema = """
        CREATE TABLE sys_user (
          id bigint,
          username varchar(100),
          depart_id bigint,
          del_flag int
        );
        CREATE TABLE sys_depart (
          id bigint,
          depart_name varchar(100)
        );
        """

        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "query_text": "列出用户名及其所属部门名称",
                        "database_schema": schema,
                    },
                    {"api_key": "test", "default_model": "model-a", "base_url": "http://llm/v1"},
                )
            )

        self.assertIn("LEFT JOIN sys_depart", result["sql"])
        self.assertEqual(result["syntax_error"], "")
        messages = client.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("总数", messages[0]["content"])
        self.assertNotIn("table_relationships", messages[0]["content"])

    def test_schema_table_extraction_supports_ddl_and_compact_definitions(self):
        schema = """
        CREATE TABLE public.orders (id bigint, user_id bigint);
        users(id bigint, username varchar)
        departments(id bigint, name varchar)
        """
        self.assertEqual(
            _schema_table_names(schema),
            {"public.orders", "users", "departments"},
        )
        self.assertEqual(
            _referenced_sql_tables(
                "SELECT u.username, d.name FROM users u "
                "JOIN departments d ON u.department_id = d.id"
            ),
            ["users", "departments"],
        )

    def test_text_to_sql_rejects_hallucinated_table_without_retry(self):
        invalid_response = types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": (
                                "SELECT u.username, r.role_name FROM sys_user u "
                                "JOIN imaginary_role r ON u.role_id = r.id"
                            ),
                            "table_fields": [],
                            "confidence": 0.4,
                            "syntax_error": "",
                        })
                    }
                }]
            },
        )
        client = AsyncMock()
        client.post.return_value = invalid_response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch("app.services.skills.builtin_data_tools.httpx.AsyncClient", return_value=context):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.text_to_sql",
                    {
                        "query_text": "列出用户和角色",
                        "database_schema": (
                            "CREATE TABLE sys_user "
                            "(id bigint, username varchar(100), role_id bigint);"
                        ),
                    },
                    {"api_key": "test", "default_model": "model-a", "base_url": "http://llm/v1"},
                )
            )

        self.assertEqual(result["sql"], "")
        self.assertIn("imaginary_role", result["syntax_error"])
        self.assertEqual(client.post.await_count, 1)


if __name__ == "__main__":
    unittest.main()
