from __future__ import annotations

from functools import lru_cache

from app.core.config import settings

from .base import FileStorage
from .local import LocalFileStorage
from .minio import MinioFileStorage


@lru_cache(maxsize=4)
def _storage_for(provider: str) -> FileStorage:
    normalized = str(provider or "local").strip().lower()
    if normalized == "minio":
        return MinioFileStorage()
    return LocalFileStorage()


def get_file_storage() -> FileStorage:
    return _storage_for(str(getattr(settings, "FILE_STORAGE_PROVIDER", "local") or "local"))


def clear_file_storage_cache() -> None:
    _storage_for.cache_clear()
