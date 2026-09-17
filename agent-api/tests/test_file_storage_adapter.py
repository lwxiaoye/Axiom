import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.services.files import user_file_service
from app.services.files.storage import get_file_storage


def test_local_file_storage_round_trip(tmp_path):
    original_provider = getattr(settings, "FILE_STORAGE_PROVIDER", "local")
    original_root = settings.USER_FILES_DIR
    settings.FILE_STORAGE_PROVIDER = "local"
    settings.USER_FILES_DIR = str(tmp_path)
    try:
        storage = get_file_storage()

        async def _run():
            await storage.write_bytes("u1/demo.txt", b"hello", content_type="text/plain")
            assert await storage.exists("u1/demo.txt")
            assert await storage.read_bytes("u1/demo.txt") == b"hello"
            await storage.delete("u1/demo.txt")
            assert not await storage.exists("u1/demo.txt")

        asyncio.run(_run())
        assert not (Path(tmp_path) / "u1" / "demo.txt").exists()
    finally:
        settings.FILE_STORAGE_PROVIDER = original_provider
        settings.USER_FILES_DIR = original_root


def test_user_file_visibility_uses_storage_adapter(monkeypatch):
    class FakeStorage:
        async def exists(self, key: str) -> bool:
            return key == "remote/ok.txt"

    monkeypatch.setattr(user_file_service, "_storage", lambda: FakeStorage())
    rows = [
        SimpleNamespace(id="ok", storage_path="remote/ok.txt"),
        SimpleNamespace(id="gone", storage_path="remote/gone.txt"),
    ]

    visible = asyncio.run(user_file_service._rows_with_existing_bytes(rows, source="test"))

    assert [row.id for row in visible] == ["ok"]
