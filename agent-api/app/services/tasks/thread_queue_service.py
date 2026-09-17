"""运行中消息队列（任务模式设计稿 §3）。

主对话/任务运行时新消息默认进待发送队列，不立即打断；当前回复/任务结束后由前端按 position
升序**逐条**派发（一次一条，不并发触发多任务）。服务端持久化——刷新、切换会话仍保留；携带附件
引用，派发时附件不丢。支持排序/编辑/删除/移回输入框。

「立即引导」不走本队列（走 run_input_service）；waiting_user 的输入直接用于恢复任务，
也不进队列——两条路径与本队列严格分开（§3/§4）。
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, List, Optional

from sqlalchemy import delete, select, func, text, update

from app.core.runtime_db import runtime_session

logger = logging.getLogger(__name__)

_MAX_QUEUE_PER_THREAD = 20  # 待发送上限，防堆积
_ACTIVE_RUN_STATUSES = ("created", "running", "routing",
                        "waiting_user", "waiting_confirmation", "waiting_system")
_DISPATCH_LEASE_SEC = 90  # 派发租约：dispatching 超此时长未被 confirm（发送失败/关标签）即回收重派


def _row_to_dict(r) -> dict:
    context = None
    raw_context = getattr(r, "context_json", None)
    if raw_context:
        try:
            context = json.loads(raw_context)
        except Exception:  # noqa: BLE001
            context = None
    return {
        "id": r.id,
        "content": r.content,
        "attachments": json.loads(r.attachments_json) if r.attachments_json else None,
        # TurnContext 快照（审计项 1）：派发时按它恢复入队时刻的 Skill/KB/文件/模型选择
        "context": context,
        "position": r.position,
        "status": getattr(r, "status", None) or "queued",
        "leaseToken": getattr(r, "lease_token", None),
        "createdAt": r.created_at.isoformat() if r.created_at else None,
    }


async def _reclaim_expired_dispatches(session, thread_id: str) -> None:
    """回收指定会话的过期派发租约；调用方须已持有 queue 咨询锁。"""
    from app.runtime_models import AgentRun, AgentThreadQueue

    expired = (
        await session.execute(
            select(AgentThreadQueue).where(
                AgentThreadQueue.thread_id == thread_id,
                AgentThreadQueue.status == "dispatching",
                AgentThreadQueue.lease_at
                < datetime.now() - timedelta(seconds=_DISPATCH_LEASE_SEC),
            )
        )
    ).scalars().all()
    for stale in expired:
        dispatched = False
        if getattr(stale, "dispatched_run_id", None):
            try:
                dispatched = (
                    await session.execute(
                        select(AgentRun.id)
                        .where(AgentRun.id == stale.dispatched_run_id)
                        .limit(1)
                    )
                ).scalar_one_or_none() is not None
            except Exception:  # noqa: BLE001
                dispatched = False
        # 条件化写回（2026-07-26 并发审计）：AgentThreadQueue 无 version_id_col，上面的
        # SELECT 也没有 FOR UPDATE，而本函数在「查 AgentRun 是否已建」处有真实网络往返——
        # 这段窗口里 bind_dispatch/finish_dispatch 可以先提交，随后被这里的 ORM 盲写
        # （UPDATE ... WHERE id=? 全字段）覆盖，表现为「同一条排队消息被派发两次」。
        # 与 task_run_service 孤儿对账同一母题（那边已改条件化 UPDATE），这里补齐。
        if dispatched:
            logger.info(
                "队列项 %s 租约过期但 Run %s 已建，确认删除不重派",
                stale.id, stale.dispatched_run_id,
            )
            # 只删「仍绑在这个 run 上」的行：并发 finish_dispatch 已删掉时 rowcount=0，
            # 不再走 ORM delete 的 flush（那会因行已消失抛 StaleDataError 把整个请求打成 500）
            await session.execute(
                delete(AgentThreadQueue).where(
                    AgentThreadQueue.id == stale.id,
                    AgentThreadQueue.dispatched_run_id == stale.dispatched_run_id,
                )
            )
            # 行已按 SQL 删除：把 ORM 对象移出会话（expire 会因实例不再 persistent 抛
            # InvalidRequestError，把整个 list_queue/pop_next 打成 500）
            session.expunge(stale)
            continue
        else:
            # 复位条件按「SELECT 时观测到的 dispatched_run_id」做 CAS——bind_dispatch 只改这
            # 一个字段（status/lease_token 都不动），所以它正是本次快照与并发写的唯一判别位：
            #   观测 NULL 且仍 NULL → 复位（正常过期回收）
            #   观测 NULL 但已被并发 bind 成 R → 失配跳过（不覆盖刚绑定的派发）
            #   观测 R 且仍是 R → 复位（bind 了但 Run 始终没建成，须回收重派，不丢）
            observed_run = getattr(stale, "dispatched_run_id", None)
            await session.execute(
                update(AgentThreadQueue)
                .where(
                    AgentThreadQueue.id == stale.id,
                    AgentThreadQueue.status == "dispatching",
                    AgentThreadQueue.lease_token == stale.lease_token,
                    AgentThreadQueue.dispatched_run_id.is_(None)
                    if observed_run is None
                    else AgentThreadQueue.dispatched_run_id == observed_run,
                )
                .values(status="queued", lease_at=None, lease_token=None,
                        dispatched_run_id=None)
            )
        # ORM 身份映射里仍是旧值：过期本行，避免同事务后续读到已被条件化写覆盖的陈旧对象
        session.expire(stale)


async def enqueue(*, thread_id: str, user_id: str, content: str,
                  attachments: Optional[List[Any]] = None,
                  context: Optional[dict] = None) -> Optional[dict]:
    """入队一条待发送消息（追加到队尾）。库未配置返回 None（前端退化为本地队列）。
    context=入队时刻的 TurnContext 快照（审计项 1），派发时按它执行、不读当前选择。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        # 并发入队护栏（审计项 28）：与 pop_next 同款事务级咨询锁按 thread 串行化——
        # count 上限检查 / max(position)+1 / INSERT 之间不再有并发窗口，杜绝同 position
        # 双行与突破 20 条上限。
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                              {"k": f"queue:{thread_id}"})
        count = (await session.execute(
            select(func.count(AgentThreadQueue.id)).where(
                AgentThreadQueue.thread_id == thread_id, AgentThreadQueue.user_id == user_id)
        )).scalar_one()
        if count >= _MAX_QUEUE_PER_THREAD:
            raise ValueError(f"待发送队列已满（上限 {_MAX_QUEUE_PER_THREAD} 条），请先发送或清理")
        max_pos = (await session.execute(
            select(func.coalesce(func.max(AgentThreadQueue.position), 0)).where(
                AgentThreadQueue.thread_id == thread_id, AgentThreadQueue.user_id == user_id)
        )).scalar_one()
        row = AgentThreadQueue(
            id=uuid.uuid4().hex, thread_id=thread_id, user_id=user_id,
            content=content, position=int(max_pos) + 1,
            attachments_json=json.dumps(attachments, ensure_ascii=False) if attachments else None,
            context_json=json.dumps(context, ensure_ascii=False) if context else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _row_to_dict(row)


async def list_queue(*, thread_id: str, user_id: str) -> List[dict]:
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        # 刷新页面本身也要回收过期租约。此前只有 pop_next 才回收：用户关闭标签后再次
        # 打开，只会永远看到 dispatching 且无法编辑/发送，除非恰好触发下一次 pop。
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"queue:{thread_id}"},
        )
        await _reclaim_expired_dispatches(session, thread_id)
        rows = (await session.execute(
            select(AgentThreadQueue)
            .where(AgentThreadQueue.thread_id == thread_id, AgentThreadQueue.user_id == user_id)
            .order_by(AgentThreadQueue.position.asc())
        )).scalars().all()
        await session.commit()
    return [_row_to_dict(r) for r in rows]


async def get_item_thread_id(*, item_id: str, user_id: str) -> Optional[str]:
    """Resolve a queue item's owning Thread before an item-only mutation.

    Queue update/delete/confirm routes do not carry ``thread_id`` in their URL.  Built-in app
    revocation therefore has to resolve it from Runtime PG before applying the shared MySQL
    application ACL; returning ``None`` preserves the existing not-found/conflict response.
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        value = (
            await session.execute(
                select(AgentThreadQueue.thread_id)
                .where(AgentThreadQueue.id == item_id, AgentThreadQueue.user_id == user_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    return str(value) if value else None


async def update_item(*, item_id: str, user_id: str, content: Optional[str] = None) -> bool:
    """仅编辑尚未认领的 queued 项；与 pop_next 竞争时由单条 UPDATE 原子裁决。"""
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as _update
    from app.runtime_models import AgentThreadQueue
    if content is None:
        return False
    async with factory() as session:
        result = await session.execute(
            _update(AgentThreadQueue)
            .where(AgentThreadQueue.id == item_id,
                   AgentThreadQueue.user_id == user_id,
                   AgentThreadQueue.status == "queued")
            .values(content=content)
        )
        await session.commit()
    return bool(getattr(result, "rowcount", 0))


async def delete_item(*, item_id: str, user_id: str) -> bool:
    """删除/移回输入框（前端删除后把内容放回输入框即「移回」）。"""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        result = await session.execute(
            delete(AgentThreadQueue).where(
                AgentThreadQueue.id == item_id,
                AgentThreadQueue.user_id == user_id,
                AgentThreadQueue.status == "queued")
        )
        await session.commit()
    return bool(getattr(result, "rowcount", 0))


async def reorder(*, thread_id: str, user_id: str, ordered_ids: List[str]) -> bool:
    """按前端拖动结果重排 queued 项；认领中的 dispatching 项不允许被移动。"""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                              {"k": f"queue:{thread_id}"})
        rows = (await session.execute(
            select(AgentThreadQueue)
            .where(AgentThreadQueue.thread_id == thread_id,
                   AgentThreadQueue.user_id == user_id,
                   AgentThreadQueue.status == "queued")
        )).scalars().all()
        by_id = {r.id: r for r in rows}
        # 全量匹配才更新：拖动期间若某项已被 pop 认领，旧快照必须失败并让前端刷新，
        # 不能把剩余项排成一半的新顺序。
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(by_id):
            return False
        pos = 1
        for iid in ordered_ids:
            if iid in by_id:
                by_id[iid].position = pos
                pos += 1
        await session.commit()
    return True


async def pop_next(*, thread_id: str, user_id: str) -> Optional[dict]:
    """认领队首待派发（租约制，**不删除**）——运行结束后派发下一条用。返回的项已置 dispatching
    并带一次性 lease_token；派发请求（/chat 携带 queue_item_id+lease_token）由**服务端**在
    Run 成功持久化后原子绑定并删除（bind_dispatch/finish_dispatch），前端不再提前 confirm。

    「逐条不并发 + 不丢消息」的服务端保障（P0，二次评审 + §10.6）：①事务级咨询锁按 thread
    串行化；②先回收超租约的 dispatching——**回收前查 dispatched_run_id**：对应 Run 已存在＝
    消息已进活动轮（Run 已建但 SSE 断开的窗口），直接删除绝不重派；Run 不存在＝派发中途失败，
    回收为 queued 重派（不丢）；③**只要存在未超租约的 dispatching 项就返回空**——堵住"标签 A
    已 pop 但尚未建 Run、标签 B 又 pop 到下一条"的网络窗口；④额外再看该 thread 是否已有活动 Run。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                              {"k": f"queue:{thread_id}"})
        # ② 回收超租约的 dispatching（发送失败/关标签留下的悬挂项）：
        #    已绑定 Run 且 Run 真实存在 → 删除（已派发成功，只是 finish 没来得及执行）；
        #    否则 → 回 queued 重派（position 不变，顺序保留）。
        await _reclaim_expired_dispatches(session, thread_id)
        # ③ 仍有未超租约的 dispatching → 一条正在派发中，不再交出第二条（关键：堵网络窗口）
        busy = (await session.execute(
            select(AgentThreadQueue.id).where(
                AgentThreadQueue.thread_id == thread_id,
                AgentThreadQueue.status == "dispatching").limit(1)
        )).scalar_one_or_none()
        if busy is not None:
            await session.commit()
            return None
        # ④ 该 thread 仍有活动 Run → 不派发（额外一层，覆盖"Run 已存在"的情形）
        try:
            from app.runtime_models import AgentRun
            active = (await session.execute(
                select(AgentRun.id).where(
                    AgentRun.thread_id == thread_id,
                    AgentRun.status.in_(_ACTIVE_RUN_STATUSES)).limit(1)
            )).scalar_one_or_none()
            if active is not None:
                await session.commit()
                return None
        except Exception:  # noqa: BLE001
            pass
        row = (await session.execute(
            select(AgentThreadQueue)
            .where(AgentThreadQueue.thread_id == thread_id, AgentThreadQueue.user_id == user_id,
                   AgentThreadQueue.status == "queued")
            .order_by(AgentThreadQueue.position.asc())
            .limit(1)
        )).scalar_one_or_none()
        if row is None:
            await session.commit()
            return None
        row.status = "dispatching"   # 认领：置 dispatching + 租约 + 一次性凭证，不删除
        row.lease_at = datetime.now()
        row.lease_token = uuid.uuid4().hex
        row.dispatched_run_id = None
        out = _row_to_dict(row)
        await session.commit()
    return out


async def bind_dispatch(*, item_id: str, lease_token: str, user_id: str, run_id: str) -> bool:
    """派发请求到达服务端时（建 Run **之前**）原子绑定目标 Run（§10.6 P0）。

    单条 UPDATE 做 CAS：必须 status=dispatching + user_id + lease_token 全匹配才绑定——
    旧租约（已被回收重派）/他人项/未认领项一律拒绝，调用方据此放弃本次派发（防重复）。
    绑定先于 create_run：此后任何环节断掉，回收逻辑都能靠 dispatched_run_id 判定
    「Run 已建→删除不重派 / Run 未建→回收重派」，端到端不丢不重。"""
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as _update
    from app.runtime_models import AgentThreadQueue
    if not (item_id and lease_token and run_id):
        return False
    async with factory() as session:
        result = await session.execute(
            _update(AgentThreadQueue)
            .where(AgentThreadQueue.id == item_id,
                   AgentThreadQueue.user_id == user_id,
                   AgentThreadQueue.status == "dispatching",
                   AgentThreadQueue.lease_token == lease_token,
                   # 只允许首绑或同 run_id 幂等重绑：同租约携带**不同** run_id 的第二次
                   # 请求（网络重试/重放）拒绝——否则覆盖首绑后两个 Run 都可能建成，
                   # 同一条消息进两轮（重复）
                   (AgentThreadQueue.dispatched_run_id.is_(None))
                   | (AgentThreadQueue.dispatched_run_id == run_id))
            .values(dispatched_run_id=run_id, lease_at=datetime.now()))
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def finish_dispatch(*, item_id: str, run_id: str) -> bool:
    """Run 成功持久化后确认删除队列项（服务端调用，取代前端提前 confirm）。
    以 dispatched_run_id 为准，防误删已被回收重派的新租约。失败无害——回收逻辑
    看到 Run 已存在同样会删除。"""
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import delete as _delete
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        result = await session.execute(
            _delete(AgentThreadQueue).where(
                AgentThreadQueue.id == item_id,
                AgentThreadQueue.dispatched_run_id == run_id))
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def release_dispatch(*, item_id: str, run_id: str) -> bool:
    """派发失败（如原子占位冲突，Run 未建成）时立即把项放回队列（不必等租约超时）。
    只释放仍绑定着本 run_id 的项，位置不变、顺序保留。"""
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as _update
    from app.runtime_models import AgentThreadQueue
    async with factory() as session:
        result = await session.execute(
            _update(AgentThreadQueue)
            .where(AgentThreadQueue.id == item_id,
                   AgentThreadQueue.status == "dispatching",
                   AgentThreadQueue.dispatched_run_id == run_id)
            .values(status="queued", lease_at=None, lease_token=None, dispatched_run_id=None))
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def confirm_dispatched(*, item_id: str, user_id: str, lease_token: str = "") -> bool:
    """（兼容入口，已不在主派发链路上）删除 dispatching 项。P0 加固：必须校验
    status=dispatching + lease_token，旧租约/未认领项的 confirm 一律拒绝——
    防止过期标签页删掉已被回收重派、正在新租约里的消息。"""
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import delete as _delete
    from app.runtime_models import AgentThreadQueue
    if not lease_token:
        return False
    async with factory() as session:
        result = await session.execute(
            _delete(AgentThreadQueue).where(
                AgentThreadQueue.id == item_id,
                AgentThreadQueue.user_id == user_id,
                AgentThreadQueue.status == "dispatching",
                AgentThreadQueue.lease_token == lease_token))
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def clear_thread(thread_id: str) -> None:
    """Thread 删除时清空其队列（尽力而为）。"""
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentThreadQueue
    try:
        async with factory() as session:
            await session.execute(
                delete(AgentThreadQueue).where(AgentThreadQueue.thread_id == thread_id))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("清空线程队列失败: %s", e)
