import json
import unittest
from unittest.mock import patch

from app.routers.workflow import _extract_run_chat_config


class RunChatConfigTests(unittest.TestCase):
    def test_extracts_welcome_text_and_quick_questions(self):
        workflow_json = json.dumps(
            {
                "nodes": [],
                "edges": [],
                "chatConfig": {
                    "welcomeText": "你好，我是助手",
                    "chatInputGuide": {
                        "open": True,
                        "textList": ["介绍一下你的能力", "  ", "帮我总结知识库"],
                    },
                },
            },
            ensure_ascii=False,
        )

        self.assertEqual(
            _extract_run_chat_config(workflow_json),
            {
                "welcomeText": "你好，我是助手",
                "quickQuestions": ["介绍一下你的能力", "帮我总结知识库"],
                "inspirationScenes": [
                    {
                        "key": "recommended",
                        "label": "推荐",
                        "tasks": ["介绍一下你的能力", "帮我总结知识库"],
                    }
                ],
                # 2026-07-17 起 chatConfig 恒带独立运行页扩展字段(远程「节点功能完善」)
                "variables": [], "fileSelectConfig": {}, "ttsConfig": {"type": "none"},
                "chatModel": "",
            },
        )

    def test_extracts_fastgpt_wrapped_chat_config(self):
        workflow_json = json.dumps(
            {
                "fastgpt": {
                    "chatConfig": {
                        "welcomeText": "欢迎",
                        "chatInputGuide": {"textList": ["问题一"]},
                    }
                }
            },
            ensure_ascii=False,
        )

        self.assertEqual(
            _extract_run_chat_config(workflow_json),
            {"welcomeText": "欢迎", "quickQuestions": ["问题一"],
             "inspirationScenes": [{"key": "recommended", "label": "推荐", "tasks": ["问题一"]}],
             "variables": [], "fileSelectConfig": {}, "ttsConfig": {"type": "none"},
             "chatModel": ""},
        )

    def test_interpolates_welcome_ctime_with_agent_business_timezone(self):
        workflow_json = json.dumps({"chatConfig": {"welcomeText": "现在是 {{cTime}}"}}, ensure_ascii=False)

        with patch("app.routers.workflow.format_agent_now", return_value="2026-08-28 15:40:00"):
            result = _extract_run_chat_config(workflow_json)

        self.assertEqual(result["welcomeText"], "现在是 2026-08-28 15:40:00")

    def test_invalid_json_returns_empty_config(self):
        self.assertEqual(
            _extract_run_chat_config("{bad"),
            {"welcomeText": "", "quickQuestions": [], "inspirationScenes": []},
        )

    def test_extracts_grouped_inspiration_scenes(self):
        workflow_json = json.dumps({
            "chatConfig": {
                "chatInputGuide": {
                    "open": True,
                    "textList": ["翻译成英文", "优化语气"],
                    "sceneList": [
                        {"key": "translate", "label": "翻译", "textList": ["翻译成英文"]},
                        {"key": "polish", "label": "润色", "textList": ["优化语气"]},
                        {"key": "empty", "label": "空场景", "textList": []},
                    ],
                }
            }
        }, ensure_ascii=False)

        result = _extract_run_chat_config(workflow_json)
        self.assertEqual(result["inspirationScenes"], [
            {"key": "translate", "label": "翻译", "tasks": ["翻译成英文"]},
            {"key": "polish", "label": "润色", "tasks": ["优化语气"]},
        ])

    def test_extracts_chat_node_model(self):
        workflow_json = json.dumps({
            "nodes": [{
                "flowNodeType": "chatNode",
                "inputs": [{"key": "model", "value": "deepseek-v4-flash-vision-exp"}],
            }],
            "chatConfig": {"welcomeText": "hi"},
        })
        self.assertEqual(
            _extract_run_chat_config(workflow_json)["chatModel"],
            "deepseek-v4-flash-vision-exp",
        )

    def test_presentation_keeps_only_safe_copy_and_drops_preset_and_style_fields(self):
        workflow_json = json.dumps({
            "chatConfig": {
                "presentation": {
                    "schemaVersion": 99,
                    "preset": "campus-welcome-v1",
                    "copy": {
                        "welcomeTitle": "欢迎来到校园",
                        "composerPlaceholder": "问问迎新助手",
                        "html": "<script>alert(1)</script>",
                    },
                    "css": "body { display: none }",
                    "backgroundUrl": "https://example.invalid/tracker.png",
                    "portableSkin": {"key": "x"},
                }
            }
        }, ensure_ascii=False)

        result = _extract_run_chat_config(workflow_json)

        self.assertEqual(result["presentation"], {
            "copy": {
                "welcomeTitle": "欢迎来到校园",
                "composerPlaceholder": "问问迎新助手",
            },
        })

    def test_presentation_copy_is_length_capped(self):
        workflow_json = json.dumps({
            "chatConfig": {
                "presentation": {"copy": {"welcomeTitle": "标" * 100, "composerPlaceholder": "占" * 150}}
            }
        }, ensure_ascii=False)

        copy = _extract_run_chat_config(workflow_json)["presentation"]["copy"]

        self.assertEqual(len(copy["welcomeTitle"]), 80)
        self.assertEqual(len(copy["composerPlaceholder"]), 120)

    def test_presentation_is_omitted_when_only_a_legacy_preset_is_present(self):
        # 旧草稿里可能还留着 preset key；现在只有一套默认外观，没有文案就不返回 presentation。
        for source in (
            {"preset": "campus-welcome-v1"},
            {"preset": "../../unsafe"},
            {"copy": {"welcomeTitle": "   "}},
            "not-an-object",
        ):
            workflow_json = json.dumps({"chatConfig": {"presentation": source}})
            self.assertNotIn("presentation", _extract_run_chat_config(workflow_json))
