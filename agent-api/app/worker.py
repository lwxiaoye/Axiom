"""Durable Agent Harness worker.

The API process only accepts commands.  This process leases PostgreSQL jobs, reconstructs the
request from RunState and drives the existing streaming engine while every public event is
persisted to ``agent_run_events``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from app.core.auth import UserContext
from app.core.config import settings
from app.core.database import async_session
from app.core.runtime_db import init_runtime_tables
from app.models import ChatMessage
from app.services import sse_protocol
from app.services.chat.turn_context_builder import merge_composer_reference_meta
from app.services.chat.run_hub import (
    recover_run_after_error,
    recovery_available_at,
    recovery_retry_allowed,
)
from app.services.agent_harness.orchestrator import harness_orchestrator
from app.services.agent_harness import HarnessKernel, run_store
from app.services.tasks import runtime_event_bus, task_run_service


logger = logging.getLogger(__name__)
WORKER_ID = f"agent-worker-{os.getpid()}-{uuid.uuid4().hex[:10]}"
RECOVERY_EXHAUSTED_BACKOFF_SECONDS = 60


def configure_event_loop_policy() -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _fail_run(run_id: str, *, raw_error: str, public_message: str) -> None:
    """Commit one failed terminal truth before the worker exits."""
    applied = await task_run_service.finalize_run(run_id, "failed", error=raw_error)
    phase_updated = await run_store.patch_run_state(
        run_id, {"terminal_reason": public_message}, phase="failed",
    )
    if not applied:
        return
    if phase_updated is not None:
        await task_run_service.append_run_event(
            run_id, "run.phase.changed", {"phase": "failed"},
        )
    await task_run_service.append_run_event(
        run_id, "run.failed", {"message": public_message},
    )
async def _is_task_recovery(run_id: str) -> bool:
    try:
        snapshot = await run_store.get_run_state(run_id)
    except Exception:  # noqa: BLE001
        return False
    state = (snapshot or {}).get("state") or {}
    return (
        str(state.get("interactive_type") or "") == "task_recovery"
        or str((state.get("orchestration") or {}).get("mode") or "") == "task_recovery"
    )


async def _finish_recovery_job(job: dict[str, Any], recovery: dict[str, Any]) -> None:
    """Atomically release the lease and schedule one delayed retry of the same Job.

    The attempt cap is an operational backoff window, never a business terminal decision.  The
    Job store resets that window after delaying a claim, so a transient outage cannot turn the
    Run into ``failed`` merely because several workers observed it.
    """
    job_id = str(job["id"])
    retry_allowed = bool(recovery.get("retry_allowed", recovery_retry_allowed(job)))
    if not bool(recovery.get("checkpoint", True)):
        await run_store.finish_job(job_id, WORKER_ID, status="waiting")
        logger.error(
            "recovery checkpoint unavailable; leave Run for zombie scanner run=%s",
            job.get("run_id"),
        )
        return
    if not retry_allowed:
        logger.warning(
            "recovery attempt window reached; keep Run waiting_system and back off run=%s",
            job.get("run_id"),
        )
    # A recovery with no usable input snapshot cannot make progress on the next one-second
    # retry.  It must stay recoverable, but a short loop must not monopolize the only worker
    # and strand every newly submitted workflow at "流程开始".
    available_at = (
        datetime.utcnow() + timedelta(seconds=RECOVERY_EXHAUSTED_BACKOFF_SECONDS)
        if not retry_allowed
        else recovery.get("available_at") or recovery_available_at()
    )
    released = await run_store.finish_job(
        job_id,
        WORKER_ID,
        status="waiting",
        wake_reason="recovery",
        available_at=available_at,
    )
    if not released:
        # The lease may have expired and been claimed by another worker.  Do not take its lease;
        # enqueue_job's row lock records the same wake on that live Job or reuses the legacy row.
        if not await run_store.enqueue_job(
            str(job["run_id"]), wake_reason="recovery", available_at=available_at,
        ):
            logger.error("recovery Job requeue unavailable run=%s", job["run_id"])


async def _finalize_worker_execution_profile(
    run_id: str, payload: dict[str, Any],
) -> dict[str, Any]:
    """Attach the durable profile before key resolution, model calls, or tool construction."""
    snapshot = await run_store.finalize_execution_profile(run_id)
    data = dict(payload)
    data["execution_profile"] = snapshot
    return data


async def _resolve_worker_input(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    user_id = str(data["user_id"])
    user_context = UserContext.model_validate(data.get("user_context") or {"user_id": user_id, "username": user_id})
    # v2.72：pending.model 为空时回退 AgentRun.model（受理时已写入最终模型）
    want_model = str(data.get("model") or "").strip() or None
    if not want_model and data.get("run_id"):
        try:
            run = await task_run_service.get_run(str(data["run_id"]), user_id)
            want_model = str((run or {}).get("model") or "").strip() or None
        except Exception:  # noqa: BLE001
            want_model = None
    newapi_key, resolved_model = await harness_orchestrator.prepare_chat(user_id, want_model)
    attachments = list(data.get("attachments") or [])

    file_ids = list(data.pop("file_ids", None) or [])
    if file_ids:
        from app.services.chat.turn_context_builder import model_supports_vision
        from app.services.files import user_file_service

        resolved = await user_file_service.build_chat_attachments(
            user_id, file_ids, newapi_key=newapi_key,
            audit_context={
                "run_id": str(data.get("run_id") or ""),
                "thread_id": str(data.get("thread_id") or ""),
            },
            # 多模态模型自己看图：「我的文件」里选的图片不再先经视觉模型转述一遍。
            ocr_visual=not model_supports_vision(resolved_model),
        )
        attachments.extend(resolved or [])

    thread_ids = list(data.pop("thread_ids", None) or [])
    if thread_ids:
        from app.services.chat import thread_reference

        referenced = await thread_reference.build_thread_attachments(
            user_id, thread_ids, current_thread_id=data.get("thread_id"),
        )
        attachments.extend(referenced or [])

    message_id = data.get("precreated_user_message_id")
    if message_id is not None:
        async with async_session() as session:
            row = await session.get(ChatMessage, int(message_id))
            if row and row.thread_id == data.get("thread_id") and row.role == "user":
                meta = merge_composer_reference_meta(
                    attachments,
                    selected_skills=data.get("selected_skills"),
                    selected_knowledge=data.get("selected_knowledge"),
                    subagent_name=str(data.get("subagent_name") or ""),
                    web_search=bool(data.get("web_search")),
                )
                if meta:
                    import json

                    row.attachments_json = json.dumps(meta, ensure_ascii=False)
                    await session.commit()

    data.update({
        "attachments": attachments or None,
        "user_context": user_context,
        "newapi_key": newapi_key,
        "resolved_model": resolved_model,
        "model": resolved_model,
        "token": str(data.pop("access_token", "") or ""),
        "protocol": sse_protocol.HARNESS,
    })
    # v2.72 diagnostics: V2 worker 路径曾出现 free-quota 403，而同 key 在 API 进程 V1 正常
    logger.info(
        "worker_input_resolved run=%s user=%s model=%s credential_set=%s token_len=%s msg_len=%s",
        data.get("run_id"), user_id, resolved_model,
        bool(newapi_key), len(str(data.get("token") or "")),
        len(str(data.get("message") or "")),
    )
    return data


async def _lease_watchdog(job_id: str, run_id: str, execution: asyncio.Task) -> None:
    ticks = 0
    while not execution.done():
        await asyncio.sleep(0.5)
        ticks += 1
        # finalize_terminal first commits the terminal CAS, then patches Harness state and
        # publishes run.completed/run.failed.  Cancelling merely because the CAS is visible races
        # that same execution and leaves a terminal AgentRun with phase=executing and no terminal
        # event.  Only an explicit persisted cancel request may interrupt the worker-owned task.
        if await task_run_service.is_run_cancel_requested(run_id):
            execution.cancel()
            return
        if ticks % 10 == 0:
            await run_store.heartbeat_job(job_id, WORKER_ID, lease_seconds=90)
            await task_run_service.heartbeat_owned_runs()


async def _execute_job(job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    run_id = str(job["run_id"])
    if not await task_run_service.claim_run_execution(run_id):
        status = await task_run_service.get_run_status(run_id)
        if status == "waiting_system" and await _is_task_recovery(run_id):
            recovery = {
                "retry_allowed": recovery_retry_allowed(job),
                "available_at": recovery_available_at(),
            }
            await _finish_recovery_job(job, recovery)
            return
        await run_store.finish_job(
            job_id, WORKER_ID,
            status="completed" if status in task_run_service.TERMINAL_RUN_STATUSES else "queued",
            error="Run lease could not be claimed",
        )
        return

    # load_pending_input 必须在 try 内：access_token 解密失败（多实例 CONNECTOR 密钥不一致）
    # 若落在 try 外，_run_guarded 只打日志，job 永久 leased、run 永久 running，前端「正在思考」挂死。
    try:
        pending = await run_store.load_pending_input(run_id)
        if not pending:
            # A missing/decryptable input snapshot is a recoverable runtime fact,
            # not evidence that the user's goal is impossible. Keep the same Run
            # in waiting_system and let the job backoff/retry rather than creating
            # a synthetic failed terminal state.
            recovery = await recover_run_after_error(
                run_id,
                reason="worker:input_snapshot_missing",
                job=job,
            )
            await _finish_recovery_job(job, recovery)
            return

        pending = await _finalize_worker_execution_profile(run_id, pending)
        kwargs = await _resolve_worker_input(pending)
        hub = harness_orchestrator._hub
        hub._run_meta[run_id] = {
            "thread_id": str(kwargs["thread_id"]),
            "user_id": str(kwargs["user_id"]),
            "protocol": sse_protocol.HARNESS,
        }
        source = HarnessKernel.stream(**kwargs)
        execution = asyncio.create_task(hub._pump_background_run(
            run_id=run_id,
            thread_id=str(kwargs["thread_id"]),
            protocol=sse_protocol.HARNESS,
            source=source,
            job=job,
        ))
        watchdog = asyncio.create_task(_lease_watchdog(job_id, run_id, execution))
        try:
            await execution
        finally:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog

        status = await task_run_service.get_run_status(run_id)
        if status in task_run_service.WAITING_RUN_STATUSES:
            if status == "waiting_system" and await _is_task_recovery(run_id):
                await _finish_recovery_job(
                    job,
                    {"retry_allowed": recovery_retry_allowed(job), "available_at": recovery_available_at()},
                )
            else:
                await run_store.finish_job(job_id, WORKER_ID, status="waiting")
        elif status in task_run_service.TERMINAL_RUN_STATUSES:
            await run_store.finish_job(
                job_id, WORKER_ID, status="completed" if status == "completed" else "dead",
            )
            await run_store.clear_pending_input(run_id)
        else:
            recovery = await recover_run_after_error(
                run_id,
                reason="worker:no_terminal_state",
                job=job,
            )
            await _finish_recovery_job(job, recovery)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Worker job failed run=%s: %s", run_id, exc)
        recovery = await recover_run_after_error(
            run_id,
            reason=f"worker:{type(exc).__name__}",
            job=job,
        )
        await _finish_recovery_job(job, recovery)


async def run_worker(stop: asyncio.Event) -> None:
    await init_runtime_tables(ddl=settings.MIGRATE_ON_STARTUP)
    logger.info("Agent Harness worker started id=%s", WORKER_ID)
    # v2.40：进程内多 job 并发。旧实现严格串行——一个长 PPT 会堵住后续 pure_qa，
    # 用户侧表现为「正在思考」挂死，而 health 仍绿。PG SKIP LOCKED 支持安全并发 claim。
    max_conc = max(1, int(getattr(settings, "WORKER_MAX_CONCURRENT", 3) or 3))
    inflight: set[asyncio.Task] = set()
    logger.info("Agent Harness worker concurrency=%s id=%s", max_conc, WORKER_ID)
    listener = asyncio.create_task(runtime_event_bus.run_job_listener())

    async def _zombie_scan_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
                return
            except asyncio.TimeoutError:
                pass
            try:
                found = await task_run_service.scan_recoverable_zombies()
                if found:
                    logger.warning(
                        "run_zombie_detected count=%s ids=%s",
                        len(found),
                        ",".join(item["id"] for item in found[:8]),
                    )
            except Exception:  # noqa: BLE001
                logger.exception("zombie scan failed")

    scanner = asyncio.create_task(_zombie_scan_loop())

    async def _run_guarded(job: dict[str, Any]) -> None:
        try:
            await _execute_job(job)
        except Exception:  # noqa: BLE001 — 已在 _execute_job 内 finalize；这里只防漏网
            logger.exception("Worker guarded job crashed")

    async def _wait_idle() -> None:
        # NOTIFY 唤醒；1s 轮询只作丢通知兜底。stop 与唤醒任一先到即返回。
        stop_task = asyncio.create_task(stop.wait())
        job_task = asyncio.create_task(runtime_event_bus.wait_for_job(timeout=1.0))
        pending = {stop_task, job_task}
        try:
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    try:
        while not stop.is_set():
            # 回收已完成任务，避免集合无限涨
            done = {t for t in inflight if t.done()}
            for t in done:
                with suppress(Exception):
                    await t  # 传播已记录的异常
            inflight -= done

            if len(inflight) >= max_conc:
                # 满载：等任一 job 结束（短超时以便响应 stop）
                if stop.is_set():
                    break
                finished, _ = await asyncio.wait(
                    inflight, timeout=0.5, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in finished:
                    with suppress(Exception):
                        await t
                    inflight.discard(t)
                continue

            job = await run_store.claim_next_job(WORKER_ID, lease_seconds=90)
            if not job:
                await _wait_idle()
                continue
            task = asyncio.create_task(_run_guarded(job))
            inflight.add(task)
    finally:
        listener.cancel()
        scanner.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await listener
        with suppress(asyncio.CancelledError, Exception):
            await scanner

    # 停机：等在途 job 收尾（最多给一点时间，避免 SIGKILL 丢半截）
    if inflight:
        await asyncio.wait(inflight, timeout=30)


def main() -> None:
    configure_event_loop_policy()
    logging.basicConfig(
        level=getattr(logging, str(getattr(settings, "LOG_LEVEL", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signum, stop.set)
        await run_worker(stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
