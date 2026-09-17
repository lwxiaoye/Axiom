from types import SimpleNamespace

import pytest

from scripts import migrate_user_files_to_object_storage as migrate


@pytest.mark.asyncio
async def test_copy_row_uploads_local_key_when_missing_from_target(monkeypatch, tmp_path):
    source_root = tmp_path / "local"
    key = "u1/current.txt"
    source_file = source_root / key
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"hello")

    uploaded = {}

    class TargetStorage:
        async def exists(self, candidate):
            return candidate in uploaded

        async def write_bytes(self, candidate, data, *, content_type=""):
            uploaded[candidate] = (data, content_type)

    result = await migrate.copy_row(
        SimpleNamespace(storage_path=key, mime="text/plain"),
        source_root=source_root,
        target_storage=TargetStorage(),
        dry_run=False,
    )

    assert result == "uploaded"
    assert uploaded == {key: (b"hello", "text/plain")}


@pytest.mark.asyncio
async def test_copy_row_skips_when_target_already_has_object(tmp_path):
    source_root = tmp_path / "local"
    key = "u1/current.txt"
    (source_root / "u1").mkdir(parents=True)
    (source_root / key).write_bytes(b"hello")

    class TargetStorage:
        async def exists(self, candidate):
            return True

        async def write_bytes(self, candidate, data, *, content_type=""):
            raise AssertionError("already migrated objects must not be overwritten")

    result = await migrate.copy_row(
        SimpleNamespace(storage_path=key, mime="text/plain"),
        source_root=source_root,
        target_storage=TargetStorage(),
        dry_run=False,
    )

    assert result == "exists"
