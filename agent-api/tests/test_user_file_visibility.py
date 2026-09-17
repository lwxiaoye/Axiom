"""文件清单只暴露真实可读的字节，不让幽灵元数据污染附件选择。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.services.files import user_file_service


class ExistingBytesFilterTests(unittest.TestCase):
    def test_missing_blob_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "u1").mkdir()
            (root / "u1" / "ok.txt").write_text("ok", encoding="utf-8")
            rows = [
                SimpleNamespace(id="ok", storage_path="u1/ok.txt"),
                SimpleNamespace(id="gone", storage_path="u1/gone.txt"),
            ]
            class _LocalStorage:
                async def exists(self, path):
                    return (root / path).is_file()
            original = settings.USER_FILES_DIR
            original_storage = user_file_service._storage
            settings.USER_FILES_DIR = str(root)
            user_file_service._storage = lambda: _LocalStorage()
            try:
                visible = asyncio.run(
                    user_file_service._rows_with_existing_bytes(
                        rows, source="test",
                    )
                )
            finally:
                settings.USER_FILES_DIR = original
                user_file_service._storage = original_storage
        self.assertEqual([row.id for row in visible], ["ok"])
