"""上下文预算与 Compaction（§13，Phase 6 后半）。

三件事：
1) **覆盖检查**：只移除已成功摘要覆盖的历史，未摘要原文全部保留；
2) **会话摘要（Compaction）**：会话估算 token 超阈值后，异步用轻量调用把旧段压缩为结构化摘要，
   存 Runtime PG `agent_thread_summaries`（MySQL 消息表保持全量 Transcript，摘要不覆盖原始记录）；
3) **Prompt 组装**：有摘要时，`covered_message_id` 之前的消息以摘要块替代。

摘要必须保留：当前目标、用户约束、已确认字段、已执行动作、关键结果与未完成事项（§13.3）。
Runtime 域库未配置时保留原文，由模型窗口预检处理，不能把未读取内容标成已覆盖。
"""
import asyncio
import json
import logging
import weakref
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.runtime_db import runtime_session
from app.services.platform import model_window
from app.services.platform.token_estimator import (
    calibration_factor,
    estimate_tokens,
    reset_thread_prompt,
    thread_prompt_floor,
)

logger = logging.getLogger(__name__)

# 同 thread 压缩串行化：避免多轮并发重复压缩 + covered 水位竞争（fire-and-forget 遗留问题）。
# 用 WeakValueDictionary 防进程级无界增长：`async with _lock_for(t)` 期间调用方栈帧持有强
# 引用，同 thread 的并发协程拿到同一把锁（串行化正确）；临界区全部退出后无强引用即自动回收，
# 十万级会话不再常驻十万把 Lock。_lock_for 是无 await 的同步函数（字节码原子，无并发窗口）。
_thread_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _lock_for(thread_id: str) -> asyncio.Lock:
    lock = _thread_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock
    return lock


# 摘要世代号（2026-07-29 深扫 P0）：编辑重发的 delete_for_thread **不持锁**（持锁会被
# 后台分片压缩阻塞到 600s，卡在用户路径上），改用世代号让"已作废的压缩结果"在写回时
# 自我识别。事故形态：后台压缩已读出 rows/covered、正等 60s 内的摘要 LLM 返回时，
# 用户编辑重发删掉摘要行并把若干消息标 archived；LLM 返回后 get() 拿到 None 于是**新插**
# 一行，把已作废分支的内容以摘要散文永久复活（原文被排除了，结论却留下），全程无异常。
# 有界 LRU（2026-07-29 自查补）：世代号只需在**一次压缩的生命周期内**有效（读消息 →
# 等 LLM ≤60s → 写回），不必永久保存。上面 _thread_locks 特意用 WeakValueDictionary 防
# 「十万级会话常驻十万把锁」，紧挨着再放一个永不清理的 dict 就是同一个坑的另一半。
# int 做不了弱引用（不可变、可能被 intern、无强引用持有者），故用 OrderedDict 定容。
# 被挤出的 thread 只是退回改动前行为（在途压缩不再自我作废），方向安全；且要挤掉它
# 需要 _EPOCH_MAX 个不同会话在那 60 秒窗口内轮换。
_EPOCH_MAX = 4096
_summary_epochs: "OrderedDict[str, int]" = OrderedDict()

# 摘要 prompt 的 transcript 字符上限（逐条累加，装不下的留给下一片，见 maybe_compact）
_TRANSCRIPT_CHAR_CAP = 24000


def _bump_epoch(thread_id: str) -> None:
    _summary_epochs[thread_id] = _summary_epochs.get(thread_id, 0) + 1
    _summary_epochs.move_to_end(thread_id)
    while len(_summary_epochs) > _EPOCH_MAX:
        _summary_epochs.popitem(last=False)


def _epoch_of(thread_id: str) -> int:
    return int(_summary_epochs.get(thread_id, 0))


async def get_summary(thread_id: str) -> Optional[dict]:
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentThreadSummary
    try:
        async with factory() as session:
            row = await session.get(AgentThreadSummary, thread_id)
            if row is None:
                return None
            raw = row.summary
            if isinstance(raw, str) and raw.lstrip().startswith("{"):
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("text"):
                    return {
                        "summary": str(payload.get("text") or ""),
                        "replacement_history": payload.get("replacement_history"),
                        "covered_message_id": row.covered_message_id,
                        "version": row.version,
                    }
            return {"summary": raw, "covered_message_id": row.covered_message_id, "version": row.version}
    except Exception as e:  # noqa: BLE001
        logger.warning("读取会话摘要失败（按无摘要处理）: %s", e)
        return None


def _summary_messages(summary: Optional[dict]) -> list[dict]:
    if not summary or not summary.get("summary"):
        return []
    history = summary.get("replacement_history")
    if isinstance(history, list) and history:
        return [{"role": "user", "content": str(item.get("content") or "")}
                for item in history if isinstance(item, dict)]
    from app.services.agent_harness.conversation_compact import SUMMARY_PREFIX
    return [{"role": "user", "content": f"{SUMMARY_PREFIX}\n{summary['summary']}"}]


def apply_context_budget(
    rows: List[Any],
    summary: Optional[dict],
    budget_tokens: Optional[int] = None,
    hard_cap_tokens: Optional[int] = None,
) -> Tuple[List[Any], str, int]:
    """返回 (纳入 Prompt 的消息行, 摘要块文本, 应急丢弃条数)。

    rows 为按时间升序的消息 ORM 行（.id/.content/.role）。有摘要时先去掉已覆盖段。

    **压缩优先，不静默丢弃**（Codex 式）：正常路径保留**全部未覆盖原文**——超量旧段应由
    发送前的 `ensure_compacted` 压进摘要，而不是在这里按预算裁掉（那会让未摘要原文永久消失）。
    超 `hard_cap` 时告警但仍保留原文；压缩失败不能靠删除未摘要消息伪装成成功。
    """
    summary_block = ""
    effective = list(rows)
    if summary and summary.get("summary"):
        covered = int(summary.get("covered_message_id") or 0)
        effective = [m for m in effective if (getattr(m, "id", None) or 0) > covered]
        history = summary.get("replacement_history")
        exact_parts: list[str] = []
        if isinstance(history, list):
            for item in history:
                if isinstance(item, dict):
                    text = str(item.get("content") or "").strip()
                elif isinstance(item, str):
                    text = item.strip()
                else:
                    text = ""
                if text:
                    exact_parts.append(text)
        summary_block = "\n\n".join(exact_parts) if exact_parts else (
            "（早前对话摘要，可能不完整，与近期消息冲突时以近期消息为准）\n" + str(summary["summary"])
        )

    hard_cap = hard_cap_tokens or (budget_tokens or settings.CONTEXT_HISTORY_TOKEN_BUDGET) * 4
    used = estimate_tokens(summary_block)
    total = used + sum(estimate_tokens(getattr(m, "content", "") or "") for m in effective)
    if total <= hard_cap:
        return effective, summary_block, 0

    logger.warning(
        "上下文超过软上限，保留全部未摘要原文交由模型窗口预检处理：tokens=%s cap=%s",
        total, hard_cap,
    )
    return effective, summary_block, 0


async def ensure_compacted(
    thread_id: str,
    model: str,
    api_key: str,
    *,
    blocking: bool = False,
    audit_run_id: str = "",
    audit_root_run_id: str = "",
) -> bool:
    """发送前压缩保障。

    默认：小段同步压完再放行；超 ``CONTEXT_COMPACT_SYNC_MAX_TOKENS`` 转后台分片。
    ``blocking=True`` 时对齐 Codex compact turn：触发即同步压，前端才能画出
    Compacting context 过程；超时后才降级后台。
    """
    window = model_window.resolve_window(model)
    trigger = model_window.compact_trigger(window)
    est = await _segment_estimate(thread_id, model)
    if (
        not blocking
        and est >= trigger
        and est > settings.CONTEXT_COMPACT_SYNC_MAX_TOKENS
    ):
        _spawn_background_compact(
            thread_id,
            model,
            api_key,
            trigger,
            audit_run_id=audit_run_id,
            audit_root_run_id=audit_root_run_id,
        )
        return False
    async with _lock_for(thread_id):
        try:
            # 硬超时：压缩 LLM 卡住不阻塞对话，超时即降级不压缩（下一轮再试）
            from app.services.agent_harness.conversation_compact import (
                COMPACT_TURN_TIMEOUT_SECONDS,
            )
            return await asyncio.wait_for(
                maybe_compact(
                    thread_id,
                    model,
                    api_key,
                    trigger_tokens=trigger,
                    audit_run_id=audit_run_id,
                    audit_root_run_id=audit_root_run_id,
                    audit_purpose="compaction_preflight",
                    audit_purpose_detail="send_preflight",
                ),
                timeout=COMPACT_TURN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("发送前压缩超时（%ss），本轮降级不压缩", COMPACT_TURN_TIMEOUT_SECONDS)
            if blocking:
                _spawn_background_compact(
                    thread_id,
                    model,
                    api_key,
                    trigger,
                    audit_run_id=audit_run_id,
                    audit_root_run_id=audit_root_run_id,
                )
            return False


async def _segment_estimate(thread_id: str, model: str) -> int:
    """只读预检（无锁）：未覆盖历史体量 = max(估算×校准, 线程级真实 usage 账本)。
    用于同步/后台分流；失败返回 0（退回同步小路径，行为等于旧版）。"""
    try:
        from sqlalchemy import select as sa_select

        from app.core.database import async_session
        from app.models import ChatMessage, live_chat_message_clause

        existing = await get_summary(thread_id)
        covered = int((existing or {}).get("covered_message_id") or 0)
        async with async_session() as session:
            contents = (
                await session.execute(
                    sa_select(ChatMessage.content)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(live_chat_message_clause())
                    .where(ChatMessage.id > covered)
                )
            ).scalars().all()
        est = int((sum(estimate_tokens(c or "") for c in contents)
                   + sum(estimate_tokens(m["content"]) for m in _summary_messages(existing)))
                  * calibration_factor(model))
        return max(est, thread_prompt_floor(thread_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("压缩预检失败，退回同步路径: %s", exc)
        return 0


_BG_COMPACTING: set = set()   # 单飞：同 thread 后台压缩不重入
_BG_TASKS: set = set()        # 强引用防 GC 提前回收 task
_BG_MAX_PASSES = 50           # 分片循环保险丝


def is_background_compacting(thread_id: str) -> bool:
    return bool(thread_id) and thread_id in _BG_COMPACTING


def _spawn_background_compact(
    thread_id: str,
    model: str,
    api_key: str,
    trigger: int,
    *,
    audit_run_id: str = "",
    audit_root_run_id: str = "",
) -> None:
    """大段后台分片压缩（pi agent_end 空闲期压缩的等价物）：每片 ≤ SYNC_MAX（保真上限内），
    压完一片推进水位再压下一片，直到降到触发线下。失败只告警——发送前预检下一轮还会再来。"""
    if thread_id in _BG_COMPACTING:
        return
    _BG_COMPACTING.add(thread_id)

    async def _run() -> None:
        try:
            async with _lock_for(thread_id):
                deadline = asyncio.get_event_loop().time() + settings.CONTEXT_COMPACT_BACKGROUND_TIMEOUT_SECONDS
                for _ in range(_BG_MAX_PASSES):
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        logger.warning("后台压缩超时退出 thread=%s（已压部分水位保留）", thread_id)
                        return
                    did = await asyncio.wait_for(
                        maybe_compact(
                            thread_id, model, api_key, trigger_tokens=trigger,
                            max_segment_tokens=settings.CONTEXT_COMPACT_SYNC_MAX_TOKENS,
                            audit_run_id=audit_run_id,
                            audit_root_run_id=audit_root_run_id,
                            audit_purpose="compaction_background",
                            audit_purpose_detail="thread_segment",
                        ),
                        timeout=remaining,
                    )
                    if not did:
                        return  # 已降到触发线下 / 无可压段
            logger.info("后台分片压缩完成 thread=%s", thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("后台压缩失败 thread=%s: %s", thread_id, exc)
        finally:
            _BG_COMPACTING.discard(thread_id)

    task = asyncio.create_task(_run())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def force_compact(
    thread_id: str,
    model: str,
    api_key: str,
    *,
    audit_run_id: str = "",
    audit_root_run_id: str = "",
) -> bool:
    """无视阈值立即压缩（超窗 400 兜底用）：模型/网关拒收上下文时，把旧段压进摘要后重试。

    trigger_tokens=1 强制触发（仍要求未覆盖消息数 > 最近保留数，否则无可压段返回 False）。
    """
    async with _lock_for(thread_id):
        try:
            return await asyncio.wait_for(
                maybe_compact(
                    thread_id,
                    model,
                    api_key,
                    trigger_tokens=1,
                    audit_run_id=audit_run_id,
                    audit_root_run_id=audit_root_run_id,
                    audit_purpose="compaction_preflight",
                    audit_purpose_detail="overflow_recovery",
                ),
                timeout=settings.CONTEXT_COMPACT_TIMEOUT_SECONDS * 2,
            )
        except asyncio.TimeoutError:
            logger.warning("强制压缩超时，放弃重试")
            return False


async def maybe_compact(
    thread_id: str, model: str, api_key: str, trigger_tokens: Optional[int] = None,
    max_segment_tokens: Optional[int] = None,
    *,
    audit_run_id: str = "",
    audit_root_run_id: str = "",
    audit_purpose: str = "compaction_preflight",
    audit_purpose_detail: str = "thread_summary",
) -> bool:
    """会话超阈值时生成/滚动摘要。返回是否执行了压缩（分片模式下=是否压完了一片）。

    trigger_tokens 未给时用兜底 `CONTEXT_COMPACT_THRESHOLD_TOKENS`（窗口未知场景）。
    max_segment_tokens 限制单次压缩片体量（后台大段分片滚动用）。
    """
    factory = runtime_session()
    if factory is None or not model or not api_key:
        return False
    from sqlalchemy import select as sa_select

    from app.core.database import async_session
    from app.models import ChatMessage, live_chat_message_clause
    from app.runtime_models import AgentThreadSummary

    try:
        # 世代号在**读消息之前**取（见 _summary_epochs 注释）：写回时比对，
        # 若期间发生过编辑重发截断则丢弃本次结果，不复活已作废分支。
        epoch_at_start = _epoch_of(thread_id)
        async with async_session() as session:
            rows = (
                await session.execute(
                    sa_select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(live_chat_message_clause())  # P1 版本化：旧版/死分支不进压缩摘要
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()

        existing = await get_summary(thread_id)
        covered = int((existing or {}).get("covered_message_id") or 0)
        uncovered = [m for m in rows if (m.id or 0) > covered]
        # 估算 × 真值校准因子（usage 回灌，§13）+ 线程级真实 usage 下限锚（2026-07-27
        # Claude/pi 式后台记账）：渠道回传的 prompt_tokens 是上一请求的真实体积，两个信号
        # 取 max——估算系统性偏低时以真值托底，方向安全（宁早压不超窗）。账本含系统块/附件
        # 会略高估未覆盖历史，只会让触发略提前，可接受。
        factor = calibration_factor(model)
        summary_messages = _summary_messages(existing)
        summary_tokens = sum(estimate_tokens(m["content"]) for m in summary_messages) * factor
        est_tokens = int(sum(estimate_tokens(m.content or "") for m in uncovered) * factor + summary_tokens)
        uncovered_tokens = max(est_tokens, thread_prompt_floor(thread_id))
        trigger = trigger_tokens or settings.CONTEXT_COMPACT_THRESHOLD_TOKENS
        if uncovered_tokens < trigger:
            return False

        # 保留近期按 token（pi keepRecentTokens=20000，小窗口自适应触发线 1/3）：
        # 从最新往回累计到预算即止；至少保留 2 条兜底（与旧条数口径的下限一致）。
        keep_budget = min(settings.CONTEXT_COMPACT_KEEP_RECENT_TOKENS, max(1, trigger // 3))
        keep_n, acc = 0, 0.0
        for m in reversed(uncovered):
            acc += estimate_tokens(m.content or "") * factor
            keep_n += 1
            if acc >= keep_budget and keep_n >= 2:
                break
        keep_n = max(2, keep_n)
        to_compact = uncovered[:-keep_n] if len(uncovered) > keep_n else []
        # 分片滚动（2026-07-27）：摘要 prompt 有 [:24000] 字符保真上限，超大段一把压会让
        # 超出部分**未进摘要即被移出上下文**。每次只压最旧一片（≤ max_segment_tokens），
        # 推进 covered 水位；大段由后台循环多片压完。
        if max_segment_tokens and to_compact:
            sliced, seg_acc = [], 0.0
            for m in to_compact:
                seg_acc += estimate_tokens(m.content or "") * factor
                sliced.append(m)
                if seg_acc >= max_segment_tokens:
                    break
            to_compact = sliced
        if not to_compact and not summary_messages:
            return False
        # 负收益保护（E2E 实证）：被压段比摘要本身还短时，压缩会让占用不降反升——
        # 视为无可压段。手动压缩由此正确提示「暂无需压缩」，不产出比原文长的摘要。
        to_compact_tokens = int(
            sum(estimate_tokens(m.content or "") for m in to_compact) * factor + summary_tokens
        )
        if to_compact_tokens < settings.CONTEXT_COMPACT_MIN_SEGMENT_TOKENS:
            return False

        # transcript 字符上限**不能用整体切片**（2026-07-29 深扫 P0：消息无声蒸发）。
        # 原实现 "\n".join(...)[:24000] 切掉的是尾部（即最新、最靠近 keep_n 边界的几条），
        # 而 new_covered 仍取 to_compact[-1].id —— 那几条既没进摘要（LLM 没看到）、
        # 又因 id ≤ covered 被永久排除在原文之外，无告警。改成逐条累加：装不下就**不收**，
        # 并把 to_compact 截到真正进了 prompt 的那一条，covered 只推进到它。
        _lines: list = []
        _used = 0
        _fitted = 0
        for m in to_compact:
            line = f"{'用户' if m.role == 'user' else '助手'}：{m.content or ''}"
            if _lines and _used + len(line) + 1 > _TRANSCRIPT_CHAR_CAP:
                break
            _lines.append(line)
            _used += len(line) + 1
            _fitted += 1
        if _fitted < len(to_compact):
            logger.info(
                "压缩段超字符上限：本次只压前 %d/%d 条，其余留给下一片（covered 相应只推进到第 %d 条）",
                _fitted, len(to_compact), _fitted)
            to_compact = to_compact[:_fitted]
        from app.services.agent_harness.conversation_compact import (
            build_compacted_history,
            collect_user_messages,
            generate_compaction_summary,
        )
        compact_messages: list[dict] = list(summary_messages)
        for m in to_compact:
            compact_messages.append({
                "role": "user" if m.role == "user" else "assistant",
                "content": m.content or "",
            })
        try:
            summary_text = await generate_compaction_summary(
                compact_messages, model=model, api_key=api_key,
                request_scope_id=str(audit_run_id or thread_id or ""),
                run_id=str(audit_run_id or ""),
                thread_id=str(thread_id or ""),
                root_run_id=str(audit_root_run_id or ""),
                purpose=str(audit_purpose or "compaction_preflight"),
                purpose_detail=str(audit_purpose_detail or "thread_summary"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话摘要生成失败：%s", exc)
            return False
        if not summary_text:
            return False

        # 世代校验：读 rows 之后若发生过 delete_for_thread（编辑重发截断），本次结果
        # 描述的是**已被作废的分支**，写回等于把它复活成永久基线——丢弃。
        if _epoch_of(thread_id) != epoch_at_start:
            logger.info("会话 %s 的摘要在生成期间被作废（编辑重发），丢弃本次压缩结果", thread_id)
            return False

        # Provider output is token-bounded. Never truncate a successful summary before
        # advancing the coverage cursor, including when Latin output exceeds a char target.
        stored = summary_text

        from app.services.agent_harness.conversation_compact import (
            _compaction_input_limit,
        )
        excerpt_budget = max(0, int((
            _compaction_input_limit(model) - estimate_tokens(stored) * calibration_factor(model) - 512
        ) / max(1.0, calibration_factor(model))))

        replacement = build_compacted_history(
            system_messages=[],
            user_messages=collect_user_messages(compact_messages),
            summary=stored,
            max_user_tokens=min(4_000, excerpt_budget),
        )
        replacement_tokens = sum(estimate_tokens(item["content"]) for item in replacement) * factor
        if replacement_tokens > _compaction_input_limit(model):
            logger.warning("摘要检查点仍超过目标窗口，保留原始上下文")
            return False
        if not to_compact and replacement_tokens >= summary_tokens:
            return False  # A summary-only pass must make progress; do not loop on it in background.
        stored_payload = json.dumps(
            {
                "text": stored,
                "replacement_history": [
                    {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
                    for item in replacement
                ],
            },
            ensure_ascii=False,
        )
        stored = stored_payload

        new_covered = (to_compact[-1].id or covered) if to_compact else covered
        async with factory() as rsession:
            row = await rsession.get(AgentThreadSummary, thread_id, with_for_update=True)
            if int(getattr(row, "covered_message_id", 0) or 0) != covered:
                logger.info("会话 %s 的摘要基线已变化，丢弃过期压缩结果", thread_id)
                return False
            if existing and existing.get("version") is not None and getattr(row, "version", None) != existing["version"]:
                logger.info("会话 %s 的摘要版本已变化，丢弃过期压缩结果", thread_id)
                return False
            if row is None:
                rsession.add(AgentThreadSummary(
                    thread_id=thread_id, summary=stored, covered_message_id=new_covered,
                ))
            else:
                # Coverage and text must describe the same snapshot; never combine an old
                # summary with another worker's newer coverage cursor.
                row.summary = stored
                row.covered_message_id = new_covered
                row.version = (row.version or 1) + 1
            await rsession.commit()
        # 清线程级 usage 账本（pi stale-usage 守卫的等价物）：旧值反映压缩前体积，
        # 不清会「刚压完立刻又触发」；清掉后退回估算路径，下一轮真实 usage 会重新记账。
        reset_thread_prompt(thread_id)
        logger.info("会话 %s 已压缩至消息 %s（摘要 %d 字）", thread_id, new_covered, len(summary_text))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("会话摘要压缩失败（不影响对话）: %s", e)
        return False


async def delete_for_thread(thread_id: str, *, strict: bool = False) -> None:
    """删除会话摘要。strict=True 时失败**必须抛**——编辑重发截断链路用：若吞掉删除失败，
    MySQL 消息删了而 PG 旧摘要残留，模型会继续看到被编辑掉的旧分支（幽灵摘要失败路径）。
    Thread 整体删除等尽力而为场景保持默认非 strict（失败仅告警）。

    Runtime 未配置时 strict 也静默返回（刻意，非遗漏）：摘要本身存在 Runtime PG——未配置
    则摘要存储不存在、幽灵摘要不可能发生，「保证无残留摘要」空真成立；此时抛错只会让降级
    环境的编辑重发永远失败，没有任何收益。"""
    # 截断/删除后旧账本必然失真（反映删前体积），先清；下一轮真实 usage 重新记账
    reset_thread_prompt(thread_id)
    # 世代号 +1：在途压缩（可能正等 LLM 返回）写回时会发现世代已变而丢弃结果，
    # 不会把刚被删掉的分支内容复活成摘要。必须在删除**之前**递增——否则存在
    # 「压缩在删除后、递增前完成」的窄窗。
    _bump_epoch(thread_id)
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentThreadSummary
    try:
        async with factory() as session:
            row = await session.get(AgentThreadSummary, thread_id)
            if row is not None:
                await session.delete(row)
                await session.commit()
    except Exception as e:  # noqa: BLE001
        if strict:
            raise RuntimeError(f"清理会话摘要失败，编辑重发已中止以避免旧摘要污染: {e}") from e
        logger.warning("清理会话摘要失败: %s", e)
