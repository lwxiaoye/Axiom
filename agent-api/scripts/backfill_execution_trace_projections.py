"""Backfill terminal history projections from a former Runtime PostgreSQL store.

The script is dry-run by default. Set ``SOURCE_RUNTIME_DATABASE_URL`` to the
Runtime store that originally executed the Run, then pass ``--apply`` after the
MySQL 0005 migration is installed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import async_session
from app.models import ChatMessage
from app.services.chat import history_trace_projection
from app.services.tasks import task_run_service


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", action="append", default=[], help="Only backfill these Run ids")
    parser.add_argument("--apply", action="store_true", help="Write projections to MySQL")
    parser.add_argument(
        "--source-checkpoint-env-file",
        help="Read CHECKPOINT_DATABASE_URL from this env file and derive its agent_runtime database",
    )
    return parser.parse_args()


def _normalise_async_runtime_url(raw: str, *, derive_runtime_database: bool) -> str:
    source = str(raw or "").strip().strip('"').strip("'")
    if not source:
        return ""
    after_scheme = source.split("://", 1)[-1]
    if derive_runtime_database:
        base = after_scheme.rsplit("/", 1)[0]
        after_scheme = f"{base}/agent_runtime"
    return f"postgresql+psycopg://{after_scheme}"


def _source_url(args: argparse.Namespace) -> str:
    explicit = str(os.environ.get("SOURCE_RUNTIME_DATABASE_URL") or "").strip()
    if explicit:
        return _normalise_async_runtime_url(explicit, derive_runtime_database=False)
    if not args.source_checkpoint_env_file:
        return ""
    env_path = Path(args.source_checkpoint_env_file)
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CHECKPOINT_DATABASE_URL="):
            return _normalise_async_runtime_url(
                line.split("=", 1)[1],
                derive_runtime_database=True,
            )
    return ""


async def main() -> int:
    args = _args()
    source_url = _source_url(args)
    if not source_url:
        raise SystemExit(
            "SOURCE_RUNTIME_DATABASE_URL or --source-checkpoint-env-file is required"
        )

    source_engine = create_async_engine(source_url, pool_pre_ping=True)
    source_factory = sessionmaker(
        source_engine, class_=AsyncSession, expire_on_commit=False,
    )
    original_task_factory = task_run_service.runtime_session
    original_projection_factory = history_trace_projection.runtime_session
    task_run_service.runtime_session = lambda: source_factory  # type: ignore[assignment]
    history_trace_projection.runtime_session = lambda: source_factory  # type: ignore[assignment]
    try:
        async with async_session() as session:
            query = select(ChatMessage).where(
                ChatMessage.role == "assistant",
                ChatMessage.run_id.isnot(None),
                ChatMessage.execution_trace_json.is_(None),
            )
            if args.run_id:
                query = query.where(ChatMessage.run_id.in_([str(item) for item in args.run_id]))
            rows = (await session.execute(query.order_by(ChatMessage.id.asc()))).scalars().all()

        print(f"candidates={len(rows)} mode={'apply' if args.apply else 'dry-run'}")
        if not args.apply:
            for row in rows:
                print(f"message_id={row.id} run_id={row.run_id}")
            return 0

        written = 0
        for row in rows:
            if await history_trace_projection.persist_terminal_execution_trace_projection(
                str(row.run_id), int(row.id),
            ):
                written += 1
        print(f"written={written} skipped={len(rows) - written}")
        return 0 if written == len(rows) else 2
    finally:
        task_run_service.runtime_session = original_task_factory
        history_trace_projection.runtime_session = original_projection_factory
        await source_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
