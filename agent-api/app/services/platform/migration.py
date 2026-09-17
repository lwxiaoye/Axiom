import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models import ChatThread, ChatMessage

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
THREADS_FILE = DATA_DIR / "threads.json"


def _parse_iso(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


async def migrate_threads_json_if_needed() -> None:
    if not THREADS_FILE.exists():
        return

    async with async_session() as session:
        existing_count = await session.scalar(select(ChatThread).limit(1))
        if existing_count is not None:
            return

        try:
            raw = json.loads(THREADS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read threads.json for migration: %s", exc)
            return

        for thread_id, tdata in raw.items():
            thread = ChatThread(
                id=thread_id,
                user_id=tdata["user_id"],
                title=(tdata.get("title") or "")[:255],
                created_at=_parse_iso(tdata.get("created_at")),
                updated_at=_parse_iso(tdata.get("updated_at")),
            )
            session.add(thread)
            await session.flush()

            for idx, msg in enumerate(tdata.get("messages", []), start=1):
                session.add(
                    ChatMessage(
                        thread_id=thread_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        created_at=_parse_iso(tdata.get("created_at")),
                    )
                )

        await session.commit()

    THREADS_FILE.rename(THREADS_FILE.with_suffix(".json.imported"))
    logger.info("Migrated legacy threads.json into MySQL and renamed file.")
