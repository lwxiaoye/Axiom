"""Successful trajectories → Skill drafts for admin review. Never auto-publish."""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

MIN_ROUNDS = 6
SIMILAR_SUCCESS_THRESHOLD = 3


def trajectory_fingerprint(report: dict[str, Any]) -> str:
    extra = report.get("extra") if isinstance(report.get("extra"), dict) else {}
    deliverable = str(extra.get("deliverable") or "对话答复")
    tools = extra.get("tool_names") if isinstance(extra.get("tool_names"), list) else []
    names = [str(name) for name in tools[:8] if name]
    raw = deliverable + "|" + ",".join(names)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def render_skill_draft_markdown(report: dict[str, Any]) -> str:
    extra = report.get("extra") if isinstance(report.get("extra"), dict) else {}
    goal = str(extra.get("goal") or report.get("terminal_reason") or "完成一类任务")
    deliverable = str(extra.get("deliverable") or "对话答复")
    tools = extra.get("tool_names") if isinstance(extra.get("tool_names"), list) else []
    steps = extra.get("plan_titles") if isinstance(extra.get("plan_titles"), list) else []
    tool_line = "、".join(str(name) for name in tools[:12] if name) or "（未记录）"
    step_lines = "\n".join(f"- {title}" for title in steps[:8] if title) or "- （未记录步骤）"
    return (
        f"# Skill 草稿（待人工确认）\n\n"
        f"## 目标\n{goal}\n\n"
        f"## 交付物\n{deliverable}\n\n"
        f"## 建议步骤\n{step_lines}\n\n"
        f"## 关键工具模式\n{tool_line}\n\n"
        "本草稿不会自动上架。管理员确认后才能发布到 Skill 广场。\n"
    )


def should_collect(report: dict[str, Any]) -> bool:
    phase = str(report.get("phase") or "")
    disposition = str(report.get("run_disposition") or "")
    if phase != "completed" and disposition not in {"", "completed"}:
        return False
    if disposition in {"failed", "cancelled", "partial"}:
        return False
    loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
    return int(loop.get("steps_used") or 0) >= MIN_ROUNDS


async def maybe_collect_from_report(report: dict[str, Any]) -> Optional[str]:
    if not should_collect(report):
        return None
    extra = report.get("extra") if isinstance(report.get("extra"), dict) else {}
    user_id = str(extra.get("user_id") or "")
    if not user_id:
        return None
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentSkillDraft

    factory = runtime_session()
    if factory is None:
        return None
    fingerprint = trajectory_fingerprint(report)
    run_id = str(report.get("run_id") or "")
    markdown = render_skill_draft_markdown(report)
    title = str(extra.get("goal") or "可复用任务套路")[:80]
    async with factory() as session:
        row = (await session.execute(
            select(AgentSkillDraft)
            .where(AgentSkillDraft.user_id == user_id)
            .where(AgentSkillDraft.trajectory_fingerprint == fingerprint)
            .where(AgentSkillDraft.status.in_(("collecting", "suggested")))
            .limit(1)
        )).scalars().first()
        if row is None:
            draft_id = uuid.uuid4().hex
            session.add(AgentSkillDraft(
                id=draft_id,
                user_id=user_id,
                deliverable_kind=str(extra.get("deliverable") or "")[:64],
                trajectory_fingerprint=fingerprint,
                title=title,
                markdown=markdown,
                success_count=1,
                status="collecting",
                source_run_ids=[run_id] if run_id else [],
            ))
            await session.commit()
            return draft_id
        ids = list(row.source_run_ids or [])
        if run_id and run_id not in ids:
            ids.append(run_id)
            row.source_run_ids = ids
            row.success_count = int(row.success_count or 0) + 1
            row.markdown = markdown
            if row.success_count >= SIMILAR_SUCCESS_THRESHOLD and row.status == "collecting":
                row.status = "suggested"
                logger.info(
                    "skill_draft_suggested id=%s count=%s",
                    row.id, row.success_count,
                )
            await session.commit()
        return row.id


async def list_suggested_drafts(*, limit: int = 50) -> list[dict[str, Any]]:
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentSkillDraft

    factory = runtime_session()
    if factory is None:
        return []
    async with factory() as session:
        rows = (await session.execute(
            select(AgentSkillDraft)
            .where(AgentSkillDraft.status == "suggested")
            .order_by(AgentSkillDraft.updated_at.desc())
            .limit(max(1, min(200, int(limit or 50))))
        )).scalars().all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "title": row.title,
            "deliverable_kind": row.deliverable_kind,
            "success_count": row.success_count,
            "status": row.status,
            "markdown": row.markdown,
            "source_run_ids": list(row.source_run_ids or []),
        }
        for row in rows
    ]
