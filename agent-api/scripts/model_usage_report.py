#!/usr/bin/env python3
"""Print a read-only JSON report from the durable model-usage ledger.

Examples:

    python scripts/model_usage_report.py --hours 24 --pretty
    python scripts/model_usage_report.py --start 2026-08-29T00:00:00Z \
        --end 2026-08-30T00:00:00Z --model deepseek-v4-flash

``--key-id`` accepts the non-reversible SHA-256 Provider-key fingerprint stored by
the attempt ledger.  Raw credentials are never accepted, persisted, or printed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.services.agent_harness.model_usage_report import (  # noqa: E402
    ModelUsageReportFilters,
    build_model_usage_report,
)


def _parse_datetime(value: str) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate logical model calls, Provider attempts, usage, LCP, and alerts.",
    )
    parser.add_argument("--hours", type=float, default=24.0, help="window size when --start is absent")
    parser.add_argument("--start", type=_parse_datetime, help="inclusive ISO-8601 start")
    parser.add_argument("--end", type=_parse_datetime, help="exclusive ISO-8601 end")
    parser.add_argument("--user-id", action="append", default=[], help="repeatable user filter")
    parser.add_argument(
        "--key-id",
        action="append",
        default=[],
        help="repeatable SHA-256 Provider-key fingerprint filter",
    )
    parser.add_argument("--model", action="append", default=[], help="repeatable model filter")
    parser.add_argument("--root-run-id", action="append", default=[], help="repeatable root Run filter")
    parser.add_argument("--purpose", action="append", default=[], help="repeatable purpose filter")
    parser.add_argument("--outcome", action="append", default=[], help="repeatable root outcome filter")
    parser.add_argument("--timeout", type=float, default=2.0, help="read query timeout in seconds")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    end_at = args.end or datetime.now(timezone.utc)
    if args.start is not None:
        start_at = args.start
    else:
        start_at = end_at - timedelta(hours=max(0.01, float(args.hours)))
    filters = ModelUsageReportFilters(
        start_at=start_at,
        end_at=end_at,
        user_ids=tuple(str(value) for value in args.user_id if str(value)),
        key_ids=tuple(str(value) for value in args.key_id if str(value)),
        models=tuple(str(value) for value in args.model if str(value)),
        root_run_ids=tuple(str(value) for value in args.root_run_id if str(value)),
        purposes=tuple(str(value) for value in args.purpose if str(value)),
        outcomes=tuple(str(value) for value in args.outcome if str(value)),
    )
    return await build_model_usage_report(filters, timeout_seconds=args.timeout)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    print(json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if args.pretty else None,
        separators=None if args.pretty else (",", ":"),
    ))
    # An unavailable observability database is represented in JSON rather than making an Agent
    # process fail.  Invalid operator input still receives a conventional CLI error.
    return 2 if report.get("status") == "invalid_filter" else 0


if __name__ == "__main__":
    raise SystemExit(main())
