"""Copy existing user-file bytes from local disk to the configured object store.

The database keeps relative storage_path values. This script preserves those
keys, so switching FILE_STORAGE_PROVIDER from local to minio needs no schema
change and can be rolled back by setting the provider back to local.

Usage inside agent-api:
    python scripts/migrate_user_files_to_object_storage.py --dry-run
    python scripts/migrate_user_files_to_object_storage.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import async_session  # noqa: E402
from app.models import AgentUserFile, AgentUserFileVersion  # noqa: E402
from app.services.files.storage import get_file_storage  # noqa: E402
from app.services.files.storage.base import clean_key  # noqa: E402

logger = logging.getLogger(__name__)


def local_path_for(source_root: Path, key: str) -> Path:
    return source_root / clean_key(key)


async def copy_row(row, *, source_root: Path, target_storage, dry_run: bool) -> str:
    key = clean_key(getattr(row, "storage_path", "") or "")
    if not key:
        return "invalid_key"
    if await target_storage.exists(key):
        return "exists"

    source_path = local_path_for(source_root, key)
    if not source_path.is_file():
        return "missing_source"
    if dry_run:
        return "would_upload"

    data = await asyncio.to_thread(source_path.read_bytes)
    await target_storage.write_bytes(key, data, content_type=getattr(row, "mime", "") or "")
    if await target_storage.exists(key):
        return "uploaded"
    return "verify_failed"


async def iter_rows(model, batch: int, limit: int | None) -> Iterable:
    seen = 0
    offset = 0
    while limit is None or seen < limit:
        size = min(batch, limit - seen) if limit is not None else batch
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(model)
                    .where(model.storage_path.isnot(None))
                    .order_by(model.id)
                    .offset(offset)
                    .limit(size)
                )
            ).scalars().all()
        if not rows:
            break
        for row in rows:
            yield row
            seen += 1
            if limit is not None and seen >= limit:
                break
        offset += len(rows)


async def migrate(*, dry_run: bool, batch: int, limit: int | None) -> Counter:
    if str(settings.FILE_STORAGE_PROVIDER or "").lower() != "minio":
        raise RuntimeError("Set FILE_STORAGE_PROVIDER=minio before running this migration.")

    source_root = Path(settings.USER_FILES_DIR)
    target_storage = get_file_storage()
    summary: Counter = Counter()

    for model in (AgentUserFile, AgentUserFileVersion):
        async for row in iter_rows(model, batch=batch, limit=limit):
            result = await copy_row(
                row,
                source_root=source_root,
                target_storage=target_storage,
                dry_run=dry_run,
            )
            summary[f"{model.__tablename__}.{result}"] += 1
            if result in {"missing_source", "verify_failed"}:
                logger.warning("%s %s: %s", model.__tablename__, result, row.storage_path)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy local user files to configured MinIO storage.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report work without writing MinIO.")
    mode.add_argument("--apply", action="store_true", help="Upload missing objects to MinIO.")
    parser.add_argument("--batch", type=int, default=200, help="Rows fetched per batch.")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-table limit for smoke tests.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = asyncio.run(migrate(dry_run=args.dry_run, batch=max(1, args.batch), limit=args.limit))
    for key, value in sorted(summary.items()):
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
