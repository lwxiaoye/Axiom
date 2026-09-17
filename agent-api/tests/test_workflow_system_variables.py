import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.workflows.workflow_engine import NodeRun, RunContext, WorkflowEngine, WorkflowExecutionError


class WorkflowSystemVariablesTest(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_ctime_uses_agent_business_timezone(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "text",
                    "flowNodeType": "textEditor",
                    "name": "文本",
                    "inputs": [{"key": "system_textareaInput", "value": "{{cTime}}"}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "text",
                    "targetHandle": "text-target-left",
                }
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="", variables={}, user_id="u1", app_id="app1")

        with patch("app.services.workflows.workflow_engine.format_agent_now", return_value="2026-08-28 15:40:00"):
            await WorkflowEngine(graph, ctx).run()

        self.assertEqual(ctx.variables["cTime"], "2026-08-28 15:40:00")
        self.assertEqual(ctx.outputs["text"]["system_text"], "2026-08-28 15:40:00")

    async def test_tool_arguments_interpolate_workflow_variables(self):
        graph = {
            "nodes": [
                {
                    "nodeId": "text2sql",
                    "flowNodeType": "tool",
                    "name": "Text to SQL",
                    "inputs": [
                        {"key": "query_text", "value": "{{userChatInput}}"},
                        {"key": "database_schema", "value": "users(id bigint, name varchar)"},
                    ],
                    "outputs": [],
                },
            ],
            "edges": [],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="列出所有用户", variables={}, user_id="u1", app_id="app1")

        args = WorkflowEngine(graph, ctx)._tool_call_args(graph["nodes"][0])  # noqa: SLF001

        self.assertEqual(args["query_text"], "列出所有用户")

    async def test_conditional_loop_without_break_fails_before_running_body(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "loop",
                    "flowNodeType": "loopRun",
                    "name": "循环节点",
                    "inputs": [{"key": "loopRunMode", "value": "conditional"}],
                    "outputs": [{"key": "loopArray"}],
                },
                {
                    "nodeId": "loopStart",
                    "parentNodeId": "loop",
                    "flowNodeType": "loopRunStart",
                    "name": "循环体",
                    "inputs": [],
                    "outputs": [],
                },
                {
                    "nodeId": "bodyText",
                    "parentNodeId": "loop",
                    "flowNodeType": "textEditor",
                    "name": "循环体文本",
                    "inputs": [{"key": "system_textareaInput", "value": "should-not-run"}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "loop",
                    "targetHandle": "loop-target-left",
                },
                {
                    "source": "loopStart",
                    "sourceHandle": "loopStart-source-right",
                    "target": "bodyText",
                    "targetHandle": "bodyText-target-left",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="开始", variables={}, user_id="u1", app_id="app1")

        engine = WorkflowEngine(graph, ctx)
        run = NodeRun(node_id="loop", node_type="loopRun", node_label="循环节点")

        with self.assertRaisesRegex(WorkflowExecutionError, "条件循环必须.*跳出循环"):
            await engine._run_loop_run(graph["nodes"][1], run)  # noqa: SLF001

        self.assertNotIn("bodyText", ctx.outputs)

    async def test_engine_interpolates_selected_upstream_output(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "text",
                    "flowNodeType": "textEditor",
                    "name": "文本",
                    "inputs": [{"key": "system_textareaInput", "value": "问题:{{node:start:userChatInput}}"}],
                    "outputs": [],
                },
            ],
            "edges": [{"source": "start", "sourceHandle": "start-source-right", "target": "text", "targetHandle": "text-target-left"}],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="问题", variables={}, user_id="u1", app_id="app1")

        await WorkflowEngine(graph, ctx).run()

        self.assertEqual(ctx.outputs["text"]["system_text"], "问题:问题")

    async def test_code_node_injects_static_output_for_downstream_node(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "code",
                    "flowNodeType": "code",
                    "name": "代码运行",
                    "inputs": [
                        {"key": "codeType", "value": "py"},
                        {"key": "code", "value": 'def main():\n    return {"result": "已注入"}'},
                    ],
                    "outputs": [{"key": "result", "type": "static"}],
                },
                {
                    "nodeId": "answer",
                    "flowNodeType": "answerNode",
                    "name": "回复",
                    "inputs": [{"key": "text", "value": ["code", "result"]}],
                    "outputs": [],
                },
            ],
            "edges": [
                {"source": "start", "sourceHandle": "start-source-right", "target": "code", "targetHandle": "code-target-left"},
                {"source": "code", "sourceHandle": "code-source-right", "target": "answer", "targetHandle": "answer-target-left"},
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="", variables={}, user_id="u1", app_id="app1")

        async def fake_run_code(*_args, **_kwargs):
            return SimpleNamespace(
                ok=True,
                stdout='__CODE_RESULT__{"result":"已注入"}',
                stderr="",
                exit_code=0,
                error=None,
            )

        with patch("app.services.sandbox.code_runner.run_code", fake_run_code):
            await WorkflowEngine(graph, ctx).run()

        self.assertEqual(ctx.outputs["code"]["result"], "已注入")
        self.assertEqual(ctx.outputs["answer"]["answerText"], "已注入")

    async def test_engine_defaults_realname_and_histories_for_interpolation(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "text",
                    "flowNodeType": "textEditor",
                    "name": "文本",
                    "inputs": [{"key": "system_textareaInput", "value": "姓名:{{realname}} 历史:{{histories}}"}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "text",
                    "targetHandle": "text-target-left",
                }
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="你好", variables={}, user_id="u1", app_id="app1", user_name="张三")

        engine = WorkflowEngine(graph, ctx)
        await engine.run()

        self.assertEqual(ctx.outputs["text"]["system_text"], "姓名:张三 历史:[]")

    async def test_start_node_outputs_request_user_files(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
            ],
            "edges": [],
            "chatConfig": {},
        }
        ctx = RunContext(
            input_text="分析附件",
            variables={
                "userFiles": [
                    " https://example.com/a.pdf ",
                    "",
                    {"url": "bad"},
                    "https://example.com/b.docx",
                ]
            },
            user_id="u1",
            app_id="app1",
        )

        engine = WorkflowEngine(graph, ctx)
        run = NodeRun(node_id="start", node_type="workflowStart", node_label="开始")
        await engine._run_start(graph["nodes"][0], run)  # noqa: SLF001

        expected = ["https://example.com/a.pdf", "https://example.com/b.docx"]
        self.assertEqual(ctx.outputs["start"]["userFiles"], expected)
        self.assertEqual(run.output["userFiles"], expected)
        self.assertEqual(ctx.outputs["start"]["userFileIds"], [])
        self.assertEqual(run.output["userFileIds"], [])

    async def test_custom_feedback_output_is_referenceable(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "feedback",
                    "flowNodeType": "customFeedback",
                    "name": "自定义反馈",
                    "inputs": [{"key": "system_textareaInput", "value": "反馈:{{node:start:userChatInput}}"}],
                    "outputs": [{"key": "system_text"}],
                },
                {
                    "nodeId": "text",
                    "flowNodeType": "textEditor",
                    "name": "文本",
                    "inputs": [{"key": "system_textareaInput", "value": ["feedback", "system_text"]}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "feedback",
                    "targetHandle": "feedback-target-left",
                },
                {
                    "source": "feedback",
                    "sourceHandle": "feedback-source-right",
                    "target": "text",
                    "targetHandle": "text-target-left",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="需要修改", variables={}, user_id="u1", app_id="app1")

        await WorkflowEngine(graph, ctx).run()

        self.assertEqual(ctx.outputs["feedback"]["system_text"], "反馈:需要修改")
        self.assertEqual(ctx.outputs["text"]["system_text"], "反馈:需要修改")

    async def test_classify_question_output_is_referenceable_and_reported(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "classify",
                    "flowNodeType": "classifyQuestion",
                    "name": "问题分类",
                    "inputs": [
                        {"key": "model", "value": "test-model"},
                        {"key": "userChatInput", "value": ["start", "userChatInput"]},
                        {"key": "agents", "value": [{"key": "a", "value": "打招呼"}, {"key": "b", "value": "其他问题"}]},
                    ],
                    "outputs": [{"key": "cqResult", "required": True}],
                },
                {
                    "nodeId": "answer",
                    "flowNodeType": "answerNode",
                    "name": "回复",
                    "inputs": [{"key": "text", "value": ["classify", "cqResult"]}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "classify",
                    "targetHandle": "classify-target-left",
                },
                {
                    "source": "classify",
                    "sourceHandle": "classify-source-b",
                    "target": "answer",
                    "targetHandle": "answer-target-left",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="帮我看一下报销", variables={}, user_id="u1", app_id="app1")
        engine = WorkflowEngine(graph, ctx)

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content='{"index": 2}')

        engine._create_llm = lambda *_args, **_kwargs: FakeLlm()

        await engine.run()

        self.assertEqual(ctx.outputs["classify"]["cqResult"], "其他问题")
        self.assertEqual(ctx.outputs["answer"]["answerText"], "其他问题")
        classify_run = next(run for run in ctx.node_runs if run.node_id == "classify")
        self.assertEqual(classify_run.output, {"cqResult": "其他问题"})
        self.assertEqual(classify_run.to_dict()["outputs"], {"cqResult": "其他问题"})

    async def test_answer_node_does_not_duplicate_direct_ai_reply_reference(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "ai",
                    "flowNodeType": "chatNode",
                    "name": "AI",
                    "inputs": [
                        {"key": "model", "value": "test-model"},
                        {"key": "userChatInput", "value": ["start", "userChatInput"]},
                        {"key": "isResponseAnswerText", "value": True},
                    ],
                    "outputs": [{"key": "answerText"}, {"key": "history"}],
                },
                {
                    "nodeId": "answer",
                    "flowNodeType": "answerNode",
                    "name": "指定回复",
                    "inputs": [{"key": "text", "value": ["ai", "answerText"]}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "ai",
                    "targetHandle": "ai-target-left",
                },
                {
                    "source": "ai",
                    "sourceHandle": "ai-source-right",
                    "target": "answer",
                    "targetHandle": "answer-target-left",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="你好", variables={}, user_id="u1", app_id="app1")
        engine = WorkflowEngine(graph, ctx)

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content="您好！有什么我可以帮忙的吗？")

        engine._create_llm = lambda *_args, **_kwargs: FakeLlm()

        await engine.run()

        self.assertEqual(ctx.outputs["ai"]["answerText"], "您好！有什么我可以帮忙的吗？")
        self.assertEqual(ctx.outputs["answer"]["answerText"], "您好！有什么我可以帮忙的吗？")
        self.assertEqual(ctx.output_parts, [(1, "您好！有什么我可以帮忙的吗？")])

    async def test_answer_node_does_not_duplicate_direct_ai_reply_template(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "ai",
                    "flowNodeType": "chatNode",
                    "name": "AI",
                    "inputs": [
                        {"key": "model", "value": "test-model"},
                        {"key": "userChatInput", "value": ["start", "userChatInput"]},
                        {"key": "isResponseAnswerText", "value": True},
                    ],
                    "outputs": [{"key": "answerText"}, {"key": "history"}],
                },
                {
                    "nodeId": "answer",
                    "flowNodeType": "answerNode",
                    "name": "指定回复",
                    "inputs": [{"key": "text", "value": "{{node:ai:answerText}}"}],
                    "outputs": [],
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "ai",
                    "targetHandle": "ai-target-left",
                },
                {
                    "source": "ai",
                    "sourceHandle": "ai-source-right",
                    "target": "answer",
                    "targetHandle": "answer-target-left",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="你好", variables={}, user_id="u1", app_id="app1")
        engine = WorkflowEngine(graph, ctx)

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content="您好！有什么我可以帮忙的吗？")

        engine._create_llm = lambda *_args, **_kwargs: FakeLlm()

        await engine.run()

        self.assertEqual(ctx.outputs["answer"]["answerText"], "您好！有什么我可以帮忙的吗？")
        self.assertEqual(ctx.output_parts, [(1, "您好！有什么我可以帮忙的吗？")])

    async def test_tool_call_uses_tool_node_configured_args_over_model_args(self):
        graph = {
            "nodes": [
                {"nodeId": "start", "flowNodeType": "workflowStart", "name": "开始", "inputs": [], "outputs": []},
                {
                    "nodeId": "tools",
                    "flowNodeType": "tools",
                    "name": "工具调用",
                    "inputs": [
                        {"key": "model", "value": "test-model"},
                        {"key": "userChatInput", "value": "现在几点"},
                    ],
                    "outputs": [],
                },
                {
                    "nodeId": "time",
                    "flowNodeType": "tool",
                    "name": "当前时间",
                    "inputs": [
                        {
                            "key": "format",
                            "value": "yyyy-MM-dd HH:mm",
                            "valueType": "string",
                            "toolDescription": "日期格式",
                        }
                    ],
                    "outputs": [],
                    "toolConfig": {"systemTool": {"toolId": "builtin.datetime"}},
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "sourceHandle": "start-source-right",
                    "target": "tools",
                    "targetHandle": "tools-target-left",
                },
                {
                    "source": "tools",
                    "sourceHandle": "selectedTools",
                    "target": "time",
                    "targetHandle": "selectedTools",
                },
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="现在几点", variables={}, user_id="u1", app_id="app1")
        captured_args = {}

        async def fake_loop(_engine, **kwargs):
            tool = kwargs["tools"][0]
            model_args = {"format": "yyyy-MM-dd HH:mm:ss"}
            result = await tool.execute(model_args)
            captured_args.update({"result": result})
            return result, [{"name": tool.name, "args": model_args, "result": result}]

        async def fake_builtin(_tool_id, args, _context=None):
            captured_args.update(args)
            return args["format"]

        with patch("app.services.agents.agent_executor.run_function_call_loop", fake_loop), patch(
            "app.services.gateway.tool_invoker.invoke_builtin_tool", fake_builtin
        ):
            await WorkflowEngine(graph, ctx).run()

        self.assertEqual(captured_args["format"], "yyyy-MM-dd HH:mm")
        self.assertEqual(captured_args["result"], "yyyy-MM-dd HH:mm")
        self.assertEqual(ctx.outputs["tools"]["answerText"], "yyyy-MM-dd HH:mm")
        tools_run = next(run for run in ctx.node_runs if run.node_id == "tools")
        self.assertEqual(tools_run.output["toolCalls"][0]["args"]["format"], "yyyy-MM-dd HH:mm")

    async def test_tool_call_suppresses_mounted_answer_node_direct_output(self):
        graph = {
            "nodes": [
                {
                    "nodeId": "tools",
                    "flowNodeType": "tools",
                    "name": "工具调用",
                    "inputs": [{"key": "model", "value": "test-model"}],
                    "outputs": [],
                },
                {
                    "nodeId": "answerTool",
                    "flowNodeType": "answerNode",
                    "name": "固定回复",
                    "inputs": [{"key": "text", "value": "同一条回复"}],
                    "outputs": [],
                    "toolDescription": "返回固定回复",
                },
            ],
            "edges": [
                {
                    "source": "tools",
                    "sourceHandle": "selectedTools",
                    "target": "answerTool",
                    "targetHandle": "selectedTools",
                }
            ],
            "chatConfig": {},
        }
        ctx = RunContext(input_text="调用工具", variables={}, user_id="u1", app_id="app1")
        engine = WorkflowEngine(graph, ctx)
        run = NodeRun(node_id="tools", node_type="tools", node_label="工具调用")
        run.output_seq = 7

        async def fake_loop(_engine, **kwargs):
            tool = kwargs["tools"][0]
            result = await tool.execute({})
            return result, [{"name": tool.name, "result": result}]

        with patch("app.services.agents.agent_executor.run_function_call_loop", fake_loop):
            await engine._run_tool_call(graph["nodes"][0], run)  # noqa: SLF001

        self.assertEqual(ctx.outputs["tools"]["answerText"], "同一条回复")
        self.assertEqual(ctx.output_parts, [(7, "同一条回复")])
        self.assertNotIn("answerTool", ctx.outputs)
        self.assertNotIn("answerTool", ctx.response_node_ids)


if __name__ == "__main__":
    unittest.main()
