from __future__ import annotations

import asyncio
from functools import cached_property

from app.core.config import settings

from .base import clean_key


class MinioFileStorage:
    @cached_property
    def _client(self):
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - exercised only in misconfigured runtime
            raise RuntimeError("MinIO 存储已启用，但 Python 依赖 minio 未安装") from exc
        raw_endpoint = str(settings.MINIO_ENDPOINT or "").strip().rstrip("/")
        secure = raw_endpoint.startswith("https://") or bool(settings.MINIO_SECURE)
        endpoint = raw_endpoint.replace("http://", "").replace("https://", "")
        if not endpoint or not settings.MINIO_ACCESS_KEY or not settings.MINIO_SECRET_KEY:
            raise RuntimeError("MinIO 存储配置不完整：请设置 MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY")
        # v2.40：list_files 会对每个文件 stat_object；默认 urllib3 pool=10 在并行 exists
        # 时狂刷 “Connection pool is full”。加大池并允许更多溢出连接。
        http_client = None
        try:
            import urllib3
            http_client = urllib3.PoolManager(
                num_pools=4,
                maxsize=32,
                retries=urllib3.Retry(total=2, backoff_factor=0.2),
            )
        except Exception:
            http_client = None
        return Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=secure,
            http_client=http_client,
        )

    @property
    def _bucket(self) -> str:
        bucket = str(settings.FILE_STORAGE_BUCKET or "").strip()
        if not bucket:
            raise RuntimeError("MinIO 存储配置不完整：请设置 FILE_STORAGE_BUCKET")
        return bucket

    def _key(self, key: str) -> str:
        base = clean_key(settings.FILE_STORAGE_PREFIX)
        item = clean_key(key)
        return f"{base}/{item}" if base else item

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def exists(self, key: str) -> bool:
        def _exists() -> bool:
            try:
                self._client.stat_object(self._bucket, self._key(key))
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_exists)

    async def read_bytes(self, key: str) -> bytes:
        def _read() -> bytes:
            obj = self._client.get_object(self._bucket, self._key(key))
            try:
                return obj.read()
            finally:
                obj.close()
                obj.release_conn()

        return await asyncio.to_thread(_read)

    async def write_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        def _write() -> None:
            from io import BytesIO

            self._ensure_bucket()
            self._client.put_object(
                self._bucket,
                self._key(key),
                BytesIO(data),
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )

        await asyncio.to_thread(_write)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.remove_object(self._bucket, self._key(key))
            except Exception:
                pass

        await asyncio.to_thread(_delete)

    async def delete_prefix(self, prefix: str) -> None:
        def _delete_prefix() -> None:
            from minio.deleteobjects import DeleteObject

            base = self._key(prefix).rstrip("/") + "/"
            objects = self._client.list_objects(self._bucket, prefix=base, recursive=True)
            self._client.remove_objects(
                self._bucket,
                (DeleteObject(obj.object_name) for obj in objects),
            )

        await asyncio.to_thread(_delete_prefix)
