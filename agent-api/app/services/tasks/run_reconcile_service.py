"""Run ↔ 消息双库对账（P0 刷新丢失修复 / P0-4 一致性最小闭环）。

三类补偿，均幂等（按 ai_chat_messages.run_id 判重）：
1. 策略拒绝隔离：敏感词终态轮仍对用户可见，但不再进入后续模型上下文。
2. 历史中断回填（兼容旧终态）：旧版本把进程非优雅死亡遗留的孤儿 Run 标成 failed 后，
   其已流出正文只存在于 PG agent_run_events——聚合 delta 回填一条 status=interrupted 的
   助手消息。新代码先把同一 Run 切到 waiting_system/task_recovery 并自动重排，不再走这条
   终态回填路径；该函数仍兼容历史 failed/cancelled Run。
3. 完成核销（启动 + 周期）：completed Run 的 message.completed 带 message_id 但 MySQL
   查无此行（正文提交后事件已发、但极端场景行丢失/回滚），按事件全文以原 id 幂等重建。

跨库无原子事务是既有架构决策（MySQL 正文 / PG 事件），这里的对账是「事后收敛」而非
Outbox——事件流本身已全量落库，收敛只做读侧聚合 + 幂等写，风险面小。
"""
import asyncio
import logging
from typing import Dict, List, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.database import async_session
from app.models import (
    POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES,
    POLICY_REJECTED_PENDING_MESSAGE_STATUS,
    POLICY_REJECTED_MESSAGE_STATUS,
    ChatMessage,
    ChatThread,
)
from app.services.tasks import task_run_service

logger = logging.getLogger(__name__)

_INTERRUPTED_PLACEHOLDER = "（服务重启，执行被中断）"

_ANCHOR_PLACEHOLDERS = {
    "cancelled": "（已停止，未生成回复）",
    "failed": "（任务执行失败，未生成回复）",
}


def _is_mysql_retryable_lock(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None) or ()
    return bool(args) and args[0] in (1205, 1213)


async def quarantine_policy_rejected_run(
    run_id: str,
    thread_id: str,
    *,
    attempts: int = 3,
) -> Optional[int]:
    """保留策略拒绝轮的展示记录，但从后续模型/摘要/记忆上下文排除。

    Run 终态在 Runtime PG，消息在 MySQL，两者不能原子提交。该写入因此必须
    幂等，并对 InnoDB 1205/1213 完整重放；启动和周期对账会再次收敛。
    """
    if not run_id or not thread_id:
        return None
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            # Phase 1: 先把 transcript 改成可见但不可进模型的中间态。
            # 这是跨库安全性的关键：后面 PG 摘要/快照删除即使失败，
            # 用户继续提问时也不会再把已拒绝原文发给网关。
            async with async_session() as session:
                rows = (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.run_id == run_id)
                        .where(ChatMessage.thread_id == thread_id)
                        .where(ChatMessage.role.in_(("user", "assistant")))
                        .order_by(ChatMessage.id.asc())
                    )
                ).scalars().all()
                user_rows = [row for row in rows if row.role == "user"]
                if not user_rows:
                    logger.error(
                        "敏感词终态无法绑定原始用户消息 run=%s thread=%s",
                        run_id,
                        thread_id,
                    )
                    return None
                thread = await session.get(ChatThread, thread_id)
                rejected_auto_titles = {
                    str(row.content or "")[:50]
                    for row in user_rows
                    if str(row.content or "")
                }
                title_needs_clear = bool(
                    thread is not None
                    and str(thread.title or "") in rejected_auto_titles
                )
                if title_needs_clear:
                    # 首轮标题是用户原话的前 50 字。若原话已被策略拒绝，
                    # 标题也不能绕过 live transcript 在「引用对话」中重新注入。
                    thread.title = None
                needs_staging = [
                    row for row in rows
                    if row.status not in {
                        "archived",
                        *POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES,
                    }
                ]
                already_pending = any(
                    row.status == POLICY_REJECTED_PENDING_MESSAGE_STATUS
                    for row in rows
                )
                if not needs_staging and not already_pending:
                    if title_needs_clear:
                        await session.commit()
                    return 0
                await session.execute(
                    update(ChatMessage)
                    .where(ChatMessage.run_id == run_id)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.role.in_(("user", "assistant")))
                    .where(or_(
                        ChatMessage.status.is_(None),
                        ChatMessage.status.notin_((
                            "archived",
                            *POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES,
                        )),
                    ))
                    .values(status=POLICY_REJECTED_PENDING_MESSAGE_STATUS)
                )
                await session.commit()

            # Phase 2: 删除可能已包含拒绝原文的 PG 派生投影。
            # delete_for_thread 同时提升摘要 epoch，在途压缩结果不能回写复活。
            from app.services.memory import context_service

            try:
                await context_service.delete_for_thread(thread_id, strict=True)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "敏感词拒绝轮摘要隔离失败 run=%s thread=%s: %s",
                    run_id,
                    thread_id,
                    exc,
                )
                return None
            if not await task_run_service.delete_context_snapshots_for_run(run_id):
                logger.error(
                    "敏感词拒绝轮快照隔离失败 run=%s thread=%s",
                    run_id,
                    thread_id,
                )
                return None

            # Phase 3: 只有派生投影清理完成后才收敛为最终态。
            # 编辑重发若并发把行改成 archived，该条件更新不会把它复活。
            async with async_session() as session:
                result = await session.execute(
                    update(ChatMessage)
                    .where(ChatMessage.run_id == run_id)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.role.in_(("user", "assistant")))
                    .where(
                        ChatMessage.status
                        == POLICY_REJECTED_PENDING_MESSAGE_STATUS
                    )
                    .values(status=POLICY_REJECTED_MESSAGE_STATUS)
                )
                await session.commit()
                return max(0, int(result.rowcount or 0))
        except OperationalError as exc:
            if not _is_mysql_retryable_lock(exc) or attempt >= attempts:
                logger.error(
                    "敏感词拒绝轮隔离失败 run=%s thread=%s: %s",
                    run_id,
                    thread_id,
                    exc,
                )
                return None
            await asyncio.sleep(0.1 * attempt)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "敏感词拒绝轮隔离失败 run=%s thread=%s: %s",
                run_id,
                thread_id,
                exc,
            )
            return None
    return None


async def reconcile_policy_rejected_messages(window_hours: int = 168) -> int:
    """对账已终态但尚未隔离的旧敏感词拒绝轮。"""
    candidates = await task_run_service.list_policy_rejected_runs(window_hours)
    reconciled = 0
    for item in candidates:
        run_id = str(item.get("run_id") or "")
        thread_id = str(item.get("thread_id") or "")
        changed = await quarantine_policy_rejected_run(
            run_id,
            thread_id,
        )
        if changed is not None:
            # 进程若在敏感词终态与通用 finally 之间退出，启动对账
            # 仍要补齐可见锚点；锚点会继承 user 的 policy 状态。
            await ensure_terminal_anchor(run_id, thread_id)
        if changed:
            reconciled += 1
    return reconciled


async def ensure_terminal_anchor(run_id: str, thread_id: str,
                                 placeholder: Optional[str] = None) -> Optional[int]:
    """无正文终态轮的历史锚点补偿（P0 2026-07-17）。

    用户在「思考中/搜索中/生成中」停止、或任务在产出任何正文前失败/过期时，该 Run 没有
    助手消息也就没有 message_id——整段执行轨迹在历史回放中被归属门丢弃。这里在 Run 已处
    cancelled/failed 终态且 MySQL 无归属行时，插入一条占位助手消息并补 message.completed
    锚点，使执行卡（含思考/工具步骤）能挂上历史。幂等：按 run_id 归属行判重。
    completed 的缺失由 reconcile_completed_messages 按事件全文原 id 重建，不在此处理。
    """
    if not run_id or not thread_id:
        return None
    status = await task_run_service.get_run_status(run_id)
    if status not in ("cancelled", "failed"):
        return None
    try:
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(ChatMessage.id)
                    .where(ChatMessage.run_id == run_id)
                    .where(ChatMessage.role == "assistant")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None  # 已有归属行（部分正文/失败说明），轨迹本就能挂上
            if await session.get(ChatThread, thread_id) is None:
                return None  # 会话已删，不回填
            policy_status = (
                await session.execute(
                    select(ChatMessage.status)
                    .where(ChatMessage.run_id == run_id)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.role == "user")
                    .where(
                        ChatMessage.status.in_(
                            POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES
                        )
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            content = placeholder or _ANCHOR_PLACEHOLDERS.get(status) or _ANCHOR_PLACEHOLDERS["failed"]
            row = ChatMessage(thread_id=thread_id, role="assistant", content=content,
                              run_id=run_id, status=(
                                  str(policy_status)
                                  if policy_status is not None else status
                              ))
            session.add(row)
            await session.commit()
            mid = row.id
        if mid is not None:
            await task_run_service.record_terminal_message_event(run_id, mid, content)
        return mid
    except IntegrityError:
        # 判重/查线程与插入之间会话被并发删除（FK 1452）：等同「会话已删」，静默跳过
        logger.debug("终态锚点回填遇会话并发删除，跳过 run=%s", run_id)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("终态锚点回填失败 run=%s: %s", run_id, e)
        return None


async def backfill_interrupted_runs(orphans: List[Dict[str, str]]) -> int:
    """把启动对账清理出的孤儿 Run 的已流出正文回填为 MySQL 中断消息（幂等）。

    正文来自 PG 事件聚合（collect_run_output_text）；一个字都没流出的 Run 也回填占位行
    ——「执行被中断」必须在 transcript 里可见，而不是与「尚未回答」不可区分。
    """
    backfilled = 0
    for orphan in orphans or []:
        run_id = str(orphan.get("id") or "")
        thread_id = str(orphan.get("thread_id") or "")
        if not run_id or not thread_id:
            continue
        try:
            # A restarted task is now kept as ``waiting_system/task_recovery`` and requeued
            # automatically.  Do not turn that live recovery segment into a historical
            # interrupted/failed message; this backfill remains for genuinely terminal legacy
            # runs only.
            if await task_run_service.get_run_status(run_id) not in {"failed", "cancelled"}:
                continue
            async with async_session() as session:
                existing = (
                    await session.execute(
                        select(ChatMessage.id)
                        .where(ChatMessage.run_id == run_id)
                        .where(ChatMessage.role == "assistant")
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue  # 已有归属行（如停止时的部分正文），不重复回填
                if await session.get(ChatThread, thread_id) is None:
                    continue  # 会话已被用户删除（delete_thread 不清 PG 侧 Run）：有意删除，不回填
                output = await task_run_service.collect_run_output_text(run_id)
                content = (output.get("text") or "").strip() or _INTERRUPTED_PLACEHOLDER
                row = ChatMessage(
                    thread_id=thread_id, role="assistant", content=content,
                    run_id=run_id, status="interrupted",
                )
                session.add(row)
                await session.commit()
                mid = row.id
            if mid is not None:
                await task_run_service.record_terminal_message_event(run_id, mid, content)
                # 终态帧补录（P0 三批）：中断 Run 的事件日志里没有任何 run.failed——
                # 订阅回放会裸 EOF；补一条让回放有业务终态可依
                await task_run_service.append_run_event(
                    run_id, "run.failed", {"message": "服务重启，运行中断"})
                backfilled += 1
        except IntegrityError:
            # 存在性检查与插入之间会话被并发删除（FK 1452，实测踩中）：等同已删，跳过
            logger.debug("中断回填遇会话并发删除，跳过 run=%s", run_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("中断轮回填失败 run=%s: %s", run_id, e)
    return backfilled


async def reconcile_completed_messages(window_hours: int = 24) -> int:
    """「Run completed 但 MySQL 消息缺失」核销：按 message.completed 事件全文以原 id 重建。

    原 id 重建是关键——执行轨迹/引用快照都按 message_id 键控，换 id 会让它们成孤儿。
    id 已被占用（正常情况：行本来就在）即跳过，天然幂等。
    """
    rebuilt = 0
    candidates = await task_run_service.list_completed_runs_missing_check(window_hours)
    for item in candidates:
        message_id = int(item.get("message_id") or 0)
        thread_id = str(item.get("thread_id") or "")
        run_id = str(item.get("run_id") or "")
        if not message_id or not thread_id:
            continue
        try:
            async with async_session() as session:
                existing = await session.get(ChatMessage, message_id)
                if existing is not None:
                    continue
                # 会话已删（delete_thread 级联清消息但不清 PG Run）：这不是丢消息，是有意
                # 删除——跳过，否则 FK 拒绝插入且每轮对账都重试刷警告（启动实测踩中）
                if await session.get(ChatThread, thread_id) is None:
                    continue
                session.add(ChatMessage(
                    id=message_id, thread_id=thread_id, role="assistant",
                    content=str(item.get("text") or "") or "（内容缺失，已由对账重建）",
                    run_id=run_id, status="completed",
                ))
                await session.commit()
                rebuilt += 1
                logger.error(
                    "双库对账重建缺失消息：run=%s message_id=%s（正文曾提交失败/丢失，请排查）",
                    run_id, message_id,
                )
        except IntegrityError:
            # 线程存在性检查与插入之间会话被并发删除（FK 1452，实测踩中）：等同已删，跳过
            logger.debug("完成核销遇会话并发删除，跳过 run=%s", run_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("完成核销重建失败 run=%s message_id=%s: %s", run_id, message_id, e)
    return rebuilt


async def reconcile_on_startup() -> Dict[str, int]:
    """启动对账入口：策略拒绝隔离 → 孤儿清理 → 中断回填 → 完成核销。

    完成核销扫描窗口取 RECONCILE_STARTUP_WINDOW_HOURS（默认 168h=7 天，覆盖跨周末停机的
    积压）；周期 sweep 走 RECONCILE_SWEEP_WINDOW_HOURS（默认 2h）。需要对更旧历史做一次性
    深扫时，临时把 startup 窗口调大（如 8760=一年）重启即可——核销幂等（按原 message_id
    判重），扫过即收敛，之后改回默认值。
    """
    from app.core.config import settings
    policy_quarantined = await reconcile_policy_rejected_messages(
        window_hours=settings.RECONCILE_STARTUP_WINDOW_HOURS)
    orphans = await task_run_service.reconcile_orphan_running_details()
    backfilled = await backfill_interrupted_runs(orphans)
    rebuilt = await reconcile_completed_messages(
        window_hours=settings.RECONCILE_STARTUP_WINDOW_HOURS)
    return {
        "policy_quarantined": policy_quarantined,
        "orphaned": len(orphans),
        "backfilled": backfilled,
        "rebuilt": rebuilt,
    }
