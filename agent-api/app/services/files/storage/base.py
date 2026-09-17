from __future__ import annotations

from typing import Protocol


class FileStorage(Protocol):
    async def exists(self, key: str) -> bool:
        ...

    async def read_bytes(self, key: str) -> bytes:
        ...

    async def write_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        ...

    async def delete(self, key: str) -> None:
        ...

    async def delete_prefix(self, prefix: str) -> None:
        ...


def clean_key(key: str) -> str:
    value = str(key or "").replace("\\", "/").lstrip("/")
    parts = [part for part in value.split("/") if part and part not in (".", "..")]
    return "/".join(parts)
