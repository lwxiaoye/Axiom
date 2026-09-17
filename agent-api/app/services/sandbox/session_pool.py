"""Run 级沙箱会话池（2026-07-22 用户拍板：从「每次 execute_in_sandbox 全新沙箱」改为「一个任务共用一个沙箱」）。

**为什么改**：execute_in_sandbox 原本 create→run→delete，任何一次重试都要从零重跑整条管线
（PPT 场景=重新生成全部 SVG→重新转换→重新渲染检查，只为改一个报错），中间产物全丢，
提示词只能反过来逼模型写「自包含巨型脚本」——其中任何一步炸掉整轮就作废。

**作用域=一个 Run**，不是整个会话：
- 主对话：一条用户消息的整个工具循环（key=run_id）；
- 任务模式：一个 graph 节点的 agentic 闭环（key=graph_id:node_key）——**必须按节点分**，
  同图的并行节点若共用一个容器会互相覆盖 /workspace/__main__.py 并共享 outputs/。
跨 Run 复用的收益已被「我的文件」覆盖，代价（跨 worker 拿不到句柄、长 TTL、状态污染）陡增，不做。

**三层回收**（少一层就是容器泄漏到进程重启）：
1. 显式关闭：run_hub 的 Run 收尾 / scheduler 的节点收尾，正常/取消/异常都走 finally；
2. 空闲 TTL + 硬生命周期上限：后台 reaper 巡检（进程还活着但显式关闭漏了）；
3. 容器自毁：local provider 的 entrypoint 不再是 `sleep infinity` 而是有限秒数
   （见 local_adapter._container_sleep_seconds）——整个进程被 kill 时也不会留下长驻容器。

**存活上限独立于执行并发闸**：SKILL_SANDBOX_MAX_CONCURRENT 管「同时在执行的调用数」，
本模块的 SANDBOX_SESSION_MAX_LIVE 管「同时存活的容器数」。二者共用一个名额的话，20 个
并发会话就会把闸打满、第 21 个排队 60s 后硬失败。满员时优先驱逐最久未用的空闲会话；
全部在忙则短等空闲会话；仍满才降级为一次性沙箱。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings

from .base import SandboxAdapter
from .factory import create_configured_sandbox

logger = logging.getLogger(__name__)

# 当前协程所属的沙箱作用域。任务模式的每个节点 worker 是独立 asyncio Task，在其中 set()
# 只影响该 Task 自己的上下文，天然按节点隔离；主对话不设置，由 execute_in_sandbox 用 run_id 兜底。
_scope: contextvars.ContextVar[str] = contextvars.ContextVar("sandbox_session_scope", default="")


def set_scope(key: str):
    """进入一个沙箱作用域，返回 token（务必在 finally 里 reset_scope）。"""
    return _scope.set(str(key or ""))


def reset_scope(token) -> None:
    try:
        _scope.reset(token)
    except (ValueError, LookupError):  # 跨 Task reset：忽略即可，Task 结束时上下文自然销毁
        pass


def current_scope() -> str:
    return _scope.get("")


@dataclass
class SandboxSession:
    key: str
    sandbox: SandboxAdapter
    lock: asyncio.Lock          # 同一会话内的调用串行化（防两次调用互相覆盖 __main__.py）
    created_at: float
    last_used_at: float
    users: int = 0              # 正在持有/等待本会话的调用数：>0 不可驱逐、不可回收
    started: bool = False       # sandbox.create() 已成功（决定回执/进度文案说“创建”还是“复用”）
    written_skills: set = field(default_factory=set)  # 已写入容器的技能包 slug（同 Run 不重复传）
    # 已镜像进容器 /workspace/files 的用户文件：{相对路径: 内容 sha256}。
    # 与 written_skills 同一个目的、同一种做法：**同一 Run 内不重复搬同样的字节**。
    # 技能包早就有这条去重，用户文件一直没有——于是每次 bash 都把整个文件区重读一遍、
    # 再逐个 put_archive 进容器，实测约 0.3 秒/文件，200 个文件上限外推 60s/次。
    # 键是内容摘要而不是版本号/时间戳：文件区的行字典拿不到可靠的版本标记（只有 size 和
    # createdAt），内容寻址是唯一能**证明**「容器里那份和库里这份一模一样」的判据。
    # 挂在 session 上而不是模块级 dict，理由同 written_skills：会话被丢弃（探活失败/池满
    # 驱逐/TTL）时这份记账必须跟着一起没，否则新容器里什么都没有却以为已经传过。
    workspace_mirror: dict = field(default_factory=dict)
    # Runtime capability preflight is performed once after a session starts.
    # Cache both success and a capability gap so a missing component does not
    # cause every later model turn to probe the same environment again.
    runtime_preflight: dict = field(default_factory=dict)
    jobs: dict = field(default_factory=dict)
    last_job_id: str = ""
    closed: bool = False


_sessions: dict[str, SandboxSession] = {}
_pool_lock = asyncio.Lock()
_reaper: Optional[asyncio.Task] = None
# 后台销毁任务的强引用（裸 create_task 会被 GC 静默丢弃，同 run_hub._spawn_bg 的教训）
_bg_tasks: set = set()


def _spawn_close(session: SandboxSession) -> None:
    try:
        task = asyncio.create_task(_snapshot_staging_then_close(session))
    except RuntimeError:  # 无事件循环（同步上下文）：交给 reaper/进程退出兜底
        return
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _enabled() -> bool:
    return bool(getattr(settings, "SANDBOX_SESSION_REUSE_ENABLED", False))


def _max_live() -> int:
    return max(1, int(getattr(settings, "SANDBOX_SESSION_MAX_LIVE", 24) or 24))


def _busy_wait_s() -> float:
    return max(0.0, float(getattr(settings, "SANDBOX_SESSION_BUSY_WAIT_S", 8) or 0))


async def acquire(key: str) -> Optional[SandboxSession]:
    """取（必要时新建）一个作用域会话；返回 None 表示调用方应退回一次性沙箱。

    只登记会话对象，**不**在这里 create() 容器——创建可能耗时数秒，不能占着池锁；
    调用方对 sandbox.create() 的调用本来就是幂等的（各 adapter 已就绪即直接返回）。
    """
    if not _enabled() or not key:
        return None
    deadline = time.monotonic() + _busy_wait_s()
    while True:
        async with _pool_lock:
            session = _sessions.get(key)
            if session is not None and not session.closed:
                session.users += 1
                session.last_used_at = time.monotonic()
                return session
            if len(_sessions) < _max_live():
                now = time.monotonic()
                session = SandboxSession(
                    key=key, sandbox=create_configured_sandbox(f"execute_in_sandbox:{key}"),
                    lock=asyncio.Lock(), created_at=now, last_used_at=now, users=1,
                )
                _sessions[key] = session
                _ensure_reaper()
                return session
            victim = _pick_victim_locked()
            if victim is not None:
                _sessions.pop(victim.key, None)
                victim.closed = True
                _spawn_close(victim)
                now = time.monotonic()
                session = SandboxSession(
                    key=key, sandbox=create_configured_sandbox(f"execute_in_sandbox:{key}"),
                    lock=asyncio.Lock(), created_at=now, last_used_at=now, users=1,
                )
                _sessions[key] = session
                _ensure_reaper()
                return session
            if time.monotonic() >= deadline:
                logger.info("沙箱会话池已满（%d 个全部在执行中），本次调用降级为一次性沙箱", len(_sessions))
                return None
        await asyncio.sleep(0.2)


def _is_evictable_locked(session: SandboxSession) -> bool:
    """可驱逐：无人持有、无未收集后台作业。jobs 是 users 记账的保险——后台启动后
    调用方会 release() 掉 acquire 名额，若没有作业持有，池满会把还在跑的容器拆掉。"""
    return session.users <= 0 and not session.closed and not session.jobs


def _pick_victim_locked() -> Optional[SandboxSession]:
    """最久未用的空闲会话（users==0 且无后台作业）；全在忙则 None。"""
    idle = [s for s in _sessions.values() if _is_evictable_locked(s)]
    return min(idle, key=lambda s: s.last_used_at) if idle else None


def retain_job(session: Optional[SandboxSession], job_id: str, meta: Optional[dict] = None) -> None:
    """后台作业额外占一个 users 名额，直到收集或取消完成。同一 job_id 不重复占。"""
    if session is None or not job_id:
        return
    if job_id not in session.jobs:
        session.users += 1
    session.jobs[job_id] = dict(meta or {})
    session.last_job_id = job_id
    session.last_used_at = time.monotonic()


def release_job(session: Optional[SandboxSession], job_id: str) -> None:
    """收集/失败收尾后放下作业持有。未登记的 job_id 是 no-op。"""
    if session is None or not job_id:
        return
    if job_id not in session.jobs:
        return
    session.jobs.pop(job_id, None)
    if session.last_job_id == job_id:
        session.last_job_id = next(iter(session.jobs), "")
    release(session)


def release(session: Optional[SandboxSession]) -> None:
    """归还会话（不销毁容器）——调用结束时必调，否则该会话永不可被驱逐/回收。

    会话若已被摘除（超硬生命周期 / close_scope 撞上执行中），最后一个使用者归还时补销毁：
    回收决策在摘除时就已作出，容器不能因为“当时正忙”而永远没人收。
    """
    if session is None:
        return
    session.users = max(0, session.users - 1)
    session.last_used_at = time.monotonic()
    if session.closed and session.users == 0:
        _spawn_close(session)


def has_live_session(key: str) -> bool:
    """True when this Run already has a reusable sandbox (do not create one)."""
    return peek(key) is not None


def peek(key: str) -> Optional[SandboxSession]:
    """Return the live session for this key, or None. Does not create or retain a user."""
    if not key:
        return None
    session = _sessions.get(key)
    if session is None or session.closed:
        return None
    return session


async def discard(session: Optional[SandboxSession]) -> None:
    """会话不可用（容器已被外部清掉/create 失败）：摘除并销毁，调用方改用一次性沙箱。"""
    if session is None:
        return
    async with _pool_lock:
        if _sessions.get(session.key) is session:
            _sessions.pop(session.key, None)
    session.closed = True
    await _close_sandbox(session)


async def close_scope(key: str) -> int:
    """关闭某作用域及其子作用域（`key` 与 `key:*`）。返回关闭数量。

    子作用域前缀匹配用于「主对话 Run 收尾时顺带兜掉该 Run 名下的派生作用域」；
    任务模式的节点作用域自己在节点收尾时关，不依赖这里。
    """
    if not key:
        return 0
    prefix = f"{key}:"
    async with _pool_lock:
        victims = _retire_locked([
            s for k, s in list(_sessions.items())
            if k == key or k.startswith(prefix)
        ])
    # 仍在执行中的（取消恰好撞上一次 execute_in_sandbox）不能当场拆容器，交由 release() 补销毁
    for s in victims:
        if s.users <= 0:
            await _close_sandbox(s)
    return len(victims)


async def close_all() -> int:
    """进程关停时清干净（lifespan shutdown 调用）。"""
    global _reaper
    async with _pool_lock:
        victims = _retire_locked(list(_sessions.values()))
    for s in victims:
        await _close_sandbox(s)  # 进程要退了：忙的也一并拆，否则容器留到自毁才消失
    if _reaper is not None:
        _reaper.cancel()
        _reaper = None
    return len(victims)


def _retire_locked(victims: list) -> list:
    """从注册表摘除并标记关闭（须持 _pool_lock）。摘除后不会再被任何新调用复用。"""
    for s in victims:
        _sessions.pop(s.key, None)
        s.closed = True
    return victims


async def _snapshot_staging_then_close(session: SandboxSession) -> None:
    """Pack unpublished PPTD staging before the container disappears."""
    try:
        from app.services.agent_harness.artifact_checkpoint import (
            capture_ppt_staging,
            run_owner,
        )

        uid, tid = await run_owner(session.key)
        if uid:
            await capture_ppt_staging(
                run_id=session.key,
                thread_id=tid,
                user_id=uid,
                session=session,
                force=True,
            )
    except Exception as e:  # noqa: BLE001 打包失败仍必须拆容器，否则泄漏
        logger.info("PPT staging capture before sandbox close skipped key=%s: %s", session.key, e)
    await _close_sandbox(session)


async def _close_sandbox(session: SandboxSession) -> None:
    try:
        await session.sandbox.delete()
    except Exception as e:  # noqa: BLE001 销毁失败只记日志：容器还有 entrypoint 到点自毁兜底
        logger.warning("沙箱会话销毁失败 key=%s: %s", session.key, e)


def _ensure_reaper() -> None:
    global _reaper
    if _reaper is None or _reaper.done():
        try:
            _reaper = asyncio.create_task(_reap_loop())
        except RuntimeError:  # 无运行中的事件循环（同步测试上下文）：跳过，显式关闭仍有效
            _reaper = None


async def _reap_loop() -> None:
    interval = max(5, int(getattr(settings, "SANDBOX_SESSION_REAP_INTERVAL_S", 60) or 60))
    while True:
        try:
            await asyncio.sleep(interval)
            await reap_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 巡检自身不能把自己搞挂
            logger.warning("沙箱会话巡检异常（忽略继续）: %s", e)


async def reap_once() -> int:
    """回收空闲超时 / 超硬生命周期的会话。返回回收数量（测试可直接调）。"""
    idle_ttl = max(30, int(getattr(settings, "SANDBOX_SESSION_IDLE_TTL_S", 900) or 900))
    max_life = max(60, int(getattr(settings, "SANDBOX_SESSION_MAX_LIFETIME_S", 3600) or 3600))
    now = time.monotonic()
    async with _pool_lock:
        expired = [
            s for s in _sessions.values()
            # 正在执行中或仍有未收集后台作业的会话：不因空闲 TTL 抽走容器。
            # 硬生命周期仍收无作业的会话；有作业的交给作业持有 / 容器自毁。
            if not s.jobs and (
                (s.users <= 0 and now - s.last_used_at > idle_ttl)
                or now - s.created_at > max_life
            )
        ]
        victims = _retire_locked(expired)
    for s in victims:
        logger.info(
            "回收沙箱会话 key=%s（存活 %.0fs，空闲 %.0fs，使用中 %d）",
            s.key, now - s.created_at, now - s.last_used_at, s.users,
        )
        if s.users <= 0:
            await _snapshot_staging_then_close(s)  # 忙的已摘除注册表，最后一个使用者归还时由 release() 收
    return len(victims)


def live_count() -> int:
    return len(_sessions)
