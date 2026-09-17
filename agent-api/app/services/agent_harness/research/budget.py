"""Durable research collection target; report calls use shared model recovery."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app.services.agent_harness import run_store

TOTAL_SECONDS = 600
REPORT_RESERVE_SECONDS = 300
FINDINGS_SECONDS = 180
MEMBER_WRAPUP_SECONDS = 40
PLAN_SECONDS = 30
ASSESS_SECONDS = 30
SUPPLEMENT_SECONDS = 45


def remaining(env, *, reserve=0, cap=None):
    deadline = getattr(env, "research_deadline", None)
    seconds = max(0.0, float(deadline) - time.time() - reserve) if deadline is not None else TOTAL_SECONDS - reserve
    return min(seconds, cap) if cap is not None else seconds


async def ensure_budget(env):
    packed = await run_store.get_run_state(env.run_id)
    if packed is None:
        raise RuntimeError("研究运行状态暂不可用")
    state = packed.get("state") or {}
    if state.get("cancel_requested") or state.get("cancel_requested_at"):
        raise asyncio.CancelledError()
    saved = state.get("research_budget")
    if not saved:
        # Root creation wins over inherited team timestamps: explicit new Runs
        # get a new budget, automatic recovery of this Run never resets it.
        stamp = packed.get("created_at") or (state.get("research_team") or {}).get("startedAt")
        start = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if stamp else datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        saved = {"started_at": start.timestamp(), "deadline_at": start.timestamp() + TOTAL_SECONDS,
                 "limit_seconds": TOTAL_SECONDS}
        if await run_store.patch_run_state(env.run_id, {"research_budget": saved}) is None:
            raise RuntimeError("研究时间预算保存失败")
    env.research_deadline = float(saved["deadline_at"])
    return env.research_deadline
