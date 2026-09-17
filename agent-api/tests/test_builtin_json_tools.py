import asyncio
import sys
import types
import unittest

sys.modules.setdefault("httpx", types.SimpleNamespace(AsyncClient=object))

from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, execute_builtin_tool


class BuiltinJsonToolTest(unittest.TestCase):
    def test_json_parse_returns_array_value_for_workflow_references(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {
                    "json": (
                        '[{"id":"qwen3.7-max-2026-05-20","is_default":true},'
                        '{"id":"glm-4-plus","is_default":false}]'
                    )
                },
            )
        )

        self.assertIsInstance(value, list)
        self.assertEqual(value[0]["id"], "qwen3.7-max-2026-05-20")
        self.assertEqual(value[1]["id"], "glm-4-plus")

    def test_json_parse_returns_object_value_for_workflow_references(self):
        value = asyncio.run(execute_builtin_tool("builtin.json_parse", {"json": '{"data":[1,2]}'}))

        self.assertEqual(value, {"data": [1, 2]})

    def test_json_parse_accepts_python_literal_style_json(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {"json": "[{'id': 'qwen3.7-max-2026-05-20', 'is_default': True}]"},
            )
        )

        self.assertEqual(value, [{"id": "qwen3.7-max-2026-05-20", "is_default": True}])

    def test_json_parse_accepts_bare_object_keys(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {"json": '[{id: "qwen3.7-max-2026-05-20", name: "qwen", is_default: true}]'},
            )
        )

        self.assertEqual(value[0]["id"], "qwen3.7-max-2026-05-20")
        self.assertIs(value[0]["is_default"], True)

    def test_json_parse_accepts_fenced_json(self):
        value = asyncio.run(execute_builtin_tool("builtin.json_parse", {"json": "```json\n[{\"id\":\"glm\"}]\n```"}))

        self.assertEqual(value, [{"id": "glm"}])

    def test_json_parse_extracts_json_from_surrounding_text(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {"json": '模型列表如下：[{id: "qwen3.7-max-2026-05-20", is_default: true}]，请处理。'},
            )
        )

        self.assertEqual(value, [{"id": "qwen3.7-max-2026-05-20", "is_default": True}])

    def test_json_parse_accepts_backslash_escaped_json(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {"json": r'[{\"id\":\"qwen3.7-max-2026-05-20\"},{\"id\":\"glm-4-plus\"}]'},
            )
        )

        self.assertEqual([item["id"] for item in value], ["qwen3.7-max-2026-05-20", "glm-4-plus"])

    def test_json_parse_accepts_json_wrapped_as_string(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.json_parse",
                {"json": '"[{\\"id\\":\\"qwen3.7-max-2026-05-20\\"}]"'},
            )
        )

        self.assertEqual(value, [{"id": "qwen3.7-max-2026-05-20"}])

    def test_json_parse_rejects_scalar_json(self):
        with self.assertRaisesRegex(ValueError, "对象或数组"):
            asyncio.run(execute_builtin_tool("builtin.json_parse", {"json": '"plain text"'}))

    def test_json_parse_is_in_builtin_catalog(self):
        tool = BUILTIN_TOOL_MAP["builtin.json_parse"]

        self.assertEqual(tool["name"], "JSON 转换")
        self.assertEqual(tool["inputKeys"], ["json"])
        self.assertEqual(tool["outputKeys"], ["result"])


if __name__ == "__main__":
    unittest.main()
