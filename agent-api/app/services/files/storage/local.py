from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.core.config import settings

from .base import clean_key


class LocalFileStorage:
    def _path(self, key: str) -> Path:
        return Path(settings.USER_FILES_DIR) / clean_key(key)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def write_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(lambda: self._path(key).unlink(missing_ok=True))

    async def delete_prefix(self, prefix: str) -> None:
        await asyncio.to_thread(lambda: shutil.rmtree(self._path(prefix), ignore_errors=True))
