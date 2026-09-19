"""Task Run 状态机（§9.3/§16.3）。持久化一次任务运行的生命周期到 Runtime PG 域库。

普通状态查询对「Runtime 域库未配置」优雅降级；用户可见的持久事件是例外：未确认
commit 时必须拒绝发布，让 Run 进入既有的 waiting_system 恢复路径。
状态枚举逐字对齐 §9.3（RUN_STATUSES）。子智能体结果协议（§10.3）是另一层，不进此状态机。
"""
import asyncio
import logging
import uuid
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.runtime_db import runtime_session

logger = logging.getLogger(__name__)


def _runtime_created_at_ms(value: Optional[datetime]) -> int:
    """Runtime created_at values use UTC even when the DB returns a naive datetime."""
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _spawn_terminal_snapshot(run_id: str) -> None:
    """终态 CAS 落地后异步侧写任务快照（fire-and-forget，best-effort，失败静默）。

    放在状态机这一层：complete/fail/cancel 的全部直调调用点（含僵尸收敛、挂起过期清理）
    都在此收口，侧写不依赖调用方知不知道快照存在。
    """
    try:
        import asyncio

        from app.services.tasks import snapshot_service
        snapshot_service.spawn_snapshot_task(
            snapshot_service.save_terminal_snapshot(run_id)
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("terminal snapshot spawn failed run=%s: %s", run_id, e)


# 本进程实例标识（P1 多 worker Run 租约，2026-07-17）：每次进程启动生成一次。create_run 落
# owner_instance_id=INSTANCE_ID + heartbeat_at，consume_resume_token 恢复时原子接管归本进程，
# 心跳循环（main.py lifespan → heartbeat_owned_runs）周期续约。僵尸判定按「owner==本进程→
# 本地任务表；外来/为空→租约过期（is_run_lease_stale）」分流，见 harness_orchestrator._is_zombie_run。
INSTANCE_ID = uuid.uuid4().hex

# 同一 Thread 同时只允许一个活动 Run（R0 不变量）。这四个状态与 get_active_run 的活动集
# 逐字对齐，也与 agent_runs 上的部分唯一索引 uq_agent_runs_active_thread 的 WHERE 一致；
# 三处任何一处改动都要同步另两处，否则原子占位与预检判定会错位。
ACTIVE_RUN_STATUSES = (
    "created", "routing", "running", "waiting_user", "waiting_confirmation", "waiting_system",
)

# 终态（P1-16）：一旦落到这三个状态之一即不可再被覆盖——尤其是不能被晚到的「停止」请求
# 拍成 cancelled，那会把已经正常收尾的结果错误地标红。
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
# HITL 挂起态(等用户/等确认/等系统):合法等待,不算僵尸、不派发队列、靠 resume 续接
WAITING_RUN_STATUSES = ("waiting_user", "waiting_confirmation", "waiting_system")


class ActiveRunConflict(Exception):
    """create_run 命中活动态部分唯一索引：该 Thread 已有活动 Run，不允许并发第二个。"""


class DuplicateRequestConflict(Exception):
    """create_run 命中 (user_id, client_request_id) 幂等唯一索引（N-02）：同一次客户端
    发送的并发重复请求——赢家 Run 已建成，本次应静默退出，客户端凭 by-request 查询
    发现赢家并订阅续接。携带既有 Run 信息供调用方直接使用。"""

    def __init__(self, run_id: str, thread_id: str):
        super().__init__(f"duplicate client request; existing run={run_id}")
        self.run_id = run_id
        self.thread_id = thread_id


class RunLookupUnavailable(Exception):
    """Runtime 状态库不可用，不能把「未知」伪装成「幂等键不存在」。"""


class EventPersistenceError(RuntimeError):
    """A user-visible durable event was not confirmed committed and must not be published."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _parse_heartbeat(value: Any) -> Optional[datetime]:
    """heartbeat_at 兼容解析：DB 行是 datetime，get_run/get_active_run 回传是 isoformat 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def is_run_lease_stale(run: Optional[Dict[str, Any]]) -> bool:
    """Run 租约过期判定（P1 多 worker 僵尸口径，2026-07-17）。

    heartbeat_at 为空（含租约上线前的 legacy 存量行——它们必然无心跳，可安全清）或早于
    now-RUN_LEASE_TTL_SECONDS 即视为租约过期。时钟基准与心跳写入同为 utcnow（同 DB 的
    sweep_expired_waiting 口径）。仅表达租约事实，不含「owner 是否本进程」的分流——那在
    harness_orchestrator._is_zombie_run。
    """
    hb = _parse_heartbeat((run or {}).get("heartbeat_at"))
    if hb is None:
        return True
    return hb < datetime.utcnow() - timedelta(seconds=int(settings.RUN_LEASE_TTL_SECONDS))


def is_run_lease_zombie(run: Optional[Dict[str, Any]]) -> bool:
    """Active-scan zombie: heartbeat older than TTL×2, so a slow-but-alive worker is not harvested.

    Missing heartbeat is not enough: a just-created Run has not heartbeated yet. Fall back to
    created_at, and refuse to harvest when both timestamps are unknown.
    """
    ttl = max(5, int(settings.RUN_LEASE_TTL_SECONDS) * 2)
    cutoff = datetime.utcnow() - timedelta(seconds=ttl)
    hb = _parse_heartbeat((run or {}).get("heartbeat_at"))
    if hb is not None:
        return hb < cutoff
    created = _parse_heartbeat((run or {}).get("created_at"))
    if created is None:
        return False
    return created < cutoff


async def scan_recoverable_zombies() -> List[Dict[str, str]]:
    """Periodic worker scan for running Runs whose lease is TTL×2 stale.

    Marks them recoverable (waiting_system + task_recovery) instead of failing, so a
    refresh can resume the same Run. Logs run_zombie_detected for each hit.
    """
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentRun
    found: List[Dict[str, str]] = []
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentRun).where(AgentRun.status.in_(("running", "routing", "created")))
                )
            ).scalars().all()
            candidates = []
            for run in rows:
                payload = {
                    "heartbeat_at": getattr(run, "heartbeat_at", None),
                    "owner_instance_id": getattr(run, "owner_instance_id", None),
                    "created_at": getattr(run, "created_at", None),
                }
                if not is_run_lease_zombie(payload):
                    continue
                candidates.append({
                    "id": str(run.id),
                    "thread_id": str(run.thread_id or ""),
                    "user_id": str(run.user_id or ""),
                    "owner_instance_id": str(run.owner_instance_id or ""),
                    "heartbeat_at": run.heartbeat_at,
                })
        for cand in candidates:
            recovered = await recover_run_after_fault(
                cand["id"],
                reason="运行进程心跳超时，任务待恢复",
                expected_owner_instance_id=cand.get("owner_instance_id") or None,
                expected_heartbeat_at=cand.get("heartbeat_at"),
            )
            if not recovered:
                continue
            logger.warning(
                "run_zombie_detected run=%s thread=%s",
                cand["id"], cand["thread_id"],
            )
            found.append({"id": cand["id"], "thread_id": cand["thread_id"], "user_id": cand["user_id"]})
        return found
    except Exception as e:  # noqa: BLE001
        logger.warning("scan_recoverable_zombies 失败: %s", e)
        return []


async def heartbeat_owned_runs() -> int:
    """本进程持有的活动执行态 Run 续租（P1 多 worker）：main.py lifespan 心跳循环周期调用。

    只续 running/routing/created——waiting_*（HITL 挂起）本就无执行任务、靠 resume_token
    恢复，不参与租约（挂起态由 sweep_expired_waiting 按小时级 TTL 治理）；恢复时
    consume_resume_token 会原子接管 owner+heartbeat。返回续约行数。
    """
    factory = runtime_session()
    if factory is None:
        return 0
    from sqlalchemy import update as sa_update

    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            result = await session.execute(
                sa_update(AgentRun)
                .where(AgentRun.owner_instance_id == INSTANCE_ID)
                .where(AgentRun.status.in_(("running", "routing", "created")))
                .values(heartbeat_at=datetime.utcnow())
            )
            await session.commit()
            return int(result.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("heartbeat_owned_runs 失败: %s", e)
        return 0


async def sweep_expired_waiting(ttl_hours: int) -> List[Dict[str, str]]:
    """Observe stale user-waiting Runs without converting them to a terminal outcome.

    ``waiting_system/task_recovery`` 不属于这个清理范围；外部故障的等待时长不能单独
    产生任务终态，恢复 Job 负责它的退避与再次执行。用户没有提供输入或确认也不是
    墙钟意义上的失败；它必须保持 waiting_user/waiting_confirmation，直到用户恢复或
    明确取消。``ttl_hours`` 保留在接口中仅为兼容已有定时任务，当前不参与状态迁移。

    对应 LangGraph checkpoint 行由单独的运维清理策略治理，不在这里盲删或伪造终态。
    返回空清单，保留旧调用方的返回类型而不产生 completed/failed/partial。
    """
    factory = runtime_session()
    if factory is None:
        return []
    return []


async def mark_run_recoverable(
    run_id: str,
    *,
    reason: str = "服务重启，任务待恢复",
    expected_owner_instance_id: Optional[str] = None,
    expected_heartbeat_at: Any = None,
    interactive_type: str = "task_recovery",
) -> Optional[str]:
    """进程死亡的任务模式 Run 收敛为「可恢复挂起」（N-08 进程级恢复）。

    CAS：仅当 Run 仍处活动执行态（running/routing/created）时，置 waiting_system、签发新
    resume_token、state.orchestration 切换为 task_recovery（保留原 kb/web/skills 上下文供
    恢复轮重建工具），并释放租约（owner/heartbeat 清空——恢复时 consume_resume_token 会
    原子接管）。前端拿到 waiting_system + interactive_type=task_recovery 后自动在**同一
    run_id/graph_id/事件 sequence** 上续跑，已成功节点冻结复用，不重头规划。

    返回新令牌；CAS 未命中（已被并发收敛/已终态）或库降级返回 None，调用方按原路径处理。
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    token = uuid.uuid4().hex
    try:
        async with factory() as session:
            row = await session.get(AgentRun, run_id, with_for_update=True)
            if not row or row.status not in ("running", "routing", "created"):
                return None
            # 僵尸判断与接管之间可能刚好有旧 worker 续租。行锁内复核调用方看到的 owner/hb，
            # 并拒绝接管租约仍新鲜的外来 Run，避免双执行。
            if expected_owner_instance_id is not None:
                if str(row.owner_instance_id or "") != str(expected_owner_instance_id or ""):
                    return None
                if _parse_heartbeat(row.heartbeat_at) != _parse_heartbeat(expected_heartbeat_at):
                    return None
            if row.owner_instance_id != INSTANCE_ID and not is_run_lease_stale(
                    {"heartbeat_at": row.heartbeat_at}):
                return None
            state = dict(row.state or {})
            orch = dict(state.get("orchestration") or {})
            prior_mode = str(orch.get("mode") or "")
            # 不重建一个有损子集：图片附件、文件说明、计划卡与候选等都属于恢复语义。
            orch.update({
                "mode": "task_recovery",
                "prior_mode": prior_mode,
                "goal": str(orch.get("goal") or row.goal or ""),
                "phases": True,
                "recovery_reason": reason,
            })
            state["orchestration"] = orch
            state["interactive_type"] = interactive_type
            state["pause_requested"] = False
            row.state = state
            row.status = "waiting_system"
            row.resume_token = token
            row.error = None
            row.owner_instance_id = None
            row.heartbeat_at = None
            await session.commit()
            return token
    except Exception as e:  # noqa: BLE001
        logger.warning("mark_run_recoverable 失败: %s", e)
        return None


async def recover_run_after_fault(
    run_id: str,
    *,
    reason: str,
    expected_owner_instance_id: Optional[str] = None,
    expected_heartbeat_at: Any = None,
    backoff_seconds: float = 1.0,
) -> bool:
    """Save a fault checkpoint and schedule the same Run for automatic recovery.

    ``waiting_system`` is used as the externally compatible status, while the internal
    ``task_recovery`` marker makes it distinguishable from a user/HITL wait.  The checkpoint is
    committed before the Job is woken so a new Worker never starts from an unrecorded segment.
    """
    try:
        from app.services.agent_harness import run_store

        status_before = await get_run_status(run_id)
        already_waiting = status_before == "waiting_system"
        checkpoint = await run_store.record_recovery_checkpoint(
            run_id,
            recovery_reason=reason,
        )
        if checkpoint is None:
            logger.error("Run recovery checkpoint failed run=%s", run_id)
            return False
        token = await mark_run_recoverable(
            run_id,
            reason=reason,
            expected_owner_instance_id=expected_owner_instance_id,
            expected_heartbeat_at=expected_heartbeat_at,
        )
        status_after = await get_run_status(run_id)
        already_waiting = already_waiting or status_after == "waiting_system"
        if not token and not already_waiting:
            return False
        available_at = datetime.utcnow() + timedelta(
            seconds=max(0.0, min(float(backoff_seconds or 0), 60.0))
        )
        queued = await run_store.enqueue_job(
            run_id,
            wake_reason="recovery",
            available_at=available_at,
        )
        if not queued:
            logger.error("Run recovery Job enqueue failed run=%s", run_id)
            return False
        await append_run_event(
            run_id,
            "run.recovery.scheduled",
            {
                "reason": str(reason or "")[:300],
                "backoff_seconds": max(0.0, min(float(backoff_seconds or 0), 60.0)),
                "execution_control": run_store.get_execution_control(
                    (checkpoint.get("state") or {})
                ),
            },
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Run recovery scheduling failed run=%s", run_id)
        return False


async def _fail_orphan_run(
    run_id: str,
    *,
    expected_owner_instance_id: Optional[str] = None,
    expected_heartbeat_at: Any = None,
) -> Optional[str]:
    """Compatibility wrapper: recover one orphan Run instead of terminally failing it."""
    recovered = await recover_run_after_fault(
        run_id,
        reason="服务重启，运行段自动恢复",
        expected_owner_instance_id=expected_owner_instance_id,
        expected_heartbeat_at=expected_heartbeat_at,
    )
    if not recovered:
        return None
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            row = await session.get(AgentRun, run_id)
            return str(getattr(row, "thread_id", "") or "") if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("_fail_orphan_run recovery lookup failed: %s", e)
        return None


async def fail_run_if_lease_matches(
    run_id: str,
    *,
    owner_instance_id: Any,
    heartbeat_at: Any,
    error: str,
) -> bool:
    """Compatibility wrapper: CAS the observed lease into automatic recovery."""
    return await recover_run_after_fault(
        run_id,
        reason=error or "运行段自动恢复",
        expected_owner_instance_id=str(owner_instance_id or "") or None,
        expected_heartbeat_at=heartbeat_at,
    )


async def reconcile_orphan_running() -> int:
    """进程启动时接管**租约已过期**、仍停留在活动执行态的 Run。

    这些 Run 的执行协程（_pump_background_run 任务）随上一个进程消亡——重启 / uvicorn
    --reload / 崩溃都会留下 PG 里 status=running 的僵尸行。它们不能被旧的清理器直接
    标成 failed，而要保存同一 Run 的恢复段并重新排队。

    waiting_*（HITL 挂起）不在此列。仅在 lifespan 启动、开始接请求之前调用一次。
    """
    return len(await reconcile_orphan_running_details())


async def reconcile_orphan_running_details() -> List[Dict[str, str]]:
    """接管孤儿 Run，并返回已排队恢复的 ``{id, thread_id}`` 清单。

    返回清单仅供启动统计；``run_reconcile_service`` 会跳过仍处于
    ``waiting_system/task_recovery`` 的 Run，不把自动恢复误写成中断终态消息。

    租约口径（P1 多 worker，2026-07-17）：只清 heartbeat_at 为空（legacy 存量行——租约上线
    前的行必然无心跳，可安全清）或早于 now-RUN_LEASE_TTL_SECONDS 的活动态行；租约仍新鲜的
    行属于其他 worker 正在真实执行的 Run，绝不动——此前无条件全清会让多副本部署「扩容即
    互杀」（新起的 worker 把别人正在跑的 Run 全标 failed）。
    tradeoff：单 worker 崩溃后立即重启时，自己上一世代的 Run 心跳可能尚未过期，最坏要等一个
    RUN_LEASE_TTL_SECONDS（默认 45s）才由本函数的下一次机会/惰性僵尸判定（_is_zombie_run 的
    外来分支）收敛——用启动瞬间的短暂延迟换多副本安全。
    """
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentRun).where(AgentRun.status.in_(("running", "routing", "created")))
                )
            ).scalars().all()
            details = []
            orphaned: List[Dict[str, Any]] = []
            for run in rows:
                if not is_run_lease_stale({"heartbeat_at": getattr(run, "heartbeat_at", None)}):
                    continue  # 租约新鲜＝别的 worker 正在跑，禁止收割
                cand = {
                    "id": run.id,
                    "thread_id": run.thread_id or "",
                    "owner_instance_id": run.owner_instance_id or "",
                    "heartbeat_at": run.heartbeat_at,
                }
                orphaned.append(cand)
        # chat 孤儿也一律按租约快照 CAS 接管：本函数上面只是一次 SELECT 快照，
        # 快照与写回之间原 owner 完全可能把 Run 正常落成 completed，或被别的 worker 接管。
        # recover_run_after_fault 内部重新核对 owner/heartbeat，未命中即放弃，不生成假终态。
        for cand in orphaned:
            recovered = await recover_run_after_fault(
                cand["id"],
                reason="服务重启，运行段自动恢复",
                expected_owner_instance_id=cand.get("owner_instance_id"),
                expected_heartbeat_at=cand.get("heartbeat_at"),
            )
            if recovered:
                details.append({"id": str(cand["id"]), "thread_id": str(cand["thread_id"] or "")})
        return details
    except Exception as e:  # noqa: BLE001
        logger.warning("孤儿运行态清理失败: %s", e)
        return []


async def create_run(
    *,
    run_id: str,
    thread_id: str,
    user_id: str,
    kind: str = "chat",
    model: Optional[str] = None,
    subagent_id: Optional[str] = None,
    goal: Optional[str] = None,
    agent_mode: str = "standard",
    client_request_id: Optional[str] = None,
    status: str = "running",
    claim_owner: bool = True,
) -> Optional[str]:
    """创建活动 Run（status=running）。同 Thread 已有活动 Run 时，agent_runs 上的部分唯一
    索引会让本次 INSERT 冲突 → 抛 ActiveRunConflict（调用方据此拒绝并发第二个 Run，即 R0
    的原子占位，堵住 get_active_run 预检与本 INSERT 之间的 TOCTOU 竞态）。

    client_request_id（N-02 可靠握手）：命中 (user_id, client_request_id) 幂等唯一索引时
    抛 DuplicateRequestConflict（携带赢家 run_id/thread_id）——同一次发送的并发重复请求
    不建第二个 Run。两种 IntegrityError 靠反查区分：先查同键既有 Run，查到即重复请求，
    否则按活动态冲突处理（安全默认）。

    其他失败（PG 未配置/连接异常）仍降级返回 None——「不阻断对话」优先，此时退回仅预检保护。
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            session.add(AgentRun(
                id=run_id, thread_id=thread_id, user_id=user_id, kind=kind,
                model=model, subagent_id=subagent_id, goal=goal, status=status,
                agent_mode=agent_mode,
                client_request_id=client_request_id or None,
                # 租约起点（P1 多 worker）：owner=本进程，heartbeat 立即生效；
                # 后续由 heartbeat_owned_runs 周期续约
                owner_instance_id=INSTANCE_ID if claim_owner else None,
                heartbeat_at=datetime.utcnow() if claim_owner else None,
            ))
            await session.commit()
        return run_id
    except IntegrityError:
        # 唯一冲突分诊（N-02）：先看是不是幂等键撞了赢家——查得到同键 Run 即重复请求；
        # 反查失败/查不到则按活动态冲突处理（上层拒绝也是安全默认）。
        if client_request_id:
            try:
                existing = await get_run_by_client_request(user_id, client_request_id)
            except Exception:  # noqa: BLE001
                existing = None
            if existing and str(existing.get("id")) != run_id:
                logger.info(
                    "create_run 命中幂等键（client_request_id=%s，赢家 run=%s）",
                    client_request_id, existing.get("id"))
                raise DuplicateRequestConflict(
                    str(existing.get("id")), str(existing.get("thread_id") or ""))
        # 部分唯一索引冲突＝已有活动 Run。区别于下面的 best-effort 降级：这是必须让上层
        # 感知的业务冲突，抛出而非吞掉（若冲突另有原因如主键重复，上层拒绝也是安全默认）。
        logger.info("create_run 命中活动态唯一约束（thread=%s 已有活动 Run）", thread_id)
        raise ActiveRunConflict(thread_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("create_run 失败（不影响对话）: %s", e)
        return None


def run_execution_is_claimable(
    *,
    owner_instance_id: Optional[str],
    heartbeat_at: Optional[datetime],
    stale_before: datetime,
) -> bool:
    """True when this process may take the Run pump. Fresh heartbeat (including self) is refused."""
    if not str(owner_instance_id or "").strip():
        return True
    if heartbeat_at is None:
        return True
    return heartbeat_at <= stale_before


async def claim_run_execution(run_id: str) -> bool:
    """Worker 领取持久 Job 后接管 Run 执行租约。

    Job 表的 ``FOR UPDATE SKIP LOCKED`` 保证同一时刻只有一个领取者；这里再用 Run
    状态和过期租约作第二道闸，防止错误配置下两个 Worker 同时执行同一副作用任务。
    """
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as sa_update
    from app.runtime_models import AgentRun
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=max(5, settings.RUN_LEASE_TTL_SECONDS))
    try:
        async with factory() as session:
            # Lock the Run row for the complete read/check/update sequence.  This is especially
            # important for recovery Jobs: two workers must not both observe the same
            # waiting_system/task_recovery segment and start duplicate side effects.
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if run is None:
                return False
            status = str(run.status or "")
            state = dict(run.state or {}) if isinstance(run.state, dict) else {}
            is_task_recovery = (
                status == "waiting_system"
                and str(state.get("interactive_type") or "") == "task_recovery"
            )
            if status not in ("created", "running", "routing") and not is_task_recovery:
                return False
            if not run_execution_is_claimable(
                owner_instance_id=getattr(run, "owner_instance_id", None),
                heartbeat_at=getattr(run, "heartbeat_at", None),
                stale_before=stale_before,
            ):
                return False
            result = await session.execute(
                sa_update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status.in_(
                        ("created", "running", "routing", "waiting_system")
                    ),
                    or_(
                        AgentRun.owner_instance_id.is_(None),
                        AgentRun.heartbeat_at.is_(None),
                        AgentRun.heartbeat_at <= stale_before,
                    ),
                )
                .values(
                    status="running",
                    owner_instance_id=INSTANCE_ID,
                    heartbeat_at=now,
                    started_at=func.coalesce(AgentRun.started_at, now),
                    # A recovery segment is a new execution lease.  Do not leave the
                    # previous input-intake close marker on the Run, otherwise the resumed
                    # segment can be treated as permanently closed by downstream gates.
                    input_intake_closed_at=None,
                )
            )
            await session.commit()
            return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.warning("claim_run_execution 失败 run=%s: %s", run_id, exc)
        return False


async def get_run_by_client_request(
    user_id: str, client_request_id: str,
) -> Optional[Dict[str, Any]]:
    """按幂等键反查 Run（N-02 可靠握手）：首帧丢失后客户端凭 client_request_id 发现
    已建 Run（订阅续接而不是重发第二轮）；create_run 的唯一冲突分诊也用它。"""
    if not client_request_id:
        return None
    factory = runtime_session()
    if factory is None:
        # 保持本地无 Runtime 配置时的既有降级语义；已配置但查询失败才是 503 未知态。
        return None
    from sqlalchemy import select
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = (await session.execute(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .where(AgentRun.client_request_id == client_request_id)
                .limit(1)
            )).scalar_one_or_none()
            if not run:
                return None
            return {"id": run.id, "thread_id": run.thread_id, "status": run.status,
                    "agent_mode": getattr(run, "agent_mode", None) or "standard"}
    except Exception as e:  # noqa: BLE001
        logger.warning("get_run_by_client_request 失败: %s", e)
        raise RunLookupUnavailable("Runtime 状态库暂时不可用") from e


async def _set_status(run_id: str, status: str, *, error: Optional[str] = None,
                      resume_token: Optional[str] = None, completed: bool = False,
                      skip_if_terminal: bool = False, outcome: Optional[str] = None) -> bool:
    """全部状态迁移走 DB 级 CAS（P0 2026-07-17）：一条 `UPDATE ... WHERE status NOT IN 终态`，
    终态一旦落定不可被任何后续迁移覆盖。此前只有 cancel_run 带 skip_if_terminal 保护，
    complete_run/fail_run/set_waiting/set_running 都能把已 completed/cancelled 的 Run 拍成
    别的状态（晚到的失败收尾覆盖正常结果、双泵竞态互拍）。返回 True=本次迁移真实生效；
    False=未命中（已是终态 / Run 不存在 / Runtime 不可用）——调用方据此决定是否发布对应
    终态事件（区分「冲突」与「不可用」用 finalize_run）。skip_if_terminal 参数保留签名
    兼容，语义已被全局 CAS 覆盖。
    """
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as sa_update

    from app.runtime_models import AgentRun, AgentRunInput
    values: Dict[str, Any] = {"status": status}
    if status == "running":
        # 恢复/接管后重新开放 Run 内指令入口；上一执行段若曾在终态候选处封口但随后
        # 转入合法恢复，不能永久把 steering 挡在外面。
        values["input_intake_closed_at"] = None
    if error is not None:
        values["error"] = error
    if resume_token is not None:
        values["resume_token"] = resume_token
    if outcome is not None:
        values["outcome"] = outcome  # 终态第二层（success/partial，设计稿 §2）
    if completed:
        values["completed_at"] = datetime.now()
        values["resume_token"] = None
        if status == "completed":
            values["error"] = None
    try:
        async with factory() as session:
            result = await session.execute(
                sa_update(AgentRun)
                .where(AgentRun.id == run_id)
                .where(AgentRun.status.notin_(TERMINAL_RUN_STATUSES))
                .values(**values)
            )
            applied = bool(result.rowcount)
            if applied and status in TERMINAL_RUN_STATUSES:
                # Run 与未消费指令同属 Runtime PG：在同一事务原子收敛，覆盖所有直接
                # complete_run/fail_run/cancel_run 调用点。此前仅 finalize_run 补偿，
                # 用户停止与异常分支直调底层函数会留下永久 queued/applying。
                reason = (
                    "任务已完成，追加要求未能在终态前应用"
                    if status == "completed"
                    else "任务已停止，追加要求未能应用"
                    if status == "cancelled"
                    else "任务执行失败，追加要求未能应用"
                )
                await session.execute(
                    sa_update(AgentRunInput)
                    .where(
                        AgentRunInput.run_id == run_id,
                        AgentRunInput.status.in_(("queued", "applying")),
                    )
                    .values(
                        status="rejected",
                        applying_at=None,
                        applied_scope="terminal",
                        failure_reason=reason,
                    )
                )
            await session.commit()
            if applied and status in TERMINAL_RUN_STATUSES:
                _spawn_terminal_snapshot(run_id)
                try:
                    from app.services.agent_harness.thread_projection_store import (
                        thread_projection_store,
                    )

                    await thread_projection_store.record_root_run_terminal(
                        run_id=run_id,
                        succeeded=status == "completed",
                    )
                except Exception:  # noqa: BLE001 - rollout accounting is fail-open
                    logger.warning(
                        "thread projection terminal accounting failed run=%s status=%s",
                        run_id,
                        status,
                        exc_info=True,
                    )
            return applied
    except Exception as e:  # noqa: BLE001
        logger.warning("set_status(%s) 失败: %s", status, e)
        return False


async def get_run_status(run_id: str) -> Optional[str]:
    """无属主校验的 Run 状态直读（内部信任路径：终态发布判定/锚点补偿用）。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            return run.status if run else None
    except Exception as e:  # noqa: BLE001
        logger.warning("get_run_status 失败: %s", e)
        return None


async def request_run_cancel(run_id: str, user_id: str) -> Optional[str]:
    """跨进程停止握手的请求端。

    只把 cancel_requested_at 写入 RunState，不提前把 Run 改成 cancelled。
    Worker 看到请求后取消真实执行协程，pump 在完成源生成器关闭、
    部分正文落库与沙箱回收后再提交 cancelled 终态。

    返回 requested / 已有终态名；None 表示 Run 不存在、不属于该用户
    或 Runtime 不可用。
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if not run or str(run.user_id) != str(user_id):
                return None
            status = str(run.status or "")
            if status in TERMINAL_RUN_STATUSES:
                return status
            merged = dict(run.state or {})
            merged["cancel_requested_at"] = datetime.utcnow().isoformat()
            run.state = merged
            run.state_version = int(getattr(run, "state_version", 0) or 0) + 1
            await session.commit()
            return "requested"
    except Exception as exc:  # noqa: BLE001
        logger.warning("request_run_cancel 失败 run=%s: %s", run_id, exc)
        return None


async def is_run_cancel_requested(run_id: str) -> bool:
    """Worker 内部轮询：只读持久化停止请求，不改状态。"""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            return bool(run and (run.state or {}).get("cancel_requested_at"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("is_run_cancel_requested 失败 run=%s: %s", run_id, exc)
        return False


async def finalize_run(run_id: str, status: str, *, error: str = "",
                       outcome: Optional[str] = None) -> bool:
    """终态收尾 + 事件发布判定（P1 CAS）：只有持久化赢家才允许发布终态 SSE。

    ``False`` 同时覆盖 CAS 未命中、Runtime 不可用、Run 状态未知/仍为非终态和数据库
    异常。未知不能被解释成“本次可以收尾”：没有 Runtime 事实就没有资格发布
    ``run.completed/run.failed/run.partial``。可恢复的情况转入同一 Run 的
    ``waiting_system``/recovery；已有终态或显式用户等待则交给其现有事实收敛。
    """
    persistence_error = ""
    try:
        if status == "completed":
            applied = await complete_run(run_id, outcome=outcome)
        elif status == "cancelled":
            applied = await cancel_run(run_id)
        else:
            applied = await fail_run(run_id, error or "任务执行失败")
    except Exception as exc:  # noqa: BLE001 - 终态持久化故障必须进入恢复层
        applied = False
        persistence_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "终态 CAS 调用异常 run=%s status=%s: %s",
            run_id, status, exc,
        )

    if applied:
        return True

    try:
        current = await get_run_status(run_id)
    except Exception as exc:  # pragma: no cover - get_run_status 当前已 fail-closed
        current = None
        persistence_error = persistence_error or f"{type(exc).__name__}: {exc}"
        logger.warning("读取终态状态异常 run=%s: %s", run_id, exc)

    if current in TERMINAL_RUN_STATUSES:
        # 另一个执行者已经赢得 CAS；本调用没有发布权。
        return False
    if current in ("waiting_user", "waiting_confirmation"):
        # 显式等待是合法控制面事实，不把它改写成系统故障。
        return False

    reason = f"terminal_persistence_{status}"
    if current:
        reason += f"_status_{current}"
    if persistence_error:
        reason += f"_{persistence_error}"
    try:
        recovered = await recover_run_after_fault(
            run_id,
            reason=reason[:300],
            backoff_seconds=1.0,
        )
        if not recovered:
            logger.warning(
                "终态未持久化且恢复未排队 run=%s status=%s current=%s",
                run_id, status, current,
            )
    except Exception:  # noqa: BLE001 - 不能因恢复层异常重新制造终态
        logger.exception("终态持久化故障的恢复提交异常 run=%s", run_id)
    return False


async def complete_run(run_id: str, outcome: Optional[str] = None) -> bool:
    # outcome（设计稿 §2）：success=required 全完成 / partial=核心完成但有非关键缺口。
    # 普通对话（无 graph 交付审查）传 None——语义即"正常完成"，不承载缺口信息。
    return await _set_status(run_id, "completed", completed=True, outcome=outcome)


async def fail_run(run_id: str, error: str) -> bool:
    return await _set_status(run_id, "failed", error=error[:2000], completed=True)


async def cancel_run(run_id: str) -> bool:
    return await _set_status(run_id, "cancelled", completed=True, skip_if_terminal=True)


async def set_running(run_id: str) -> bool:
    return await _set_status(run_id, "running")


async def set_waiting(run_id: str, status: str, resume_token: Optional[str] = None) -> bool:
    """置为等待态（waiting_user/waiting_confirmation/waiting_system）。"""
    if status not in ("waiting_user", "waiting_confirmation", "waiting_system"):
        status = "waiting_user"
    return await _set_status(run_id, status, resume_token=resume_token)


async def consume_resume_token(run_id: str, client_resume_id: Optional[str]) -> bool:
    """原子消费一次性恢复令牌并置 running（CAS，堵并发重复 resume 的双执行窗口）。

    仅当 Run 仍处 waiting_* 且令牌匹配（客户端未传令牌时不校验，兼容旧前端）时，
    一条 UPDATE 同时置 status=running、清空 resume_token；并发的第二次消费 WHERE
    不再命中（状态已非 waiting_* / 令牌已空），返回 False。
    Runtime 库不可用返回 True 放行——该场景下 get_run 已返回 None，调用方先行拒绝。
    """
    factory = runtime_session()
    if factory is None:
        return True
    from sqlalchemy import update
    from app.runtime_models import AgentRun
    conditions = [
        AgentRun.id == run_id,
        AgentRun.status.in_(("waiting_user", "waiting_confirmation", "waiting_system")),
    ]
    if client_resume_id:
        conditions.append(AgentRun.resume_token == client_resume_id)
    try:
        async with factory() as session:
            result = await session.execute(
                # 同一条 CAS 顺带接管租约（P1 多 worker）：resume 后执行任务在本进程，
                # owner/heartbeat 必须归本进程——否则原 owner 已死时租约到期，别的 worker
                # 会把正在恢复执行的 Run 判僵尸收割。
                update(AgentRun).where(*conditions).values(
                    status="running", resume_token=None,
                    owner_instance_id=INSTANCE_ID, heartbeat_at=datetime.utcnow(),
                    input_intake_closed_at=None,
                )
            )
            await session.commit()
            return bool(result.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.warning("consume_resume_token 失败: %s", e)
        return False


async def set_clarifying(run_id: str) -> bool:
    """置 waiting_clarification（§9.3）。

    注意：**不**纳入 get_active_run 的 R0 阻塞集——该态是本轮的等待终点＋审计记录，
    不拦截下一条消息。
    """
    return await _set_status(run_id, "waiting_clarification", completed=True)


def _active_run_payload(row: Any) -> Dict[str, Any]:
    """Serialize the one foreground Run shape shared by single and batch lookup."""
    state = row.state or {}
    return {
        "id": row.id,
        "status": row.status,
        "kind": row.kind,
        "model": str(row.model or "") or None,
        "agent_mode": getattr(row, "agent_mode", None) or "standard",
        "phase": state.get("phase"),
        "capability_scope": state.get("capability_scope"),
        "approved_plan_version": state.get("approved_plan_version"),
        "goal_contract": (
            state.get("goal_contract")
            if isinstance(state.get("goal_contract"), dict)
            else None
        ),
        "interactive_type": state.get("interactive_type"),
        "resume_token": row.resume_token,
        # 租约字段（P1 多 worker）：run_hub._is_zombie_run 据此分流僵尸判定。
        "owner_instance_id": getattr(row, "owner_instance_id", None),
        "heartbeat_at": (
            row.heartbeat_at.isoformat()
            if getattr(row, "heartbeat_at", None)
            else None
        ),
        "started_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "state": {
            key: state.get(key)
            for key in (
                "goal_contract",
                "approved_plan_version",
                "capability_scope",
                "phase",
                "agent_mode",
            )
            if key in state
        },
    }


async def get_active_runs(thread_ids: Sequence[str], user_id: str) -> Dict[str, Dict[str, Any]]:
    """Read foreground Runs for a history page in one Runtime query.

    The R0 partial unique index permits at most one returned status per thread.  Ordering still
    makes this fail-safe for legacy rows that predate that invariant.  Callers that need zombie
    recovery remain responsible for it; this helper is deliberately read-only.
    """
    normalized_ids = list(dict.fromkeys(
        str(thread_id).strip() for thread_id in thread_ids if str(thread_id).strip()
    ))
    if not normalized_ids:
        return {}
    factory = runtime_session()
    if factory is None:
        return {}
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            rows = (await session.execute(
                select(AgentRun)
                .where(AgentRun.thread_id.in_(normalized_ids))
                .where(AgentRun.user_id == user_id)
                .where(AgentRun.status.in_(["running", "waiting_user", "waiting_confirmation", "waiting_system"]))
                .order_by(AgentRun.thread_id.asc(), AgentRun.created_at.desc())
            )).scalars().all()
            active_by_thread: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                thread_id = str(row.thread_id or "")
                if thread_id and thread_id not in active_by_thread:
                    active_by_thread[thread_id] = _active_run_payload(row)
            return active_by_thread
    except Exception as e:  # noqa: BLE001
        logger.warning("get_active_runs 失败: %s", e)
        return {}


async def get_active_run(thread_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """R0：取该 Thread 的前台活动 Run（running / waiting_*），供恢复优先于重新路由。"""
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return None
    return (await get_active_runs([normalized_thread_id], user_id)).get(normalized_thread_id)


async def record_steps(run_id: str, steps: List[Dict[str, Any]]) -> None:
    """批量落 Run 执行步骤（§16.5，开发计划 Phase 3 可观测）。

    step: {type: message/tool/interrupt, content?, meta?, seq?}。
    best-effort：由调用方 fire-and-forget，不阻塞对话流。
    """
    factory = runtime_session()
    if factory is None or not steps:
        return
    from app.runtime_models import AgentStep
    try:
        async with factory() as session:
            for i, st in enumerate(steps):
                session.add(AgentStep(
                    id=_new_id(), run_id=run_id, seq=int(st.get("seq", i)),
                    type=str(st.get("type") or "tool"),
                    content=st.get("content"), meta=st.get("meta"),
                ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("record_steps 失败: %s", e)


async def save_run_state(run_id: str, state: Dict[str, Any]) -> bool:
    """合并写入 Run 的结构化 state（如 HITL 的 resume 上下文）。

    AgentRun.state 同时被 retired runtime CAS checkpoint 写入。这里必须先锁行、
    再基于最新 state 合并，并与 CAS 共用 state_version；否则旧的
    read-modify-write 会在并发 checkpoint 之后把 phase/pending_input/plan_id 等
    新字段整体覆盖回旧快照，而版本号还虚假地保持不变。
    """
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if not run:
                return False
            merged = dict(run.state or {})
            merged.update(state)
            run.state = merged
            run.state_version = int(getattr(run, "state_version", 0) or 0) + 1
            await session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("save_run_state 失败: %s", e)
        return False


async def record_tool_observations(
    run_id: str,
    trace: List[Dict[str, Any]],
    *,
    fail_closed: bool = False,
) -> int:
    """Persist observations without repurposing the legacy text result column.

    The immutable event log remains the replay source; these rows provide a compact audit and
    recovery index for tools. Historical/V1 rows are untouched. New writes are
    idempotent on the provider call id rather than the trace-list position so each
    observation can be committed before its public SSE event without colliding with
    another same-name call in the Run.
    """
    factory = runtime_session()
    if factory is None:
        if fail_closed and any(
            isinstance(item, dict) and isinstance(item.get("observation"), dict)
            for item in (trace or [])
        ):
            raise RuntimeError("Harness tool observation store is unavailable")
        return 0
    from app.runtime_models import AgentToolCall
    saved = 0
    try:
        async with factory() as session:
            for index, item in enumerate(trace or []):
                if not isinstance(item, dict) or not isinstance(item.get("observation"), dict):
                    continue
                observation = dict(item["observation"])
                call_identity = str(
                    observation.get("call_id")
                    or f"legacy:{index}:{item.get('name') or ''}"
                )
                digest = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{run_id}:{call_identity}",
                ).hex
                key = f"harness-observation:{run_id}:{digest}"
                existing = await session.scalar(select(AgentToolCall).where(
                    AgentToolCall.idempotency_key == key
                ).limit(1))
                if existing:
                    continue
                session.add(AgentToolCall(
                    id=_new_id(), run_id=run_id, name=str(item.get("name") or "tool"),
                    step_id=str(observation.get("plan_step_id") or "")[:64] or None,
                    args=dict(item.get("args") or {}), status=(
                        "failed" if observation.get("status") in {"failed", "unknown"} else "succeeded"
                    ),
                    observation=observation, raw_result_ref=observation.get("result_handle"),
                    idempotency_key=key,
                ))
                saved += 1
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_tool_observations 失败 run=%s: %s", run_id, exc)
        if fail_closed:
            raise RuntimeError("Harness tool observations could not be persisted") from exc
        return 0
    return saved


async def set_agent_mode(run_id: str, agent_mode: str) -> bool:
    """Persist the Run column used by get_run / 前端 Profile 恢复。"""
    mode = str(agent_mode or "").strip().lower()
    if mode not in {"standard", "plan", "research"}:
        return False
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import update as sa_update
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            result = await session.execute(
                sa_update(AgentRun).where(AgentRun.id == run_id).values(agent_mode=mode)
            )
            await session.commit()
            return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_agent_mode 失败 run=%s: %s", run_id, exc)
        return False


async def get_run(run_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """按 id + user 取 Run（含 state / resume_token / model）。"""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if not run or run.user_id != user_id:
                return None
            return {"id": run.id, "thread_id": run.thread_id, "status": run.status,
                    "kind": run.kind, "model": run.model,
                    "agent_mode": getattr(run, "agent_mode", None) or "standard",
                    # error/outcome：断流仲裁接口（/chat/runs/{id}/state）回传真实终态细节
                    "error": run.error, "outcome": getattr(run, "outcome", None),
                    # 租约字段（P1 多 worker）：harness_orchestrator._is_zombie_run 据此分流僵尸判定
                    "owner_instance_id": getattr(run, "owner_instance_id", None),
                    "heartbeat_at": (run.heartbeat_at.isoformat()
                                     if getattr(run, "heartbeat_at", None) else None),
                    "resume_token": run.resume_token, "state": run.state or {}}
    except Exception as e:  # noqa: BLE001
        logger.warning("get_run 失败: %s", e)
        return None


def _sse_payload(payload: Dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def parse_harness_sse_payload(payload: str) -> Optional[Dict[str, Any]]:
    """Parse one Harness Protocol 1 envelope; transport sentinels return ``None``."""
    for line in (payload or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line.replace("data:", "", 1).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("version") == "harness/1" and data.get("run_id"):
            return data
    return None


async def record_sse_payload(run_id: str, payload: str) -> None:
    """Route one catalogued Harness event to durable or ephemeral delivery."""
    data = parse_harness_sse_payload(payload)
    if not data or data.get("run_id") != run_id:
        return
    event_type = str(data.get("type") or "unknown")
    from app.services.agent_harness.event_catalog import event_definition
    try:
        definition = event_definition(event_type)
    except ValueError:
        logger.error("Rejected non-catalog Harness event type=%s run=%s", event_type, run_id)
        return

    async def _persist() -> None:
        event_data = dict(data.get("data") or {})
        # 心跳只服务实时观感，不落库：60s 沙箱任务≈60 行纯噪声；断线重连的耗时恢复
        # 由阶段事件 + tool.started/completed 的信封时间戳完成，不依赖心跳回放。
        if event_type == "tool.progress" and event_data.get("heartbeat"):
            return
        event_data["_event_timestamp"] = int(data.get("timestamp") or 0)
        await record_event(
            run_id=run_id,
            event_id=str(data.get("event_id") or uuid.uuid4().hex),
            sequence=int(data.get("sequence") or 0),
            etype=event_type,
            data=event_data,
        )

    if not definition.persisted:
        from app.services.tasks import runtime_event_bus
        # Live protocol stays ephemeral (catalog persisted=False). Coalesced
        # message.delta is also written so reconnect replay is byte-complete.  Commit it before
        # either the cross-worker bus or the local Run hub may expose it.
        if event_type == "message.delta":
            await _persist()
        await runtime_event_bus.publish_ephemeral_payload(run_id, payload)
        return
    await _persist()


_PAYLOAD_CACHE_MAX = 256
_payload_cache: dict[tuple[str, str, int], List[str]] = {}


def invalidate_event_payload_cache(run_id: str) -> None:
    rid = str(run_id)
    for key in [item for item in _payload_cache if item[0] == rid]:
        _payload_cache.pop(key, None)


def _store_payload_cache(key: tuple[str, str, int], payloads: List[str]) -> None:
    # 空列表绝不能进缓存：API 与 worker 分进程，worker 落库只能作废本进程缓存。
    # 若把 after=N 的空结果缓存住，subscribe_run 的 100ms 轮询会永远以为没有新帧，
    # 客户端只收到 `: ping`，页面卡住，刷新才看到步骤和白卡。
    if not payloads:
        return
    if len(_payload_cache) >= _PAYLOAD_CACHE_MAX:
        for old in list(_payload_cache)[:64]:
            _payload_cache.pop(old, None)
    _payload_cache[key] = list(payloads)


async def list_event_payloads(
    run_id: str,
    user_id: str,
    after_sequence: int = 0,
    *,
    use_cache: bool = True,
) -> List[str]:
    """按 sequence 回放 Run 事件，供断线/切会话后恢复订阅。"""
    key = (str(run_id), str(user_id), int(after_sequence or 0))
    cached = _payload_cache.get(key) if use_cache else None
    # 空缓存视为未命中并丢掉：热进程里可能已有被毒化的 []。
    if cached:
        return list(cached)
    if use_cache:
        _payload_cache.pop(key, None)
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentRun, AgentRunEvent, AgentRunInput
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if not run or run.user_id != user_id:
                return []
            rows = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == run_id)
                    .where(AgentRunEvent.sequence > int(after_sequence or 0))
                    .order_by(AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                )
            ).scalars().all()
            payloads: List[str] = []
            for row in rows:
                event_data = dict(row.data or {})
                stored_timestamp = int(event_data.pop("_event_timestamp", 0) or 0)
                timestamp = stored_timestamp or (
                    _runtime_created_at_ms(row.created_at)
                    or int(datetime.now(timezone.utc).timestamp() * 1000)
                )
                payloads.append(_sse_payload({
                    "event_id": row.event_id,
                    "version": "harness/1",
                    "schema_version": 1,
                    "thread_id": run.thread_id,
                    "run_id": run_id,
                    "sequence": row.sequence,
                    "timestamp": timestamp,
                    "type": row.type,
                    "data": event_data,
                }))
            # 活动 Run 的缺口查询会随 worker 写入持续变长，跨进程无法作废 API 缓存。
            if use_cache and payloads and str(getattr(run, "status", "") or "") in TERMINAL_RUN_STATUSES:
                _store_payload_cache(key, payloads)
            return payloads
    except Exception as e:  # noqa: BLE001
        logger.warning("list_event_payloads 失败: %s", e)
        return []


# 轨迹重建只需要这些事件类型；message.delta 是每 token 一行的大头，
# 全量拉回内存会让长会话的历史接口显著变慢，必须在 SQL 层过滤掉。
_TRACE_EVENT_TYPES = (
    # Run 接受、阶段推进和能力加载都是持久事实：刷新后若消失，用户会看到 Run 已在执行，
    # 但计划/工具时间线仍停留在旧状态。H5 会统一迁入 Harness Event Catalog。
    "run.accepted", "run.phase.changed", "capability.loaded", "artifact.saved",
    "run.started", "run.completed", "run.partial", "run.failed", "run.cancelled",
    "plan.updated", "research.team",
    "tool.started", "tool.progress", "tool.completed", "tool.failed",
    "message.completed", "message.commentary",
    "message.reasoning.completed",
    # 运行中引导生效帧：刷新后要能还原「这条引导是在轮中被吸收的」，否则时间线上
    # 只剩一条孤立的用户消息，看不出它没有另起一轮。
    "input.received", "input.applied", "input.rejected",
    # 附件读取置信度（P0）：降级提示条随历史回放还原
    "attachments.status",
    # 消息级贴条（2026-07-29 补齐）：压缩提示在**实时**会话里一旦出现就一直挂在那条
    # 助手消息上，用户不点也不会消失——不在白名单里就会「刷新一次就没了」。判据是
    # 「实时侧不会主动收起」：能被用户操作收起的卡（审批、input.required）**故意不在这里**，
    # 回放它们等于把已经答完的卡片复活或给出一张点不动的死卡，比丢失更糟。
    "context.compacted", "context.compaction",
)

#: 会在时间线上留下「已生成并保存 N 个文件」产物行的工具。
#: 与前端 `executionTimeline.ts` 的 `ARTIFACT_PRODUCERS` 是同一份清单——回放和实时用的是
#: 两套代码，判据分岔的表现就是「刷新一次产物行的位置和条数全变了」。
_ARTIFACT_PRODUCERS = frozenset({
    "bash", "write_file", "download_url", "edit_file",
    # ⚠️ 下面三个已在 2026-07-29 从工具集里彻底删除，**但这里必须留着**：
    # 这个函数是 get_execution_traces_by_thread 读**历史**轨迹时用的，老会话里存的
    # step 名字就是这几个。从这里删掉的后果是「几天前那次生成的产物行凭空消失」。
    # 旧注释写的理由（"开关关闭时仍在"）已经失效——SANDBOX_WORKSPACE_SYNC 开关本身
    # 都没了，照那条理由读的人会理直气壮地删掉它们。真实理由只有一个：历史回放。
    "create_file", "update_file",
})


def _is_deliverable_row(row: Dict[str, Any]) -> bool:
    """产物行是否该把这个文件计进去。

    优先信行里算好的 `deliverable`（`user_file_service._serialize_row` 的唯一出口），
    老事件里没有这个字段时按 文件名+来源 现算——与前端 `deliverable.ts` 同一顺序。
    """
    flag = row.get("deliverable")
    if isinstance(flag, bool):
        return flag
    from app.services.files import deliverable as _deliverable
    return _deliverable.is_deliverable(str(row.get("filename") or ""), row.get("source"))


async def get_execution_traces_by_thread(
    thread_id: str,
    input_messages: Optional[List[Dict[str, Any]]] = None,
    run_traces: Optional[Dict[str, Dict[str, Any]]] = None,
    only_run_id: Optional[str] = None,
) -> Dict[int, Dict[str, Any]]:
    """    把已持久化的 SSE 事件还原为历史消息的结构化执行摘要。

    原始 reasoning delta 不查询；``message.reasoning.completed`` 只投影截断后的
    思考正文与秒数，不回放逐 token 原文。
    """
    factory = runtime_session()
    if factory is None:
        return {}
    from app.runtime_models import AgentRun, AgentRunEvent, AgentRunInput
    try:
        async with factory() as session:
            run_query = select(AgentRun).where(AgentRun.thread_id == thread_id)
            if only_run_id:
                run_query = run_query.where(AgentRun.id == str(only_run_id))
            runs = (
                await session.execute(run_query.order_by(AgentRun.created_at.asc()))
            ).scalars().all()
            if not runs:
                return {}
            run_ids = [run.id for run in runs]
            events = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id.in_(run_ids))
                    .where(AgentRunEvent.type.in_(_TRACE_EVENT_TYPES))
                    .order_by(AgentRunEvent.run_id.asc(), AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                )
            ).scalars().all()
            input_rows = (
                await session.execute(
                    select(AgentRunInput)
                    .where(AgentRunInput.run_id.in_(run_ids))
                    .order_by(
                        AgentRunInput.run_id.asc(),
                        AgentRunInput.input_sequence.asc().nullslast(),
                        AgentRunInput.created_at.asc(),
                        AgentRunInput.id.asc(),
                    )
                )
            ).scalars().all()
        by_run: Dict[str, List[Any]] = {}
        for event in events:
            by_run.setdefault(event.run_id, []).append(event)
        # 运行中插话的“收到”事实落在主库 ChatMessage（status=instruction），而不是从
        # 活动 SSE producer 之外直接写 AgentRunEvent。这里把它们合成只读伪事件并按真实
        # 时间放回轨迹，刷新/换端仍能重建「旧执行段 → 用户插话 → 新执行段」。
        # MySQL ChatMessage.created_at 是本地时间，Runtime PG created_at 是 UTC（可无 tzinfo）。
        # 直接混算会稳定产生 8 小时的“已处理 480 分钟”。以 Runtime Instruction 的时间为
        # 事实源，MySQL 时间只作为没有迁移记录的旧数据兜底；同内容重复指令按创建顺序消费。
        input_facts: Dict[tuple[str, str], List[Any]] = {}
        input_facts_by_message: Dict[tuple[str, int], Any] = {}
        for row in input_rows:
            input_facts.setdefault(
                (str(row.run_id), str(row.content or "")), [],
            ).append(row)
            source_message_id = getattr(row, "source_message_id", None)
            if source_message_id is not None:
                input_facts_by_message[(str(row.run_id), int(source_message_id))] = row
        received_message_ids = {
            (str(event.run_id), int((event.data or {}).get("message_id")))
            for event in events
            if event.type == "input.received"
            and (event.data or {}).get("message_id") is not None
        }
        for item in input_messages or []:
            run_id = str(item.get("run_id") or "")
            message_id = item.get("message_id")
            if not run_id or message_id is None or run_id not in run_ids:
                continue
            if (run_id, int(message_id)) in received_message_ids:
                continue
            content = str(item.get("content") or "")
            fact = input_facts_by_message.get((run_id, int(message_id)))
            if fact is None:
                candidates = input_facts.get((run_id, content)) or []
                fact = candidates.pop(0) if candidates else None
            created_at = fact.created_at if fact is not None else item.get("created_at")
            timestamp = (
                _runtime_created_at_ms(created_at) if fact is not None else (
                    int(created_at.timestamp() * 1000)
                    if isinstance(created_at, datetime) else int(item.get("timestamp") or 0)
                )
            )
            by_run.setdefault(run_id, []).append(SimpleNamespace(
                run_id=run_id,
                sequence=0,
                id=0,
                type="input.received",
                data={
                    "message_id": int(message_id),
                    "input_id": str(fact.id) if fact is not None else "",
                    "content": content,
                    "_event_timestamp": timestamp,
                },
                created_at=created_at,
            ))
        for run_id, run_events in by_run.items():
            run_events.sort(key=lambda event: (
                int((event.data or {}).get("_event_timestamp") or (
                    _runtime_created_at_ms(event.created_at)
                )),
                int(event.sequence or 0),
                int(event.id or 0),
            ))
        result: Dict[int, Dict[str, Any]] = {}
        for run in runs:
            steps: List[Dict[str, Any]] = []
            plan_items: List[Dict[str, Any]] = []
            task_plan_steps: List[Dict[str, Any]] = []
            task_goal_contract: Optional[Dict[str, Any]] = None
            task_approved_version: Optional[int] = None
            task_plan_diverged = False
            task_plan_version: Optional[int] = None
            active_plan_key: Optional[str] = None  # 当前进行中的任务步骤 title（交错归属）
            files: List[Dict[str, Any]] = []
            # 已计过的 file_id（本 Run 内）：产物行按「首次出现」计数，覆写/二次同步回带的
            # 同一个 id 不再重复出行，与实时侧 applyTimelineTool 的 known 集合同义。
            known_file_ids: set[str] = set()
            attachments_status: List[Dict[str, Any]] = []
            # 消息级贴条/卡片（2026-07-29）：实时侧一旦出现就常驻在该条助手消息上，
            # 刷新后必须原样回来。每种都是**整帧替换**（最后一帧为准），不做累加——
            # 同一轮里后发的那帧就是模型/后端最终认定的结果。
            compacted_note = ""
            accepted_route = ""
            run_phase = ""
            loaded_capabilities: List[str] = []
            preamble = ""
            plan_report = ""
            reasoning_summary = ""
            reasoning_seconds = None
            execution_segments: List[Dict[str, Any]] = []
            segment_step_start = 0
            message_id = None
            started_ms = _runtime_created_at_ms(run.created_at)
            completed_ms = int(run.completed_at.timestamp() * 1000) if run.completed_at else 0
            segment_started_ms = started_ms
            segment_sequence_start: Optional[int] = None
            latest_positive_sequence: Optional[int] = None
            last_event_ms = started_ms

            def _last_running(
                kind: str, name: str, call_id: str = "",
            ) -> Optional[Dict[str, Any]]:
                return next(
                    (s for s in reversed(steps)
                     if s.get("kind") == kind
                     and s.get("status") == "running"
                     and (
                         s.get("callId") == call_id
                         if call_id else s.get("name") == name
                     )),
                    None,
                )

            _last_dedup_seq = 0
            for event in by_run.get(run.id, []):
                # 重复行防御（与 uq_agent_run_events_run_seq 唯一索引互补，兜历史存量）：
                # 同 (run_id, sequence>0) 的重复事件只投影一次；sequence=0 的旧兜底行不去重
                if event.sequence and event.sequence > 0:
                    if event.sequence == _last_dedup_seq:
                        continue
                    _last_dedup_seq = event.sequence
                    latest_positive_sequence = int(event.sequence)
                    if segment_sequence_start is None:
                        segment_sequence_start = int(event.sequence)
                data = dict(event.data or {})
                timestamp = int(data.pop("_event_timestamp", 0) or 0)
                if not timestamp and event.created_at:
                    timestamp = _runtime_created_at_ms(event.created_at)
                if timestamp:
                    last_event_ms = timestamp
                if event.type == "run.accepted":
                    accepted_route = str(data.get("route") or "")
                elif event.type == "run.phase.changed":
                    run_phase = str(data.get("phase") or "")
                elif event.type == "capability.loaded":
                    for name in data.get("names") or []:
                        value = str(name or "").strip()
                        if value and value not in loaded_capabilities:
                            loaded_capabilities.append(value)
                elif event.type == "artifact.saved":
                    fresh_files = []
                    for row in data.get("files") or []:
                        if not isinstance(row, dict) or not row.get("id"):
                            continue
                        file_id = str(row["id"])
                        if file_id in known_file_ids:
                            continue
                        known_file_ids.add(file_id)
                        normalized = dict(row)
                        origin = normalized.get("origin") if isinstance(normalized.get("origin"), dict) else {}
                        if not normalized.get("source") and str(origin.get("tool") or "") == "research":
                            normalized["source"] = "research"
                        fresh_files.append(normalized)
                    files.extend(fresh_files)
                    delivered = [row for row in fresh_files if _is_deliverable_row(row)]
                    if delivered:
                        review_failed = any(
                            str((row.get("review") or {}).get("status") or "") == "failed"
                            for row in delivered
                        )
                        steps.append({
                            "kind": "artifact",
                            "status": "failed" if review_failed else "completed",
                            "label": (
                                f"{len(delivered)} 个产物可用性检查未通过，已保存为草稿"
                                if review_failed
                                else f"已生成并保存 {len(delivered)} 个文件"
                            ),
                            "files": delivered,
                            "planKey": active_plan_key,
                        })
                elif event.type == "run.started" and timestamp:
                    started_ms = timestamp
                elif event.type == "plan.updated":
                    # 权威计划整表回传：直接替换（保留最新一版）
                    task_plan_steps = [
                        {
                            "key": str(s.get("key") or f"plan-{i}"),
                            "title": str(s.get("title") or ""),
                            "status": str(s.get("status") or "pending"),
                            "detail": str(s.get("detail") or "") or None,
                            **(
                                {"acceptance": str(s.get("acceptance") or "").strip()[:60]}
                                if str(s.get("acceptance") or "").strip()
                                else {}
                            ),
                        }
                        for i, s in enumerate(data.get("steps") or [])
                        if isinstance(s, dict) and str(s.get("title") or "").strip()
                    ]
                    if isinstance(data.get("goal_contract"), dict):
                        task_goal_contract = data.get("goal_contract")
                    if data.get("approved_version") is not None and data.get("approved_version") != "":
                        try:
                            task_approved_version = int(data.get("approved_version"))
                        except (TypeError, ValueError):
                            pass
                    if data.get("diverged"):
                        task_plan_diverged = True
                    if data.get("plan_version") is not None:
                        try:
                            task_plan_version = int(data.get("plan_version") or 0)
                        except (TypeError, ValueError):
                            pass
                    # 「当前步骤」跟踪（与前端 applyTaskPlan 同一规则）：首个 running，否则首个
                    # pending；必须保存稳定 key 而不是 title——模型允许生成重复标题，按标题持久化
                    # 会让刷新后的第二个同名步骤错误归到第一个步骤下。
                    active_plan_key = next(
                        (s["key"] for s in task_plan_steps if s["status"] in ("running", "in_progress")),
                        None,
                    ) or next(
                        (s["key"] for s in task_plan_steps if s["status"] == "pending"),
                        None,
                    ) or ("__fallback_tail__" if task_plan_steps else None)
                elif event.type == "tool.started":
                    name = str(data.get("name") or "")
                    call_id = str(data.get("call_id") or "")
                    # HITL 工具（ask_user_choice）挂起时可能只有 started 无 completed；
                    # 后续任意工具开跑即视为提问步骤已结束，避免历史回放永久「正在请求补充信息…」。
                    if name and name != "ask_user_choice":
                        for prev in steps:
                            if (
                                prev.get("kind") == "tool"
                                and prev.get("name") == "ask_user_choice"
                                and prev.get("status") == "running"
                            ):
                                prev["status"] = "completed"
                                if timestamp and prev.get("startedAt"):
                                    prev["durationMs"] = max(
                                        0, int(timestamp) - int(prev["startedAt"] or timestamp),
                                    )
                    planned = next(
                        (p for p in plan_items if p.get("name") == name and p.get("status") == "pending"),
                        None,
                    )
                    if planned:
                        planned["status"] = "running"
                    # Codex 式工具行回放：实际执行的脚本/命令（args.code / args.command 已随
                    # 事件持久化）作为 command 还原，前端可展开面板展示「跑了什么」。
                    # ⚠️ args.command 必须一起取：只取 code 的话 bash 行刷新后面板是空的，
                    # 与实时表现不一致（实时侧同款修复见 agentApi.ts）。
                    args = data.get("args") or {}
                    command = (
                        str(args.get("code") or args.get("command") or "")[:4000]
                        if isinstance(args, dict) else ""
                    )
                    # 模型现写的调用意图（args.intent）：回放同实时——行标题优先用它
                    intent = str(args.get("intent") or "")[:80] if isinstance(args, dict) else ""
                    steps.append({
                        "kind": "tool", "name": name,
                        "label": name or "调用工具", "status": "running",
                        "startedAt": timestamp,
                        "planKey": active_plan_key,
                        **({"callId": call_id} if call_id else {}),
                        **({"command": command} if command else {}),
                        **({"intent": intent} if intent else {}),
                    })
                elif event.type == "tool.progress":
                    step = _last_running(
                        "tool",
                        str(data.get("name") or ""),
                        str(data.get("call_id") or ""),
                    )
                    if step:
                        step["label"] = str(data.get("label") or step["label"])
                        step["stage"] = str(data.get("stage") or "")
                        step["elapsedMs"] = int(data.get("elapsed_ms") or 0)
                elif event.type in ("tool.completed", "tool.failed"):
                    name = str(data.get("name") or "")
                    call_id = str(data.get("call_id") or "")
                    planned = next(
                        (p for p in plan_items if p.get("name") == name and p.get("status") == "running"),
                        None,
                    )
                    if planned:
                        planned["status"] = "failed" if event.type == "tool.failed" else "completed"
                    step = _last_running("tool", name, call_id)
                    meta = data.get("meta") or {}
                    if step:
                        step["status"] = "failed" if event.type == "tool.failed" else "completed"
                        step["durationMs"] = max(0, timestamp - int(step.get("startedAt") or timestamp))
                        # 工具结果片段回放（Codex 式输出面板）：completed 存 preview、**failed 存
                        # error**（2026-07-28 修）。此前失败也一律写 preview，与实时侧（step.error）
                        # 落库口径相反：同一次失败刷新前有行内红字摘要 + 「输出」段，刷新后红字没了。
                        if event.type == "tool.failed":
                            failure = str(data.get("error") or data.get("result_preview") or "")
                            if failure:
                                step["error"] = failure[:2000]
                        else:
                            preview = str(data.get("result_preview") or "")
                            if preview:
                                step["preview"] = preview[:2000]
                        # search_web 的结构化元信息：来源 favicon 计数标签 + 「浏览 N 个页面」行
                        if isinstance(meta.get("count"), int):
                            step["count"] = meta["count"]
                        if isinstance(meta.get("urls"), list) and meta["urls"]:
                            step["urls"] = [str(u) for u in meta["urls"]][:10]
                        # 页面快照（2026-07-27）：只在实时链路显示的话，刷新一次图就没了。
                        # 只收 data: URI —— 轨迹会原样喂给前端的 img src，放任意外链等于开了个
                        # 由工具回执控制的外部请求通道（可用来探测内网 / 追踪用户）。
                        shot = meta.get("shot")
                        if isinstance(shot, str) and shot.startswith("data:"):
                            step["shot"] = shot
                        action = meta.get("action") or {}
                        if isinstance(action, dict):
                            step["operation"] = str(action.get("operation") or "") or None
                            step["target"] = str(action.get("target") or "") or None
                            step["fileId"] = str(action.get("file_id") or "") or None
                            step["added"] = max(0, int(action.get("added") or 0))
                            step["removed"] = max(0, int(action.get("removed") or 0))
                    if isinstance(meta.get("read"), list) and meta["read"]:
                        pages = [
                            {"title": str(p.get("title") or ""), "url": str(p.get("url") or "")}
                            for p in meta["read"] if isinstance(p, dict) and p.get("url")
                        ]
                        # 挂在 search_web 步骤自身，不再另起 kind=read 平级行（前端图标/步骤名对齐）
                        if pages and step and str(step.get("name") or "") == "search_web":
                            step["pages"] = pages
                        elif pages:
                            steps.append({"kind": "read", "pages": pages, "planKey": active_plan_key})
                    # 首次出现的文件才算本步的产出：同一个 file_id 被后续工具再次回带
                    # （覆写、二次同步）时不能重复计数，与实时侧 applyTimelineTool 同款去重。
                    fresh_files: List[Dict[str, Any]] = []
                    if isinstance(meta.get("files"), list):
                        for row in meta["files"]:
                            if not isinstance(row, dict) or not row.get("id"):
                                continue
                            file_id = str(row["id"])
                            if file_id in known_file_ids:
                                continue
                            known_file_ids.add(file_id)
                            fresh_files.append(row)
                        files.extend(fresh_files)
                    review_status = str(meta.get("review_status") or "")
                    if review_status:
                        # 查错语义：只有 failed 显示为未通过；unknown=检查缺席照常交付（拍板）
                        review_blocked = review_status == "failed"
                        steps.append({
                            "kind": "verification",
                            "status": "failed" if review_blocked else "completed",
                            "label": "可用性检查发现问题" if review_blocked else "已完成可用性检查",
                            "reviewStatus": review_status,
                            "planKey": active_plan_key,
                        })
                    # 产物行必须**按发生位置**进 steps（2026-07-28 修）。此前只把文件汇总进
                    # trace.files，前端 restoreExecutionTrace 的 `!steps.some(artifact)` 分支
                    # 因此恒成立，回放永远只在末尾合成一行：一轮里 bash 分三次各存一个 pptx，
                    # 实时是三行「已生成并保存 1 个文件」穿插在对应步骤后，刷新后塌成末尾一行
                    # 「已生成并保存 3 个文件」——位置与条数全变了。
                    # 判据与实时侧 applyTimelineTool 逐条对齐：产物生产者工具 + 交付物才出行；
                    # 顺序也一致（verification 在前、artifact 在后）。
                    delivered = [row for row in fresh_files if _is_deliverable_row(row)]
                    if delivered and name in _ARTIFACT_PRODUCERS:
                        review_failed = any(
                            str((row.get("review") or {}).get("status") or "") == "failed"
                            for row in delivered
                        )
                        steps.append({
                            "kind": "artifact",
                            "status": "failed" if review_failed else "completed",
                            "label": (
                                f"{len(delivered)} 个产物可用性检查未通过，已保存为草稿"
                                if review_failed
                                else f"已生成并保存 {len(delivered)} 个文件"
                            ),
                            "files": delivered,
                            "planKey": active_plan_key,
                        })
                elif event.type == "attachments.status":
                    attachments_status = [
                        item for item in (data.get("items") or []) if isinstance(item, dict)
                    ]
                elif event.type == "context.compaction":
                    status = str(data.get("status") or "started")
                    if status == "started":
                        steps.append({
                            "kind": "compaction",
                            "status": "running",
                            "startedAt": timestamp,
                            "planKey": active_plan_key,
                        })
                    else:
                        step = next(
                            (item for item in reversed(steps)
                             if item.get("kind") == "compaction" and item.get("status") == "running"),
                            None,
                        )
                        if step is None:
                            step = {
                                "kind": "compaction",
                                "startedAt": timestamp,
                                "planKey": active_plan_key,
                            }
                            steps.append(step)
                        step["status"] = "completed" if status == "completed" else "failed"
                        if data.get("seconds") is not None:
                            step["seconds"] = max(0, int(data.get("seconds") or 0))
                        elif step.get("startedAt") and timestamp:
                            step["seconds"] = max(1, int((timestamp - int(step["startedAt"])) / 1000))
                elif event.type == "context.compacted":
                    compacted_note = str(data.get("note") or "Context compacted")
                    if not any(item.get("kind") == "compaction" for item in steps):
                        steps.append({
                            "kind": "compaction",
                            "status": "completed",
                            "planKey": active_plan_key,
                        })
                elif event.type == "input.received":
                    input_message_id = data.get("message_id")
                    if input_message_id is not None:
                        execution_segments.append({
                            "inputMessageId": int(input_message_id),
                            "inputId": str(data.get("input_id") or ""),
                            "startedAt": segment_started_ms or None,
                            "completedAt": timestamp or None,
                            "durationMs": (
                                max(0, timestamp - segment_started_ms)
                                if segment_started_ms and timestamp else None
                            ),
                            "startSequence": segment_sequence_start,
                            "endSequence": latest_positive_sequence,
                            "steps": [dict(item) for item in steps[segment_step_start:]],
                            "preamble": preamble or None,
                            # 2026-07-29：这里原先有**两行重复的 plan_report**，其中一行缩进
                            # 错位到字典字面量的外层列——Python 语法上合法（后写覆盖先写），
                            # 所以零报错、静默存在。删重复的那行。
                            "plan_report": plan_report or None,
                            "reasoning_summary": reasoning_summary or None,
                            "reasoning_seconds": reasoning_seconds,
                            "status": "received",
                        })
                        segment_step_start = len(steps)
                        segment_started_ms = timestamp or segment_started_ms
                        segment_sequence_start = None
                        # 新分段不预置回执文案（Codex 对齐 2026-07-26：引导注入后没有任何
                        # UI 回执，确认由模型的后续叙述与最终回答对账完成）。刷新回放必须
                        # 与实时渲染逐字一致，否则「刷新后多出一句机械回执」。
                        preamble = None
                        # 上一段的 Plan 报告已经随 segment 持久化。新的用户输入开启
                        # 新显示段后必须清空，否则最终段会再投影一次同一张计划卡。
                        plan_report = ""
                        reasoning_summary = ""
                        reasoning_seconds = None
                elif event.type == "input.applied":
                    # 生效同样不落任何文案：只有「没生效」才需要如实告诉用户。
                    pass
                elif event.type == "input.rejected":
                    reason = str(data.get("reason") or "无法安全合并到当前任务")
                    text = f"未能应用这条追加要求：{reason}"
                    if not preamble:
                        preamble = text
                    else:
                        steps.append({"kind": "note", "text": text,
                                      "planKey": active_plan_key})
                elif event.type in (
                    "message.reasoning.completed",
                    "message.reasoning_completed",
                ):
                    from app.services.sse_protocol import compact_reasoning_summary
                    raw_text = str(data.get("text") or "")
                    text = compact_reasoning_summary(raw_text) or (
                        "已完成思考" if raw_text.strip() else ""
                    )
                    seconds = data.get("seconds")
                    try:
                        seconds_val = round(float(seconds), 1) if seconds not in (None, "") else None
                    except (TypeError, ValueError):
                        seconds_val = None
                    if text:
                        step = {
                            "kind": "thinking",
                            "text": text,
                            "status": "completed",
                            "planKey": active_plan_key,
                        }
                        if seconds_val:
                            step["seconds"] = seconds_val
                        steps.append(step)
                        reasoning_summary = text
                        if seconds_val:
                            reasoning_seconds = seconds_val
                elif event.type == "message.commentary":
                    # 过程说明回放：任何工具/子智能体动作之前的首段=开场白（渲染在执行
                    # 时间线上方），其余=轮间衔接语（时间线 note 步骤，与动作按时序交错）
                    # ——与前端 applyCommentary 的实时判定同一规则
                    text = str(data.get("text") or "").strip()
                    from app.services.agent_harness.plan_content import extract_proposed_plan
                    legacy_tagged_plan = extract_proposed_plan(text)
                    # 计划报告（kind="plan"）单独还原成 plan_report：它渲染成一张卡片，
                    # 不是开场白气泡、更不是时间线里的 note 行（2026-07-28 用户拍板）。
                    # 与前端 applyCommentary 的实时判定同一规则。
                    if text and (
                        str(data.get("kind") or "") == "plan"
                        or legacy_tagged_plan is not None
                    ):
                        plan_report = legacy_tagged_plan or text
                        continue
                    # 旧 reasoning_summary 事件按普通公开 commentary 回放。它不能继续使用
                    # 与瞬时 reasoning 尾窗相同的渐隐样式，否则看起来像思考结束后仍被保留。
                    # kind=initial_progress 只服务实时“首帧别空白”，不是模型叙述；
                    # 若投影进 durable preamble，历史打开后工具/答案都齐了仍挂着「正在处理…」。
                    if str(data.get("kind") or "") == "initial_progress":
                        continue
                    acted = any(
                        s.get("kind") == "tool"
                        for s in steps[segment_step_start:]
                    )
                    if text and not preamble and not acted:
                        preamble = text
                    elif text:
                        steps.append({"kind": "note", "text": text, "planKey": active_plan_key})
                elif event.type in (
                    "message.completed",
                    "run.completed",
                    "run.partial",
                    "run.failed",
                    "run.cancelled",
                ):
                    if data.get("message_id") is not None:
                        message_id = int(data["message_id"])
                    if event.type.startswith("run.") and timestamp:
                        completed_ms = timestamp
                    if event.type == "run.completed":
                        # HITL 工具可能以“等待用户”挂起，没有独立 tool.completed；同一个
                        # Run 最终完成即代表这些已开始的计划项已经得到处理，避免历史回放
                        # 仍显示永久转圈。尚未开始的 pending 项保持原样，便于发现被跳过动作。
                        for item in plan_items:
                            if item.get("status") == "running":
                                item["status"] = "completed"
            state_phase = str((run.state or {}).get("phase") or "")
            effective_phase = run_phase or state_phase
            effective_status = str(run.status or "")
            if effective_status == "completed" and str(getattr(run, "outcome", "") or "") == "partial":
                effective_status = "partial"
            if effective_phase in {"completed", "partial", "failed", "cancelled"}:
                effective_status = effective_phase
            research_progress = None
            research_blob = (run.state or {}).get("research") if isinstance(run.state, dict) else None
            if isinstance(research_blob, dict):
                try:
                    from app.services.agent_harness.research.engine import progress_payload
                    from app.services.agent_harness.research.report import ledger_from_state

                    research_progress = progress_payload(ledger_from_state(research_blob))
                except Exception:  # noqa: BLE001
                    research_progress = None
            if str(getattr(run, "agent_mode", "") or (run.state or {}).get("agent_mode") or "") == "research":
                from app.services.agent_harness.research.team import public_snapshot

                team_snapshot = public_snapshot((run.state or {}).get("research_team"))
                if team_snapshot:
                    research_progress = {**(research_progress or {}), "team": team_snapshot}
            payload = {
                    # 与这份轨迹快照同批读到的最后事件游标。前端切回活动 Run 时
                    # 先一次性恢复快照，再从此游标订阅新事件，禁止从 sequence=0
                    # 把已执行的 commentary/reasoning/tool 逐条动画重演。
                    "event_cursor": latest_positive_sequence or 0,
                    "startedAt": started_ms or None,
                    "completedAt": completed_ms or None,
                    "durationMs": max(0, completed_ms - started_ms) if started_ms and completed_ms else None,
                    "steps": steps[segment_step_start:],
                    "plan": plan_items,
                    "task_plan": task_plan_steps or None,
                    "goal_contract": task_goal_contract or (
                        (run.state or {}).get("goal_contract")
                        if isinstance((run.state or {}).get("goal_contract"), dict)
                        else None
                    ),
                    "approved_version": (
                        task_approved_version
                        if task_approved_version is not None
                        else ((run.state or {}).get("approved_plan_version")
                              if (run.state or {}).get("approved_plan_version") is not None
                              else None)
                    ),
                    "plan_version": task_plan_version,
                    "diverged": task_plan_diverged or None,
                    "files": files,
                    "attachments_status": attachments_status or None,
                    # 消息级贴条/卡片（2026-07-29）：一律「有就给、空就 None」，前端按可选字段
                    # 读。新增字段对老前端是纯增量（多余的键被忽略），不需要版本协商。
                    "compacted_note": compacted_note or None,
                    "accepted_route": accepted_route or None,
                    "run_phase": effective_phase or None,
                    "loaded_capabilities": loaded_capabilities or None,
                    "status": effective_status,
                    "error": _public_trace_error(run) if effective_status == "failed" else None,
                    "preamble": preamble or None,
                    # 计划报告（2026-07-29 补）：分段字典里一直在投影它，**顶层却漏了**——
                    # 而前端 executionTimeline.restoreExecutionTrace 读的正是顶层这个键
                    # （trace.plan_report）。净效果：计划卡刷新后永远回不来，而分段里那份
                    # 只在"用户插过话"的会话里才存在，掩盖了这个漏项。
                    "plan_report": plan_report or None,
                    "reasoning_summary": reasoning_summary or None,
                    "reasoning_seconds": reasoning_seconds,
                    "segments": execution_segments or None,
                    "run_id": run.id,
                    "agent_mode": str(getattr(run, "agent_mode", None) or "") or None,
                    "research_progress": research_progress,
                }
            if message_id is not None:
                result[message_id] = payload
            if run_traces is not None:
                run_traces[str(run.id)] = payload
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("get_execution_traces_by_thread 失败: %s", e)
        return {}


async def collect_run_output_text(run_id: str) -> Dict[str, Any]:
    """聚合某 Run 已落库的正文产出（对账回填用，P0 刷新丢失修复）。

    优先取最后一条带全文的 message.completed（附 message_id）；没有则按 sequence 拼接
    message.delta 逐帧重建。commentary（过程说明）已随 delta 流出，重建文本可能含它——
    对账回填场景可接受（诚实的部分产出优于整条消失）。
    """
    factory = runtime_session()
    if factory is None:
        return {"text": "", "message_id": None}
    from app.runtime_models import AgentRunEvent
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == run_id)
                    .where(AgentRunEvent.type.in_(("message.delta", "message.completed")))
                    .order_by(AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                )
            ).scalars().all()
        parts: List[str] = []
        completed_text: Optional[str] = None
        message_id: Optional[int] = None
        for row in rows:
            data = row.data or {}
            if row.type == "message.completed":
                if data.get("text"):
                    completed_text = str(data["text"])
                if data.get("message_id") is not None:
                    message_id = int(data["message_id"])
            else:
                text = str(data.get("text") or "")
                if text:
                    parts.append(text)
        return {"text": (completed_text if completed_text is not None else "".join(parts)),
                "message_id": message_id}
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_run_output_text 失败: %s", e)
        return {"text": "", "message_id": None}


async def list_completed_runs_missing_check(window_hours: int = 24) -> List[Dict[str, Any]]:
    """双库对账扫描素材（P0-4 最小闭环）：近 window_hours 内 completed 的 Run 及其
    message.completed 事件里的 message_id/全文——由 run_reconcile_service 对照 MySQL
    消息表核销「Run 完成但消息缺失」。"""
    factory = runtime_session()
    if factory is None:
        return []
    from datetime import timedelta
    from app.runtime_models import AgentRun, AgentRunEvent
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(window_hours)))
    try:
        async with factory() as session:
            runs = (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.status == "completed")
                    .where(AgentRun.completed_at >= cutoff)
                )
            ).scalars().all()
            if not runs:
                return []
            run_ids = [r.id for r in runs]
            events = (
                await session.execute(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id.in_(run_ids))
                    .where(AgentRunEvent.type == "message.completed")
                    .order_by(AgentRunEvent.sequence.asc(), AgentRunEvent.id.asc())
                )
            ).scalars().all()
        by_run: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            data = ev.data or {}
            if data.get("message_id") is None:
                continue
            by_run[ev.run_id] = {
                "message_id": int(data["message_id"]),
                "text": str(data.get("text") or ""),
            }
        return [
            {"run_id": r.id, "thread_id": r.thread_id or "", **by_run[r.id]}
            for r in runs if r.id in by_run
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("list_completed_runs_missing_check 失败: %s", e)
        return []


def _public_trace_error(run) -> str:
    from app.services.agent_harness.public_errors import GENERIC_RUN_FAILURE, public_terminal_reason

    state = run.state if isinstance(run.state, dict) else {}
    return public_terminal_reason(
        getattr(run, "error", None) or state.get("terminal_reason"), phase="failed",
    ) or GENERIC_RUN_FAILURE


async def list_policy_rejected_runs(window_hours: int = 168) -> List[Dict[str, str]]:
    """返回近期已由 New API 敏感词策略终止的 Run，供 MySQL transcript 对账。

    历史 Run 尚无独立 policy code 列，因此使用两条持久化强证据：Provider
    attempt 的结构化 error_code，或 Harness 写入的完整公开终态文案。
    不按用户正文或模糊错误关键词猜测。
    """
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentModelAttemptAudit, AgentRun
    from app.services.agent_harness.public_errors import (
        SENSITIVE_WORDS_REJECTION_MESSAGE,
    )

    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(window_hours)))
    try:
        async with factory() as session:
            structured_policy_attempt = (
                select(AgentModelAttemptAudit.id)
                .where(AgentModelAttemptAudit.run_id == AgentRun.id)
                .where(
                    AgentModelAttemptAudit.error_code == "sensitive_words_detected"
                )
                .exists()
            )
            rows = (
                await session.execute(
                    select(AgentRun.id, AgentRun.thread_id)
                    .where(AgentRun.status == "failed")
                    .where(or_(
                        AgentRun.error == SENSITIVE_WORDS_REJECTION_MESSAGE,
                        structured_policy_attempt,
                    ))
                    .where(AgentRun.completed_at >= cutoff)
                )
            ).all()
        return [
            {"run_id": str(row[0] or ""), "thread_id": str(row[1] or "")}
            for row in rows
            if row[0] and row[1]
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("list_policy_rejected_runs 失败: %s", e)
        return []


async def delete_context_snapshots_for_run(run_id: str) -> bool:
    """删除策略拒绝 Run 的断点侧写，防止「继续」再注入被拒绝目标。"""
    if not run_id:
        return False
    factory = runtime_session()
    if factory is None:
        return False
    from sqlalchemy import delete as sa_delete
    from app.runtime_models import AgentRunContextSnapshot

    try:
        async with factory() as session:
            await session.execute(
                sa_delete(AgentRunContextSnapshot).where(
                    AgentRunContextSnapshot.run_id == run_id
                )
            )
            await session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_context_snapshots_for_run 失败 run=%s: %s", run_id, e)
        return False


async def get_last_event_sequence(run_id: str, user_id: str) -> int:
    """取 Run 已持久化事件的最大 sequence；HITL resume 继续沿用同一 run_id 时用于续号。"""
    factory = runtime_session()
    if factory is None:
        return 0
    from app.runtime_models import AgentRun, AgentRunEvent
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if not run or run.user_id != user_id:
                return 0
            value = (
                await session.execute(
                    select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.run_id == run_id)
                )
            ).scalar_one_or_none()
            return int(value or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("get_last_event_sequence 失败: %s", e)
        return 0


# 事件落库失败计数（P0 双库监控最小闭环）：断线重连回放、慢订阅者摘除、历史轨迹都以
# 「事件已全量落库」为前提；这里失败意味着回放会缺帧，必须可观测。/health 暴露此计数。
_event_persist_failures = 0


def get_event_persist_failures() -> int:
    return _event_persist_failures


def _advance_harness_event_cursor(run: Any, sequence: int) -> None:
    """Advance the durable cursor without replacing concurrently committed RunState fields."""
    if run is None:
        return
    from app.services.agent_harness.run_store import HARNESS_STATE_SCHEMA_VERSION

    state = dict(run.state or {})
    if int(state.get("schema_version") or 0) != HARNESS_STATE_SCHEMA_VERSION:
        return
    current_cursor = int(state.get("event_cursor") or 0)
    next_cursor = int(sequence or 0)
    if next_cursor <= current_cursor:
        return
    state["event_cursor"] = next_cursor
    run.state = state
    run.state_version = int(run.state_version or 0) + 1


async def record_event(run_id: str, event_id: str, sequence: int, etype: str,
                       data: Optional[dict] = None) -> bool:
    """持久化 SSE v1 事件（§16.7），供回放/审计。

    A durable frame is publishable only after this function confirms commit.  Transient
    failures get a short bounded retry; an unconfirmed commit raises so the Run enters the
    existing waiting_system recovery path instead of creating live-only history.
    """
    global _event_persist_failures
    factory = runtime_session()
    if factory is None:
        raise EventPersistenceError("Harness event store is unavailable")
    from app.runtime_models import AgentRun, AgentRunEvent
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with factory() as session:
                run = await session.get(AgentRun, run_id, with_for_update=True)
                if run is None:
                    raise EventPersistenceError(f"Harness Run does not exist: {run_id}")
                session.add(AgentRunEvent(
                    run_id=run_id, event_id=event_id, sequence=sequence, type=etype, data=data,
                ))
                _advance_harness_event_cursor(run, sequence)
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    await session.execute(
                        text("SELECT pg_notify('agent_harness_run_events', :run_id)"),
                        {"run_id": str(run_id)},
                    )
                await session.commit()
            invalidate_event_payload_cache(run_id)
            return True
        except IntegrityError:
            # (run_id, sequence) 唯一索引命中＝该帧已落库（resume 双段/重试重放），幂等成功
            invalidate_event_payload_cache(run_id)
            logger.debug("record_event 重复帧已忽略: run=%s seq=%s", run_id, sequence)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))
    _event_persist_failures += 1
    logger.error(
        "record_event 失败（未发布该帧，累计 %d 次）: run=%s seq=%s type=%s err=%s",
        _event_persist_failures, run_id, sequence, etype, last_error,
    )
    raise EventPersistenceError(
        f"Harness event commit unconfirmed: run={run_id} seq={sequence} type={etype}"
    ) from last_error


async def append_run_event(run_id: str, etype: str, data: Optional[dict] = None) -> None:
    """事后补录一条 Run 事件（内部信任路径，不做属主校验）：sequence 取当前最大值+1，
    与并发写入竞争同号时靠 (run_id,sequence) 唯一索引冲突重试取新号。用于僵尸收敛/
    过期清理/启动回填等**没有活动 SSE 通道**的场景把终态事实写进回放日志——否则
    订阅回放永远拿不到终态帧、只能裸 EOF。
    """
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentRun, AgentRunEvent
    for _attempt in range(3):
        try:
            async with factory() as session:
                run = await session.get(AgentRun, run_id, with_for_update=True)
                value = (
                    await session.execute(
                        select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.run_id == run_id)
                    )
                ).scalar_one_or_none()
                session.add(AgentRunEvent(
                    run_id=run_id, event_id=uuid.uuid4().hex,
                    sequence=int(value or 0) + 1,
                    type=etype, data=dict(data or {}),
                ))
                next_sequence = int(value or 0) + 1
                _advance_harness_event_cursor(run, next_sequence)
                await session.commit()
                invalidate_event_payload_cache(run_id)
                return
        except IntegrityError:
            continue  # 与并发帧竞争同一 sequence：重读最大值再试
        except Exception as e:  # noqa: BLE001
            logger.warning("append_run_event(%s) 失败: %s", etype, e)
            return


async def record_terminal_message_event(run_id: str, message_id: int, text: str = "") -> None:
    """中断/取消/对账回填轮补锚（P0 刷新丢失修复）：部分正文落库后补一条带 message_id 的
    message.completed 事件，使 get_execution_traces_by_thread 的归属门能把该 Run 的执行
    轨迹挂到这条消息上——此前用户停止/进程死亡的 Run 整段轨迹因无 message_id 被静默丢弃。
    """
    await append_run_event(run_id, "message.completed",
                           {"text": (text or "")[:20000], "message_id": int(message_id)})
