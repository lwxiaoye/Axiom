"""版式文档预览缓存后台预热(2026-07-20 进入预览慢优化)。

首次预览要起沙箱跑 LibreOffice(约 10-30s),用户点开干等。生成/覆盖文件后立刻后台预转,
用户点开时命中缓存秒显。回归目标:支持格式触发预转、非版式格式跳过、转换失败不外溢。
"""
import asyncio
import unittest

from app.services.files import user_file_service as ufs


class PreviewPrewarmTests(unittest.TestCase):
    def setUp(self):
        self._orig = ufs.get_preview_pdf
        self.calls = []

    def tearDown(self):
        ufs.get_preview_pdf = self._orig

    def _install(self, boom=False):
        async def _fake(user_id, file_id):
            self.calls.append((user_id, file_id))
            if boom:
                raise RuntimeError("sandbox busy")
            return b"%PDF-1.4 fake"
        ufs.get_preview_pdf = _fake

    def _drive(self, coro):
        async def _outer():
            coro()
            # 收敛后台预热任务(fire-and-forget)
            pending = [t for t in ufs._preview_prewarm_tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        asyncio.run(_outer())

    def test_supported_format_triggers_conversion(self):
        """docx/pptx/xlsx:后台预转被触发一次。"""
        self._install()
        # 实际支持集 = _PREVIEW_PDF_EXTS = {.doc/.docx/.ppt/.pptx/.xls/.xlsx}
        for name in ("方案.docx", "旧版.doc", "汇报.pptx", "旧版.ppt", "数据.xlsx", "旧版.xls"):
            self.calls.clear()
            self._drive(lambda n=name: ufs.schedule_preview_prewarm("u1", "F1", n))
            self.assertEqual(self.calls, [("u1", "F1")], f"{name} 应触发预转")

    def test_unsupported_format_skipped(self):
        """图片/纯文本/无扩展名:直接跳过,不起沙箱。"""
        self._install()
        for name in ("图表.png", "说明.txt", "笔记.md", "无扩展名", ""):
            self._drive(lambda n=name: ufs.schedule_preview_prewarm("u1", "F1", n))
        self.assertEqual(self.calls, [], "非版式格式不应触发预转")

    def test_conversion_failure_swallowed(self):
        """预转抛错:吞掉、不外溢(保存/对话主流程不受影响)。"""
        self._install(boom=True)
        # 不抛异常即通过
        self._drive(lambda: ufs.schedule_preview_prewarm("u1", "F1", "会失败.docx"))
        self.assertEqual(self.calls, [("u1", "F1")])

    def test_no_running_loop_is_silent(self):
        """无运行中事件循环(同步上下文)调用:静默跳过,不抛。"""
        self._install()
        ufs.schedule_preview_prewarm("u1", "F1", "同步.docx")  # 无 loop
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
