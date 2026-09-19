"""任务状态快照（Task Context Snapshot）。

激活休眠的 ``agent_run_context_snapshots``：终态 / HITL 挂起时异步侧写当前任务的
进行状态（目标 / 计划 / 产物 / 技能 / 子智能体进度），供「继续 / 接着做」的断点
上下文注入（``turn_context_builder.build_resume_checkpoint``）读取——把「做一半
做到哪」从散落的事件流里结构化为一个可注入的视图。

纪律：
- 全部 best-effort：失败静默，绝不阻塞 / 影响主链路；无快照时断点注入走原逻辑。
- 快照是侧写表：只读写 ``agent_run_context_snapshots``，不触碰 ``agent_runs.status``
  （终态 CAS 保持原样）。
- 只存 goal / 计划标题 / 文件名 / 工具行 / 技能 id 这类低敏摘要（均 ≤ 原断点注入量），
  不含工具完整参数与正文。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

TASK_SNAPSHOT_SCHEMA_VERSION = 2

_MAX_PLAN_STEPS = 20
_MAX_TOOL_LINES = 12
_MAX_ARTIFACTS = 12
_GOAL_MAX_CHARS = 200
# 与 turn_decision 同口径：只有光秃秃的续接控制语才继承上一份快照。
# 「继续修改封面」是新的修订目标，不得被这里误判成裸续接。
_BARE_CONTINUE_RE = re.compile(
    r"^(继续|接着|恢复|接着做|继续执行)(吧|下去|就行|即可)?[。！! ]*$",
)
_INTERRUPTED_STATUSES = {"partial", "interrupted", "cancelled", "failed"}
_DELIVERABLE_EXT_RE = re.compile(
    r"\.(?:pptx|docx|xlsx|pdf|md|txt|csv|html?)$",
    re.I,
)

# 后台侧写强引用集合（B4 教训：裸 create_task 可能被 GC 静默丢弃）
_bg_tasks: set[asyncio.Task] = set()


def spawn_snapshot_task(coro) -> None:
    """后台侧写任务（fire-and-forget，强引用防 GC；调度失败静默）。"""
    try:
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except Exception as e:  # noqa: BLE001
        logger.debug("snapshot spawn failed: %s", e)


# ===== 数据源（与 turn_context_builder._last_run_tool_progress 同源同格式）=====


def _event_data(ev) -> dict:
    data = ev.data if isinstance(getattr(ev, "data", None), dict) else {}
    if not isinstance(data, dict):
        raw = getattr(ev, "payload", None) or getattr(ev, "data", None)
        if isinstance(raw, dict):
            data = raw
        else:
            data = {}
    return data


def _compact_plan_projection(steps: list) -> list[dict]:
    """Keep snapshot-sized plan facts; full evidence remains in the Plan Store."""
    compact: list[dict] = []
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if not title:
            continue
        compact.append({
            "key": str(step.get("key") or f"plan-{index}"),
            "title": title[:80],
            "status": str(step.get("status") or "pending"),
            "detail": str(step.get("detail") or "")[:240],
            "reason": str(step.get("reason") or "")[:240],
            "required": bool(step.get("required", True)),
            "plan_version": max(0, int(step.get("plan_version") or 0)),
            "goal_revision": max(0, int(step.get("goal_revision") or 0)),
        })
    return compact[-_MAX_PLAN_STEPS:]


def _filter_artifact_receipts(receipts: list) -> list[dict]:
    """Keep only user-visible persisted files, including for legacy snapshots."""
    filtered: list[dict] = []
    seen: set[str] = set()
    for receipt in receipts or []:
        if not isinstance(receipt, dict):
            continue
        file_id = str(receipt.get("file_id") or receipt.get("id") or "").strip()
        filename = str(receipt.get("filename") or receipt.get("name") or "").strip()
        if not file_id or file_id in seen or not _DELIVERABLE_EXT_RE.search(filename):
            continue
        seen.add(file_id)
        filtered.append(dict(receipt))
    return filtered[-_MAX_ARTIFACTS:]


async def collect_run_activity(
    thread_id: str,
    *,
    limit: int = 12,
    run_id: Optional[str] = None,
) -> dict:
    """从同线程最近 Run 的事件里抽出续做视图。

    返回 ``{"plan": [{title, status}], "lines": [str], "skill_ids": [str],
    "artifact_receipts": [{file_id, filename}]}``：
    - plan：权威 Agent Plan Store 的完整步骤快照（≤ _MAX_PLAN_STEPS）
    - lines：tool.completed/failed 行（与 _last_run_tool_progress 相同格式，≤ limit）
    - skill_ids：use_skill 动态加载的技能 id（去重）
    - artifact_receipts：仅来自 ``artifact.saved`` 的持久化交付回执
    """
    out: dict = {"plan": [], "lines": [], "skill_ids": [], "artifact_receipts": []}
    if not thread_id:
        return out
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return out
    from app.runtime_models import AgentRun, AgentRunEvent
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id) if run_id else (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.thread_id == thread_id)
                    .order_by(desc(AgentRun.created_at))
                    .limit(1)
                )
            ).scalars().first()
            if not run:
                return out
            events = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run.id,
                        AgentRunEvent.type.in_((
                            "tool.completed", "tool.failed", "artifact.saved",
                        )),
                    )
                    .order_by(AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                    .limit(80)
                )
            ).scalars().all()
        plan: list = []
        try:
            from app.services.tasks import plan_service
            plan = _compact_plan_projection(await plan_service.get_current_plan(run.id))
        except Exception as e:  # noqa: BLE001
            logger.debug("collect authoritative plan failed run=%s: %s", run.id, e)
        lines: list[str] = []
        skill_ids: list[str] = []
        artifact_receipts: list[dict[str, str]] = []
        seen_file_ids: set[str] = set()
        for ev in events:
            data = _event_data(ev)
            et = str(getattr(ev, "type", "") or "")
            if et == "artifact.saved":
                for row in data.get("files") or []:
                    if not isinstance(row, dict):
                        continue
                    file_id = str(row.get("id") or row.get("file_id") or "").strip()
                    filename = str(row.get("filename") or row.get("name") or "").strip()
                    review = row.get("review") if isinstance(row.get("review"), dict) else {}
                    if (
                        not file_id
                        or file_id in seen_file_ids
                        or not _DELIVERABLE_EXT_RE.search(filename)
                        or str(review.get("status") or "").lower() == "failed"
                    ):
                        continue
                    seen_file_ids.add(file_id)
                    artifact_receipts.append({
                        "file_id": file_id,
                        "filename": filename,
                        "source_run_id": str(run.id),
                    })
                continue
            name = str(data.get("name") or data.get("tool") or "").strip()
            if not name:
                continue
            status = "failed" if et == "tool.failed" else "ok"
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            if name == "use_skill":
                sid = str(args.get("skill_id") or args.get("id") or "").strip()
                if sid and sid not in skill_ids:
                    skill_ids.append(sid)
            detail = str(
                data.get("preview")
                or data.get("summary")
                or data.get("filename")
                or (args.get("path") if args else "")
                or ""
            ).strip()
            detail = " ".join(detail.split())
            if len(detail) > 80:
                detail = detail[:80] + "…"
            lines.append(f"- {name} ({status})" + (f"：{detail}" if detail else ""))
        out["plan"] = plan[-_MAX_PLAN_STEPS:]
        out["lines"] = lines[-max(1, int(limit)):]
        out["skill_ids"] = skill_ids
        out["artifact_receipts"] = _filter_artifact_receipts(artifact_receipts)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_run_activity 失败 thread=%s: %s", thread_id, e)
        return out


async def collect_skill_work_lines(thread_id: str, *, limit: int = 6) -> str:
    """技能执行摘要（供记忆抽取，v3.0）：最近 Run 中技能相关工具行。

    过滤：name ∈ {use_skill, bash, write_file, edit_file} 且（use_skill，或 preview /
    path 命中产物扩展名 .pptx/.docx/.pdf/.xlsx/.md）。行格式与 collect_run_activity 一致。
    无内容返回空串。
    """
    if not thread_id:
        return ""
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return ""
    from app.runtime_models import AgentRun, AgentRunEvent
    _ARTIFACT_EXT_RE = re.compile(r"\.(pptx?|docx?|pdf|xlsx?|md)\b", re.I)
    _SKILL_TOOLS = {"use_skill", "bash", "write_file", "edit_file"}
    try:
        async with factory() as session:
            run = (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.thread_id == thread_id)
                    .order_by(desc(AgentRun.created_at))
                    .limit(1)
                )
            ).scalars().first()
            if not run:
                return ""
            events = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run.id,
                        AgentRunEvent.type.in_(("tool.completed", "tool.failed")),
                    )
                    .order_by(AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                    .limit(80)
                )
            ).scalars().all()
        lines: list[str] = []
        for ev in events:
            data = _event_data(ev)
            name = str(data.get("name") or data.get("tool") or "").strip()
            if name not in _SKILL_TOOLS:
                continue
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            preview = str(data.get("preview") or data.get("summary") or "").strip()
            if name != "use_skill" and not (
                _ARTIFACT_EXT_RE.search(preview)
                or str(args.get("path") or "").endswith((".pptx", ".docx", ".pdf", ".xlsx", ".md"))
            ):
                continue
            status = "failed" if str(getattr(ev, "type", "") or "") == "tool.failed" else "ok"
            detail = " ".join(preview.split())
            if len(detail) > 80:
                detail = detail[:80] + "…"
            lines.append(f"- {name} ({status})" + (f"：{detail}" if detail else ""))
        lines = lines[-max(1, int(limit)):]
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_skill_work_lines 失败 thread=%s: %s", thread_id, e)
        return ""


async def _resolve_run(run_id: str) -> Optional[dict]:
    """按 run_id 取 Run 行（thread_id / user_id / goal / status）。"""
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if not run:
                return None
            return {
                "thread_id": str(run.thread_id or ""),
                "user_id": str(run.user_id or ""),
                "goal": str(run.goal or ""),
                "status": str(run.status or ""),
                "error": str(run.error or ""),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve_run 失败 run=%s: %s", run_id, e)
        return None


async def _run_state_skill_ids(run_id: str) -> List[str]:
    """从 run.state 读请求级技能清单（accept/stream 时持久化，见 harness_orchestrator）。"""
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if not run or not isinstance(run.state, dict):
                return []
            raw = run.state.get("skill_ids")
            seen: list[str] = []
            for s in raw or []:
                s = str(s or "").strip()
                if s and s not in seen:
                    seen.append(s)
            return seen[:20]
    except Exception as e:  # noqa: BLE001
        logger.debug("run.state.skill_ids 读取失败 run=%s: %s", run_id, e)
        return []


async def _recent_user_goal(thread_id: str) -> str:
    """最近一条非「继续」类用户消息（≤200 字），作 goal 回退。"""
    if not thread_id:
        return ""
    try:
        from app.core.database import async_session
        from app.models import ChatMessage, live_chat_message_clause
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.thread_id == thread_id,
                        ChatMessage.role == "user",
                        live_chat_message_clause(),
                    )
                    .order_by(desc(ChatMessage.id))
                    .limit(5)
                )
            ).scalars().all()
        for row in rows:
            content = str(getattr(row, "content", None) or "").strip()
            if not content or _BARE_CONTINUE_RE.match(content):
                continue
            one = " ".join(content.split())
            return one[:_GOAL_MAX_CHARS]
        return ""
    except Exception as e:  # noqa: BLE001
        logger.warning("_recent_user_goal 失败 thread=%s: %s", thread_id, e)
        return ""


async def _run_has_interrupted_message(thread_id: str, run_id: str) -> bool:
    """当前 Run 的助手消息是否存在半成品标记。"""
    if not thread_id or not run_id:
        return False
    try:
        from app.core.database import async_session
        from app.models import ChatMessage, live_chat_message_clause
        async with async_session() as session:
            row = (
                await session.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.thread_id == thread_id,
                        ChatMessage.run_id == run_id,
                        ChatMessage.role == "assistant",
                        live_chat_message_clause(),
                    )
                    .order_by(desc(ChatMessage.id))
                    .limit(1)
                )
            ).scalars().first()
        if not row:
            return False
        status = str(getattr(row, "status", None) or "").strip().lower()
        return status in _INTERRUPTED_STATUSES
    except Exception as e:  # noqa: BLE001
        logger.warning("_run_has_interrupted_message 失败 run=%s: %s", run_id, e)
        return False


async def _last_event_sequence(run_id: str) -> int:
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return 0
    from app.runtime_models import AgentRunEvent
    try:
        async with factory() as session:
            seq = await session.scalar(
                select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.run_id == run_id)
            )
        return max(0, int(seq or 0))
    except Exception as e:  # noqa: BLE001
        logger.debug("last_event_sequence 失败 run=%s: %s", run_id, e)
        return 0


async def _previous_thread_snapshot_summary(
    thread_id: str,
    *,
    exclude_run_id: str,
) -> dict:
    """Return the newest prior Run snapshot for a bare continuation handoff."""
    if not thread_id:
        return {}
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return {}
    from app.runtime_models import AgentRun, AgentRunContextSnapshot
    try:
        async with factory() as session:
            current_created_at = await session.scalar(
                select(AgentRun.created_at).where(AgentRun.id == exclude_run_id)
            )
            if current_created_at is None:
                return {}
            row = (
                await session.execute(
                    select(AgentRunContextSnapshot)
                    .join(AgentRun, AgentRun.id == AgentRunContextSnapshot.run_id)
                    .where(
                        AgentRun.thread_id == thread_id,
                        AgentRun.id != exclude_run_id,
                        AgentRun.created_at < current_created_at,
                    )
                    .order_by(
                        AgentRun.created_at.desc(),
                        AgentRunContextSnapshot.version.desc(),
                    )
                    .limit(1)
                )
            ).scalars().first()
        return dict(row.summary or {}) if row else {}
    except Exception as e:  # noqa: BLE001
        logger.debug("previous thread snapshot failed thread=%s: %s", thread_id, e)
        return {}


# ===== 快照组装 / 落库 / 读取 =====


async def build_task_snapshot_summary(
    *,
    run_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    goal: Optional[str] = None,
    skill_ids: Optional[List[str]] = None,
    pending_decisions: Optional[List[str]] = None,
    light: bool = False,
) -> dict:
    """组装任务快照 summary（每个字段 try/except 降级省略；light 跳过重活字段）。

    light=True 供高频周期写：跳过产物清单与子智能体进度两路重活。
    """
    summary: dict = {"schema_version": TASK_SNAPSHOT_SCHEMA_VERSION}
    if not thread_id or not user_id or goal is None:
        run = await _resolve_run(run_id)
        if not run:
            return summary
        thread_id = thread_id or run["thread_id"]
        user_id = user_id or run["user_id"]
        if goal is None:
            goal = run["goal"]
    if not thread_id:
        return summary
    summary["goal"] = str(goal or "")[:_GOAL_MAX_CHARS]
    # 计划 / 工具进度 / use_skill 动态技能（与断点注入同一数据源）
    # 终态快照必须读取正在侧写的精确 Run。若按 thread 最新一条重查，用户紧接着
    # 发“继续”时新 Run 可能已经创建，后台任务会把旧 Run 的交付回执读丢。
    act = await collect_run_activity(thread_id, run_id=run_id)
    if act.get("plan"):
        summary["plan"] = act["plan"]
    if act.get("lines"):
        summary["tool_summary"] = act["lines"]
    if act.get("artifact_receipts"):
        summary["artifact_receipts"] = act["artifact_receipts"]
    # 技能 id：调用方（HITL tool_env）→ use_skill 事件 → run.state 请求级清单
    skills: list[str] = [str(s) for s in (skill_ids or []) if str(s or "").strip()]
    for sid in act.get("skill_ids") or []:
        if sid not in skills:
            skills.append(sid)
    if not skills:
        skills = await _run_state_skill_ids(run_id)
    if skills:
        summary["skill_ids"] = skills[:20]
    # 纯续接回合可能是零工具回执确认：它自身没有新 artifact.saved，
    # 但不能因此把上一个 Run 的持久化文件回执从“最新快照”中抹掉。
    # 只在 goal 是精确裸控制语时继承；普通新任务和显式修订不走这条路。
    if _BARE_CONTINUE_RE.fullmatch(str(goal or "").strip()):
        previous = await _previous_thread_snapshot_summary(
            thread_id,
            exclude_run_id=run_id,
        )
        for key in ("plan", "artifact_receipts", "skill_ids"):
            if not summary.get(key) and previous.get(key):
                summary[key] = (
                    _filter_artifact_receipts(previous[key])
                    if key == "artifact_receipts" else list(previous[key])
                )
        previous_goal = str(previous.get("goal") or "").strip()
        if previous_goal and not _BARE_CONTINUE_RE.fullmatch(previous_goal):
            summary["goal"] = previous_goal[:_GOAL_MAX_CHARS]
    if pending_decisions:
        summary["pending_decisions"] = [str(p) for p in pending_decisions if str(p or "").strip()][:8]
    if not light:
        if user_id:
            try:
                from app.services.files import user_file_service
                recent = await user_file_service.list_resume_file_names(
                    user_id, thread_id, limit=_MAX_ARTIFACTS,
                )
                names = [
                    str(r.get("filename") or "").strip()
                    for r in (recent or []) if isinstance(r, dict)
                ]
                names = [n for n in names if n]
                if names:
                    summary["artifacts"] = names[:_MAX_ARTIFACTS]
            except Exception as e:  # noqa: BLE001
                logger.debug("快照产物清单失败 run=%s: %s", run_id, e)
    summary["interrupted"] = await _run_has_interrupted_message(thread_id, run_id)
    return summary


async def save_task_snapshot(
    *,
    run_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    covered_sequence: Optional[int] = None,
    summary: Optional[dict] = None,
    light: bool = False,
    skill_ids: Optional[List[str]] = None,
    pending_decisions: Optional[List[str]] = None,
) -> Optional[dict]:
    """落库一条任务快照（IntegrityError 重试一次；全部失败静默返回 None）。"""
    if summary is None:
        summary = await build_task_snapshot_summary(
            run_id=run_id, thread_id=thread_id, user_id=user_id,
            skill_ids=skill_ids, pending_decisions=pending_decisions, light=light,
        )
    if not summary.get("goal") and not summary.get("plan") and not summary.get("tool_summary"):
        return None  # 无可侧写内容，不落空快照
    from app.services.agent_harness import run_store
    if covered_sequence is None:
        covered_sequence = await _last_event_sequence(run_id)
    for attempt in range(2):
        try:
            return await run_store.create_context_snapshot(
                run_id, covered_sequence=covered_sequence, summary=summary,
            )
        except IntegrityError as e:
            # 并发写撞 (run_id, version) 唯一约束：create_context_snapshot 内部
            # version = max+1 非原子，重读最新 version 后重试一次即可
            if attempt == 0:
                logger.debug("快照并发写冲突 run=%s，重试一次", run_id)
                continue
            logger.warning("快照写冲突重试仍失败 run=%s: %s", run_id, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.debug("快照写失败（不阻断）run=%s: %s", run_id, e)
            return None
    return None


async def save_terminal_snapshot(run_id: str) -> Optional[dict]:
    """终态落地后异步侧写（task_run_service 终态钩子调用，全量字段）。"""
    run = await _resolve_run(run_id)
    if not run or not run["thread_id"]:
        return None
    from app.services.agent_harness.public_errors import (
        SENSITIVE_WORDS_REJECTION_MESSAGE,
    )
    if (
        run.get("status") == "failed"
        and run.get("error") == SENSITIVE_WORDS_REJECTION_MESSAGE
    ):
        # 该目标只对用户可见，不是可由「继续」恢复的任务断点。
        return None
    return await save_task_snapshot(
        run_id=run_id, thread_id=run["thread_id"], user_id=run["user_id"],
    )


async def save_hitl_snapshot(
    *,
    run_id: str,
    skill_ids: Optional[List[str]] = None,
    pending_decisions: Optional[List[str]] = None,
) -> Optional[dict]:
    """HITL 挂起时侧写（_suspend_orchestration 调用），带挂起上下文（待确认决策）。"""
    run = await _resolve_run(run_id)
    if not run or not run["thread_id"]:
        return None
    return await save_task_snapshot(
        run_id=run_id, thread_id=run["thread_id"], user_id=run["user_id"],
        skill_ids=skill_ids, pending_decisions=pending_decisions,
    )


async def get_latest_task_snapshot(thread_id: str) -> Optional[dict]:
    """按 thread 取最新一条快照（JOIN agent_runs，取最近 Run 的最新 version）。

    用户「继续」轮会先建新 Run，按 run_id 查会查空——按 thread 查才能命中上一段。
    """
    if not thread_id:
        return None
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun, AgentRunContextSnapshot
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    select(AgentRunContextSnapshot)
                    .join(AgentRun, AgentRun.id == AgentRunContextSnapshot.run_id)
                    .where(AgentRun.thread_id == thread_id)
                    .order_by(
                        AgentRun.created_at.desc(),
                        AgentRunContextSnapshot.version.desc(),
                    )
                    .limit(1)
                )
            ).scalars().first()
        if not row:
            return None
        return {
            "run_id": row.run_id,
            "version": row.version,
            "covered_sequence": row.covered_sequence,
            "summary": dict(row.summary or {}),
            "created_at": row.created_at,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("get_latest_task_snapshot 失败 thread=%s: %s", thread_id, e)
        return None


async def get_task_snapshot(
    run_id: str,
    *,
    thread_id: str = "",
    user_id: str = "",
) -> Optional[dict]:
    """Load the latest snapshot version for one already-authorized source Run."""
    if not run_id:
        return None
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun, AgentRunContextSnapshot
    try:
        async with factory() as session:
            query = (
                select(AgentRunContextSnapshot)
                .join(AgentRun, AgentRun.id == AgentRunContextSnapshot.run_id)
                .where(AgentRunContextSnapshot.run_id == str(run_id))
                .order_by(AgentRunContextSnapshot.version.desc())
                .limit(1)
            )
            if thread_id:
                query = query.where(AgentRun.thread_id == str(thread_id))
            if user_id:
                query = query.where(AgentRun.user_id == str(user_id))
            row = (await session.execute(query)).scalars().first()
        if not row:
            return None
        return {
            "run_id": row.run_id,
            "version": row.version,
            "covered_sequence": row.covered_sequence,
            "summary": dict(row.summary or {}),
            "created_at": row.created_at,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("get_task_snapshot 失败 run=%s: %s", run_id, e)
        return None


async def resolve_resume_skill_ids(
    user_id: str,
    thread_id: str,
    *,
    source_run_id: str = "",
) -> List[str]:
    """resume 轮技能合并源：取该线程最新快照的 skill_ids（去重，≤20）。

    无快照 / 快照无技能返回 []；与当轮显式技能合并后由 effective_ppt_skill_ids 去重。
    """
    if not thread_id:
        return []
    snap = (
        await get_task_snapshot(
            source_run_id,
            thread_id=thread_id,
            user_id=user_id,
        )
        if source_run_id
        else await get_latest_task_snapshot(thread_id)
    )
    if not snap:
        return []
    raw = (snap.get("summary") or {}).get("skill_ids") or []
    seen: list[str] = []
    for s in raw:
        s = str(s or "").strip()
        if s and s not in seen:
            seen.append(s)
    return seen[:20]


def format_snapshot_block(summary: dict) -> str:
    """把任务快照渲染为断点注入块；无内容返回空串。"""
    if not isinstance(summary, dict):
        return ""
    lines: list[str] = ["【任务快照（平台注入，上一轮断点事实）】"]
    goal = str(summary.get("goal") or "").strip()
    if goal:
        lines.append(f"任务目标：{goal}")
    plan = summary.get("plan") or []
    if plan:
        steps = [
            f"{str(p.get('title') or '').strip()}[{str(p.get('status') or 'pending')}]"
            for p in plan
            if isinstance(p, dict) and str(p.get("title") or "").strip()
        ]
        if steps:
            lines.append("任务计划：" + " → ".join(steps))
    receipts = _filter_artifact_receipts(summary.get("artifact_receipts") or [])
    artifacts = summary.get("artifacts") or []
    if artifacts and not receipts:
        lines.append(
            "同线程恢复范围文件（仅供定位半成品，不代表本任务已交付）："
            + "、".join(str(a) for a in artifacts)
        )
    receipt_lines = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        file_id = str(receipt.get("file_id") or "").strip()
        filename = str(receipt.get("filename") or "").strip()
        if file_id and filename and _DELIVERABLE_EXT_RE.search(filename):
            receipt_lines.append(f"- {filename}（file_id={file_id}）")
    if receipt_lines:
        lines.append("已持久化交付回执（平台已核验，可确认完成）：")
        lines.extend(receipt_lines)
    tools = summary.get("tool_summary") or []
    if tools:
        lines.append("已执行工具：")
        lines.extend(str(t) for t in tools)
    skills = summary.get("skill_ids") or []
    if skills:
        lines.append(
            f"技能记录：{len(skills)} 个（本轮须按持久化 ID 重新校验 ACL 与取包；"
            "未通过校验前不得宣称已加载或执行）"
        )
    pends = summary.get("pending_decisions") or []
    if pends:
        lines.append("待确认决策：" + "；".join(str(p) for p in pends))
    if summary.get("interrupted"):
        lines.append("上一轮状态：中断/未完整交付（消息带 [partial]/[interrupted] 标记）")
    if len(lines) == 1:
        return ""
    return chr(10).join(lines)
