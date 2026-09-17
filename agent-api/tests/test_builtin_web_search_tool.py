import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("httpx", types.SimpleNamespace(AsyncClient=object))

from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, execute_builtin_tool
from app.services.workflows.node_template_service import build_preview_node


class BuiltinWebSearchToolTest(unittest.TestCase):
    def test_catalog_contains_web_search_tool(self):
        tool = BUILTIN_TOOL_MAP["builtin.web_search"]

        self.assertEqual(tool["name"], "联网搜索")
        self.assertEqual(tool["category"], "搜索")
        self.assertEqual(tool["inputKeys"], ["query", "with_images"])
        self.assertEqual(
            tool["outputKeys"],
            ["text", "results", "images", "scraped_pages", "enabled"],
        )

    def test_preview_exposes_query_and_image_inputs_with_structured_outputs(self):
        node = asyncio.run(build_preview_node(None, None, template_id="builtin.web_search"))

        inputs = {item["key"]: item for item in node["inputs"]}
        self.assertEqual(inputs["query"]["label"], "搜索关键词")
        self.assertEqual(inputs["query"]["renderTypeList"], ["textarea", "reference"])
        self.assertEqual(inputs["with_images"]["label"], "包含图片")
        self.assertEqual(inputs["with_images"]["renderTypeList"], ["switch", "reference"])
        outputs = {item["key"]: item for item in node["outputs"]}
        self.assertEqual(outputs["text"]["label"], "搜索摘要")
        self.assertEqual(outputs["results"]["valueType"], "arrayObject")
        self.assertIn("system_rawResponse", outputs)

    def test_execute_reuses_existing_web_search_service(self):
        search = AsyncMock(
            return_value={
                "enabled": True,
                "results": [{"title": "标题", "url": "https://example.com", "content": "摘要"}],
                "text": "[1] 标题\n摘要\n来源: https://example.com",
                "scraped_pages": [{"title": "标题", "url": "https://example.com"}],
                "images": [{"url": "https://example.com/a.png"}],
            }
        )
        with patch("app.services.knowledge.web_search_service.search_web", search):
            result = asyncio.run(
                execute_builtin_tool(
                    "builtin.web_search",
                    {"query": "杭州 2026 新能源补贴", "with_images": True},
                )
            )

        search.assert_awaited_once_with("杭州 2026 新能源补贴", start_index=1, with_images=True)
        self.assertEqual(result["text"], "[1] 标题\n摘要\n来源: https://example.com")
        self.assertTrue(result["enabled"])
        self.assertEqual(result["images"][0]["url"], "https://example.com/a.png")

    def test_execute_treats_string_false_as_disabled_images(self):
        search = AsyncMock(
            return_value={
                "enabled": True,
                "results": [],
                "text": "",
                "scraped_pages": [],
                "images": [],
            }
        )
        with patch("app.services.knowledge.web_search_service.search_web", search):
            asyncio.run(
                execute_builtin_tool(
                    "builtin.web_search",
                    {"query": "杭州 2026 新能源补贴", "with_images": "false"},
                )
            )

        search.assert_awaited_once_with("杭州 2026 新能源补贴", start_index=1, with_images=False)

    def test_execute_requires_query(self):
        with self.assertRaisesRegex(ValueError, "搜索关键词不能为空"):
            asyncio.run(execute_builtin_tool("builtin.web_search", {"query": "  "}))


if __name__ == "__main__":
    unittest.main()
