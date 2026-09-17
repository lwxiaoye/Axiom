import asyncio
import sys
import types
import unittest
from zoneinfo import available_timezones

sys.modules.setdefault("httpx", types.SimpleNamespace(AsyncClient=object))

from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP
from app.services.workflows.node_template_service import build_preview_node, list_system_tool_templates


class BuiltinNodeTemplateServiceTest(unittest.TestCase):
    def test_builtin_tool_preview_does_not_emit_version_badge_fields(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.datetime"))

        self.assertNotIn("versionLabel", node)
        self.assertNotIn("version", node)

    def test_builtin_tool_inputs_use_chinese_labels(self):
        cases = {
            "builtin.timestamp": {"unit": "单位"},
            "builtin.time_convert": {
                "source_value": "时间/时间戳",
                "source_format": "源格式",
                "target_format": "目标格式",
            },
            "builtin.timezone_convert": {
                "time_str": "时间",
                "from_timezone": "转换前时区",
                "to_timezone": "转换后时区",
            },
            "builtin.json_parse": {
                "json": "JSON 字符串",
            },
            "builtin.base64": {
                "content": "输入内容",
                "operation": "处理方式",
                "is_file": "文件模式",
            },
            "builtin.text_to_sql": {
                "model": "生成模型",
                "query_text": "自然语言查询",
                "database_schema": "库表定义",
                "limit_rows": "返回条数",
            },
            "builtin.sql_query": {
                "sql": "SQL 语句",
                "db_uri": "DB URI",
            },
            "builtin.table_transform": {
                "source": "输入表格",
                "source_format": "输入格式",
                "output_format": "输出格式",
                "dedupe": "去重",
            },
            "builtin.text_extract": {
                "text": "输入文本",
                "mode": "提取方式",
                "pattern": "正则表达式",
            },
            "builtin.url_fetch": {
                "url": "URL",
                "max_chars": "最大正文字数",
            },
            "builtin.crypto_digest": {
                "operation": "算法",
                "content": "输入内容",
                "secret": "HMAC 密钥",
            },
            "builtin.date_range": {
                "start_date": "开始日期",
                "end_date": "结束日期",
                "operation": "计算方式",
            },
            "builtin.chart": {
                "dataset": "结构化数据",
                "chart_type": "图表类型",
            },
            "builtin.web_search": {
                "query": "搜索关键词",
                "with_images": "包含图片",
            },
            "builtin.weekday": {"date_str": "日期", "start_week": "一周起始日"},
        }

        for tool_id, expected_labels in cases.items():
            with self.subTest(tool_id=tool_id):
                node = asyncio.run(build_preview_node(None, None, template_id=tool_id))
                by_key = {item["key"]: item for item in node["inputs"]}
                self.assertEqual({key: by_key[key]["label"] for key in expected_labels}, expected_labels)

    def test_timezone_inputs_are_selects_with_all_builtin_timezones(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.timezone_convert"))
        by_key = {item["key"]: item for item in node["inputs"]}
        expected_zones = available_timezones()

        for key in ("from_timezone", "to_timezone"):
            with self.subTest(key=key):
                item = by_key[key]
                values = {option["value"] for option in item["list"]}
                self.assertEqual(item["renderTypeList"], ["select", "reference"])
                self.assertIs(item["searchable"], True)
                self.assertEqual(len(values), len(expected_zones))
                self.assertIn("UTC", values)
                self.assertIn("Asia/Shanghai", values)
                self.assertIn("America/New_York", values)

    def test_timezone_convert_catalog_outputs_timezone_separately(self):
        tool = BUILTIN_TOOL_MAP["builtin.timezone_convert"]

        self.assertEqual(tool["outputKeys"], ["target_time", "target_timezone", "utc_timestamp"])

    def test_json_parse_preview_exposes_result_output(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.json_parse"))

        output_keys = [item["key"] for item in node["outputs"]]
        self.assertIn("result", output_keys)
        self.assertIn("system_rawResponse", output_keys)

    def test_data_tools_expose_structured_outputs(self):
        expected = {
            "builtin.base64": {"text", "base64", "file"},
            "builtin.text_to_sql": {"sql", "table_fields", "confidence", "syntax_error"},
            "builtin.sql_query": {"columns", "rows", "row_count", "affected_rows", "truncated", "database_type"},
            "builtin.table_transform": {"columns", "rows", "text", "row_count"},
            "builtin.text_extract": {"matches", "text", "count"},
            "builtin.url_fetch": {"url", "final_url", "status_code", "title", "text", "links", "source", "fetched_at"},
            "builtin.crypto_digest": {"result", "algorithm"},
            "builtin.date_range": {
                "days_between",
                "workdays",
                "result_date",
                "week_start",
                "week_end",
                "month_start",
                "month_end",
            },
            "builtin.chart": {
                "data_markdown",
                "image_base64",
                "image_mime_type",
                "echarts_option",
                "html",
                "chart_type",
                "render_warning",
            },
            "builtin.web_search": {"text", "results", "images", "scraped_pages", "enabled"},
        }
        for tool_id, output_keys in expected.items():
            with self.subTest(tool_id=tool_id):
                node = asyncio.run(build_preview_node(None, None, template_id=tool_id))
                actual = {item["key"] for item in node["outputs"]}
                self.assertTrue(output_keys.issubset(actual))
                self.assertIn("system_rawResponse", actual)

    def test_text_to_sql_keeps_node_contract_simple(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.text_to_sql"))

        self.assertEqual(
            [item["key"] for item in node["inputs"]],
            ["model", "query_text", "database_schema", "limit_rows"],
        )
        self.assertEqual(
            [item["key"] for item in node["outputs"]],
            ["sql", "table_fields", "confidence", "syntax_error", "system_rawResponse", "system_error_text"],
        )

    def test_text_to_sql_model_input_uses_llm_model_selector(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.text_to_sql"))

        model = next(item for item in node["inputs"] if item["key"] == "model")
        self.assertEqual(model["renderTypeList"], ["selectLLMModel"])
        self.assertEqual(model["value"], "default")

    def test_sql_query_db_uri_lists_supported_database_examples(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.sql_query"))

        db_uri = next(item for item in node["inputs"] if item["key"] == "db_uri")
        self.assertIn("mysql+pymysql://", db_uri["description"])
        self.assertIn("postgresql+psycopg://", db_uri["description"])
        self.assertIn("sqlite:///", db_uri["description"])

    def test_chart_type_options_use_chinese_labels_with_stable_values(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.chart"))

        chart_type = next(item for item in node["inputs"] if item["key"] == "chart_type")
        self.assertEqual(
            chart_type["list"],
            [
                {"label": "自动", "value": "auto"},
                {"label": "折线图", "value": "line"},
                {"label": "柱状图", "value": "bar"},
                {"label": "饼图", "value": "pie"},
                {"label": "散点图", "value": "scatter"},
            ],
        )

    def test_calculator_and_http_request_are_not_builtin_tools(self):
        self.assertNotIn("builtin.calculator", BUILTIN_TOOL_MAP)
        self.assertNotIn("builtin.http_request", BUILTIN_TOOL_MAP)
        ids = {item["id"] for item in list_system_tool_templates(None, None)}
        self.assertNotIn("builtin.calculator", ids)
        self.assertNotIn("builtin.http_request", ids)


if __name__ == "__main__":
    unittest.main()
