"""「最近的对话」引用（2026-07-28）：把选中的历史会话渲染成转录附件注入本轮。

回归目标集中在三处最容易悄悄坏掉的地方：
- **归属与自引用**：别人的会话读不到、当前会话不重复注入；
- **裁剪的诚实性**：预算不够时必须留下"更早的 N 条被省略"的痕迹，不能悄悄给半截；
- **附件名不误触办公工具**：标题里带 .xlsx 的会话不该让本轮凭空多出 Excel 工具。
"""
import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.services.chat import thread_reference as tr


def _run(coro):
    return asyncio.run(coro)


def _msg(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


class ThreadReferenceTests(unittest.TestCase):
    def setUp(self):
        self._orig = (tr.async_session, tr._load_thread, tr._load_messages, tr._load_artifacts)

        @asynccontextmanager
        async def _fake_session():
            yield object()

        tr.async_session = _fake_session

    def tearDown(self):
        (tr.async_session, tr._load_thread, tr._load_messages, tr._load_artifacts) = self._orig

    def _patch(self, threads: dict, messages: dict, artifacts: dict | None = None):
        """threads: {tid: title|None（None=不存在/无权）}；messages: {tid: [msg]}。"""
        async def _load_thread(_s, _uid, tid):
            title = threads.get(tid)
            return SimpleNamespace(title=title) if title is not None else None

        async def _load_messages(_s, tid):
            return list(messages.get(tid) or [])

        async def _load_artifacts(_s, _uid, tid):
            return list((artifacts or {}).get(tid) or [])

        tr._load_thread = _load_thread
        tr._load_messages = _load_messages
        tr._load_artifacts = _load_artifacts

    # ---- 基本注入 ----

    def test_renders_transcript_with_roles(self):
        self._patch(
            {"t1": "上海补贴政策"},
            {"t1": [_msg("user", "上海的补贴比例是多少"), _msg("assistant", "最高 30%，上限 50 万")]},
        )
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "ok")
        self.assertIn("上海补贴政策", out[0]["text"])
        self.assertIn("【用户】上海的补贴比例是多少", out[0]["text"])
        self.assertIn("【助手】最高 30%，上限 50 万", out[0]["text"])
        # file_id 必须留空：它不是「我的文件」里的文件，塞个假 id 会诱导模型去调 read_file
        self.assertEqual(out[0]["file_id"], "")

    def test_artifacts_listed_with_read_hint(self):
        """产物清单是「上次那个 PPT」这类追问的承载物：给文件名 + 怎么读。"""
        self._patch(
            {"t1": "做个 PPT"},
            {"t1": [_msg("user", "做个节能减排的 PPT")]},
            {"t1": ["节能减排.pptx"]},
        )
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        self.assertIn("节能减排.pptx", out[0]["text"])
        self.assertIn("read_file", out[0]["text"])

    # ---- 归属 / 自引用 / 去重 ----

    def test_missing_or_foreign_thread_degrades_not_raises(self):
        """别人的会话（_load_thread 返 None）降级为 failed 占位块，不掀翻整轮。"""
        self._patch({}, {})
        out = _run(tr.build_thread_attachments("u1", ["nope"]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "failed")
        self.assertIn("不存在或无权访问", out[0]["text"])

    def test_current_thread_is_filtered_out(self):
        self._patch({"t1": "标题"}, {"t1": [_msg("user", "你好")]})
        out = _run(tr.build_thread_attachments("u1", ["t1"], current_thread_id="t1"))
        self.assertEqual(out, [])

    def test_duplicate_ids_collapse(self):
        self._patch({"t1": "标题"}, {"t1": [_msg("user", "你好")]})
        out = _run(tr.build_thread_attachments("u1", ["t1", "t1", " t1 "]))
        self.assertEqual(len(out), 1)

    def test_over_limit_reports_instead_of_silently_dropping(self):
        titles = {f"t{i}": f"会话{i}" for i in range(5)}
        msgs = {f"t{i}": [_msg("user", "你好")] for i in range(5)}
        self._patch(titles, msgs)
        out = _run(tr.build_thread_attachments("u1", [f"t{i}" for i in range(5)]))
        self.assertEqual(len(out), tr.MAX_THREADS + 1)
        self.assertEqual(out[-1]["status"], "failed")
        self.assertIn("未被读取", out[-1]["text"])

    # ---- 裁剪的诚实性 ----

    def test_budget_overflow_keeps_newest_and_states_omission(self):
        """预算不够时保留**最新**的几条，并明写更早的被省略了多少条。"""
        long_msgs = [_msg("user", f"第{i}条：" + "内" * 400) for i in range(60)]
        long_msgs.append(_msg("assistant", "最后一条结论"))
        self._patch({"t1": "长会话"}, {"t1": long_msgs})
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        text = out[0]["text"]
        self.assertIn("最后一条结论", text)          # 最新的留下了
        self.assertNotIn("第0条", text)              # 最早的被裁了
        self.assertIn("条消息因篇幅未包含在内", text)  # 缺口如实写出
        # 裁剪不是故障：status 保持 ok，否则每引用一个长会话都被逼出「我没读全」的开场白
        self.assertEqual(out[0]["status"], "ok")

    def test_summary_replaces_omitted_head_when_available(self):
        long_msgs = [_msg("user", f"第{i}条：" + "内" * 400) for i in range(60)]
        self._patch({"t1": "长会话"}, {"t1": long_msgs})

        class _FakeCtx:
            @staticmethod
            async def get_summary(_tid):
                return {"summary": "这次会话讨论了节能改造的三种方案。"}

        import app.services.memory as memory_pkg
        orig = memory_pkg.context_service
        memory_pkg.context_service = _FakeCtx
        try:
            out = _run(tr.build_thread_attachments("u1", ["t1"]))
        finally:
            memory_pkg.context_service = orig
        self.assertIn("这次会话讨论了节能改造的三种方案。", out[0]["text"])

    def test_single_oversized_message_still_yields_content(self):
        """哪怕第一条就撑爆预算也要留下它——空转录会让用户以为引用生效了其实什么都没带。"""
        self._patch({"t1": "巨型"}, {"t1": [_msg("assistant", "论" * 20000)]})
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        self.assertIn("【助手】", out[0]["text"])
        self.assertIn("中间省略", out[0]["text"])

    def test_empty_thread_says_so(self):
        self._patch({"t1": "空会话"}, {"t1": []})
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        self.assertIn("该会话暂无消息", out[0]["text"])

    # ---- 附件名不能误触办公工具 ----

    def test_filename_suffix_defuses_office_kind_detection(self):
        """标题叫「帮我改 report.xlsx」的会话不该被 tool_scope 当成本轮在处理 Excel。"""
        from app.services.chat.tools.tool_scope import resolve_tool_scope

        self._patch({"t1": "帮我改 report.xlsx"}, {"t1": [_msg("user", "改一下")]})
        out = _run(tr.build_thread_attachments("u1", ["t1"]))
        scope = resolve_tool_scope(message="这次说点别的", attachments=out)
        self.assertNotIn("workbook", scope.artifact_kinds)
        self.assertFalse(scope.office_tools)


class ThreadRefNotAThreadAssetTests(unittest.TestCase):
    """引用块借 attachments 通道，但绝不能沾上「会话资产」那一套语义。

    这两条是 2026-07-28 实现时踩到的真坑：附件通道默认会把内容持久化成 thread 级资产、
    并在此后每一轮用向量检索重新注入——照单全收的话，「引用一次」会变成「永久粘在这个
    会话上，每轮重灌几千 token」，而且转录会被相关度切碎、连"更早 N 条已省略"一起切没。
    """

    def test_thread_ref_is_not_persisted_as_thread_asset(self):
        from app.services.files import thread_attachment_service as tas

        captured = {"called": False}

        def _fake_runtime_session():
            captured["called"] = True
            return None  # 到这一步就说明它没被提前过滤掉

        orig = tas.runtime_session
        tas.runtime_session = _fake_runtime_session
        try:
            _run(tas.persist_for_message(
                thread_id="t", user_id="u", message_id=1,
                attachments=[{"filename": "旧对话（对话记录）", "kind": tas.THREAD_REF_KIND,
                              "text": "【用户】你好", "file_id": ""}],
            ))
            self.assertFalse(captured["called"], "引用块不应进入会话资产持久化")

            # 反面：普通上传附件必须照旧走到持久化（别把过滤写成一刀切）
            _run(tas.persist_for_message(
                thread_id="t", user_id="u", message_id=1,
                attachments=[{"filename": "a.txt", "kind": "text", "text": "正文", "file_id": "F1"}],
            ))
            self.assertTrue(captured["called"], "普通附件仍应持久化")
        finally:
            tas.runtime_session = orig

    def test_thread_ref_injected_verbatim_not_retrieved(self):
        from app.services.files import thread_attachment_service as tas

        orig_load = tas._load_thread_attachments
        orig_gen = tas._build_generated_files_block
        orig_retrieve = tas.session_file_service.retrieve_relevant

        async def _no_persisted(_tid, _uid):
            return []

        async def _no_generated(_tid, _uid):
            return ""

        async def _boom(_q, _t):
            raise AssertionError("引用块不该走向量重检索")

        tas._load_thread_attachments = _no_persisted
        tas._build_generated_files_block = _no_generated
        tas.session_file_service.retrieve_relevant = _boom
        try:
            ctx = _run(tas.build_file_context(
                thread_id="t", user_id="u", query="无关的问题",
                attachments=[{
                    "filename": "旧对话（对话记录）", "kind": tas.THREAD_REF_KIND, "file_id": "",
                    "text": "【用户】上海补贴多少\n\n（注意：该会话更早的 12 条消息因篇幅未包含在内。）",
                }],
            ))
        finally:
            tas._load_thread_attachments = orig_load
            tas._build_generated_files_block = orig_gen
            tas.session_file_service.retrieve_relevant = orig_retrieve

        self.assertIn("【用户】上海补贴多少", ctx)
        self.assertIn("更早的 12 条消息因篇幅未包含在内", ctx)  # 省略说明没被切掉
        self.assertIn("显式引用", ctx)                        # 带上"这不是本会话内容"的定性


if __name__ == "__main__":
    unittest.main()
