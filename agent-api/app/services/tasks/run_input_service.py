"""Durable inputs submitted to a running Harness Run.

Inputs are ordered and claimed at model/tool safe points.  Goal revision is the
only steering version; there is no graph-level replan path.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, List, Optional

from sqlalchemy import delete, func, select, text, update

from app.core.database import async_session
from app.core.runtime_db import runtime_session
from app.models import ChatMessage, ChatThread


async def _advisory_lock(session, key: str) -> None:
    """事务级咨询锁（PostgreSQL）：同一 key 的并发事务串行化，事务结束自动释放。
    用于把「检查是否已有 applying + 认领下一条」两步合成对同一 run 的临界区，
    修掉两次并发 claim 各自 SKIP LOCKED 到不同行、双双置 applying 的竞态（P0）。"""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})

logger = logging.getLogger(__name__)

_STATUSES = ("queued", "applying", "applied", "rejected", "withdrawn")
_ACCEPTING_RUN_STATUSES = (
    "created", "running", "routing",
    "waiting_user", "waiting_confirmation", "waiting_system",
)
_INPUT_BOUNDARY_LOCK = "run-input-boundary:"


class RunInputConflict(RuntimeError):
    """Run 不存在、已封口或已终态；status_code 供 API 保持 404/409 语义。"""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code

# applying 悬挂回收阈值：超过这个时长仍停在 applying 视为持有方已崩溃，打回 queued。
# 安全点吸收通常是毫秒级；10 分钟只是防止 worker 崩溃留下永久 applying。
_APPLYING_STALE_SEC = 600
_REPRIORITIZE_RE = re.compile(r"(优先|先做|后做|顺序|放到最后|提前|延后)")
_NEW_TASK_RE = re.compile(r"(另一个|另外一个|全新|无关).{0,8}(任务|问题|项目)")
_SCOPE_CHANGE_RE = re.compile(
    r"(新增|追加|删掉|删除|不要|改成|换成|交付物|目标|范围|文件|附件|第\s*\d+\s*页)"
)


def classify_input(content: str) -> str:
    text = str(content or "").strip()
    if _NEW_TASK_RE.search(text):
        return "new_task"
    if _REPRIORITIZE_RE.search(text):
        return "reprioritize"
    if _SCOPE_CHANGE_RE.search(text):
        return "scope_change"
    return "guidance"


def _row_to_dict(r) -> dict:
    return {
        "id": r.id, "runId": r.run_id, "content": r.content,
        "attachments": json.loads(r.attachments_json) if r.attachments_json else None,
        "baseGoalRevision": r.base_goal_revision,
        "appliedGoalRevision": r.applied_goal_revision,
        "sequence": getattr(r, "input_sequence", None),
        "inputType": getattr(r, "input_type", None) or classify_input(r.content),
        "sourceMessageId": getattr(r, "source_message_id", None),
        "status": r.status,
        "appliedAtSafePoint": getattr(r, "applied_scope", None),
        "failureReason": getattr(r, "failure_reason", None),
        "createdAt": r.created_at.isoformat() if r.created_at else None,
    }


async def submit(*, run_id: str, thread_id: str, user_id: str, content: str,
                 base_goal_revision: Optional[int] = None,
                 attachments: Optional[List[Any]] = None,
                 input_id: Optional[str] = None,
                 source_message_id: Optional[int] = None) -> Optional[dict]:
    """提交一条运行中输入（queued）。

    与最终总结封口共用 run 级咨询锁：封口前提交成功的指令一定能被终态检查看到；
    封口后提交明确 409。input_id 可由客户端稳定生成，网络重试不会产生重复输入。
    库未配置返回 None。
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun, AgentRunInput
    async with factory() as session:
        await _advisory_lock(session, f"{_INPUT_BOUNDARY_LOCK}{run_id}")
        stable_id = str(input_id or uuid.uuid4().hex)
        existing = await session.get(AgentRunInput, stable_id)
        if existing is not None:
            if str(existing.run_id) != str(run_id) or str(existing.user_id) != str(user_id):
                raise RunInputConflict("输入幂等键已被其他任务使用")
            out = _row_to_dict(existing)
            out["_created"] = False
            return out
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if run is None or str(run.user_id) != str(user_id):
            raise RunInputConflict("运行任务不存在", status_code=404)
        if (
            str(run.status) not in _ACCEPTING_RUN_STATUSES
            or getattr(run, "input_intake_closed_at", None) is not None
        ):
            raise RunInputConflict("任务已经进入最终收尾，无法再追加到当前任务")
        await _advisory_lock(session, f"input-seq:{run_id}")
        next_sequence = int((await session.execute(
            select(func.coalesce(func.max(AgentRunInput.input_sequence), 0))
            .where(AgentRunInput.run_id == run_id)
        )).scalar_one()) + 1
        row = AgentRunInput(
            id=stable_id, run_id=run_id, thread_id=thread_id, user_id=user_id,
            content=content, base_goal_revision=base_goal_revision, status="queued",
            input_sequence=next_sequence,
            input_type=classify_input(content),
            source_message_id=source_message_id,
            attachments_json=json.dumps(attachments, ensure_ascii=False) if attachments else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        out = _row_to_dict(row)
        out["_created"] = True
        return out


async def get_by_id(
    *, input_id: str, run_id: str, user_id: str,
) -> Optional[dict]:
    """按客户端稳定 id 查询已受理指令，供未知网络结果幂等恢复。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRunInput
    async with factory() as session:
        row = await session.get(AgentRunInput, input_id)
        if (
            row is None
            or str(row.run_id) != str(run_id)
            or str(row.user_id) != str(user_id)
        ):
            return None
        return _row_to_dict(row)


async def persist_user_message(*, run_id: str, thread_id: str, content: str,
                               attachments: Optional[List[Any]] = None) -> Optional[int]:
    """把运行中追加要求写入主对话历史，让它像普通用户消息一样可见、可回放。

    AgentRunInput 在 Runtime PG 负责执行语义；ChatMessage 在 MySQL 负责 transcript 语义。
    两库无法做原子事务，因此持久化失败只记日志，不撤销已经受理的指令。
    """
    safe_attachments = []
    for raw in (attachments or [])[:10]:
        if not isinstance(raw, dict):
            continue
        item = {
            "filename": str(raw.get("filename") or "附件")[:255],
            "kind": str(raw.get("kind") or "file")[:32],
            "status": str(raw.get("status") or "ok")[:16],
            "note": str(raw.get("note") or "")[:500],
        }
        file_id = str(raw.get("file_id") or "").strip()[:64]
        if file_id:
            item["file_id"] = file_id
        sha256 = str(raw.get("sha256") or "").strip()[:64]
        if sha256:
            item["sha256"] = sha256
        # 只落压缩缩略图，不把原图 data URL 或解析正文复制进 MySQL 消息行。
        preview = raw.get("preview_url")
        if isinstance(preview, str) and len(preview) <= 200_000:
            item["preview_url"] = preview
        safe_attachments.append(item)
    # MySQL 锁冲突退避重试：正常链路已确保主回合不跨模型调用持有 thread 行锁；这里仍
    # 兜住并发收尾的 1205/1213。每次尝试用全新 session 整体重放；函数级 import 避免
    # chat 包模块级环引。
    from app.services.chat.turn_finalizer import is_mysql_retryable_lock
    for attempt in range(1, 4):
        try:
            async with async_session() as session:
                row = ChatMessage(
                    thread_id=thread_id,
                    role="user",
                    content=content,
                    run_id=run_id,
                    status="run_input",
                    attachments_json=(
                        json.dumps(safe_attachments, ensure_ascii=False)
                        if safe_attachments else None
                    ),
                )
                session.add(row)
                thread = await session.get(ChatThread, thread_id)
                if thread is not None:
                    thread.updated_at = func.now()
                await session.commit()
                return row.id
        except Exception as exc:  # noqa: BLE001 — 指令已受理，历史落库失败不能反向取消任务
            if is_mysql_retryable_lock(exc) and attempt < 3:
                logger.warning("运行中追加要求落库撞 MySQL 锁冲突(1205/1213)，重试 %d/2 run=%s",
                               attempt, run_id)
                await asyncio.sleep(0.1 * attempt)
                continue
            logger.warning("运行中追加要求写入对话历史失败 run=%s: %s", run_id, exc)
            return None
    return None


async def delete_persisted_user_message(*, thread_id: str, message_id: int) -> None:
    """Run 输入未受理时补偿删除 transcript 行，避免刷新后出现幽灵消息。"""
    try:
        async with async_session() as session:
            await session.execute(
                delete(ChatMessage).where(
                    ChatMessage.id == int(message_id),
                    ChatMessage.thread_id == thread_id,
                    ChatMessage.status == "run_input",
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("补偿删除未受理插话失败 thread=%s message=%s: %s",
                       thread_id, message_id, exc)


async def persist_received_event(
    *, run_id: str, input_id: str, message_id: int, content: str,
) -> None:
    """持久化 input.received 审计事实。

    sequence=0 刻意不参与活动 SSE 通道的正序游标，避免外部请求与 producer 的本地
    sequence 竞争；历史投影会按 _event_timestamp 精确插回 ExecutionSegment。
    """
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentRunEvent
    try:
        async with factory() as session:
            session.add(AgentRunEvent(
                run_id=run_id,
                event_id=f"input-received-{input_id}",
                sequence=0,
                type="input.received",
                data={
                    "input_id": input_id,
                    "message_id": int(message_id),
                    "content": str(content or ""),
                    "_event_timestamp": int(time.time() * 1000),
                },
            ))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("input.received 审计事件持久化失败 run=%s: %s",
                       run_id, exc)


async def claim_next(*, run_id: str) -> Optional[dict]:
    """在模型/工具轮安全点原子认领下一条待处理输入。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRunInput
    async with factory() as session:
        # 咨询锁把「查 applying + 认领」合成对本 run 的临界区（两次并发 claim 不再各自认领一条）
        await _advisory_lock(session, f"input:{run_id}")
        # 悬挂回收：进程在 applying 与 finish 之间崩溃会让该条永久卡住，而「同一 Run 最多一条
        # applying」的门禁会连带永久阻塞这个 Run 的所有后续引导。超时后打回 queued 重新排队
        # （轮级消费的认领→注入→finish 在同一次循环迭代内完成，正常耗时是毫秒级，
        # _APPLYING_STALE_SEC 远大于它，不会误伤正在进行的应用）。
        await session.execute(
            update(AgentRunInput)
            .where(AgentRunInput.run_id == run_id,
                   AgentRunInput.status == "applying",
                   AgentRunInput.applying_at.isnot(None),
                   AgentRunInput.applying_at < func.now() - text(
                       f"interval '{_APPLYING_STALE_SEC} seconds'"))
            .values(status="queued", applying_at=None)
        )
        busy = (await session.execute(
            select(AgentRunInput.id).where(
                AgentRunInput.run_id == run_id,
                AgentRunInput.status == "applying").limit(1)
        )).scalar_one_or_none()
        if busy:
            await session.commit()  # 释放咨询锁 + 落库上面的回收
            return None
        row = (await session.execute(
            select(AgentRunInput)
            .where(AgentRunInput.run_id == run_id, AgentRunInput.status == "queued")
            .order_by(
                AgentRunInput.input_sequence.asc().nullslast(),
                AgentRunInput.created_at.asc(),
                AgentRunInput.id.asc(),
            )
            .limit(1)
        )).scalar_one_or_none()
        if row is None:
            await session.commit()
            return None
        row.status = "applying"
        row.applying_at = func.now()
        out = _row_to_dict(row)
        await session.commit()
        return out


async def has_pending(run_id: str) -> bool:
    """当前 Run 是否有待应用（queued）指令——调度器在节点边界据此决定是否触发引导 replan。"""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRunInput
    try:
        async with factory() as session:
            hit = (await session.execute(
                select(AgentRunInput.id).where(
                    AgentRunInput.run_id == run_id,
                    AgentRunInput.status == "queued").limit(1)
            )).scalar_one_or_none()
        return hit is not None
    except Exception:  # noqa: BLE001
        return False


async def finish(*, input_id: str, status: str, applied_goal_revision: Optional[int] = None,
                 scope: Optional[str] = None, failure_reason: Optional[str] = None) -> bool:
    """收尾一条指令：applied（已被消费）/ rejected（CAS 未命中且无法重规划）/ withdrawn。

    scope 记录消费安全点：turn=模型轮边界。applying_at 一并清空，
    避免收尾后的行仍被悬挂回收逻辑扫到。
    """
    if status not in ("applied", "rejected", "withdrawn"):
        return False
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRunInput
    async with factory() as session:
        values: dict = {"status": status, "applied_goal_revision": applied_goal_revision,
                        "applying_at": None}
        if scope:
            values["applied_scope"] = scope
        if failure_reason:
            values["failure_reason"] = str(failure_reason)[:2000]
        allowed_from = (
            ("queued", "applying") if status == "withdrawn" else ("applying",)
        )
        result = await session.execute(
            update(AgentRunInput)
            .where(
                AgentRunInput.id == input_id,
                AgentRunInput.status.in_(allowed_from),
            )
            .values(**values)
        )
        await session.commit()
    return bool(result.rowcount)


async def seal_intake_for_completion(run_id: str) -> Optional[bool]:
    """原子关闭指令入口。

    True=没有 queued/applying 指令且已封口；False=封口前已有待处理指令，调用方必须继续；
    None=Runtime 不可用，调用方按降级模式收尾。
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun, AgentRunInput
    async with factory() as session:
        await _advisory_lock(session, f"{_INPUT_BOUNDARY_LOCK}{run_id}")
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if run is None:
            return None
        if getattr(run, "input_intake_closed_at", None) is not None:
            return True
        pending = (await session.execute(
            select(AgentRunInput.id)
            .where(
                AgentRunInput.run_id == run_id,
                AgentRunInput.status.in_(("queued", "applying")),
            )
            .limit(1)
        )).scalar_one_or_none()
        if pending is not None:
            await session.commit()
            return False
        run.input_intake_closed_at = func.now()
        await session.commit()
        return True


async def reject_unfinished_for_terminal(run_id: str, reason: str) -> int:
    """Run 失败/取消等终态时收敛尚未消费的指令，避免永久停在 queued/applying。"""
    factory = runtime_session()
    if factory is None:
        return 0
    from app.runtime_models import AgentRunInput
    async with factory() as session:
        result = await session.execute(
            update(AgentRunInput)
            .where(
                AgentRunInput.run_id == run_id,
                AgentRunInput.status.in_(("queued", "applying")),
            )
            .values(
                status="rejected",
                applying_at=None,
                applied_scope="terminal",
                failure_reason=str(reason or "任务已结束")[:2000],
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def list_for_run(run_id: str) -> List[dict]:
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentRunInput
    async with factory() as session:
        rows = (await session.execute(
            select(AgentRunInput)
            .where(AgentRunInput.run_id == run_id)
            .order_by(
                AgentRunInput.input_sequence.asc().nullslast(),
                AgentRunInput.created_at.asc(),
                AgentRunInput.id.asc(),
            )
        )).scalars().all()
    return [_row_to_dict(r) for r in rows]
