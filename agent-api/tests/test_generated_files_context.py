"""本会话生成物「可原地修改」上下文注入(2026-07-20)。

回归目标:用户生成文件后说「换个色调/改一下」时,模型上下文里要出现该产物的 file_id +
确切文件名 + 「原地改、直接做、别问、别复述内部机制」指引——从根上消除「重新生成一份新
文件 / 反问用户要不要重生 / 向用户泄漏 SVG-PPTX 管线等内部机制」这三种不人性化行为。
"""
import asyncio
import unittest

from app.services.files import thread_attachment_service as tas


def _run(coro):
    return asyncio.run(coro)


class GeneratedFilesContextTests(unittest.TestCase):
    def setUp(self):
        self._orig_load = tas._load_thread_attachments
        self._orig_list = None
        from app.services.files import user_file_service
        self._ufs = user_file_service
        self._orig_list = user_file_service.list_generated_files

    def tearDown(self):
        tas._load_thread_attachments = self._orig_load
        self._ufs.list_generated_files = self._orig_list

    def _patch(self, uploaded, generated):
        async def _fake_load(_tid, _uid):
            return list(uploaded)

        async def _fake_list(_uid, _tid, limit=12):
            return list(generated)

        tas._load_thread_attachments = _fake_load
        self._ufs.list_generated_files = _fake_list

    def test_generated_only_surfaces_block_with_guidance(self):
        """无上传附件、有生成物:上下文非空,含 file_id/文件名 + 三条人性化指引。"""
        self._patch(uploaded=[], generated=[
            {"id": "F123", "filename": "节能减排.pptx", "size": 512000},
        ])
        ctx = _run(tas.build_file_context(
            thread_id="t1", user_id="u1", query="换个色调", attachments=None))
        self.assertIn("节能减排.pptx", ctx)
        self.assertIn("file_id=F123", ctx)
        # ① 原地改不新建
        self.assertIn("原文件上修改", ctx)
        self.assertIn("完全相同的文件名", ctx)
        # ② 直接做、别问、别泄漏内部机制
        self.assertIn("直接动手做完", ctx)
        self.assertIn("不要向用户复述内部机制", ctx)
        self.assertIn("SVG/PPTX", ctx)  # 点名禁止复述的内部机制
        # ③ 收尾措辞不吓人
        self.assertIn("不要说成", ctx)

    def test_no_files_returns_empty(self):
        """既无上传也无生成物:保持原「返回空串」语义,不凭空注入。"""
        self._patch(uploaded=[], generated=[])
        ctx = _run(tas.build_file_context(
            thread_id="t1", user_id="u1", query="你好", attachments=None))
        self.assertEqual(ctx, "")

    def test_uploaded_and_generated_both_present(self):
        """既有上传附件又有生成物:两块并存,上传块在前、生成物块在后。"""
        self._patch(
            uploaded=[{"filename": "需求.txt", "text": "把封面换成蓝色", "sha256": "d1"}],
            generated=[{"id": "F9", "filename": "方案.docx", "size": 20480}],
        )

        async def _fake_retrieve(_q, text):
            return text
        orig_retrieve = tas.session_file_service.retrieve_relevant
        tas.session_file_service.retrieve_relevant = _fake_retrieve
        try:
            ctx = _run(tas.build_file_context(
                thread_id="t1", user_id="u1", query="改一下", attachments=None))
        finally:
            tas.session_file_service.retrieve_relevant = orig_retrieve
        self.assertIn("用户上传过的文件", ctx)      # 上传块
        self.assertIn("需求.txt", ctx)
        self.assertIn("方案.docx", ctx)             # 生成物块
        self.assertLess(ctx.index("需求.txt"), ctx.index("方案.docx"))  # 顺序:上传在前

    def test_generated_list_failure_degrades_silently(self):
        """列生成物抛错:降级为空块,不阻断(仍能返回上传块或空串)。"""
        async def _boom(_uid, _tid, limit=12):
            raise RuntimeError("db down")
        async def _fake_load(_tid, _uid):
            return []
        tas._load_thread_attachments = _fake_load
        self._ufs.list_generated_files = _boom
        ctx = _run(tas.build_file_context(
            thread_id="t1", user_id="u1", query="改一下", attachments=None))
        self.assertEqual(ctx, "")


if __name__ == "__main__":
    unittest.main()
