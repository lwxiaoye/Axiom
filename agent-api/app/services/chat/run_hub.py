"""Run 并发治理(结构手术 Phase 2d:自 harness_orchestrator 原样搬迁,行为零变化)。

进程内事件分发器:后台 Run 继续执行,当前/新窗口通过 run_id 订阅事件——Runtime PG
是事实源,内存队列只负责低延迟推送与未刷新场景。职责全景:
- 发布/缓冲:_publish_run_payload(慢订阅者摘除)+ 每 Run 800 帧环形缓冲;
- 泵:_pump_background_run(落库+分发+取消/异常终态 CAS+终态锚点+按身份摘除);
- 启动:launch_stream / launch_resume(占位登记+create_task+写回同一锁持有期,
  P0-11;resume 带排水窗口与重复提交幂等);
- 订阅:subscribe_run(先回放已落库事件再接实时队列,seq 去重,僵尸当场收敛);
- 取消:cancel_chat_run(等收敛/pending 语义/无任务分支终态帧+锚点);
- 仲裁:get_run_state / _resolve_active_run / _is_zombie_run(多 worker 租约口径);
- 部分落库:停止生成的部分正文 fire-and-forget 落库与收敛等待(P1-4)。
时序敏感段(锁持有期/CAS/shield)自搬迁起逐字未动——改动前先读原方法的 P0/P1 批注。
"""
import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import HTTPException

from app.services import sse_protocol
from app.services.sandbox import session_pool as sandbox_session_pool

from app.services.tasks import task_run_service
from app.services.chat import turn_finalizer
from app.services.agent_harness import run_store
from app.services.agent_harness.public_errors import TerminalRunError, public_run_error

logger = logging.getLogger(__name__)

# 跨进程订阅靠 PG 事件库 + NOTIFY。旧泵「每 token 一落库」会：
# 1) 反压模型 aiter_lines（写完一帧才读下一帧）→ 首字后仍卡顿；
# 2) 订阅端 500ms 兜底轮询一次拉回一大坨 → 前端体感「空白很久再整段喷出」。
# 正文 / 思考 delta 在短窗内合并后再推送（工具/计划/终态仍即时落库）。
# 思考若不合并，NOTIFY 会按 token 狂轰，前端就整段喷出或一顿一顿往外蹦。
_DELTA_COALESCE_S = 0.045
_DELTA_COALESCE_CHARS = 64
_STREAM_DELTA_TYPES = frozenset({"message.delta", "message.reasoning.delta"})
_CANCEL_ACK_TIMEOUT_S = 5.0
_CANCEL_ACK_POLL_S = 0.05


def coalesce_delta_texts(
    pieces: list[str], *, max_chars: int = _DELTA_COALESCE_CHARS,
) -> list[str]:
    """Char-window coalesce matching the pump: first piece flushes immediately.

    Time-window (45ms) is runtime-only. Concatenation of the returned groups must
    equal the original token stream.
    """
    items = [str(piece) for piece in pieces if str(piece)]
    if not items:
        return []
    out = [items[0]]
    buf = ""
    for piece in items[1:]:
        nxt = buf + piece
        if buf and len(nxt) >= max_chars:
            out.append(buf)
            buf = piece
        else:
            buf = nxt
    if buf:
        out.append(buf)
    return out


def _payload_event_type(payload: str) -> str:
    data = task_run_service.parse_harness_sse_payload(payload)
    return str((data or {}).get("type") or "")


def _payload_is_message_delta(payload: str) -> bool:
    return _payload_event_type(payload) == "message.delta"


def _payload_is_stream_delta(payload: str) -> bool:
    return _payload_event_type(payload) in _STREAM_DELTA_TYPES


def sandbox_should_outlive_pump(run_status: Optional[str]) -> bool:
    """Same Run waiting for the user must keep the container; a new Run still uses checkpoints."""
    return str(run_status or "") in task_run_service.WAITING_RUN_STATUSES


def _delta_text_of(payload: str) -> str:
    data = task_run_service.parse_harness_sse_payload(payload) or {}
    body = data.get("data") if isinstance(data.get("data"), dict) else {}
    return str((body or {}).get("text") or "")


def _rewrite_delta_text(payload: str, text: str) -> str:
    """保留信封（event_id/sequence/run_id/type），只替换流式 delta 的 text。"""
    lines = (payload or "").split("\n")
    out: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw and raw != "[DONE]":
                try:
                    obj = json.loads(raw)
                    if str(obj.get("type") or "") in _STREAM_DELTA_TYPES:
                        data = dict(obj.get("data") or {})
                        data["text"] = text
                        obj["data"] = data
                        out.append("data: " + json.dumps(obj, ensure_ascii=False))
                        continue
                except Exception:  # noqa: BLE001
                    pass
        out.append(line)
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    if not body.endswith("\n\n"):
        body += "\n"
    return body

# 内存中保留的历史 Run 上限（元数据 + SSE 缓冲）。超出即淘汰「已结束且无订阅者」的最旧 Run，
# 防止 _run_meta / _run_buffers 随进程生命周期无界增长（后台 Run 缓冲每个上限 800 帧）。
_MAX_RETAINED_RUNS = 256


def _user_facing_run_error(error: Exception) -> str:
    """Compatibility wrapper for callers/tests; the policy lives at the Harness boundary."""
    return public_run_error(error)


RECOVERY_BACKOFF_SECONDS = 1.0


def recovery_retry_allowed(job: Optional[Dict[str, Any]]) -> bool:
    """Do not let a recoverable fault enter the Job dead-letter path."""
    if not job:
        return True
    try:
        maximum = int(job.get("max_attempts") or 0)
        attempt = int(job.get("attempt_count") or 0)
    except (TypeError, ValueError):
        return True
    return maximum <= 0 or attempt < maximum


def recovery_available_at(*, now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()) + timedelta(seconds=RECOVERY_BACKOFF_SECONDS)


async def recover_run_after_error(
    run_id: str,
    *,
    reason: str,
    expected_owner_instance_id: Optional[str] = None,
    expected_heartbeat_at: Any = None,
    job: Optional[Dict[str, Any]] = None,
    persist_phase_event: bool = True,
) -> Dict[str, Any]:
    """Save one recovery segment and schedule the same Run without a fake failure."""
    checkpoint = None
    try:
        checkpoint = await run_store.record_recovery_checkpoint(
            run_id, recovery_reason=str(reason or "runtime_error")[:160],
        )
    except Exception:  # noqa: BLE001
        logger.exception("recovery checkpoint failed run=%s", run_id)

    status_before = await task_run_service.get_run_status(run_id)
    already_waiting = status_before == "waiting_system"
    if checkpoint is None and not already_waiting:
        logger.error("recovery checkpoint unavailable; leave Run for zombie scanner run=%s", run_id)
        return {
            "marked": False,
            "checkpoint": False,
            "queued": False,
            "retry_allowed": recovery_retry_allowed(job),
            "available_at": recovery_available_at(),
        }

    token = await task_run_service.mark_run_recoverable(
        run_id,
        reason=str(reason or "runtime_error")[:160],
        expected_owner_instance_id=expected_owner_instance_id,
        expected_heartbeat_at=expected_heartbeat_at,
        interactive_type="task_recovery",
    )
    status = await task_run_service.get_run_status(run_id)
    already_waiting = already_waiting or status == "waiting_system"
    marked = bool(token) or already_waiting
    retry_allowed = recovery_retry_allowed(job)
    available_at = recovery_available_at()
    queued = False
    # A Worker-owned Job must be released before it can be requeued.  Calling enqueue_job while
    # that lease is still live only updates wake_reason; the subsequent finish_job would clear
    # the lease and lose the recovery wake-up.  The Worker calls _finish_recovery_job after this
    # helper, while non-Worker callers (zombie polling/API fallback) have no live Job and may
    # enqueue here directly.
    can_schedule = bool(checkpoint) and (token or already_waiting) and job is None
    if can_schedule and retry_allowed:
        try:
            queued = await run_store.enqueue_job(
                run_id, wake_reason="recovery", available_at=available_at,
            )
        except Exception:  # noqa: BLE001
            logger.exception("recovery requeue failed run=%s", run_id)
    elif can_schedule:
        logger.warning(
            "recovery retry cap reached; keep waiting_system run=%s attempt=%s max=%s",
            run_id, (job or {}).get("attempt_count"), (job or {}).get("max_attempts"),
        )

    if token and persist_phase_event:
        try:
            await task_run_service.append_run_event(
                run_id, "run.phase.changed", {"phase": "waiting_system"},
            )
        except Exception:  # noqa: BLE001
            logger.debug("recovery phase event skipped run=%s", run_id, exc_info=True)
    logger.warning(
        "run recovery marked run=%s reason=%s checkpoint=%s queued=%s retry_allowed=%s",
        run_id, reason, bool(checkpoint), queued, retry_allowed,
    )
    return {
        "marked": marked,
        "checkpoint": checkpoint is not None,
        "queued": queued,
        "retry_allowed": retry_allowed,
        "available_at": available_at,
    }



class RunHub:
    def __init__(self) -> None:
        # 进程内事件分发器：后台 Run 继续执行；当前/新窗口通过 run_id 订阅事件。
        # Runtime PG 是事实源，内存队列只负责低延迟推送和未刷新场景。
        self._run_tasks: Dict[str, asyncio.Task] = {}
        self._run_subscribers: Dict[str, set[asyncio.Queue[str]]] = {}
        self._run_buffers: Dict[str, List[str]] = {}
        self._run_meta: Dict[str, Dict[str, str]] = {}
        self._run_lock = asyncio.Lock()
        # 取消路径的部分内容落库任务必须持强引用：裸 ensure_future 的 task 可能在完成前被 GC
        self._bg_persist_tasks: set = set()
        # 按 run_id / thread_id 索引最近一次「停止生成」部分内容落库任务（P1-4）：
        # cancel_chat_run 据 run_id 等它收敛；新一轮写入/删除前据 thread_id 等它收敛，
        # 避免「已停止」回包先于 INSERT 提交发出，与下一轮写入产生错序/漏删竞态。
        self._persist_tasks_by_run: Dict[str, asyncio.Task] = {}
        self._persist_tasks_by_thread: Dict[str, asyncio.Task] = {}
        # 同一 Run 的「助手归属行」写者互斥锁（P0 竞态修复 2026-07-26）：
        # 部分正文落库（_spawn_partial_persist）与终态占位锚点（ensure_terminal_anchor）
        # 是同进程内的两个并发写者，各自「先查有没有归属行、再插」。不互斥时二者的
        # 短事务会交叠——锚点查不到尚未提交的正文行 → 两边都插 → 占位行与正文行双双
        # 存活。二者都在本进程同一事件循环里，进程内锁即可完全消除交叠；真正落到
        # 「谁先谁后」的差异由 persist_partial_assistant 的按 run_id 收敛兜住
        # （锚点先赢时原地改写占位行，不产生第二条）。
        self._run_write_locks: Dict[str, asyncio.Lock] = {}

    def _run_write_lock(self, run_id: str) -> asyncio.Lock:
        """取某 Run 的归属行写锁（见 _run_write_locks 注释）。同步函数：get 与 setdefault
        之间没有 await，不会有「两个协程各建一把锁」的竞态。

        回收：新建时顺带清掉未被持有的旧锁，条目数与 _MAX_RETAINED_RUNS 同量级，
        不随进程生命周期无界增长。"""
        lock = self._run_write_locks.get(run_id)
        if lock is None:
            if len(self._run_write_locks) > _MAX_RETAINED_RUNS:
                for rid, held in list(self._run_write_locks.items()):
                    if not held.locked():
                        self._run_write_locks.pop(rid, None)
            lock = self._run_write_locks.setdefault(run_id, asyncio.Lock())
        return lock

    async def _ensure_terminal_anchor(self, run_id: str, thread_id: str,
                                      label: str = "终态锚点兜底",
                                      placeholder: Optional[str] = None) -> None:
        """终态锚点补偿的唯一入口（本文件 5 处调用点共用）：持 Run 写锁调用，与部分
        正文落库互斥。失败一律只记日志——锚点是补偿，绝不能反噬调用方的收尾流程。
        label 保留各调用点原有的日志措辞，便于按场景检索。"""
        try:
            from app.services.tasks import run_reconcile_service
            async with self._run_write_lock(run_id):
                await run_reconcile_service.ensure_terminal_anchor(
                    run_id, thread_id, placeholder=placeholder,
                )
        # 这是终态补偿路径，不能因为外层 Run 正在取消/收尾而把 CancelledError
        # 逃逸到 worker。Python 3.11 中 CancelledError 继承 BaseException，不会被
        # `except Exception` 捕获；日志中曾因此直接杀掉 agent-worker，后续 Run 全部
        # 只能停在“正在思考”。补偿可丢，但主执行器必须继续完成清理。
        except (Exception, asyncio.CancelledError) as anchor_err:  # noqa: BLE001
            logger.warning("%s失败 run=%s: %s", label, run_id, anchor_err)

    @staticmethod
    def _payload_sequence(payload: str) -> int:
        data = task_run_service.parse_harness_sse_payload(payload)
        if not data:
            return 0
        try:
            return int(data.get("sequence") or 0)
        except Exception:
            return 0

    def _evict_stale_runs_locked(self) -> None:
        """调用方须持有 self._run_lock。内存中滞留的历史 Run 超过上限时，从最旧开始淘汰
        「任务已结束且无订阅者」的 Run 的元数据与缓冲，避免长驻进程内存无界增长。
        仍在运行或仍有订阅者读缓冲的 Run 一律保留。"""
        if len(self._run_meta) <= _MAX_RETAINED_RUNS:
            return
        for rid in list(self._run_meta.keys()):
            if len(self._run_meta) <= _MAX_RETAINED_RUNS:
                break
            task = self._run_tasks.get(rid)
            if task is not None and not task.done():
                continue
            if self._run_subscribers.get(rid):
                continue
            self._run_meta.pop(rid, None)
            self._run_buffers.pop(rid, None)

    async def _publish_run_payload(self, run_id: str, payload: str) -> None:
        if not payload:
            return
        async with self._run_lock:
            buffer = self._run_buffers.setdefault(run_id, [])
            buffer.append(payload)
            if len(buffer) > 800:
                del buffer[:-800]
            queues = list(self._run_subscribers.get(run_id, set()))
        for queue in queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 慢订阅者防护（第二轮评审）：长时间不消费不再无界积压内存。摘除该订阅、
                # 腾一格投 [DONE] 让其生成器排空后干净退出；事件已全量落库
                # （record_sse_payload），客户端重连 ?after=lastSeq 游标续传无损补齐。
                async with self._run_lock:
                    subs = self._run_subscribers.get(run_id)
                    if subs is not None:
                        subs.discard(queue)
                try:
                    queue.get_nowait()
                    queue.put_nowait("data: [DONE]")
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                logger.warning("Run %s 存在慢订阅者（队列满 %d），已摘除", run_id, queue.maxsize)

    async def _pump_background_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        protocol: str,
        source: AsyncGenerator[str, None],
        job: Optional[Dict[str, Any]] = None,
    ) -> None:
        done_sent = False
        recovering = False
        terminal_anchor_text: Optional[str] = None
        # 终止事件必须接着已发事件的 sequence 续号：若新开 channel 从 1 起，订阅端
        # 「seq <= last_sequence 判旧丢弃」会把 run.failed 吞掉，且落库 seq 冲突污染回放。
        last_seq = 0
        # 正文 / 思考 delta 合并缓冲（见模块顶注释）；类型切换时先冲旧缓冲。
        delta_text = ""
        delta_payload: Optional[str] = None
        delta_since = 0.0
        delta_flush_task: Optional[asyncio.Task] = None
        first_delta_emitted = False

        async def _flush_delta(*, force: bool = False) -> None:
            nonlocal delta_text, delta_payload, delta_since, last_seq, delta_flush_task
            if not delta_payload or not delta_text:
                delta_text = ""
                delta_payload = None
                return
            if not force:
                age = time.monotonic() - delta_since
                if age < _DELTA_COALESCE_S and len(delta_text) < _DELTA_COALESCE_CHARS:
                    return
            scheduled_flush = delta_flush_task
            current_task = asyncio.current_task()
            if scheduled_flush is not None and scheduled_flush is not current_task:
                delta_flush_task = None
                if not scheduled_flush.done():
                    scheduled_flush.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduled_flush
            payload = _rewrite_delta_text(delta_payload, delta_text)
            delta_text = ""
            delta_payload = None
            delta_since = 0.0
            seq = self._payload_sequence(payload)
            if seq > last_seq:
                last_seq = seq
            await task_run_service.record_sse_payload(run_id, payload)
            await self._publish_run_payload(run_id, payload)

        def _arm_delta_flush() -> None:
            """短窗内若无后续 delta，到期强制冲出，避免尾包卡在缓冲里。"""
            nonlocal delta_flush_task

            async def _later() -> None:
                try:
                    await asyncio.sleep(_DELTA_COALESCE_S)
                    await _flush_delta(force=True)
                except asyncio.CancelledError:
                    raise

            if delta_flush_task and not delta_flush_task.done():
                delta_flush_task.cancel()
            elif delta_flush_task and delta_flush_task.done():
                # Consume and propagate a delayed commit failure before replacing its Task
                # handle.  Otherwise the live stream would continue after losing a frame.
                error = delta_flush_task.exception()
                if error is not None:
                    raise error
            delta_flush_task = asyncio.create_task(_later())

        try:
            async for payload in source:
                if not payload:
                    continue
                if payload.strip() == "data: [DONE]":
                    await _flush_delta(force=True)
                    done_sent = True
                    seq = self._payload_sequence(payload)
                    if seq > last_seq:
                        last_seq = seq
                    await task_run_service.record_sse_payload(run_id, payload)
                    await self._publish_run_payload(run_id, payload)
                    continue
                if _payload_is_stream_delta(payload):
                    piece = _delta_text_of(payload)
                    if not piece:
                        continue
                    if delta_payload and _payload_event_type(delta_payload) != _payload_event_type(payload):
                        await _flush_delta(force=True)
                        first_delta_emitted = False
                    if not delta_payload:
                        delta_since = time.monotonic()
                    delta_payload = payload  # 保留最新信封（sequence 取最后一帧）
                    delta_text += piece
                    # 本段第一个可见字立即冲出；之后按 45ms/64 字合并，降低 PG 反压
                    if not first_delta_emitted:
                        await _flush_delta(force=True)
                        first_delta_emitted = True
                    else:
                        await _flush_delta(force=False)
                        if delta_payload:
                            _arm_delta_flush()
                    continue
                # 非流式 delta：先冲缓冲，再即时落库（工具/计划/终态不可合并）
                await _flush_delta(force=True)
                first_delta_emitted = False
                seq = self._payload_sequence(payload)
                if seq > last_seq:
                    last_seq = seq
                await task_run_service.record_sse_payload(run_id, payload)
                await self._publish_run_payload(run_id, payload)
                # checkpoint 从每 5 帧改为每 40 帧：正文帧密集时每 5 帧写 RunState 等于二次反压
                if seq and seq % 40 == 0:
                    try:
                        from app.services.agent_harness import run_store
                        await run_store.patch_run_state(
                            run_id, {"last_checkpoint_sequence": int(seq)},
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("checkpoint sequence write skipped run=%s", run_id, exc_info=True)
            await _flush_delta(force=True)
        except asyncio.CancelledError:
            with suppress(Exception):
                await _flush_delta(force=True)
            # 终态 CAS（P0 2026-07-17）：迁移生效才对外发布 run.failed——Run 已被并发收敛成
            # completed/failed 时（如恰在收尾后到达的停止），不能再广播「已停止生成」覆盖真结果
            channel = sse_protocol.SSEChannel(protocol, thread_id, run_id, start_sequence=last_seq)
            if await task_run_service.finalize_run(run_id, "cancelled"):
                try:
                    from app.services.agent_harness import run_store
                    await run_store.patch_run_state(
                        run_id,
                        {"cancel_requested": True, "terminal_reason": "user_cancelled"},
                        phase="cancelled",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("cancelled terminal checkpoint failed run=%s", run_id, exc_info=True)
                terminal_anchor_text = "已停止生成"
                payload = channel.run_cancelled("user_cancelled")
                await task_run_service.record_sse_payload(run_id, payload)
                await self._publish_run_payload(run_id, payload)
            await self._publish_run_payload(run_id, channel.done())
            done_sent = True
        except Exception as e:  # noqa: BLE001
            channel = sse_protocol.SSEChannel(protocol, thread_id, run_id, start_sequence=last_seq)
            terminal_failure = isinstance(e, TerminalRunError)
            if terminal_failure:
                logger.warning("运行达到确定性停止条件，结束本轮 run=%s type=%s", run_id, type(e).__name__)
            else:
                logger.exception("后台 Run 执行失败，转入自动恢复: %s", e)
            # Deterministic validation/provider-policy rejections are terminal. Persistence
            # failures still use recovery, and a lost CAS cannot overwrite cancellation.
            finalized = terminal_failure and await task_run_service.finalize_run(
                run_id, "failed", error=e.public_message,
            )
            if finalized:
                context_quarantined: Optional[bool] = None
                if bool(getattr(e, "exclude_run_messages_from_future_context", False)):
                    from app.services.tasks import run_reconcile_service

                    quarantine_result = (
                        await run_reconcile_service.quarantine_policy_rejected_run(
                            run_id,
                            thread_id,
                        )
                    )
                    context_quarantined = quarantine_result is not None
                    if not context_quarantined:
                        # Run 已由 Runtime PG 终态 CAS 确认，不能再伪装成可恢复。
                        # 启动/周期对账会重试 MySQL 隔离；显式留痕便于告警。
                        logger.error(
                            "敏感词终态已落库但会话上下文未隔离 run=%s thread=%s",
                            run_id,
                            thread_id,
                        )
                try:
                    from app.services.agent_harness import run_store
                    state_patch = {"terminal_reason": e.public_message}
                    if context_quarantined is not None:
                        state_patch["policy_rejection_context_quarantined"] = bool(
                            context_quarantined
                        )
                    await run_store.patch_run_state(
                        run_id, state_patch, phase="failed",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("terminal failure checkpoint failed run=%s", run_id, exc_info=True)
                payload = channel.run_failed(e.public_message)
                last_seq = self._payload_sequence(payload)
                await task_run_service.record_sse_payload(run_id, payload)
                await self._publish_run_payload(run_id, payload)
            current = "failed" if finalized else await task_run_service.get_run_status(run_id)
            if current not in task_run_service.TERMINAL_RUN_STATUSES:
                recovery = await recover_run_after_error(
                    run_id,
                    reason=f"pump:{type(e).__name__}",
                    job=job,
                    persist_phase_event=False,
                )
                recovering = bool(recovery["marked"] or current == "waiting_system")
                if recovering:
                    payload = channel.run_phase_changed("waiting_system")
                    await task_run_service.record_sse_payload(run_id, payload)
                    await self._publish_run_payload(run_id, payload)
                else:
                    logger.error("Run %s recovery could not be marked; no terminal event emitted", run_id)
            try:
                await self._publish_run_payload(run_id, channel.done())
            except Exception:  # noqa: BLE001
                pass
            done_sent = True
        finally:
            # 关生成器排在等待落库/补锚**之前**（P0 竞态修复 2026-07-26）：取消打在本泵
            # 自己的 await（record_sse_payload / _publish_run_payload——每帧都是真实 PG IO）
            # 上时，source 还挂在 yield 点上、根本没收到取消，它的中断兜底
            # （main_tool_turn 初始轮 / turn_finalizer.stream_resume_events 续接轮）此刻
            # 一行都没跑过，_persist_tasks_by_run 自然是空的：下面「等落库任务收敛」因为
            # 没任务可等立刻放行 → 补锚查不到归属行 → 插占位语「（已停止，未生成回复）」；
            # 等本泵结束、生成器被 asyncgen finalizer 关闭时，兜底才把真实正文落成第二行。
            # ⚠️ 这一步只把窗口压小，**关不成同步**：生成器链是嵌套 async generator，裸
            # `async for ... : yield` 不会连带关闭子生成器，外层 aclose 抛出的 GeneratorExit
            # 只能靠 finalizer 逐层异步下传（实测每层慢一个事件循环迭代），而持有兜底的是
            # 最内层（初始轮第 2 层、续接轮第 3 层）。所以单行不变量最终由另外两道闸保证：
            # _run_write_lock（两个写者互斥，消除短事务交叠）+ persist_partial_assistant
            # 的按 run_id 收敛（锚点先赢时原地改写占位行）。三者的必要性有消融测试锁定，
            # 见 tests/test_resume_partial_persist.py 的 flat/nested 两档。
            await self._close_source(run_id, source)
            if not done_sent:
                await self._publish_run_payload(run_id, sse_protocol.SSEChannel(protocol, thread_id, run_id).done())
            # 无正文轮锚点兜底（P0 2026-07-17）：先等「停止生成」的部分正文落库任务收敛
            # （避免与其竞态双写），Run 已 cancelled/failed 且 MySQL 无归属行时补占位消息 +
            # message.completed 锚——「思考中/搜索中停止」的执行轨迹从此能挂上历史。
            try:
                persist = self._persist_tasks_by_run.get(run_id)
                if persist is not None and not persist.done():
                    await asyncio.wait_for(asyncio.shield(persist), timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            # MySQL 归属行只写 run_reconcile_service 的规范占位语；terminal_anchor_text 是
            # SSE 的用户可读错误，不能混成另一套“占位事实”。迟到的 partial 依赖规范集合
            # 识别并原地覆盖锚点——若这里写“已停止生成”或动态错误文案，它会被误当真实正文，
            # 最终出现用户明明读到 partial、刷新后却只剩错误提示。
            if not recovering:
                await self._ensure_terminal_anchor(run_id, thread_id)
            # 收尾 checkpoint：把最终 sequence 固化到 RunState（崩溃审计 / 续跑边界）。
            if last_seq > 0:
                try:
                    from app.services.agent_harness import run_store
                    await run_store.patch_run_state(
                        run_id, {"last_checkpoint_sequence": int(last_seq)},
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("final checkpoint sequence write skipped run=%s", run_id, exc_info=True)
            # PPTD 工程检查点必须在 close_scope 之前：容器按 Run 销毁，中间工程靠 tar 跨 Run。
            # 泵被取消时也要等打包结束，不能把 CancelledError 吞掉后立刻拆容器。
            capture_task = None
            try:
                from app.services.agent_harness.artifact_checkpoint import (
                    capture_ppt_staging,
                    run_owner,
                )
                meta = self._run_meta.get(run_id) or {}
                uid = str(meta.get("user_id") or "")
                tid = str(thread_id or meta.get("thread_id") or "")
                if not uid:
                    uid, tid2 = await run_owner(run_id)
                    tid = tid or tid2
                if uid:
                    capture_task = asyncio.create_task(capture_ppt_staging(
                        run_id=run_id, thread_id=tid, user_id=uid, force=True,
                    ))
                    try:
                        await asyncio.wait_for(asyncio.shield(capture_task), timeout=30)
                    except asyncio.CancelledError:
                        if capture_task is not None and not capture_task.done():
                            with suppress(Exception, asyncio.TimeoutError):
                                await asyncio.wait_for(asyncio.shield(capture_task), timeout=30)
                        raise
            except asyncio.CancelledError:
                raise
            except (Exception, asyncio.TimeoutError):  # noqa: BLE001
                logger.info("PPT staging capture skipped run=%s", run_id, exc_info=True)
            # 同 Run 等待用户确认时不要拆容器：确认后续接还是这一份工程。
            # 跨 Run「继续」仍走检查点，不靠会话级长驻。
            try:
                status = await task_run_service.get_run_status(run_id)
                if sandbox_should_outlive_pump(status):
                    live = sandbox_session_pool.peek(run_id)
                    if live is not None:
                        live.last_used_at = time.monotonic()
                    logger.info("Run %s 状态=%s：保留沙箱会话", run_id, status)
                else:
                    closed = await asyncio.shield(sandbox_session_pool.close_scope(run_id))
                    if closed:
                        logger.info("Run %s 收尾：回收 %d 个沙箱会话", run_id, closed)
            except (Exception, asyncio.CancelledError) as e:  # noqa: BLE001
                logger.warning("Run %s 沙箱回收未完成: %s", run_id, e)
            async with self._run_lock:
                # 按身份比对再摘除（P0-11）：resume 双提交时，先失败收尾的「输家」不能把
                # 仍在跑的「赢家」刚登记进 _run_tasks[run_id] 的引用顶掉——否则赢家仍在
                # 真实执行，却已从追踪表里消失，订阅端/取消逻辑都会误判为「已断开」。
                if self._run_tasks.get(run_id) is asyncio.current_task():
                    self._run_tasks.pop(run_id, None)

    async def _close_source(self, run_id: str, source) -> None:
        """关闭本 Run 的事件生成器（P0 2026-07-26，调用点在 pump 的 finally，那里有完整
        时序说明）。只保证**最外层**立即收到 GeneratorExit：嵌套层要靠 asyncgen finalizer
        逐层异步下传，因此这里是「把窗口压小」，不是「把兜底跑完」。

        为什么不是裸 `await source.aclose()`（三条理由，缺一条就退化成新的坑）：
        ① 本协程通常**已经被取消**（pump 的 finally 多半是 cancel 打进来的）。裸 await
           一旦生成器把 CancelledError 原样抛回（续接/初始轮兜底就是「落库后 re-raise」，
           且被取消的协程里再 await 本就可能立刻二次取消），异常会打断 pump 的整个
           finally——补锚、沙箱回收、任务表按身份摘除全部跳过。放进独立任务后，任何
           异常都被那个任务吃住，绝不反噬收尾流程；
        ② 生成器的 finally 链上有真实 IO（DB session 收尾等），必须有超时上界：一次
           卡住的关闭不能把 pump 永久挂死（那会让 cancel_chat_run 恒回 pending、
           Run 永远留在 _run_tasks 里）；
        ③ 与本文件既有的 shield 用法同源（cancel_chat_run 等落库任务 / 沙箱回收）：
           超时只是**本次不等**，关闭本身继续在后台跑完，不会被撤销成「关了一半」。
        非生成器 source（测试替身/普通异步可迭代对象）没有 aclose：直接跳过。
        """
        closer = getattr(source, "aclose", None)
        if closer is None:
            return
        try:
            closing = asyncio.ensure_future(closer())
        except Exception:  # noqa: BLE001
            return
        # 强引用（B4 教训）：裸 ensure_future 的任务可能在完成前被 GC
        self._bg_persist_tasks.add(closing)

        def _closed(task: asyncio.Task) -> None:
            self._bg_persist_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.warning("Run %s 事件流关闭异常: %s", run_id, task.exception())

        closing.add_done_callback(_closed)
        try:
            # asyncio.wait 不撤销超时的任务、也不抛出任务内异常；它自身在本协程被
            # 二次取消时会抛 CancelledError，一并吞掉继续走完 finally。
            await asyncio.wait({closing}, timeout=5.0)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001
            pass
        if not closing.done():
            logger.warning("Run %s 事件流关闭超时，中断兜底落库可能迟于终态锚点", run_id)

    async def launch_stream(self, *, run_id: str, thread_id: str, user_id: str,
                            protocol: str, source) -> Dict[str, str]:
        """登记并启动一个后台对话 Run(原 start_stream_chat_run 的时序敏感段:
        占位登记+创建任务+写回引用同一次锁持有期,P0-11)。source=完整 SSE 生成器。"""
        # 占位登记 + 创建任务 + 写回任务引用同一次锁持有期完成（P0-11，与 start_resume_chat_run
        # 对齐）：run_id 本身是新生成的 uuid，理论上不会撞现有 key，但登记与任务创建之间不留
        # 锁外空隙，防御性保持与 resume 入口同款不变量。
        async with self._run_lock:
            self._run_meta[run_id] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "protocol": protocol,
            }
            self._run_buffers.setdefault(run_id, [])
            self._evict_stale_runs_locked()
            task = asyncio.create_task(self._pump_background_run(
                run_id=run_id,
                thread_id=thread_id,
                protocol=protocol,
                source=source,
            ))
            self._run_tasks[run_id] = task
        return {"thread_id": thread_id, "run_id": run_id}


    async def launch_resume(self, *, user_id: str, run_id: str, protocol: str,
                            source_factory) -> Dict[str, Any]:
        """把 HITL 恢复也放入后台 Run，避免用户切走页面后中断续跑。"""
        run = await task_run_service.get_run(run_id, user_id)
        if not run:
            raise HTTPException(status_code=404, detail="运行任务不存在")
        # 排水窗口（任务模式 2.0 E2E 实证 P0）：挂起轮的旧泵在发出 done 帧后还有 ~1s 收尾
        # （生成器 return 后的清理 await）。此窗口内到达的 resume 若按「已有在跑任务」早退，
        # 会被**静默吞掉**——前端以为提交成功、Run 永远停在 waiting_*。Run 已处 waiting_*
        # 说明旧泵只是在收尾而非执行，改为有界等待其收敛后照常续跑。
        if str(run.get("status") or "") in task_run_service.WAITING_RUN_STATUSES:
            async with self._run_lock:
                draining = self._run_tasks.get(run_id)
            if draining is not None and not draining.done():
                try:
                    await asyncio.wait({draining}, timeout=5.0)
                except Exception:  # noqa: BLE001
                    pass
        # 检查 existing → 占位登记 → create_task → 写回任务引用必须整段在同一次锁持有期内
        # 完成（P0-11）：若中途释放锁，同一 run_id 的并发重复 resume 请求都能通过「existing
        # 为空/已结束」检查，各自 create_task 并先后覆盖 self._run_tasks[run_id]——真正在跑的
        # 「赢家」任务引用被后完成检查的「输家」顶掉，之后没有任何机制能再追踪/取消它。
        async with self._run_lock:
            existing = self._run_tasks.get(run_id)
            if existing and not existing.done():
                return {
                    "thread_id": run.get("thread_id", ""),
                    "run_id": run_id,
                    "after_sequence": await task_run_service.get_last_event_sequence(run_id, user_id),
                }
            last_sequence = await task_run_service.get_last_event_sequence(run_id, user_id)
            self._run_meta[run_id] = {
                "thread_id": run.get("thread_id", ""),
                "user_id": user_id,
                "protocol": protocol,
            }
            # 同一 run_id 从 waiting_* 续跑时会产生第二段事件流；清掉上一段内存 DONE，持久事件仍可回放。
            self._run_buffers[run_id] = []
            self._evict_stale_runs_locked()
            source = source_factory()
            task = asyncio.create_task(self._pump_background_run(
                run_id=run_id,
                thread_id=run.get("thread_id", ""),
                protocol=protocol,
                source=source,
            ))
            self._run_tasks[run_id] = task
        return {"thread_id": run.get("thread_id", ""), "run_id": run_id, "after_sequence": last_sequence}


    async def subscribe_run(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int = 0,
        protocol: str = "v1",
    ) -> AsyncGenerator[str, None]:
        """订阅 Run 事件；先回放已落库事件，再接实时队列。"""
        run = await task_run_service.get_run(run_id, user_id)
        meta = self._run_meta.get(run_id)
        if not run and not (meta and meta.get("user_id") == user_id):
            raise HTTPException(status_code=404, detail="运行任务不存在")

        # 有界队列（第二轮评审）：慢客户端最多积压 2000 帧（≈单 Run 缓冲上限的 2.5 倍），
        # 满即被 _publish_run_payload 摘除——事件已落库，重连游标续传可无损补齐
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2000)
        from app.services.tasks import runtime_event_bus
        async with self._run_lock:
            self._run_subscribers.setdefault(run_id, set()).add(queue)
        runtime_event_bus.register_ephemeral_subscriber(run_id, queue)

        last_sequence = int(after_sequence or 0)
        try:
            if protocol == sse_protocol.HARNESS:
                for payload in await task_run_service.list_event_payloads(
                    run_id, user_id, last_sequence, use_cache=False,
                ):
                    seq = self._payload_sequence(payload)
                    if seq and seq <= last_sequence:
                        continue
                    if seq:
                        last_sequence = seq
                    yield payload

            # 内存缓冲兜底：补齐后台任务先于订阅者发出的帧，也覆盖 Runtime PG 暂不可用时的当前进程恢复。
            async with self._run_lock:
                buffered = list(self._run_buffers.get(run_id, []))
            for payload in buffered:
                if payload.strip() == "data: [DONE]":
                    yield payload
                    return
                seq = self._payload_sequence(payload)
                if seq and seq <= last_sequence:
                    continue
                if seq:
                    last_sequence = seq
                yield payload

            run = await task_run_service.get_run(run_id, user_id)
            active_statuses = {"created", "running", "routing", "waiting_user", "waiting_confirmation", "waiting_system"}
            if run and run.get("status") not in active_statuses:
                yield sse_protocol.SSEChannel(protocol, run.get("thread_id", ""), run_id).done()
                return

            task_alive = await self._run_task_alive(run_id)
            if run and run.get("status") in set(task_run_service.WAITING_RUN_STATUSES) and not task_alive:
                yield sse_protocol.SSEChannel(protocol, run.get("thread_id", ""), run_id).done()
                return
            if run and run.get("status") == "running" and not task_alive and await self._is_zombie_run(run):
                # N-08：任务模式僵尸先尝试转入可恢复挂起——不发 run.failed（那会把可恢复的
                # 任务在前端定格成失败），发 done 让前端裸 EOF 走仲裁：getRunState 返回
                # waiting_system + task_recovery 后自动在同一 Run 上续跑
                if await self._try_recover_task_zombie(run_id, run):
                    yield sse_protocol.SSEChannel(protocol, run.get("thread_id", ""), run_id).done()
                    return
                logger.warning("Run %s zombie recovery was not claimed; keep non-terminal state", run_id)
                yield sse_protocol.SSEChannel(protocol, run.get("thread_id", ""), run_id).done()
                return
            if not run and not task_alive:
                yield sse_protocol.SSEChannel(protocol, (meta or {}).get("thread_id", ""), run_id).done()
                return

            while True:
                try:
                    queue_task = asyncio.create_task(queue.get())
                    from app.services.tasks import runtime_event_bus
                    # 2026-08-08：跨 worker 兜底轮询从 500ms 收到 100ms——NOTIFY 抖动时
                    # 半秒一捞会把多帧正文攒成一坨喷给前端（「空白很久再整段出来」）。
                    notify_task = asyncio.create_task(
                        runtime_event_bus.wait_for_run_event(run_id, timeout=0.1)
                    )
                    done, pending = await asyncio.wait(
                        {queue_task, notify_task}, return_when=asyncio.FIRST_COMPLETED,
                    )
                    for waiter in pending:
                        waiter.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if queue_task in done:
                        payload = queue_task.result()
                    else:
                        queue_task.cancel()
                        try:
                            await queue_task
                        except asyncio.CancelledError:
                            pass
                        raise asyncio.TimeoutError
                except asyncio.TimeoutError:
                    # 跨 worker 兜底（N-12）：订阅命中非 owner worker 时，本进程既没有执行任务
                    # 也没有跨进程实时通道——内存队列永远等不来帧。周期性：①按游标从事件库
                    # 补拉新事件（owner 落库即可见，等效低频实时通道）；②复核 Run 状态与
                    # owner 租约——终态发 done 收尾、僵尸当场收敛（任务模式优先转入可恢复
                    # 挂起），不再无限只发 keepalive。本地 owner + 任务存活时此分支只是空转
                    # 复查（seq 闸挡重复），无副作用。
                    run_now = await task_run_service.get_run(run_id, user_id)
                    if run_now is None:
                        continue
                    if protocol == sse_protocol.HARNESS:
                        try:
                            fresh = await task_run_service.list_event_payloads(
                                run_id, user_id, last_sequence, use_cache=False)
                        except Exception:  # noqa: BLE001
                            fresh = []
                        for polled in fresh:
                            pseq = self._payload_sequence(polled)
                            if pseq and pseq <= last_sequence:
                                continue
                            if pseq:
                                last_sequence = pseq
                            yield polled
                    status_now = str(run_now.get("status") or "")
                    if status_now not in active_statuses:
                        yield sse_protocol.SSEChannel(
                            protocol, run_now.get("thread_id", ""), run_id).done()
                        return
                    if (status_now == "running" and not await self._run_task_alive(run_id)
                            and await self._is_zombie_run(run_now)):
                        if await self._try_recover_task_zombie(run_id, run_now):
                            yield sse_protocol.SSEChannel(
                                protocol, run_now.get("thread_id", ""), run_id).done()
                            return
                        logger.warning("Run %s polled zombie recovery was not claimed", run_id)
                        yield sse_protocol.SSEChannel(
                            protocol, run_now.get("thread_id", ""), run_id,
                        ).done()
                        return
                        continue
                    continue
                if payload.strip() == "data: [DONE]":
                    yield payload
                    return
                seq = self._payload_sequence(payload)
                # Worker 与 API 分进程时，瞬时帧通过独立的 PG NOTIFY 通道到达。即使
                # sequence=9 的瞬时 delta 已进队列，sequence=8 的持久完成帧也可能仍在
                # 等本进程处理 durable NOTIFY。先从事实库补齐所有早于当前帧的持久事件，
                # 才能维护协议要求的全局递增顺序，避免客户端把迟到的 message.completed
                # 当成旧帧丢弃，并带着 after=9 永久越过权威全文。
                if protocol == sse_protocol.HARNESS and seq > last_sequence:
                    try:
                        preceding = await task_run_service.list_event_payloads(
                            run_id, user_id, last_sequence, use_cache=False,
                        )
                    except Exception:  # noqa: BLE001
                        preceding = []
                    for durable in preceding:
                        durable_seq = self._payload_sequence(durable)
                        if durable_seq and durable_seq >= seq:
                            break
                        if durable_seq and durable_seq <= last_sequence:
                            continue
                        if durable_seq:
                            last_sequence = durable_seq
                        yield durable
                if seq and seq <= last_sequence:
                    continue
                if seq:
                    last_sequence = seq
                yield payload
        finally:
            runtime_event_bus.unregister_ephemeral_subscriber(run_id, queue)
            async with self._run_lock:
                subscribers = self._run_subscribers.get(run_id)
                if subscribers:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._run_subscribers.pop(run_id, None)

    async def cancel_chat_run(self, user_id: str, run_id: str) -> Dict[str, str]:
        """停止后台 Run。返回 {"status": "cancelled"|"pending"}（P0 三批）：
        pending=停止已发出但执行任务在等待窗口内未收敛——**不得当停干净**，前端不得
        终态收尾/派发队列，应保持订阅等 pump 真正收敛后的 run.cancelled 帧。

        的 API 与 Worker 是不同进程：非本地任务不再先把 PG 改成
        cancelled，而是持久化 cancel_requested_at，等 Worker watchdog 真正
        cancel pump，再由 pump 在资源/落库收敛后提交 cancelled。超过等待窗只能
        回 pending，绝不得把“已发停止请求”伪装成“已停干净”。"""
        run = await task_run_service.get_run(run_id, user_id)
        meta = self._run_meta.get(run_id)
        if not run and not (meta and meta.get("user_id") == user_id):
            raise HTTPException(status_code=404, detail="运行任务不存在")
        async with self._run_lock:
            task = self._run_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            # 等后台任务真正收敛：pump 捕获 CancelledError 后会把状态落库为 cancelled 并从
            # _run_tasks 摘除，正常返回（不再抛出）。等它结束再返回，紧随其后的「打断并重发」
            # 才不会因旧 run 仍是 running 被 stream_chat 的活动任务护栏挡回（§7.1）。有界等待，
            # 避免底层流取消不及时时把取消请求本身拖死。
            try:
                await asyncio.wait({task}, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            if not task.done():
                # 超时未收敛（P0 三批）：旧任务可能仍在执行——此前无条件返回成功，前端
                # 误清 activeRun/派发下一条，与仍在跑的旧 Run 并发冲突或后台继续生成
                logger.warning("cancel_chat_run 等待收敛超时，返回 pending: run=%s", run_id)
                return {"status": "pending"}
            # 部分内容落库任务收敛（P1-4）：pump 收敛不代表落库已提交——那是独立 fire-and-forget
            # 任务；「已停止」回包若先于它提交发出，紧随其后的下一轮写入/regenerate 删除会与它
            # 竞态（错序或漏删）。shield 防有界等待超时时连带撤销：超时只是本次不等，任务本身
            # 仍在后台继续跑完。
            # 超时=**没等到**落库提交，必须如实回 pending（P1 修正 2026-07-26）：此前
            # `except Exception: pass` 把 TimeoutError 吞掉仍回 cancelled，前端据此认定
            # 「已停干净」立刻发下一条，迟到的部分回复 created_at 反而晚于新用户消息——
            # 对话顺序错乱并污染下一轮上下文。语义与上面的等待收敛超时分支一致：
            # pending 不等于失败，落库任务（shield 保护）仍在后台跑完，前端保持订阅即可。
            persist_task = self._persist_tasks_by_run.get(run_id)
            if persist_task and not persist_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(persist_task), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "cancel_chat_run 等待部分正文落库超时，返回 pending: run=%s", run_id)
                    return {"status": "pending"}
                except Exception:  # noqa: BLE001
                    # 落库任务自身异常（已在其内部尽力兜底）：停止本身已收敛，照常回 cancelled
                    pass
        else:
            run_status = str((run or {}).get("status") or "")
            # 正在执行/排队的 Run 必须走跨进程确认。waiting_* 没有
            # 存活 Worker 协程，租约已过期的孤儿也无执行者，这两类可安全
            # 直接收终态。Runtime 降级时 run=None，保留原来的内存兜底。
            needs_worker_ack = bool(
                run
                and run_status in {"created", "routing", "running"}
                and not task_run_service.is_run_lease_stale(run)
            )
            if needs_worker_ack:
                requested = await task_run_service.request_run_cancel(run_id, user_id)
                if requested in task_run_service.TERMINAL_RUN_STATUSES:
                    return {"status": str(requested)}
                if requested == "requested":
                    deadline = time.monotonic() + _CANCEL_ACK_TIMEOUT_S
                    while time.monotonic() < deadline:
                        fresh = await task_run_service.get_run(run_id, user_id)
                        fresh_status = str((fresh or {}).get("status") or "")
                        if fresh_status in task_run_service.TERMINAL_RUN_STATUSES:
                            return {"status": fresh_status}
                        await asyncio.sleep(_CANCEL_ACK_POLL_S)
                    logger.warning(
                        "cancel_chat_run 等待 Worker 确认超时，返回 pending: run=%s",
                        run_id,
                    )
                    return {"status": "pending"}
                # 请求位写失败/短暂不可用时，不能退回到旧的
                # “直接把状态改 cancelled”路径：Worker 可能仍在真实执行。
                # 此时只能陈述尚未确认，由前端保持订阅/允许重试停止。
                fresh = await task_run_service.get_run(run_id, user_id)
                fresh_status = str((fresh or {}).get("status") or "")
                if fresh_status in task_run_service.TERMINAL_RUN_STATUSES:
                    return {"status": fresh_status}
                logger.warning(
                    "cancel_chat_run 停止请求未持久，返回 pending: run=%s",
                    run_id,
                )
                return {"status": "pending"}

            cancelled = await task_run_service.cancel_run(run_id)
            if not cancelled:
                # cancel_run 幂等返回 False 有两种情况：Runtime PG 未配置（run 查不到，走
                # meta 兜底仍需广播）；Run 已处于 completed/failed/cancelled 终态——此时不再
                # 广播「已停止生成」覆盖真实结果（P1-16：晚到的停止请求不能拍掉刚完成的 Run）。
                fresh = await task_run_service.get_run(run_id, user_id)
                if fresh and fresh.get("status") in task_run_service.TERMINAL_RUN_STATUSES:
                    # 统一结构返回（P1 五批）：此前返回 bool，router 按 dict 取 .get() 直接
                    # AttributeError 500——「任务恰好先完成」的正常竞态被打成服务器错误
                    return {"status": str(fresh.get("status"))}
            thread_id = (run or meta or {}).get("thread_id", "")
            protocol = (meta or {}).get("protocol", sse_protocol.HARNESS)
            last_seq = await task_run_service.get_last_event_sequence(run_id, user_id)
            channel = sse_protocol.SSEChannel(protocol, thread_id, run_id, start_sequence=last_seq)
            from app.services.agent_harness import run_store
            phase_updated = await run_store.patch_run_state(run_id, {}, phase="cancelled")
            payload = channel.run_cancelled("user_cancelled")
            # 终态帧持久化 + 历史锚点（P1 五批）：本分支没有 pump（任务引用已丢/进程切换/
            # 非 owner worker），此前只投内存缓冲——刷新后 Run 显示已取消，但「已停止生成」
            # 帧与执行轨迹在回放里整段缺席，正是「刷新后执行过程不见了」的残余路径。
            await task_run_service.record_sse_payload(run_id, payload)
            await self._publish_run_payload(run_id, payload)
            if phase_updated is not None:
                phase_payload = channel.run_phase_changed("cancelled")
                await task_run_service.record_sse_payload(run_id, phase_payload)
                await self._publish_run_payload(run_id, phase_payload)
            await self._publish_run_payload(run_id, channel.done())
            await self._ensure_terminal_anchor(run_id, thread_id, "无任务取消分支锚点回填")
        # 回包只能陈述**真实**终态（2026-07-26 并发审计）：上面的 else 分支早已为「晚到的
        # 停止请求撞上刚完成的 Run」硬化过（P1-16），有存活任务的分支却无条件回 cancelled。
        # 而 pump 的 finally 很长（关流 ≤5s + 等部分正文落库 ≤5s + 补锚 + 沙箱回收），这段
        # 时间里 task.done() 仍为 False，DB 里 Run 却早已 completed（finalize_run 在 finally
        # 之前就执行了）——用户在收到 run.completed 的同一两秒内点停止，就会拿到 cancelled，
        # 与 /chat/runs/{id}/state 的 completed 两套事实，前端据此标红/派发下一条队列项。
        fresh_after = await task_run_service.get_run(run_id, user_id)
        fresh_status = str((fresh_after or {}).get("status") or "")
        if fresh_status in task_run_service.TERMINAL_RUN_STATUSES and fresh_status != "cancelled":
            return {"status": fresh_status}
        return {"status": "cancelled"}

    async def get_run_state(self, user_id: str, run_id: str) -> Dict[str, Any]:
        """单 Run 权威状态（P0 三批，断流仲裁接口）：前端裸 EOF/停止失败后据此区分
        completed/failed/cancelled/waiting_*/running，而不是只知道「是否还活跃」。
        running 的僵尸判定与 _resolve_active_run 同口径（_is_zombie_run 租约分流），
        命中即当场收敛（含终态事件落库+锚点，订阅回放不再裸 EOF）。"""
        run = await task_run_service.get_run(run_id, user_id)
        meta = self._run_meta.get(run_id)
        if not run:
            if not (meta and meta.get("user_id") == user_id):
                raise HTTPException(status_code=404, detail="运行任务不存在")
            async with self._run_lock:
                alive = run_id in self._run_tasks and not self._run_tasks[run_id].done()
            # Runtime 降级：只有内存视角
            return {"run_id": run_id, "thread_id": meta.get("thread_id", ""),
                    "status": "running" if alive else "unknown", "error": None}
        if run.get("status") == "running":
            # 租约口径（P1 多 worker）：外来 worker 的 Run 租约新鲜时如实回 running，不收敛
            if await self._is_zombie_run(run):
                await self._try_recover_task_zombie(run_id, run)
                run = await task_run_service.get_run(run_id, user_id) or run
        return {"run_id": run_id, "thread_id": run.get("thread_id", ""),
                "status": run.get("status"), "error": run.get("error"),
                "outcome": run.get("outcome"), "state": run.get("state") or {}}

    async def _run_task_alive(self, run_id: str) -> bool:
        """本进程内该 Run 是否有存活的执行任务（_pump_background_run）。"""
        async with self._run_lock:
            task = self._run_tasks.get(run_id)
            return task is not None and not task.done()

    async def _is_zombie_run(self, run: Dict[str, Any]) -> bool:
        """僵尸判定统一口径（P1 多 worker 租约，2026-07-17）——只对 status=running 的行调用：

        - owner 是本进程（owner_instance_id==task_run_service.INSTANCE_ID）→ 沿用本进程
          任务表判定：_run_tasks 里没有存活任务即僵尸。进程内事实最准，不必等租约过期；
        - owner 是其他实例或为空（legacy 存量行 / Runtime 降级期落库）→ **只有租约过期
          （is_run_lease_stale）才判僵尸**：租约新鲜说明另一个 worker 正在真实执行，
          绝不 fail 别的 worker 正在跑的 Run（此前按本地任务表判会「扩容即互杀」）。
        """
        run_id = str(run.get("id") or run.get("run_id") or "")
        if run.get("owner_instance_id") == task_run_service.INSTANCE_ID:
            return not await self._run_task_alive(run_id)
        return task_run_service.is_run_lease_stale(run)

    async def _try_recover_task_zombie(self, run_id: str, run: Dict[str, Any]) -> bool:
        result = await recover_run_after_error(
            run_id,
            reason="worker_lease_lost",
            expected_owner_instance_id=run.get("owner_instance_id"),
            expected_heartbeat_at=run.get("heartbeat_at"),
        )
        return bool(result.get("marked"))

    async def _resolve_active_run(self, thread_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """活动 Run 判定的唯一入口：PG 里 status=running 的 Run 只有在本进程内确有存活的
        执行任务时才算真活动。否则它是僵尸——进程重启/pump 已亡留下的 running 行——当场
        保存恢复现场并重新排队，不把可恢复故障伪造成 failed，避免它永久卡住前端导航转圈。

        waiting_*（HITL 挂起）不做此判定：它本就无存活任务、靠 resume_token 恢复，属合法等待态。

        多 worker 租约口径（P1 2026-07-17，取代原「单 worker 假设」边界）：僵尸判定经
        _is_zombie_run 分流——owner==本进程沿用 _run_tasks 存活判定；owner 是其他实例/为空时
        只有租约（heartbeat_at + RUN_LEASE_TTL_SECONDS）过期才判僵尸，租约新鲜一律当活动，
        绝不 fail 别的 worker 正在执行的 Run。至此 R0（部分唯一索引 + 本判定）在多副本下也
        成立。⚠️ 仍存在的边界：①cancel/stop 只能取消**本进程**的执行任务，跨 worker 取消
        需要请求路由到 owner worker（本平台当前单实例部署，租约先消除「扩容即互杀」）；
        ②跨 worker 无进程内事件通道，实时订阅同样需路由到 owner；③create_run 对 PG 未配置/
        连接异常仍 fail-open 返回 None，故严格说 R0 是「PG 健康时」的原子约束。
        """
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            return None
        return (await self._resolve_active_runs([normalized_thread_id], user_id)).get(normalized_thread_id)

    async def _resolve_active_runs(
        self,
        thread_ids: List[str],
        user_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Resolve a history page's active Runs without an N+1 Runtime query.

        The initial lookup is one read for every thread on the page.  Zombie recovery retains
        the single-thread behavior exactly, but only runs for the rare ``running`` rows that
        require it; their post-recovery state is fetched in one additional batch read.
        """
        active_by_thread = await task_run_service.get_active_runs(thread_ids, user_id)
        if not active_by_thread:
            return {}

        resolved = dict(active_by_thread)
        recovered: Dict[str, Dict[str, Any]] = {}
        for thread_id, active in active_by_thread.items():
            if active.get("status") != "running" or not await self._is_zombie_run(active):
                continue
            await self._try_recover_task_zombie(active["id"], active)
            recovered[thread_id] = active

        if not recovered:
            return resolved

        refreshed_by_thread = await task_run_service.get_active_runs(list(recovered), user_id)
        for thread_id, original in recovered.items():
            refreshed = refreshed_by_thread.get(thread_id)
            # 与单条路径一致：CAS 恢复期间若行消失或换成另一 Run，仍返回起始快照。
            resolved[thread_id] = (
                refreshed
                if refreshed and refreshed.get("id") == original["id"]
                else original
            )
        return resolved

    def _spawn_partial_persist(self, run_id: str, thread_id: str, content: str,
                               status: str = "cancelled") -> None:
        """停止生成时把已产出部分内容的落库任务登记为 fire-and-forget（P0-9/P1-4）：
        独立 Task 天然不受当前被取消协程牵连；按 run_id/thread_id 双索引供
        cancel_chat_run 与下一轮写入/删除前据此等它收敛，避免落库还没提交就被认为已完成。
        落库成功后补发带 message_id 的 message.completed 事件（P0 刷新丢失修复）：
        取消轮的执行轨迹此前因无归属锚点在历史回放中整段丢弃。
        status：cancelled=用户停止 / interrupted=收尾落库失败的一致性回填（1213 兜底）。

        写锁（P0 竞态修复 2026-07-26）：落库整段持 Run 写锁，与 _ensure_terminal_anchor
        互斥——否则两个「先查再插」的短事务会交叠，锚点看不到本任务尚未提交的正文行，
        双方各插一条。补事件（record_terminal_message_event）在锁外：那是 PG 事件流，
        与 MySQL 归属行不争用，没必要延长临界区。"""
        async def _persist_and_anchor() -> None:
            async with self._run_write_lock(run_id):
                mid = await turn_finalizer.persist_partial_assistant(
                    thread_id, content, run_id=run_id, status=status)
            if mid is not None and run_id:
                await task_run_service.record_terminal_message_event(run_id, mid, content)

        _t = asyncio.ensure_future(_persist_and_anchor())
        self._bg_persist_tasks.add(_t)
        self._persist_tasks_by_run[run_id] = _t
        self._persist_tasks_by_thread[thread_id] = _t

        def _cleanup(_task: asyncio.Task, _run_id: str = run_id, _thread_id: str = thread_id) -> None:
            self._bg_persist_tasks.discard(_task)
            if self._persist_tasks_by_run.get(_run_id) is _task:
                self._persist_tasks_by_run.pop(_run_id, None)
            if self._persist_tasks_by_thread.get(_thread_id) is _task:
                self._persist_tasks_by_thread.pop(_thread_id, None)

        _t.add_done_callback(_cleanup)

    async def _await_pending_partial_persist(self, thread_id: str) -> None:
        """新一轮写入/regenerate 删除前收敛同一 thread 上「停止生成」的部分内容落库任务
        （P1-4）：用 shield 防止本次有界等待超时时连带撤销仍在跑的落库任务——超时也只是
        本次不等，任务本身继续在后台跑完，不会被撤销成「写了一半」。"""
        task = self._persist_tasks_by_thread.get(thread_id)
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except Exception:  # noqa: BLE001
            pass

    def _spawn_bg(self, coro) -> None:
        """fire-and-forget 后台任务（持引用防 GC 丢任务——B4 教训）。"""
        t = asyncio.ensure_future(coro)
        self._bg_persist_tasks.add(t)
        t.add_done_callback(self._bg_persist_tasks.discard)
