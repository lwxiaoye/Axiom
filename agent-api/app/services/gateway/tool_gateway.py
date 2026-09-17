"""Tool Gateway（Phase 7，§11）：工具调用的幂等去重 + 敏感工具审批网关。

我方拥有的是网关层（幂等键、审批状态、结果缓存）；业务工具本体由子智能体/其它团队提供，
经 `executor` 回调注入。敏感工具（如提交、支付类）先 needs_approval，用户审批后再执行；
幂等键命中已完成调用直接返回缓存结果，防重放造成重复副作用。
"""
import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session
from app.models import ToolGatewayCall

logger = logging.getLogger(__name__)

Executor = Callable[[], Awaitable[Any]]

# 网络超时/连接失败视为「不确定」而非失败（§11.3）：业务系统可能已成功但响应丢失，
# 直接标 failed + 自动重试会造成重复副作用，故进 unknown，等查询确认后再决定。
_UNKNOWN_EXC = (asyncio.TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)
_UNKNOWN_NOTE = "调用结果未知（业务可能已提交），请先通过查询接口确认状态，勿直接重试以免重复提交。"


def build_idempotency_key(run_id: str, tool_call_id: str, action_version: str = "v1") -> str:
    """§11.3 幂等键规范：task_run_id + tool_call_id + action_version（同一 Run 内工具调用幂等）。"""
    return f"{run_id}:{tool_call_id}:{action_version}"


def _args_hash(args: Any) -> str:
    try:
        raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(args)
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _load(result_json: Optional[str]) -> Any:
    if not result_json:
        return None
    try:
        return json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return result_json


def _serialize_result(result: Any) -> str:
    """Serialize one complete gateway result without slicing through its JSON representation.

    Provider-visible sizing happens later at the Harness projection boundary.  The gateway owns
    idempotent replay, so keeping an invalid 16,000-character JSON fragment here could make a
    repeated side-effecting call return a different type (raw fragment instead of the original
    value) and permanently destroys the only recoverable copy.
    """

    try:
        return json.dumps(result, ensure_ascii=False, default=str, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        # The business action already completed.  Persist a valid, explicit envelope instead of
        # throwing and tempting the caller to execute the side effect again.
        try:
            summary = str(result)[:2_000]
        except Exception:  # noqa: BLE001 - even a hostile __str__ must not trigger a replay
            summary = f"<{type(result).__name__}: unavailable>"
        return json.dumps({
            "kind": "tool_gateway_result",
            "fullAvailable": False,
            "serializationError": type(exc).__name__,
            "summary": summary,
        }, ensure_ascii=False)


def _to_dict(row: ToolGatewayCall) -> Dict[str, Any]:
    return {
        "callId": row.id,
        "idempotencyKey": row.idempotency_key,
        "toolName": row.tool_name,
        "sensitive": bool(row.sensitive),
        "status": row.status,
        "result": _load(row.result_json),
        "error": row.error,
        "approvedBy": row.approved_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


_RUNNING_NOTE = "同一调用正在执行中（并发去重），请稍候按 call_id 查询结果，勿重复提交。"

# 幂等键在表上是**全局唯一**（models.py agent_tool_gateway_call.idempotency_key unique），
# 而键值可由调用方给定（/gateway/execute 直接透传请求体）。命中别人的行必须拒绝：
# 返回其 result_json 等于把他人工具结果泄露给当前用户，抢占 approved/failed 行的执行权
# 更是拿别人的审批放行去执行自己的调用。
# 选择「明确报错」而不是「当作无命中按新调用插入」：唯一索引会让插入直接撞 IntegrityError，
# 也不改键格式（改成带 user_id 前缀会让存量 pending_approval/approved 行全部失配，
# 已审批的敏感调用在用户重试时变成重新申请审批）。
_KEY_CONFLICT_NOTE = "幂等键与其他用户的调用记录冲突，出于安全未执行（不返回他人结果、不抢占执行权）。"


def _conflict(row: ToolGatewayCall, user_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
    """行归属校验：不属于当前用户即按键冲突拒绝（不回 callId，避免泄露他人调用 id）。"""
    if row.user_id == user_id:
        return None
    logger.warning(
        "Tool Gateway 幂等键跨用户冲突 tool=%s key=%s caller=%s owner=%s",
        tool_name, row.idempotency_key, user_id, row.user_id,
    )
    return {"status": "failed", "error": _KEY_CONFLICT_NOTE}


def _by_status(row: ToolGatewayCall, tool_name: str) -> Optional[Dict[str, Any]]:
    """终态/等待态直接按状态返回；返回 None 表示可尝试抢占执行（approved/failed）。"""
    if row.status == "completed":
        return {"status": "completed", "cached": True, "result": _load(row.result_json), "callId": row.id}
    if row.status == "pending_approval":
        return {"status": "needs_approval", "callId": row.id, "toolName": tool_name}
    if row.status == "rejected":
        return {"status": "rejected", "callId": row.id}
    if row.status == "unknown":
        # §11.3：不确定态不自动重试，需先查询业务侧真实状态再显式决定
        return {"status": "unknown", "callId": row.id, "note": _UNKNOWN_NOTE}
    if row.status == "running":
        # 并发同键抢占失败方 / 重放撞上执行中：不重复执行（B6 双副作用防护）
        return {"status": "unknown", "callId": row.id, "note": _RUNNING_NOTE}
    return None  # approved / failed → 可抢占执行


async def _finalize(call_id: str, status: str, *, result_json: Optional[str] = None,
                    error: Optional[str] = None) -> None:
    async with async_session() as session:
        await session.execute(
            update(ToolGatewayCall).where(ToolGatewayCall.id == call_id)
            .values(status=status, result_json=result_json, error=error)
        )
        await session.commit()


async def execute(
    *,
    idempotency_key: str,
    user_id: str,
    tool_name: str,
    args: Any,
    sensitive: bool = False,
    executor: Optional[Executor] = None,
) -> Dict[str, Any]:
    """幂等 + 审批网关执行。

    - 同 idempotency_key 已 completed → 返回缓存结果（cached=True，防重放）。
    - sensitive 且尚未审批 → 建/置 pending_approval，返回 needs_approval + callId（不执行）。
    - 已审批（approved）或非敏感 → 执行 executor，落 completed/failed。

    并发正确性（B6）：执行权在**执行前**持久化——新键 INSERT `running`（唯一键裁决首插竞争）、
    已有行 CAS `approved/failed → running`（一条 UPDATE 只有一个赢家）；没抢到执行权的一方
    按当前状态返回（running → unknown 不确定态，禁重复执行）。原实现「先 executor 后 commit」
    在并发同键时会双执行副作用。running 残留仅出现在进程崩溃窗口，按 unknown 语义查询确认。
    """
    args_hash = _args_hash(args)

    # ---- 第一阶段：抢占执行权（短事务，不跨 executor）----
    call_id: Optional[str] = None
    async with async_session() as session:
        row = (
            await session.execute(
                select(ToolGatewayCall).where(ToolGatewayCall.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

        if row is not None:
            conflict = _conflict(row, user_id, tool_name)
            if conflict is not None:
                return conflict
            settled = _by_status(row, tool_name)
            if settled is not None:
                return settled
            # approved / failed → CAS 抢占（防两个并发都进执行分支）
            won = (
                await session.execute(
                    update(ToolGatewayCall)
                    .where(ToolGatewayCall.id == row.id,
                           ToolGatewayCall.status.in_(("approved", "failed")))
                    .values(status="running")
                )
            ).rowcount == 1
            await session.commit()
            if not won:
                fresh = (
                    await session.execute(
                        select(ToolGatewayCall).where(ToolGatewayCall.id == row.id)
                    )
                ).scalar_one_or_none()
                if fresh is not None:
                    return _by_status(fresh, tool_name) or {
                        "status": "unknown", "callId": fresh.id, "note": _RUNNING_NOTE,
                    }
                return {"status": "failed", "callId": row.id, "error": "调用记录丢失"}
            call_id = row.id
        else:
            row = ToolGatewayCall(
                id=uuid.uuid4().hex,
                idempotency_key=idempotency_key,
                user_id=user_id,
                tool_name=tool_name,
                args_hash=args_hash,
                sensitive=1 if sensitive else 0,
                status="pending_approval" if sensitive else "running",
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # 首插竞争输家：另一并发已建行——重读按其状态走，绝不重复执行
                await session.rollback()
                async with async_session() as s2:
                    fresh = (
                        await s2.execute(
                            select(ToolGatewayCall).where(
                                ToolGatewayCall.idempotency_key == idempotency_key
                            )
                        )
                    ).scalar_one_or_none()
                if fresh is not None:
                    # 首插竞争的赢家可能是**别的用户**（键全局唯一）：同样按键冲突拒绝
                    conflict = _conflict(fresh, user_id, tool_name)
                    if conflict is not None:
                        return conflict
                    return _by_status(fresh, tool_name) or {
                        "status": "unknown", "callId": fresh.id, "note": _RUNNING_NOTE,
                    }
                raise
            if sensitive:
                return {"status": "needs_approval", "callId": row.id, "toolName": tool_name}
            call_id = row.id

    # ---- 第二阶段：执行（已持有执行权，事务不挂着等待业务调用）----
    if executor is None:
        await _finalize(call_id, "failed", error="无执行器（业务工具未接入网关）")
        return {"status": "failed", "callId": call_id, "error": "无执行器（业务工具未接入网关）"}

    try:
        result = await executor()
    except asyncio.CancelledError:
        # CancelledError 继承 BaseException，不会被下面的 except Exception 捕获；若放任其
        # 直接向上穿透，DB 行会永久停在 running（_by_status 对 running 恒判「同调用执行中」，
        # 且只有进程重启的 reconcile_orphan_running 才能把它转出）。用 shield 保护收尾写库
        # 不被同一次取消连带打断，写完（unknown，允许后续查询/对账语义）后原样重抛，
        # 取消语义仍正确传播给调用方。
        try:
            await asyncio.shield(_finalize(call_id, "unknown", error="调用被取消"))
        except BaseException:  # noqa: BLE001
            # 收尾落库若自身也失败（取消撞上二次 DB 故障），不能让新异常顶替原始
            # CancelledError——否则调用方的 task.cancelled() 变 False，取消语义传播断裂
            logger.warning("Tool Gateway 取消收尾落库失败 tool=%s call_id=%s", tool_name, call_id, exc_info=True)
        raise
    except _UNKNOWN_EXC as exc:
        # 超时/连接失败 → unknown（§11.3），不标 failed、不自动重试
        await _finalize(call_id, "unknown", error=f"{type(exc).__name__}: {str(exc)[:400]}")
        logger.warning("Tool Gateway 调用超时进入 unknown tool=%s", tool_name)
        return {"status": "unknown", "callId": call_id, "note": _UNKNOWN_NOTE}
    except Exception as exc:  # noqa: BLE001
        await _finalize(call_id, "failed", error=str(exc)[:512])
        logger.warning("Tool Gateway 执行失败 tool=%s", tool_name, exc_info=True)
        return {"status": "failed", "callId": call_id, "error": str(exc)[:512]}

    # ---- 第三阶段：落结果 ----
    result_json = _serialize_result(result)
    try:
        await _finalize(call_id, "completed", result_json=result_json)
    except Exception:  # noqa: BLE001
        # executor 已真实执行成功，只是收尾落库失败（瞬时故障）——绝不能让异常冒泡给调用方，
        # 否则 model_driver._run_one_tool 会把它误判成「网关不可用」走降级直连，
        # 对有副作用的写工具（execute_in_sandbox/create_file 等）造成二次重复执行。
        # 落库状态留给后台对账补齐，这里仅记录告警。
        logger.warning("Tool Gateway 收尾落库失败 tool=%s call_id=%s", tool_name, call_id, exc_info=True)
    return {"status": "completed", "cached": False, "result": result, "callId": call_id}


async def reconcile_orphan_running() -> int:
    """启动对账（B6 配套）：`running` 残留 = 进程崩溃/重启时 in-flight 的调用——executor 与
    进程同生共死（单实例假设，同 §9.3），重启时一律置 `unknown`（查询确认语义，禁自动重试）。"""
    async with async_session() as session:
        res = await session.execute(
            update(ToolGatewayCall)
            .where(ToolGatewayCall.status == "running")
            .values(status="unknown", error="进程中断，结果未知（启动对账）")
        )
        await session.commit()
        n = int(res.rowcount or 0)
    if n:
        logger.warning("Tool Gateway 启动对账：%d 条 running 残留置 unknown", n)
    return n


async def approve(call_id: str, user_id: str) -> Dict[str, Any]:
    """审批通过（用户对自己的敏感工具调用放行）。放行后由下一次同 key 的 execute 真正执行。"""
    async with async_session() as session:
        row = await session.get(ToolGatewayCall, call_id)
        if not row or row.user_id != user_id:
            return {"status": "not_found"}
        if row.status != "pending_approval":
            return {"status": row.status, "callId": row.id}
        row.status = "approved"
        row.approved_by = user_id
        await session.commit()
        return {"status": "approved", "callId": row.id, "idempotencyKey": row.idempotency_key}


async def reject(call_id: str, user_id: str) -> Dict[str, Any]:
    async with async_session() as session:
        row = await session.get(ToolGatewayCall, call_id)
        if not row or row.user_id != user_id:
            return {"status": "not_found"}
        if row.status != "pending_approval":
            return {"status": row.status, "callId": row.id}
        row.status = "rejected"
        row.approved_by = user_id
        await session.commit()
        return {"status": "rejected", "callId": row.id}


async def supersede_pending(call_id: str, user_id: str) -> Dict[str, Any]:
    """Expire an approval request that the model replaced with a later action batch.

    This is not a user rejection: ``approved_by`` remains empty and ``error`` records the
    lifecycle reason.  Reusing the existing ``rejected`` terminal status deliberately advances
    ``resolve_approval_key`` to a fresh episode if the same dangerous action is requested again.
    """
    async with async_session() as session:
        row = await session.get(ToolGatewayCall, call_id)
        if not row or row.user_id != user_id:
            return {"status": "not_found"}
        if row.status != "pending_approval":
            return {"status": row.status, "callId": row.id}
        row.status = "rejected"
        row.approved_by = None
        row.error = "superseded_by_later_tool_batch"
        await session.commit()
        return {"status": "superseded", "callId": row.id}


# 「审批回合已经了结」的状态——**只有这两个**。落到它们之后再沿用同一个稳定键，
# 这条命令就在本会话里被上一次的判定永久粘住了。
#
# 为什么不含 failed / unknown / running：
# - failed  ：`_by_status` 对它返回 None（可抢占重试）——同一次审批授权下的重试，
#             换键等于把用户刚给的批准作废，还要再问一遍；
# - unknown ：§11.3 明令不确定态不自动重试，必须先查业务侧真实状态。换键就是绕过它；
# - running ：并发去重正靠它，换键会让两条同参调用真的双执行。
_SETTLED_APPROVAL_STATUSES = ("completed", "rejected")


# 同一条危险命令在一个会话里最多分这么多个审批回合。到顶之后不再增长（永远复用最后一个
# 回合的键）——正常用法一轮撑死几次，撞到上限说明模型在刷同一条危险命令，此时"粘住"反而对。
_MAX_APPROVAL_EPISODES = 50


async def resolve_approval_key(base_key: str, user_id: str) -> str:
    """给危险/敏感工具算出本次该用的幂等键：`{base_key}:v{审批回合序号}`。

    键必须**跨 Run 稳定**：审批卡与模型重发之间隔着一整个 Run，不稳定的话用户点了
    「通过」、模型再发一次却建了条新记录，又要审批一次，永远执行不了。

    但稳定只能稳到**这一个审批回合结束为止**（2026-07-28 修）。原实现是一个恒定的
    `:v1`，落到终态还接着用，于是这条命令在本会话里被上一次的判定永久粘住：
    - completed 粘住 → 第二次发同样的危险命令**根本不执行**，直接把上次的 stdout 标
      cached 回给模型，它以为清理成功了，而那些东西还在；
    - rejected  粘住 → 用户拒绝过一次之后该命令在这个会话里**永远**执行不了，回执恒为
      「该操作已被你拒绝，未执行」，还没有任何线索说明这是历史遗留判定，用户改主意也没辙。
    所以了结之后开一个**新回合**（序号 +1）重新走审批：危险动作再来一次本来就该再问一次
    人，而不是拿上一次的判定替用户做决定。不变量「没批准就不执行」丝毫不变。

    `:v1` 与改动前的键格式逐字相同 —— 存量 pending_approval/approved 行照旧命中，
    已审批的敏感调用不会在用户重试时变成重新申请。

    查库失败退回 `:v1`：宁可沿用老行为，也不要因为一次读库抖动凭空开一个新回合。
    """
    if not base_key:
        return ""
    try:
        async with async_session() as session:
            rows = (
                await session.execute(
                    # LIKE 前缀扫（idempotency_key 上有唯一索引）。base_key 里的 `_`
                    # 会被 LIKE 当通配符，但那只会**多**捞几行，下面按精确键取值，
                    # 不影响正确性。
                    select(ToolGatewayCall).where(
                        ToolGatewayCall.idempotency_key.like(f"{base_key}:v%"))
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 网关不可用由 execute() 统一处理，这里只是选键
        logger.warning("查询审批回合状态失败 base=%s", base_key, exc_info=True)
        return f"{base_key}:v1"
    by_key = {r.idempotency_key: r for r in rows if r.user_id == user_id}
    for episode in range(1, _MAX_APPROVAL_EPISODES + 1):
        key = f"{base_key}:v{episode}"
        row = by_key.get(key)
        if row is None or row.status not in _SETTLED_APPROVAL_STATUSES:
            return key
    return f"{base_key}:v{_MAX_APPROVAL_EPISODES}"


async def get_call(call_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    async with async_session() as session:
        row = await session.get(ToolGatewayCall, call_id)
        if not row or row.user_id != user_id:
            return None
        return _to_dict(row)


async def list_calls(user_id: str, limit: int = 50) -> list:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ToolGatewayCall)
                .where(ToolGatewayCall.user_id == user_id)
                .order_by(ToolGatewayCall.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [_to_dict(r) for r in rows]
