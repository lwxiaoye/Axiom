import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.workflows.workflow_engine import NodeRun, RunContext, WorkflowEngine


class WorkflowChatKnowledgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_chat_retrieves_selected_knowledge_and_injects_quote_prompt(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "用户问题"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}, {"datasetId": "kb-2"}]},
                {"key": "aiChatQuoteRole", "value": "system"},
                {"key": "quoteTemplate", "value": "资料: {{q}} / {{source}}"},
                {"key": "quotePrompt", "value": "仅依据引用回答 {{question}}:\\n{{quote}}"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="默认问题", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[{"q": "片段", "sourceName": "文档"}])
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        engine._retrieve_knowledge_quotes.assert_awaited_once_with(["kb-1", "kb-2"], "用户问题")
        self.assertIn("仅依据引用回答 用户问题", captured_messages[0].content)
        self.assertIn("资料: 片段 / 文档", captured_messages[0].content)
        self.assertIn("知识库引用边界", captured_messages[0].content)
        self.assertIn("逐项回答", captured_messages[0].content)
        self.assertIn("学院、专业、班级", captured_messages[0].content)

    async def test_query_extension_keeps_original_question_and_outputs_query_array(self):
        node = {
            "nodeId": "rewrite",
            "flowNodeType": "cfr",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "学校地址"},
            ],
        }
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, RunContext(input_text="学校地址", user_id="u1"))
        engine._create_llm = lambda *args, **kwargs: object()
        engine._ainvoke_workflow_llm = AsyncMock(return_value=SimpleNamespace(
            content="学校各校区地址和位置\n学校校址",
        ))
        run = NodeRun(node_id="rewrite", node_type="cfr", node_label="问题优化")

        await engine._run_query_extension(node, run)

        self.assertEqual(
            engine.ctx.outputs["rewrite"]["system_text"],
            ["学校地址", "学校各校区地址和位置", "学校校址"],
        )
        self.assertEqual(run.output, engine.ctx.outputs["rewrite"]["system_text"])
        self.assertEqual(
            engine._ainvoke_workflow_llm.await_args.kwargs["request_options"],
            {"temperature": 0.1, "max_tokens": 128},
        )

    async def test_query_extension_defaults_to_three_history_rounds(self):
        node = {
            "nodeId": "rewrite",
            "flowNodeType": "cfr",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "那周末呢？"},
            ],
        }
        histories = [
            {"role": "user", "content": "最早的问题"},
            {"role": "assistant", "content": "最早的回答"},
            {"role": "user", "content": "第二轮问题"},
            {"role": "assistant", "content": "第二轮回答"},
            {"role": "user", "content": "第三轮问题"},
            {"role": "assistant", "content": "第三轮回答"},
            {"role": "user", "content": "第四轮问题"},
            {"role": "assistant", "content": "第四轮回答"},
        ]
        ctx = RunContext(input_text="那周末呢？", user_id="u1")
        ctx.variables["histories"] = histories
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, ctx)
        engine._create_llm = lambda *args, **kwargs: object()
        engine._ainvoke_workflow_llm = AsyncMock(return_value=SimpleNamespace(content="图书馆周末开放时间"))
        run = NodeRun(node_id="rewrite", node_type="cfr", node_label="问题优化")

        await engine._run_query_extension(node, run)

        prompt = engine._ainvoke_workflow_llm.await_args.args[1][0].content
        self.assertNotIn("最早的问题", prompt)
        self.assertIn("第二轮问题", prompt)
        self.assertIn("第四轮回答", prompt)

    async def test_query_extension_decomposes_multi_intent_question_with_constraints(self):
        node = {
            "nodeId": "rewrite",
            "flowNodeType": "cfr",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "我是26级新生，广阳校区地址在哪，需要去哪里报道，找哪个辅导员"},
            ],
        }
        question = "我是26级新生，广阳校区地址在哪，需要去哪里报道，找哪个辅导员"
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, RunContext(input_text=question, user_id="u1"))
        engine._create_llm = lambda *args, **kwargs: object()
        engine._ainvoke_workflow_llm = AsyncMock(return_value=SimpleNamespace(
            content=(
                "26级新生 广阳校区地址\n"
                "26级新生 广阳校区报到地点\n"
                "26级新生 辅导员对应学院专业班级规则"
            ),
        ))
        run = NodeRun(node_id="rewrite", node_type="cfr", node_label="问题优化")

        await engine._run_query_extension(node, run)

        self.assertEqual(
            engine.ctx.outputs["rewrite"]["system_text"],
            [
                question,
                "26级新生 广阳校区地址",
                "26级新生 广阳校区报到地点",
                "26级新生 辅导员对应学院专业班级规则",
            ],
        )

    async def test_query_extension_honors_configured_limit(self):
        node = {
            "nodeId": "rewrite",
            "flowNodeType": "cfr",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "maxQueries", "value": 6},
                {"key": "userChatInput", "value": "新生需要了解哪些事项"},
            ],
        }
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, RunContext(input_text="新生需要了解哪些事项", user_id="u1"))
        engine._create_llm = lambda *args, **kwargs: object()
        engine._ainvoke_workflow_llm = AsyncMock(return_value=SimpleNamespace(
            content="地址\n报到\n材料\n缴费\n住宿\n交通",
        ))
        run = NodeRun(node_id="rewrite", node_type="cfr", node_label="问题优化")

        await engine._run_query_extension(node, run)

        self.assertEqual(
            engine.ctx.outputs["rewrite"]["system_text"],
            ["新生需要了解哪些事项", "地址", "报到", "材料", "缴费", "住宿"],
        )
        self.assertEqual(run.input["maxQueries"], 6)

    def test_merge_query_quote_batches_gives_each_query_a_first_quote_before_filling(self):
        batches = [
            [{"id": "address"}, {"id": "address-more"}],
            [{"id": "checkin"}, {"id": "checkin-more"}],
            [{"id": "advisor"}],
        ]

        self.assertEqual(
            [quote["id"] for quote in WorkflowEngine._merge_query_quote_batches(batches)],
            ["address", "checkin", "advisor", "address-more", "checkin-more"],
        )

    async def test_dataset_search_executes_query_variants_in_parallel_and_deduplicates_quotes(self):
        node = {
            "nodeId": "search",
            "flowNodeType": "datasetSearchNode",
            "inputs": [
                {"key": "datasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "datasetSearchInput", "value": ["学校地址", "学校各校区地址"]},
            ],
        }
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, RunContext(input_text="学校地址", user_id="u1"))
        engine._retrieve_knowledge_quotes = AsyncMock(side_effect=[
            [
                {"id": "guangyang", "q": "广阳校区地址", "sourceName": "广阳校区"},
                {"id": "shared", "q": "学校地址总览", "sourceName": "总览"},
            ],
            [
                {"id": "shared", "q": "学校地址总览", "sourceName": "总览"},
                {"id": "east", "q": "东站校区地址", "sourceName": "东站校区"},
            ],
        ])
        run = NodeRun(node_id="search", node_type="datasetSearchNode", node_label="知识库搜索")

        await engine._run_dataset_search(node, run)

        self.assertEqual(engine._retrieve_knowledge_quotes.await_count, 2)
        self.assertEqual(
            [quote["id"] for quote in engine.ctx.outputs["search"]["quoteQA"]],
            ["guangyang", "shared", "east"],
        )
        self.assertEqual(run.input["queries"], ["学校地址", "学校各校区地址"])

    async def test_image_only_chat_skips_empty_knowledge_query_and_still_calls_vision_model(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": ""},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[])
        engine._load_runtime_attachments = AsyncMock(
            return_value=([], ["data:image/png;base64,YWJj"])
        )
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="图片讲解", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        engine._retrieve_knowledge_quotes.assert_not_awaited()
        human = captured_messages[-1].content
        self.assertIsInstance(human, list)
        self.assertEqual(human[-1]["image_url"]["url"], "data:image/png;base64,YWJj")
        self.assertEqual(run.output, "图片讲解")

    async def test_text_only_followup_marks_historical_images_as_not_current_attachments(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "住宿费一年多少钱？"},
                {"key": "history", "value": 6},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="住宿费一年多少钱？",
            user_id="u1",
            app_id="app1",
            variables={
                "histories": [
                    {"role": "assistant", "content": "你上传的图片是一整块纯黑色区域。"},
                ],
            },
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="住宿费1000元/生/年。", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        guard = next(
            message.content for message in captured_messages
            if message.__class__.__name__ == "SystemMessage" and "【本轮附件状态】" in message.content
        )
        self.assertIn("本轮没有上传任何附件", guard)
        self.assertIn("不得说‘你上传的图片’", guard)
        self.assertEqual(captured_messages[-1].content, "住宿费一年多少钱？")

    async def test_text_only_turn_does_not_load_historical_attachment_pixels(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": "住宿费一年多少钱？"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="住宿费一年多少钱？",
            user_id="u1",
            app_id="app1",
            variables={
                "currentTurnUserFileIds": [],
                "historicalUserFileIds": ["historical-image"],
                "userFileIds": [],
            },
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content="住宿费1000元/生/年。", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")
        with patch(
            "app.services.files.user_file_service.get_content",
            new=AsyncMock(),
        ) as get_content:
            await engine._run_chat(chat_node, run)

        get_content.assert_not_awaited()
        self.assertEqual(run.output, "住宿费1000元/生/年。")

    async def test_explicit_followup_loads_only_latest_historical_attachment(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": "再看看刚才那张图片"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="再看看刚才那张图片",
            user_id="u1",
            app_id="app1",
            variables={
                "currentTurnUserFileIds": [],
                "historicalUserFileIds": ["latest-image", "older-image"],
                "userFileIds": [],
            },
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="历史图片讲解", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")
        fake_row = SimpleNamespace(filename="latest.png", mime="image/png")
        with (
            patch(
                "app.services.files.user_file_service.get_content",
                new=AsyncMock(return_value={
                    "filename": "latest.png",
                    "kind": "image",
                    "text": "",
                    "status": "ok",
                }),
            ) as get_content,
            patch(
                "app.services.files.user_file_service.read_bytes",
                new=AsyncMock(return_value=(fake_row, b"png-bytes")),
            ),
            patch(
                "app.services.files.user_file_service._build_vision_data_url",
                return_value="data:image/png;base64,abc",
            ),
        ):
            await engine._run_chat(chat_node, run)

        get_content.assert_awaited_once_with(
            "u1", "latest-image", newapi_key="", ocr_embedded_images=False,
            ocr_visual=False,
        )
        guard = next(
            message.content for message in captured_messages
            if message.__class__.__name__ == "SystemMessage" and "【本轮附件状态】" in message.content
        )
        self.assertIn("本轮没有新上传附件", guard)
        self.assertIn("之前上传的附件", guard)

    async def test_history_output_does_not_persist_runtime_attachment_injection(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "请解释图片"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="请解释图片",
            user_id="u1",
            app_id="app1",
            variables={"userFileIds": ["file-img"]},
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        engine._load_runtime_attachments = AsyncMock(
            return_value=(["【用户上传图片《shot.png》已以多模态方式直传】"], ["data:image/png;base64,YWJj"])
        )

        class FakeLlm:
            async def ainvoke(self, messages):
                return SimpleNamespace(content="图片回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        history = engine.ctx.outputs["chat"]["history"]
        self.assertEqual(history[-2], {"role": "user", "content": "请解释图片"})
        self.assertNotIn("已以多模态方式直传", history[-2]["content"])

    async def test_dataset_search_forwards_configured_retrieval_parameters(self):
        node = {
            "nodeId": "search",
            "flowNodeType": "datasetSearchNode",
            "inputs": [
                {"key": "datasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "datasetSearchInput", "value": "新生报到地点"},
                {"key": "similarity", "value": 0.62},
                {"key": "searchMode", "value": "mixedRecall"},
                {"key": "embeddingWeight", "value": 0.7},
                {"key": "usingReRank", "value": True},
            ],
        }
        engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, RunContext(input_text="新生报到地点", user_id="u1"))
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[])
        run = NodeRun(node_id="search", node_type="datasetSearchNode", node_label="知识库搜索")

        await engine._run_dataset_search(node, run)

        engine._retrieve_knowledge_quotes.assert_awaited_once_with(
            ["kb-1"],
            "新生报到地点",
            0.62,
            retrieval_mode="HYBRID",
            semantic_weight=0.7,
            keyword_weight=0.30000000000000004,
            rerank_enabled=True,
        )

    async def test_vision_chat_preserves_inline_image_and_ignores_loose_metadata_image(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": "图里有什么"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="图里有什么", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        image_url = "/api/sys/common/static/knowledge/chunk-images/inline.png"
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[
            {
                "q": f"片段 1\n\n![知识库图片](<{image_url}>)\n\n片段 1 后续",
                "sourceName": "文档",
                "imageUrls": [image_url, image_url],
            },
            {
                "q": "片段 2",
                "sourceName": "文档",
                "imageUrls": ["https://example.com/image.png", "javascript:alert(1)"],
            },
        ])
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=f"回答\n\n![知识库图片](<{image_url}>)\n\n后续说明",
                    additional_kwargs={},
                )

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        human = captured_messages[-1].content
        self.assertIsInstance(human, str)
        self.assertNotIn("image_url", str(captured_messages))
        self.assertIn(image_url, str(captured_messages))
        self.assertIn("不要把图片集中挪到回答末尾", str(captured_messages))
        self.assertNotIn("学校资料中的相关图片", run.output)
        self.assertEqual(run.output.count(image_url), 1)
        self.assertNotIn("https://example.com/image.png", run.output)

    def test_select_knowledge_images_routes_explicit_resource_and_campus(self):
        quotes = [
            {
                "sourceName": "QR-001-微信公众号.md",
                "q": "QR-001｜微信公众号\n\n![二维码](<http://store:9098/bucket/qr.png>)",
                "imageUrls": ["http://store:9098/bucket/qr.png"],
            },
            {
                "sourceName": "MAP-000-广阳校区及东站校区地图知识库.md",
                "q": (
                    "MAP-000-广阳校区及东站校区地图知识库.md\n"
                    "MAP-002｜东站校区新生报到导视图\n\n校区名称：东站校区\n\n"
                    "![地图](<http://store:9098/bucket/dz.png>)"
                ),
                "imageUrls": ["http://store:9098/bucket/dz.png"],
            },
            {
                "sourceName": "MAP-000-广阳校区及东站校区地图知识库.md",
                "q": (
                    "MAP-000-广阳校区及东站校区地图知识库.md\n"
                    "本库包含广阳校区和东站校区。\n\n"
                    "MAP-001｜广阳校区新生报到导视图\n\n校区名称：广阳校区\n\n"
                    "![地图](<http://store:9098/bucket/gy.png>)"
                ),
                "imageUrls": ["http://store:9098/bucket/gy.png"],
            },
        ]
        all_images = [
            {"url": "http://store:9098/bucket/qr.png", "sourceName": "QR-001-微信公众号.md"},
            {"url": "http://store:9098/bucket/dz.png", "sourceName": "MAP-002-东站校区地图.md"},
            {"url": "http://store:9098/bucket/gy.png", "sourceName": "MAP-001-广阳校区地图.md"},
        ]
        all_images[1]["sourceName"] = "MAP-000-广阳校区及东站校区地图知识库.md"
        all_images[2]["sourceName"] = "MAP-000-广阳校区及东站校区地图知识库.md"
        self.assertEqual(
            WorkflowEngine._select_knowledge_images(quotes, "给我看广阳校区地图"),
            [all_images[2]],
        )
        self.assertEqual(
            WorkflowEngine._select_knowledge_images(quotes, "学校微信公众号二维码在哪里"),
            [all_images[0]],
        )
        self.assertEqual(
            WorkflowEngine._select_knowledge_images(quotes, "广阳校区和东站校区的地图都给我"),
            all_images[1:],
        )
        self.assertEqual(WorkflowEngine._select_knowledge_images(quotes, "书本费怎么交"), all_images)
        self.assertEqual(
            WorkflowEngine._select_knowledge_images(quotes[1:], "学校微信公众号二维码在哪里"),
            all_images[1:],
        )

    async def test_chat_appends_only_the_explicitly_requested_campus_map(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "东站校区内部地图"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="东站校区内部地图", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        east_url = "/api/sys/common/static/knowledge/east-station-map.png"
        guangyang_url = "/api/sys/common/static/knowledge/guangyang-map.png"
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[
            {
                "q": f"MAP-001｜广阳校区导视图\n校区名称：广阳校区\n![地图](<{guangyang_url}>)",
                "sourceName": "MAP-000-两校区地图.md",
                "imageUrls": [guangyang_url],
            },
            {
                "q": f"MAP-002｜东站校区导视图\n校区名称：东站校区\n![地图](<{east_url}>)",
                "sourceName": "MAP-000-两校区地图.md",
                "imageUrls": [east_url],
            },
        ])
        engine._ocr_knowledge_images = AsyncMock(return_value=([], 0))
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="这是东站校区导视图。", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        self.assertIn(east_url, run.output)
        self.assertNotIn(guangyang_url, run.output)
        self.assertIn(east_url, str(captured_messages))
        self.assertNotIn(guangyang_url, str(captured_messages))

    async def test_smart_mode_honors_model_image_choice_from_ambiguous_candidates(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "学校内部地图"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "aiChatKnowledgeImages", "value": "smart"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="学校内部地图", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        east_url = "/api/sys/common/static/knowledge/east-station-map.png"
        guangyang_url = "/api/sys/common/static/knowledge/guangyang-map.png"
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[
            {
                "q": f"MAP-001｜广阳校区导视图\n校区名称：广阳校区\n![地图](<{guangyang_url}>)",
                "sourceName": "MAP-000-两校区地图.md",
                "imageUrls": [guangyang_url],
            },
            {
                "q": f"MAP-002｜东站校区导视图\n校区名称：东站校区\n![地图](<{east_url}>)",
                "sourceName": "MAP-000-两校区地图.md",
                "imageUrls": [east_url],
            },
        ])

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(
                    content=f"根据问题，这里展示东站校区图：\n\n![地图](<{east_url}>)",
                    additional_kwargs={},
                )

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        self.assertEqual(run.output.count(east_url), 1)
        self.assertNotIn(guangyang_url, run.output)
        self.assertNotIn("学校资料中的相关图片", run.output)

    def test_metadata_only_document_image_is_not_eligible_for_output(self):
        quotes = [{
            "sourceName": "03-图书馆常见问题_问答20250314.docx",
            "q": "问题52：微信公众号\n答案：",
            "imageUrls": ["http://store:9098/chunk-images/opaque-asset.png"],
        }]

        self.assertEqual(WorkflowEngine._select_knowledge_images(quotes, "图书馆开到几点？"), [])

    def test_knowledge_image_links_do_not_leak_into_later_model_turns(self):
        historical_answer = (
            "请查看下方地图。\n\n"
            "学校资料中的相关图片：\n\n"
            "![广阳地图](</api/sys/common/static/bucket/gy.png>)"
        )
        self.assertEqual(
            WorkflowEngine._strip_knowledge_image_suffix(historical_answer),
            "请查看下方地图。",
        )
        self.assertEqual(
            WorkflowEngine._strip_markdown_images(
                "公众号资料 ![旧图](https://example.com/old-map.png) 请以文字为准"
            ),
            "公众号资料  请以文字为准",
        )

    def test_browser_knowledge_image_url_proxies_private_object_storage(self):
        self.assertEqual(
            WorkflowEngine._browser_knowledge_image_url(
                "http://127.0.0.1:9098/ai-platform/knowledge/chunk-images/map.png"
            ),
            "/api/sys/common/static/ai-platform/knowledge/chunk-images/map.png",
        )

    def test_select_knowledge_images_keeps_java_static_relative_url(self):
        image_url = (
            "/api/sys/common/static/knowledge/chunk-images/2092173131618717697/"
            "2093277065199931394/0/image6131094135.png"
        )

        self.assertEqual(
            WorkflowEngine._select_knowledge_images(
                [{
                    "sourceName": "document.docx",
                    "q": f"校区地图\n\n![地图](<{image_url}>)",
                    "imageUrls": [image_url],
                }],
                "给我看图片",
            ),
            [{"url": image_url, "sourceName": "document.docx"}],
        )

    async def test_on_site_registration_flow_returns_recalled_knowledge_image(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "2026级新生现场报到流程"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="2026级新生现场报到流程", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        image_url = "/api/sys/common/static/knowledge/check-in-flow.png"
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[{
            "q": f"请按现场报到指引完成材料核验与入学手续。\n\n![知识库图片](<{image_url}>)",
            "sourceName": "2026迎新图文导航",
            "imageUrls": [image_url],
        }])
        engine._ocr_knowledge_images = AsyncMock(return_value=([], 0))

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content="请按报到指引办理。", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        self.assertIn("学校资料中的相关图片", run.output)
        self.assertIn(f"![学校资料中的图片 1](<{image_url}>)", run.output)

    async def test_model_placed_recalled_image_is_not_appended_twice(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "东站校区内部地图"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="东站校区内部地图", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        image_url = "/api/sys/common/static/knowledge/east-station-map.png"
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[{
            "q": f"东站校区导视图\n\n![知识库图片](<{image_url}>)",
            "sourceName": "MAP-002-东站校区地图.md",
            "imageUrls": [image_url],
        }])
        engine._ocr_knowledge_images = AsyncMock(return_value=([], 0))

        class FakeLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(
                    content=f"这是东站校区导视图：\n\n![知识库图片](<{image_url}>)",
                    additional_kwargs={},
                )

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        self.assertEqual(run.output.count(image_url), 1)
        self.assertNotIn("学校资料中的相关图片", run.output)

    async def test_smart_mode_does_not_append_image_for_non_image_question(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": "书本费、体检费和医保费怎么交？"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="书本费、体检费和医保费怎么交？",
            user_id="u1",
            app_id="app1",
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=[{
            "q": "书本费由班级与教材供应商结算。\n\n![资料图](<data:image/png;base64,YWJj>)",
            "sourceName": "新生入学须知.docx",
            "imageUrls": ["data:image/png;base64,YWJj"],
        }])
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        human = captured_messages[-1].content
        self.assertIsInstance(human, str)
        self.assertNotIn("data:image", str(captured_messages))
        self.assertNotIn("学校资料中的相关图片", run.output)
        self.assertNotIn("data:image/png;base64,YWJj", run.output)
        guard = next(
            message.content for message in captured_messages
            if message.__class__.__name__ == "SystemMessage" and "【本轮附件状态】" in message.content
        )
        self.assertIn("本轮没有上传任何附件", guard)

    async def test_text_chat_sends_recalled_images_through_platform_ocr(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "图里有什么"},
                {"key": "aiChatDatasets", "value": [{"datasetId": "kb-1"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="图里有什么", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        recalled = [{
            "q": "片段\n\n![知识库图片](<data:image/png;base64,YWJj>)",
            "sourceName": "文档",
            "imageUrls": ["data:image/png;base64,YWJj"],
        }]
        engine._retrieve_knowledge_quotes = AsyncMock(return_value=recalled)
        engine._ocr_knowledge_images = AsyncMock(return_value=(["【图片 1】\n人物站在门前"], 0))
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        await engine._run_chat(chat_node, run)

        engine._ocr_knowledge_images.assert_awaited_once_with([
            {"url": "data:image/png;base64,YWJj", "sourceName": "文档"},
        ])
        self.assertIn("人物站在门前", captured_messages[0].content)
        self.assertNotIn("data:image/", str(captured_messages))
        self.assertIsInstance(captured_messages[-1].content, str)

    async def test_recalled_image_ocr_reuses_platform_document_parser(self):
        ctx = RunContext(
            input_text="默认问题",
            user_id="u1",
            app_id="app1",
            llm_api_key="newapi-key",
        )
        engine = WorkflowEngine({"nodes": [], "edges": [], "chatConfig": {}}, ctx)
        parse_upload = AsyncMock(return_value={
            "text": "【图片内容（由视觉模型识别）】\n测试图",
            "status": "ok",
        })

        with patch(
            "app.services.files.document_parse_service.parse_upload",
            new=parse_upload,
        ):
            blocks, failures = await engine._ocr_knowledge_images([{
                "url": "data:image/png;base64,YWJj",
                "sourceName": "文档",
            }])

        parse_upload.assert_awaited_once_with(
            "knowledge-image-1.png",
            b"abc",
            newapi_key="newapi-key",
            ocr_embedded_images=True,
            ocr_visual=True,
        )
        self.assertEqual(failures, 0)
        self.assertIn("测试图", blocks[0])


    async def test_ai_chat_injects_selected_skill_docs(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "systemPrompt", "value": "基础提示"},
                {"key": "userChatInput", "value": "用户问题"},
                {"key": "skills", "value": [{"skillId": "skill-1", "name": "数据分析", "source": "mine"}]},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(input_text="默认问题", token="token-1", user_id="u1", app_id="app1")
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        captured_messages = []

        async def fake_skill_docs(engine_arg, node_arg):
            self.assertIs(engine_arg, engine)
            self.assertIs(node_arg, chat_node)
            return [{"skillId": "skill-1", "name": "数据分析", "content": "SKILL.md 正文"}]

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")

        with patch("app.services.agents.agent_executor._load_selected_skill_content_docs", fake_skill_docs):
            await engine._run_chat(chat_node, run)

        self.assertIn("基础提示", captured_messages[0].content)
        self.assertIn("数据分析", captured_messages[0].content)
        self.assertIn("SKILL.md 正文", captured_messages[0].content)

    async def test_ai_chat_resolves_standalone_runtime_file_ids_into_user_content(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "test-model"},
                {"key": "userChatInput", "value": "请提取字段"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="默认问题",
            user_id="u1",
            app_id="app1",
            variables={"userFileIds": ["file-1"]},
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")
        with patch(
            "app.services.files.user_file_service.get_content",
            new=AsyncMock(return_value={
                "filename": "验收.docx",
                "text": "合同编号：DOC-001",
                "status": "ok",
            }),
        ) as get_content:
            await engine._run_chat(chat_node, run)

        get_content.assert_awaited_once_with(
            "u1", "file-1", newapi_key="", ocr_embedded_images=True,
            ocr_visual=True,
        )
        self.assertIn("验收.docx", captured_messages[-1].content)
        self.assertIn("合同编号：DOC-001", captured_messages[-1].content)

    async def test_vision_chat_sends_uploaded_image_pixels_without_ocr(self):
        chat_node = {
            "nodeId": "chat",
            "flowNodeType": "chatNode",
            "name": "AI 对话",
            "inputs": [
                {"key": "model", "value": "deepseek-v4-flash-vision-exp"},
                {"key": "aiChatVision", "value": True},
                {"key": "userChatInput", "value": "图里有什么"},
                {"key": "isResponseAnswerText", "value": True},
            ],
            "outputs": [],
        }
        ctx = RunContext(
            input_text="图里有什么",
            user_id="u1",
            app_id="app1",
            variables={"userFileIds": ["file-img"]},
        )
        engine = WorkflowEngine({"nodes": [chat_node], "edges": [], "chatConfig": {}}, ctx)
        captured_messages = []

        class FakeLlm:
            async def ainvoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(content="回答", additional_kwargs={})

        engine._create_llm = lambda *args, **kwargs: FakeLlm()
        run = NodeRun(node_id="chat", node_type="chatNode", node_label="AI 对话")
        fake_row = SimpleNamespace(filename="shot.png", mime="image/png")
        with (
            patch(
                "app.services.files.user_file_service.get_content",
                new=AsyncMock(return_value={
                    "filename": "shot.png",
                    "kind": "image",
                    "text": "",
                    "status": "ok",
                }),
            ) as get_content,
            patch(
                "app.services.files.user_file_service.read_bytes",
                new=AsyncMock(return_value=(fake_row, b"png-bytes")),
            ),
            patch(
                "app.services.files.user_file_service._build_vision_data_url",
                return_value="data:image/png;base64,abc",
            ),
            patch(
                "app.services.files.user_file_service.document_page_data_urls",
                new=AsyncMock(return_value=([], "")),
            ),
        ):
            await engine._run_chat(chat_node, run)

        get_content.assert_awaited_once_with(
            "u1", "file-img", newapi_key="", ocr_embedded_images=False,
            ocr_visual=False,
        )
        human = captured_messages[-1].content
        self.assertIsInstance(human, list)
        self.assertTrue(any(part.get("type") == "image_url" for part in human))


if __name__ == "__main__":
    unittest.main()
