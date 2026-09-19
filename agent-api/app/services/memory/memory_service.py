"""长期记忆（§14）。关系库（agent_user_memories）为事实源。

写入策略（§14.3）：validKeys 白名单、写前去重、敏感属性（健康/政治/财务/身份）不入、
用户纠正标 superseded。召回策略（§14.4）：仅当前用户 + active + 未过期，Top-K，注入时
标记「可能过期的用户记忆，与用户当前输入冲突以输入为准」。全部对 Runtime 库未配置降级 no-op。
"""
import asyncio
import hashlib
import json
import logging
import os
import math
import re
import time
import uuid
import weakref
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.model_endpoint import get_model_base_url
from app.core.config import settings
from app.core.runtime_db import runtime_session
from .governance import IDENTITY_INSTRUCTIONS, grounded_quote, memory_identity, sensitive_reason

logger = logging.getLogger(__name__)

# 记忆抽取/摘要都是后台任务，延迟不影响用户感知，但带推理的模型处理上千字提示词
# 加数千字证据时 30s 明显不够（实测 grok-4.6 稳定 ReadTimeout，每轮白耗一次调用）。
# 放宽到 120s；真正卡死仍由外层任务生命周期收敛。
MEMORY_MODEL_TIMEOUT = float(os.environ.get("MEMORY_MODEL_TIMEOUT", "120"))
# 记忆文本 embedding 的内容级 LRU 缓存（按内容哈希）：稳态下每次召回只需为「当前问题」算 1 次，
# 候选记忆向量命中缓存。进程级、重启回暖；embedding 不可用时按关键词相关性召回。
_MEM_VEC_CACHE: "OrderedDict[str, List[float]]" = OrderedDict()
_MEM_VEC_CACHE_MAX = 2000
_RECALL_CANDIDATE_CAP = 200  # 覆盖用户容量上限，旧偏好不因最近新增事实而无法召回


def _cache_get(text: str) -> Optional[List[float]]:
    h = hashlib.sha256(text.encode()).hexdigest()
    vec = _MEM_VEC_CACHE.get(h)
    if vec is not None:
        _MEM_VEC_CACHE.move_to_end(h)
    return vec


def _cache_put(text: str, vec: List[float]) -> None:
    h = hashlib.sha256(text.encode()).hexdigest()
    _MEM_VEC_CACHE[h] = vec
    _MEM_VEC_CACHE.move_to_end(h)
    while len(_MEM_VEC_CACHE) > _MEM_VEC_CACHE_MAX:
        _MEM_VEC_CACHE.popitem(last=False)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0

# §14 记忆类型白名单（validKeys）——白名单外一律拒写，防模型乱写
VALID_TYPES = {"preference", "fact", "skills", "interests", "work_info", "context"}
# 敏感类别（§14.3）：模型推测不得写入
MAX_MEMORIES_PER_USER = 200
RECALL_TOP_K = 5
MEMORY_PROMPT_TOKEN_BUDGET = 2_400
# 偏好类常驻注入上限（Phase 1 双通道）：preference 是「每轮都该生效」的全局设定
# （如「回答简洁」「用中文」），走语义相关性召回会被 RECALL_MIN_SIM 下限误杀
# （实证：「喜欢简洁的回答」vs「帮我写活动通知」余弦仅 0.294 < 0.30 → 召回不到）。
# 故偏好类不做相关性过滤，按新近取上限常驻注入；其余类型仍走语义 Top-K。
PREFERENCE_ALWAYS_CAP = 10
# 线程通道上限（v3.0）：本会话内记忆按新近取 ≤3 条常驻——用户在**当前对话**里让助手
# 记住的内容可能与进行中的任务直接相关，不受全局语义阈值影响
_THREAD_RECALL_CAP = 3
# 召回相关性下限：低于此值的记忆不注入（宁缺毋噪；随 embedding 模型需调）。
# Phase 4 起作用于「余弦 + 关键词加分」的合成相关度，而非裸余弦——实体型查询
# （人名/项目名/ID）纯向量相似度可能偏低，关键词命中可把它拉回下限之上。
RECALL_MIN_SIM = 0.30

# ---- Phase 4 召回综合分 / 重复强化（零新增依赖，纯进程内计算）----
# 关键词通道最高加分：relevance = 余弦 + _KW_BOOST × 查询 gram 覆盖率。
# 只做补强不做主导——0.15 足以救回「余弦 0.25 + 强关键词命中」的实体查询，
# 又不至于让纯字面巧合越过 RECALL_MIN_SIM。
_KW_BOOST = 0.15
# 排序分 = relevance × (0.85+0.15×conf) × (0.85+0.15×fresh)：置信度/新鲜度只做
# 乘性折扣（最多各折 15%），不能把不相关的记忆抬进 Top-K。
_CONF_WEIGHT = 0.15
_FRESH_WEIGHT = 0.15
_AGE_HALF_LIFE_DAYS = 90.0  # 新鲜度指数衰减半衰期
_LN2 = math.log(2)
# 重复强化：相同身份内精确命中既有记忆时 confidence +5（封顶 100）
# 并刷新 updated_at——反复被提起的事实更"牢固"，且免于容量淘汰按旧时间清掉。
_REINFORCE_BUMP = 5
# 轮后自动抽取的记忆是模型推断，置信度低于用户显式 remember/手动添加（100），
# 综合分里天然被降权；被反复强化后可涨回 100。
_EXTRACT_CONFIDENCE = 80

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _grams(text: str) -> set:
    """查询/记忆文本 → 匹配单元集合：ASCII 连串按整词、非 ASCII（中文等）按字符 2-gram
    （单字符串保留自身）。不引分词依赖，60 条候选规模下进程内毫秒级。"""
    text = (text or "").lower()
    grams = set(_ASCII_TOKEN_RE.findall(text))
    for run in re.split(r"[\s\x00-\x7f]+", text):
        if not run:
            continue
        if len(run) == 1:
            grams.add(run)
        for i in range(len(run) - 1):
            grams.add(run[i:i + 2])
    return grams


def _keyword_score(query_grams: set, content: str) -> float:
    """内容对查询 gram 的覆盖率 ∈[0,1]。按查询侧归一：长查询自然稀释单点命中。"""
    if not query_grams:
        return 0.0
    return len(query_grams & _grams(content)) / len(query_grams)


def _is_sensitive(content: str) -> bool:
    return sensitive_reason(content) is not None


# 同 user 记忆写入串行化（store_memory 的「查重 → 插入」临界区）。表上无唯一约束、
# 无行锁，READ COMMITTED 下并发双写会各自查不到对方而双双插入。
# 同 context_service._lock_for：WeakValueDictionary 防进程级无界增长——`async with
# _write_lock_for(u)` 期间调用方栈帧持有强引用（同 user 的并发协程拿到同一把锁），
# 临界区全部退出后自动回收；本函数无 await（字节码原子，无 check-then-create 窗口）。
_write_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _write_lock_for(user_id: str) -> asyncio.Lock:
    lock = _write_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[user_id] = lock
    return lock


# ---- 用户级记忆开关（Phase 2）----
# 进程内 TTL 缓存：避免每轮对话为「是否启用」多打一次 PG。默认启用（缺行=开）。
_ENABLED_CACHE: "OrderedDict[str, tuple]" = OrderedDict()  # user_id -> (enabled, expire_ts)
_ENABLED_TTL_SEC = 60
# 写后竞态哨兵：set_enabled 每次成功提交后 +1。is_enabled 在发起 DB 查询前记下版本号，
# 查询期间若该版本号被 set_enabled 改动过（说明查询读到的是被覆盖前的旧值），则放弃把
# 这个结果写回缓存——否则一条『失效前读到、失效后写入』的陈旧值会在缓存里再存活 60s，
# 绕过用户刚做出的开关操作（复审 P2）。
_ENABLED_GEN: Dict[str, int] = {}



def _loads_json_lenient(content: str):
    """模型的 JSON 输出经常带壳：```json 围栏、一句「以下是抽取结果：」前言、末尾附注。
    先按原样解析，不行就截取第一个 [ / { 到最后一个 ] / } 之间的片段再解析；两次都失败才抛，
    并把开头 80 字符带进异常，日志里不再只有一句 Expecting value (char 0)。"""
    text = str(content or "").strip()
    fenced = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (fenced.find("["), fenced.find("{")) if i >= 0]
    ends = [i for i in (fenced.rfind("]"), fenced.rfind("}")) if i >= 0]
    if starts and ends and max(ends) > min(starts):
        try:
            return json.loads(fenced[min(starts):max(ends) + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"模型输出不是 JSON（开头: {text[:80]!r}）")

async def is_enabled(user_id: str, *, _now: Optional[float] = None) -> bool:
    """当前用户是否开启记忆（默认开）。带 60s 进程内缓存；Runtime 未配置视为开
    （反正 recall/store 自身对未配置降级 no-op，不影响主链路）。"""
    import time as _time
    now = _now if _now is not None else _time.time()
    hit = _ENABLED_CACHE.get(user_id)
    if hit and hit[1] > now:
        _ENABLED_CACHE.move_to_end(user_id)
        return hit[0]
    gen_before = _ENABLED_GEN.get(user_id, 0)  # 查询开始前的版本号，写回缓存前核对
    enabled = True
    factory = runtime_session()
    if factory is not None:
        from app.runtime_models import AgentUserSetting
        try:
            async with factory() as session:
                row = await session.get(AgentUserSetting, user_id)
                if row is not None:
                    enabled = bool(row.memory_enabled)
        except Exception as e:  # noqa: BLE001
            # fail-closed 且**不缓存**：本函数是写入硬门禁的依据，PG 瞬断时若默认放行并
            # 缓存 True 60s，硬门禁在故障窗口会变成敞开（复审 P1）。宁可故障期间记忆功能
            # 短暂停用（召回空/写入拒），恢复后下一次调用即读到真值。
            logger.warning("读取记忆开关失败（fail-closed，不缓存）: %s", e)
            return False
    if _ENABLED_GEN.get(user_id, 0) != gen_before:
        # 查询期间 set_enabled 已经改过版本号：这次读到的值可能是覆盖前的旧值，
        # 不写回缓存，让下一次调用重新查库（避免旧值又在缓存里存活 60s）
        return enabled
    _ENABLED_CACHE[user_id] = (enabled, now + _ENABLED_TTL_SEC)
    _ENABLED_CACHE.move_to_end(user_id)
    while len(_ENABLED_CACHE) > 2000:
        _ENABLED_CACHE.popitem(last=False)
    return enabled


async def set_enabled(user_id: str, enabled: bool) -> bool:
    """设置用户记忆开关。**任何未落库的情况都必须抛**（复审修订）——包括 Runtime 未配置：
    此前未配置/失败也返回用户期望的新值，UI 显示「已保存」但实际没生效（开关可信度问题）。"""
    factory = runtime_session()
    if factory is None:
        raise ValueError("记忆存储未配置，无法保存开关设置")
    from app.runtime_models import AgentUserSetting
    async with factory() as session:
        row = await session.get(AgentUserSetting, user_id)
        if row is None:
            session.add(AgentUserSetting(user_id=user_id, memory_enabled=1 if enabled else 0))
        else:
            row.memory_enabled = 1 if enabled else 0
        await session.commit()
    _ENABLED_GEN[user_id] = _ENABLED_GEN.get(user_id, 0) + 1  # 先推进版本号，供并发 is_enabled 探测竞态
    _ENABLED_CACHE.pop(user_id, None)  # 立即失效缓存，避免旧值 60s 生效滞后
    while len(_ENABLED_GEN) > 5000:  # 版本号表只增不减会缓慢泄漏；淘汰最早写入的条目，
        # 最坏影响是该用户一次并发竞态探测失手（旧值多缓存 60s），可接受
        _ENABLED_GEN.pop(next(iter(_ENABLED_GEN)))
    return enabled


# ---- 「已更新记忆」提示的进程内待推池（Phase 2，best-effort）----
# 抽取在流关闭后才 create_task，本轮 SSE 已关无法推事件；落库成功的新记忆摘要暂存此处，
# 下一轮该用户对话首帧 pop 出来下发 memory.updated chip。进程重启丢失可接受——管理页才是事实源。
_PENDING_UPDATES: "OrderedDict[str, List[str]]" = OrderedDict()
_PENDING_MAX_PER_USER = 10


def _push_pending(user_id: str, summaries: List[str]) -> None:
    if not summaries:
        return
    cur = _PENDING_UPDATES.get(user_id) or []
    cur.extend(summaries)
    _PENDING_UPDATES[user_id] = cur[-_PENDING_MAX_PER_USER:]
    _PENDING_UPDATES.move_to_end(user_id)
    while len(_PENDING_UPDATES) > 5000:
        _PENDING_UPDATES.popitem(last=False)


def pop_pending(user_id: str) -> List[str]:
    """取出并清空某用户待推的新记忆摘要（供下一轮首帧下发 chip）。"""
    return _PENDING_UPDATES.pop(user_id, [])


# ---- 记忆摘要自动刷新（v3.0，best-effort）----
# 用户新增记忆后延迟触发（复刻 ChatGPT「记忆摘要自动更新」）：进程内冷却 6h + 单飞，
# 防每轮触发烧 token；仅当存在「记忆新于现有摘要」的过期缺口才生成。手动「更新摘要」
# 按钮（router /memories/summarize）仍即时生效，不受冷却约束。
_AUTO_SUMMARY_COOLDOWN_SEC = 6 * 3600
_AUTO_SUMMARY_DELAY_SEC = 30
_AUTO_SUMMARY_AT: "Dict[str, float]" = {}          # user_id -> last attempt monotonic
_auto_summarizing: "set[str]" = set()              # 单飞，防并发重复生成
_auto_summary_tasks: "set[asyncio.Task]" = set()   # 强引用防 GC（B4 教训）


async def _summary_is_stale(user_id: str, memories: List[Dict[str, Any]]) -> bool:
    """摘要是否存在过期缺口：无摘要时间，或存在记忆新于摘要时间。

    与前端 MemoryDrawer.vue 的 stale 判定同一口径（summaryAt 早于任一记忆 updated_at）。
    """
    if not memories:
        return False
    newest_at = str(memories[0].get("updated_at") or "")  # list_memories 按 updated_at 倒序
    try:
        from app.services.memory import personalization_service
        cfg = await personalization_service.get_personalization(user_id)
        summary_at = str(cfg.get("memorySummaryAt") or "").strip()
        if not summary_at:
            return True
        try:
            _at = datetime.fromisoformat(summary_at)
            if _at.tzinfo is None:
                from datetime import timezone
                _at = _at.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        if not newest_at:
            return False
        try:
            _new = datetime.fromisoformat(newest_at)
            if _new.tzinfo is None:
                from datetime import timezone
                _new = _new.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        return _new > _at
    except Exception as e:  # noqa: BLE001
        logger.info("摘要 stale 判定失败（按过期处理）: %s", e)
        return True


async def maybe_auto_summarize(
    user_id: str,
    *,
    model: str,
    api_key: str,
    force: bool = False,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
) -> bool:
    """自动刷新记忆摘要（进程内冷却 + 单飞 + 仅 stale；失败静默）。"""
    if not model or not api_key:
        return False
    _now = time.monotonic()
    _last_attempt = _AUTO_SUMMARY_AT.get(user_id)
    # monotonic 从进程启动计时，默认 0 不是“很久以前”：进程运行不足 6h 时
    # now - 0 < cooldown，会把用户的第一次摘要刷新也静默拦掉。
    if (
        not force
        and _last_attempt is not None
        and (_now - _last_attempt) < _AUTO_SUMMARY_COOLDOWN_SEC
    ):
        return False
    if user_id in _auto_summarizing:
        return False
    _auto_summarizing.add(user_id)
    try:
        memories = await list_memories(user_id, limit=500)
        if not memories or not await _summary_is_stale(user_id, memories):
            return False
        summary_kwargs: Dict[str, Any] = {"model": model, "api_key": api_key}
        if run_id or thread_id or root_run_id:
            summary_kwargs.update({
                "run_id": run_id,
                "thread_id": thread_id,
                "root_run_id": root_run_id,
            })
        summary = await generate_summary(user_id, **summary_kwargs)
        if not summary:
            return False
        from datetime import timezone
        from app.services.memory import personalization_service
        await personalization_service.save_personalization(
            user_id, {
                "memorySummary": summary,
                "memorySummaryAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("自动刷新记忆摘要失败（忽略）: %s", e)
        return False
    finally:
        _auto_summarizing.discard(user_id)
        _AUTO_SUMMARY_AT[user_id] = time.monotonic()


def _guarded_auto_summarize(
    user_id: str,
    model: str,
    api_key: str,
    *,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
) -> None:
    """延迟调度自动摘要（持强引用防 GC，B4 教训）。"""
    async def _later() -> None:
        try:
            await asyncio.sleep(_AUTO_SUMMARY_DELAY_SEC)
            summary_kwargs: Dict[str, Any] = {"model": model, "api_key": api_key}
            if run_id or thread_id or root_run_id:
                summary_kwargs.update({
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "root_run_id": root_run_id,
                })
            await maybe_auto_summarize(user_id, **summary_kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
    try:
        task = asyncio.create_task(_later())
        _auto_summary_tasks.add(task)
        task.add_done_callback(_auto_summary_tasks.discard)
    except Exception:  # noqa: BLE001
        pass


# ---- 管理端读写（Phase 2）：全部强制 user_id 归属，Runtime 未配置降级空/no-op ----
async def list_memories(
    user_id: str, mem_type: Optional[str] = None, limit: int = 100, offset: int = 0,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出本人 active 记忆（管理页用），按更新时间倒序。可按 type 过滤、内容搜索、分页。"""
    factory = runtime_session()
    if factory is None:
        return []
    from app.runtime_models import AgentUserMemory
    try:
        async with factory() as session:
            stmt = (
                select(AgentUserMemory)
                .where(AgentUserMemory.user_id == user_id)
                .where(AgentUserMemory.status == "active")
                .order_by(AgentUserMemory.updated_at.desc())
            )
            if mem_type:
                stmt = stmt.where(AgentUserMemory.type == mem_type)
            if query and query.strip():
                # LIKE 通配符转义，避免用户输入 %/_ 变成通配匹配
                escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                stmt = stmt.where(AgentUserMemory.content.ilike(f"%{escaped}%", escape="\\"))
            rows = (await session.execute(
                stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
            )).scalars().all()
        return [{
            "id": r.id,
            "type": r.type,
            "content": r.content,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("list_memories 失败: %s", e)
        return []


async def delete_all_memories(user_id: str) -> int:
    """删除本人全部 active 记忆（标记删，保留审计）。返回删除条数。"""
    factory = runtime_session()
    if factory is None:
        return 0
    from app.runtime_models import AgentUserMemory
    try:
        async with factory() as session:
            rows = (await session.execute(
                select(AgentUserMemory)
                .where(AgentUserMemory.user_id == user_id)
                .where(AgentUserMemory.status == "active")
            )).scalars().all()
            for row in rows:
                row.status = "deleted"
            await session.commit()
            return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_all_memories 失败: %s", e)
        return 0


async def update_memory(
    user_id: str, mem_id: str, *, mem_type: Optional[str] = None, content: Optional[str] = None
) -> Dict[str, Any]:
    """管理页编辑（真 PUT，原地改）。校验失败抛 ValueError（带用户可读信息），不存在抛 KeyError。

    治理口径（复审修订）：
    - **总开关硬门禁**——把旧记忆改成新信息同样是「记录新信息」，关着时 PUT 也不能写；
    - **排除自身的去重**——与**其它** active 记忆内容完全相同时拒绝
      （否则编辑会制造两条重复记忆污染召回）；与**自身**相似是小编辑的常态，正常放行。
      不走「以新代旧」自动替代：手动编辑场景下自动动别的条目容易出人意料，重复即明确拒绝。
    旧的「先加后删」模拟已弃用——相似小编辑会命中旧条又删同条，整条记忆消失。
    """
    if mem_type is not None and mem_type not in VALID_TYPES:
        raise ValueError(f"记忆类型 {mem_type!r} 不合法")
    if content is not None:
        content = content.strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        if _is_sensitive(content):
            raise ValueError("内容含敏感信息（健康/政治/财务/身份），不能保存")
    if not await is_enabled(user_id):
        raise ValueError("记忆功能已关闭，无法修改记忆内容")
    factory = runtime_session()
    if factory is None:
        raise ValueError("记忆存储未配置")
    from app.runtime_models import AgentUserMemory
    async with factory() as session:
        row = await session.get(AgentUserMemory, mem_id)
        if row is None or row.user_id != user_id or row.status != "active":
            raise KeyError("记忆不存在")
        if content is not None:
            # 手动编辑只拒精确重复，不以相似措辞拒绝合法修订。
            peers = (await session.execute(
                select(AgentUserMemory)
                .where(AgentUserMemory.user_id == user_id)
                .where(AgentUserMemory.status == "active")
                .where(AgentUserMemory.id != mem_id)
                .order_by(AgentUserMemory.updated_at.desc())
                .limit(100)
            )).scalars().all()
            for p in peers:
                if p.content == content:
                    raise ValueError("已有一条内容相同的记忆，无需重复保存")
        if mem_type is not None:
            row.type = mem_type
        if content is not None:
            row.content = content
            # Free-text editing cannot silently preserve a model-assigned old fact identity.
            row.structured_value = {"provenance": {"source": "user_edit", "quote": content}}
            row.source_message_ids = None
            row.source_thread_id = None
        row.updated_at = datetime.now()  # 显式刷（字段未变时 onupdate 不触发）
        await session.commit()
        return {
            "id": row.id, "type": row.type, "content": row.content,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


async def delete_memory(user_id: str, mem_id: str) -> bool:
    """删除本人一条记忆（标记删，保留审计）。ACL 强制 user_id 匹配，跨用户删除不生效。"""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentUserMemory
    try:
        async with factory() as session:
            row = await session.get(AgentUserMemory, mem_id)
            if row is None or row.user_id != user_id or row.status == "deleted":
                return False
            row.status = "deleted"
            await session.commit()
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_memory 失败: %s", e)
        return False


# forget 语义匹配删除的判定阈值：Top-1 ≥ 此值直接删，否则列候选让用户确认（宁可多问不误删）
_FORGET_AUTO_SIM = 0.80


async def forget_by_description(user_id: str, description: str) -> Dict[str, Any]:
    """按自然语言描述语义匹配本人 active 记忆并删除（Phase 3，forget_memory 工具用）：

    - Top-1 相似度 ≥ _FORGET_AUTO_SIM → 直接标 deleted，返回 {"deleted": {...}}；
    - 有候选但都不够像 → 返回 {"candidates": [...]} 让调用方（模型）请用户确认，**不删**；
    - 无任何 active 记忆 → {"none": True}。
    embedding 不可用/无 description → 返回候选让用户确认，绝不自动删（避免误删）。
    """
    desc = (description or "").strip()
    factory = runtime_session()
    if factory is None:
        return {"none": True}
    from app.runtime_models import AgentUserMemory
    try:
        now = datetime.now()
        async with factory() as session:
            rows = (await session.execute(
                select(AgentUserMemory)
                .where(AgentUserMemory.user_id == user_id)
                .where(AgentUserMemory.status == "active")
                .order_by(AgentUserMemory.updated_at.desc())
                .limit(_RECALL_CANDIDATE_CAP)
            )).scalars().all()
            candidates = [r for r in rows if not (r.expires_at and r.expires_at < now)]
            if not candidates:
                return {"none": True}

            scored: List[tuple] = []
            if desc:
                vecs = await _embed_many([desc] + [r.content for r in candidates])
                if vecs and vecs[0] is not None:
                    for r, v in zip(candidates, vecs[1:]):
                        if v is not None:
                            scored.append((_cosine(vecs[0], v), r))
                    scored.sort(key=lambda x: x[0], reverse=True)

            if scored and scored[0][0] >= _FORGET_AUTO_SIM:
                target = scored[0][1]
                target.status = "deleted"
                await session.commit()
                return {"deleted": {"id": target.id, "type": target.type, "content": target.content}}

            # 不够像 / 无 embedding：列候选（Top-5）让用户确认，不删
            ordered = [r for _, r in scored] if scored else candidates
            return {"candidates": [
                {"id": r.id, "type": r.type, "content": r.content} for r in ordered[:5]
            ]}
    except Exception as e:  # noqa: BLE001
        logger.warning("forget_by_description 失败: %s", e)
        return {"none": True}


async def _embed_many(
    texts: List[str],
    *,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_purpose_detail: str = "memory_embedding",
) -> Optional[List[Optional[List[float]]]]:
    """带内容级缓存的批量 embedding；平台未配置/失败返回 None（调用方降级）。"""
    try:
        from app.services.knowledge import embedding_service
        cfg = await embedding_service.get_active_embedding_config()
        if not cfg:
            return None
        out: List[Optional[List[float]]] = [_cache_get(t) for t in texts]
        misses = [i for i, v in enumerate(out) if v is None]
        if misses:
            fetched = await embedding_service.embed_texts(
                [texts[i] for i in misses],
                config=cfg,
                return_exceptions=True,
                audit_run_id=audit_run_id,
                audit_thread_id=audit_thread_id,
                audit_root_run_id=audit_root_run_id,
                audit_purpose_detail=audit_purpose_detail,
            )
            for i, vec in zip(misses, fetched):
                if isinstance(vec, Exception):
                    continue
                out[i] = vec
                _cache_put(texts[i], vec)
        return out
    except Exception as e:  # noqa: BLE001
        logger.info("记忆 embedding 批量失败（降级）: %s", e)
        return None


async def user_memory_source(user_id: str, thread_id: str, run_id: str) -> dict:
    """Resolve evidence from the owned user message, never from model-supplied message IDs."""
    if not (user_id and thread_id and run_id):
        return {}
    try:
        from app.core.database import async_session
        from app.models import ChatMessage, ChatThread, live_chat_message_clause
        async with async_session() as session:
            row = (await session.execute(
                select(ChatMessage).join(ChatThread, ChatThread.id == ChatMessage.thread_id)
                .where(ChatThread.user_id == user_id, ChatMessage.thread_id == thread_id,
                       ChatMessage.run_id == run_id, ChatMessage.role == "user",
                       live_chat_message_clause())
                .order_by(ChatMessage.id.asc()).limit(1)
            )).scalars().first()
        if not row:
            return {}
        sources = [{"message_id": row.id, "text": row.content or ""}]
        # Only an owned Run's applied inputs can supply evidence; queued instructions
        # have not taken effect and cannot supersede a prior preference.
        try:
            from app.services.tasks import run_input_service
            for item in await run_input_service.list_for_run(run_id):
                if item.get("status") == "applied" and item.get("content"):
                    sources.append({"message_id": item.get("sourceMessageId"),
                                    "input_id": item.get("id"), "text": item["content"]})
        except Exception:
            logger.info("记忆补充指令来源暂不可读，仅使用已核对原话")
        return {"text": "\n".join(s["text"] for s in sources), "sources": sources}
    except Exception:  # noqa: BLE001 - optional memory must not break a turn
        logger.warning("记忆来源暂不可读，跳过需要该证据的写入")
        return {}


def memory_source_provenance(source: dict, quote: str, *, run_id: str, kind: str) -> tuple[dict, list[int]]:
    records = source.get("sources") or [source]
    matching = [record for record in records if quote in str(record.get("text") or "")]
    message_ids = list(dict.fromkeys(int(r["message_id"]) for r in matching if r.get("message_id")))
    input_ids = list(dict.fromkeys(str(r["input_id"]) for r in matching if r.get("input_id")))
    provenance = {"source": kind, "quote": quote, "run_id": run_id}
    if input_ids:
        provenance["run_input_ids"] = input_ids
    return {"provenance": provenance}, message_ids


async def store_memory(
    *,
    user_id: str,
    mem_type: str,
    content: str,
    source_thread_id: Optional[str] = None,
    source_message_ids: Optional[List[int]] = None,
    confidence: int = 100,
    return_new: bool = False,
    expires_at: Optional[datetime] = None,
    structured_value: Optional[Dict[str, Any]] = None,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
):
    """写入一条长期记忆（§14.3），带写入侧治理：

    1) 精确重复 → **重复强化**（Phase 4）：不再静默跳过，命中条 confidence
       +_REINFORCE_BUMP（封顶 100）且刷新 updated_at——反复被提起的记忆更"牢固"，
       综合分更高、免于容量淘汰；
    2) **范围治理**：只有相同对象、范围和单值属性的 active 记忆才能互相替代；
       缺少身份的旧记忆只做精确去重，不依据文本相似度猜测矛盾；
    3) **容量上限执行**：active 超 MAX_MEMORIES_PER_USER 时按 updated_at 淘汰最旧（标 superseded）。
    已知来源消息较旧的延迟提取不能覆盖较新来源。

    返回：默认返回记忆 id（新建或去重命中的既有 id）；`return_new=True` 时返回
    `(id, is_new)`——is_new 仅在真正新建插入时 True，去重命中/拒写为 False（供调用方区分
    「刚记住」vs「已经记着了」，如 chip 提示、remember_fact 工具回话）。
    """
    def _ret(mid: Optional[str], is_new: bool):
        return (mid, is_new) if return_new else mid

    if mem_type not in VALID_TYPES:
        logger.info("记忆类型 %s 不在白名单，拒写", mem_type)
        return _ret(None, False)
    content = (content or "").strip()
    if not content or _is_sensitive(content):
        return _ret(None, False)
    if structured_value and _is_sensitive(json.dumps(structured_value, ensure_ascii=False)):
        return _ret(None, False)
    identity = memory_identity(structured_value)
    # 总开关硬门禁：UI 承诺「关闭后不再记录新信息」，必须在存储层兜死——
    # 否则 remember_fact 工具/手动添加/NL 更新等任何绕过上层检查的路径都能写入（信任问题）。
    if not await is_enabled(user_id):
        logger.info("用户 %s 已关闭记忆，拒写", user_id)
        return _ret(None, False)
    factory = runtime_session()
    if factory is None:
        return _ret(None, False)
    from app.runtime_models import AgentUserMemory
    try:
        # 写入串行化（按 user_id）：「查重 → 范围治理 → 插入」的读改写，
        # 表上没有唯一约束、事务隔离为 READ COMMITTED，两条并发写（轮后 fire-and-forget
        # 抽取 + remember_fact 工具同轮调用）会各自查不到对方、双双插入 —— 同一事实两条
        # active 记忆污染 recall Top-K，并更快撞 MAX_MEMORIES_PER_USER。
        # PostgreSQL 事务咨询锁覆盖 API/Worker；本地锁也支持隔离 SQLite 测试。
        async with _write_lock_for(user_id):
            async with factory() as session:
                # Serialize across API/Worker processes too. SQLite tests use the local lock.
                bind = session.get_bind() if hasattr(session, "get_bind") else None
                if bind is not None and bind.dialect.name == "postgresql":
                    from sqlalchemy import text
                    lock_key = int.from_bytes(hashlib.sha256(f"user-memory:{user_id}".encode()).digest()[:8], "big", signed=True)
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
                # 1) 精确去重 → 重复强化（Phase 4）：反复被提起的记忆 confidence 上调、
                # updated_at 刷新（confidence 已封顶时行不脏、onupdate 不触发，须显式刷）。
                exact_rows = (await session.execute(
                    select(AgentUserMemory)
                    .where(AgentUserMemory.user_id == user_id)
                    .where(AgentUserMemory.status == "active")
                    .where(AgentUserMemory.content == content)
                )).scalars().all()
                exists = next((row for row in exact_rows
                               if memory_identity(getattr(row, "structured_value", None)) == identity), None)
                if exists:
                    if source_message_ids and max(source_message_ids) < max(exists.source_message_ids or [0]):
                        return _ret(exists.id, False)
                    exists.confidence = min(100, (exists.confidence or 100) + _REINFORCE_BUMP)
                    exists.updated_at = datetime.now()
                    if source_thread_id:
                        exists.source_thread_id = source_thread_id
                    if source_message_ids:
                        exists.source_message_ids = list(dict.fromkeys(
                            [*(exists.source_message_ids or []), *source_message_ids]
                        ))[-32:]
                    if structured_value:
                        exists.structured_value = structured_value
                    await session.commit()
                    return _ret(exists.id, False)

                # Replacement requires the same explicit object/scope/attribute. Similar
                # wording is not evidence of a contradiction, especially across courses.
                peers = []
                if identity is not None:
                    peers = (await session.execute(
                        select(AgentUserMemory)
                        .where(AgentUserMemory.user_id == user_id)
                        .where(AgentUserMemory.status == "active")
                    )).scalars().all()
                if peers:
                    newer = next((p for p in peers if memory_identity(p.structured_value) == identity
                                  and source_message_ids
                                  and max(source_message_ids) < max(p.source_message_ids or [0])), None)
                    if newer:
                        # Return no receipt for a stale value: callers must not claim it was remembered.
                        return _ret(None, False)
                    superseded = 0
                    for p in peers:
                        if memory_identity(p.structured_value) == identity:
                            p.status = "superseded"
                            superseded += 1
                    if superseded:
                        logger.info("记忆治理：%d 条旧记忆被新事实取代（user=%s type=%s）",
                                    superseded, user_id, mem_type)

                mem_id = uuid.uuid4().hex
                session.add(AgentUserMemory(
                    id=mem_id, user_id=user_id, type=mem_type, content=content,
                    source_thread_id=source_thread_id, confidence=confidence,
                    source_message_ids=source_message_ids,
                    sensitivity="normal", status="active", valid_from=datetime.now(),
                    expires_at=expires_at,
                    structured_value=structured_value,
                ))

                # 3) 容量上限：超出按最旧淘汰。offset(MAX) 跳过排名前 MAX（含刚插入的这条），
                # 只把第 MAX+1 名及以后标 superseded，稳态保留 MAX 条（offset(MAX-1) 会多删 1 条）。
                overflow = (await session.execute(
                    select(AgentUserMemory)
                    .where(AgentUserMemory.user_id == user_id)
                    .where(AgentUserMemory.status == "active")
                    .order_by(AgentUserMemory.updated_at.desc())
                    .offset(MAX_MEMORIES_PER_USER)
                )).scalars().all()
                for old in overflow:
                    old.status = "superseded"

                await session.commit()
                return _ret(mem_id, True)
    except Exception as e:  # noqa: BLE001
        logger.warning("store_memory 失败: %s", e)
        return _ret(None, False)


async def recall(
    user_id: str, query: Optional[str] = None, top_k: int = RECALL_TOP_K,
    thread_id: Optional[str] = None,
    *,
    audit_run_id: str = "",
    audit_root_run_id: str = "",
) -> List[Dict[str, Any]]:
    """召回当前用户 active/未过期记忆（§14.4），**三通道**（Phase 1 + v3.0 线程通道）：

    - **常驻通道**：`type='preference'` 的偏好，按新近取 PREFERENCE_ALWAYS_CAP 条，
      **不做相关性过滤**——偏好是「每轮都该生效」的全局设定，走语义召回会被
      RECALL_MIN_SIM 下限误杀（见 PREFERENCE_ALWAYS_CAP 注释的实证）。
    - **线程通道**（v3.0）：`source_thread_id == thread_id` 的会话内记忆，按新近取
      ≤ _THREAD_RECALL_CAP 条——用户在此对话里让助手记住的内容可能与当前任务直接
      相关，不受全局语义阈值影响（`source_thread_id` 抽取时已写入，此前无人消费）。
    - **语义通道**：其余类型（fact/skills/interests/work_info/context）给了 `query`
      且 embedding 可用时按与当前问题相似度取 Top-K（"该用时想起该记住的"），
      无 query / embedding 不可用 / 异常 → 降级回按新近取。

    返回列表中偏好条目在前、线程条目次之、语义条目在后；format_for_prompt 按 source
    分三段渲染（thread 条目带 ``"source": "thread"`` 标记）。
    """
    factory = runtime_session()
    if factory is None:
        return []
    if not await is_enabled(user_id):  # 用户关闭记忆 → 不注入
        return []
    from app.runtime_models import AgentUserMemory
    try:
        now = datetime.now()
        async with factory() as session:
            rows = (await session.execute(
                select(AgentUserMemory)
                .where(AgentUserMemory.user_id == user_id)
                .where(AgentUserMemory.status == "active")
                .order_by(AgentUserMemory.updated_at.desc())
                .limit(_RECALL_CANDIDATE_CAP)
            )).scalars().all()
        candidates = [r for r in rows if not (r.expires_at and r.expires_at < now)
                      and not _is_sensitive(r.content)]
        if not candidates:
            return []

        # 常驻通道：偏好类按新近取上限，不过相关性
        def global_preference(row):
            identity = memory_identity(getattr(row, "structured_value", None))
            return row.type == "preference" and (identity is None or identity[1] in {"global", "全局"})

        prefs = [r for r in candidates if global_preference(r)][:PREFERENCE_ALWAYS_CAP]
        # 语义通道：其余类型按与当前问题相关性排序取 Top-K。
        # query 过短（"继续"/"嗯"这类承接语）没有可用语义，旧行为会按新近盲注 Top-K
        # 条与当前话题无关的记忆——宁缺毋噪，直接关掉本轮语义通道（2026-07-27）。
        others = [r for r in candidates if not global_preference(r)]
        if not query or len(query.strip()) < 6:
            ranked = []
        else:
            ranked = await _rank_by_relevance(
                query,
                others,
                audit_run_id=audit_run_id,
                audit_thread_id=str(thread_id or ""),
                audit_root_run_id=audit_root_run_id,
            )

        # 线程通道：本会话内记忆按新近取，与**实际返回**的常驻/语义条目按内容去重
        #（不能用全量 others 做基准——语义通道关闭（query 过短）时 others 里的线程记忆
        # 并未返回，拿它们当基准会把线程通道误杀成空）
        ranked = ranked[:top_k]
        _seen = {r.content for r in prefs} | {r.content for r in ranked}
        thread_mems: list = []
        if thread_id:
            for r in candidates:
                if r.source_thread_id != thread_id:
                    continue
                if r.content in _seen:
                    continue
                _seen.add(r.content)
                thread_mems.append(r)
                if len(thread_mems) >= _THREAD_RECALL_CAP:
                    break

        def project(row, source=""):
            identity = memory_identity(getattr(row, "structured_value", None))
            item = {"type": row.type, "content": row.content}
            if source:
                item["source"] = source
            if identity:
                item["scope"] = " / ".join(identity)
            return item

        result = [project(r) for r in prefs]
        result += [project(r, "thread") for r in thread_mems]
        result += [project(r, "semantic") for r in ranked]
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("recall 失败: %s", e)
        return []


async def _rank_by_relevance(
    query: Optional[str],
    candidates: list,
    *,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
) -> list:
    """综合分排序（Phase 4）：
    relevance = 余弦 + _KW_BOOST×关键词覆盖率，低于 RECALL_MIN_SIM 过滤（宁缺毋噪）；
    排序分 = relevance × (0.85+0.15×confidence) × (0.85+0.15×新鲜度指数衰减)。
    置信度/新鲜度只折扣不提升——不相关的记忆进不了 Top-K。
    embedding 不可用时只保留关键词匹配项，无 query 时保持新近序。批量 embed，冷启动一次调用。"""
    if not query or not query.strip():
        return candidates
    if not candidates:
        # 没有候选记忆还去给 query 做 embedding，等于每轮白付一次向量服务往返（实测 1.5 s
        # 且占用 prepare_turn 预算）——大多数学生账号根本没有语义记忆。
        return []
    try:
        vecs = await _embed_many(
            [query] + [r.content for r in candidates],
            audit_run_id=audit_run_id,
            audit_thread_id=audit_thread_id,
            audit_root_run_id=audit_root_run_id,
            audit_purpose_detail="memory_recall_ranking",
        )
        if not vecs or vecs[0] is None:
            return [r for r in candidates if _keyword_score(_grams(query), r.content) > 0]
        qvec = vecs[0]
        qgrams = _grams(query)
        now = datetime.now()
        scored = []
        for r, vec in zip(candidates, vecs[1:]):
            if vec is None:
                continue
            relevance = _cosine(qvec, vec) + _KW_BOOST * _keyword_score(qgrams, r.content)
            if relevance < RECALL_MIN_SIM:
                continue
            conf = max(0, min(100, r.confidence if r.confidence is not None else 100)) / 100.0
            if r.updated_at:
                age_days = max(0.0, (now - r.updated_at).total_seconds() / 86400.0)
                fresh = math.exp(-age_days * _LN2 / _AGE_HALF_LIFE_DAYS)
            else:
                fresh = 1.0  # 无时间戳不惩罚
            score = (relevance
                     * (1.0 - _CONF_WEIGHT + _CONF_WEIGHT * conf)
                     * (1.0 - _FRESH_WEIGHT + _FRESH_WEIGHT * fresh))
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]
    except Exception as e:  # noqa: BLE001
        logger.info("记忆语义召回降级为关键词匹配: %s", e)
        return [r for r in candidates if _keyword_score(_grams(query), r.content) > 0]


def format_for_prompt(memories: List[Dict[str, Any]]) -> str:
    """三通道渲染（Phase 1 + v3.0）：偏好段常驻；线程段（本会话让助手记住的）紧贴当前
    任务；语义段按相关性。三段都保留「与本轮明确输入冲突以输入为准、不覆盖权威业务
    数据」的免责口径。"""
    if not memories:
        return ""
    from app.services.platform.token_estimator import estimate_tokens
    bounded = []
    remaining = MEMORY_PROMPT_TOKEN_BUDGET - 400  # section titles and provenance labels
    for memory in memories:
        content = str(memory.get("content") or "")
        if not content or _is_sensitive(content):
            continue
        if memory.get("scope"):
            content = f"[适用范围：{memory['scope']}] {content}"
        cost = estimate_tokens(content) + 16
        if cost <= remaining:
            bounded.append({**memory, "content": content})
            remaining -= cost
    memories = bounded
    prefs = [m for m in memories if m.get("type") == "preference" and not m.get("source")]
    thread_mems = [m for m in memories if m.get("source") == "thread"]
    others = [
        m for m in memories
        if m not in prefs and m.get("source") != "thread"
    ]
    parts: List[str] = []
    if prefs:
        lines = "\n".join(f"- {m['content']}" for m in prefs)
        parts.append(
            # 措辞降压（2026-07-27，治「一条误抽偏好轮轮放大」）：偏好是历史观察到的倾向，
            # 不是每轮铁律——「均应遵循」会让弱模型把一次性要求当终身设定执行，用户体感"怪"。
            "用户在既往对话中表现出的稳定倾向（供参考：与本轮明确要求或当前任务性质不符时，"
            "以本轮为准，不必生搬硬套）：\n" + lines
        )
    if thread_mems:
        lines = "\n".join(f"- {m['content']}" for m in thread_mems)
        parts.append(
            "本会话相关记忆（来自你在此对话中曾让助手记住的内容，可能与当前任务直接相关；"
            "与本轮明确输入冲突时以本轮为准）：\n" + lines
        )
    if others:
        lines = "\n".join(f"- [{m['type']}] {m['content']}" for m in others)
        parts.append(
            "以下是可能与当前问题相关的用户长期记忆（可能过期；与用户本轮明确输入冲突时"
            "以输入为准，不得据此覆盖权威业务数据）：\n" + lines
        )
    return "\n\n".join(parts)


async def _begin_memory_model_audit(
    *,
    purpose: str,
    purpose_detail: str,
    run_id: str,
    thread_id: str,
    root_run_id: str,
    model: str,
    api_key: str,
    payload: Dict[str, Any],
):
    from app.services.agent_harness import model_usage_audit

    if not run_id:
        return model_usage_audit, None, None
    logical = await model_usage_audit.begin_logical_call(
        run_id=str(run_id),
        root_run_id=str(root_run_id or ""),
        thread_id=str(thread_id or ""),
        model=str(model or ""),
        transport="chat_completions",
        purpose=purpose,
        purpose_detail=purpose_detail,
        scope_key=purpose,
        provider_api_key=api_key,
    )
    attempt = await model_usage_audit.begin_attempt(
        logical,
        wire_payload=payload,
        attempt_kind="http_chat",
    )
    return model_usage_audit, logical, attempt


async def _finish_memory_model_audit(
    audit,
    logical,
    attempt,
    *,
    terminal_status: str,
    response_payload: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
    error: Optional[BaseException] = None,
    committed: bool = False,
) -> None:
    response_seen = http_status is not None
    await audit.finish_attempt(
        attempt,
        terminal_status=terminal_status,
        usage=audit.provider_usage_from_response(response_payload or {}),
        response_id=audit.provider_response_id(response_payload or {}),
        provider_event_seen=response_seen,
        terminal_seen=response_seen,
        http_status=http_status,
        error_code=type(error).__name__ if error is not None else "",
        committed=committed,
    )
    await audit.finish_logical_call(
        logical,
        terminal_status=terminal_status,
        selected_attempt_id=(attempt.attempt_id if attempt else ""),
        committed=committed,
    )


async def extract_and_store(
    *,
    user_id: str,
    thread_id: str,
    conversation_text: str,
    model: str,
    api_key: str,
    run_id: Optional[str] = None,
    root_run_id: str = "",
    skill_summary: Optional[str] = None,
    user_text: str = "",
) -> None:
    """对话后异步抽取稳定偏好/事实并入库（§14.3）。失败静默。
    成功入库的新记忆摘要推入待推池，供下一轮首帧下发 memory.updated chip。

    仅用户原话与已应用的补充指令能支撑记忆；助手结论、工具产物不能自证用户偏好。
    conversation_text / skill_summary 保留调用兼容，不作为记忆来源。
    """
    if not conversation_text.strip() or not api_key or not model:
        return
    source = await user_memory_source(user_id, thread_id, str(run_id or ""))
    evidence_text = str(source.get("text") or user_text)
    if not evidence_text:
        # Legacy callers may provide only a transcript. It may contain assistant claims;
        # without an independently supplied user source, do not invent memory provenance.
        return
    if not await is_enabled(user_id):  # 用户关闭记忆 → 不抽取
        return
    # 「自动管理」关闭 → 轮后自动抽取停（治模型过度主动记噪音）；
    # 用户显式 remember_fact 与管理页手动添加不走本函数，不受影响。
    from app.services.memory import personalization_service
    if not await personalization_service.auto_manage_enabled(user_id):
        return
    import httpx
    prompt = (
        "从下面的对话中抽取关于用户的长期偏好或稳定信息，**判定从严**："
        "只记那些**会持续影响你以后如何帮助该用户**的内容（回答风格、称呼偏好、"
        "固定工作流/工具、长期目标、稳定的职业身份等）。"
        "不要记：闲聊中随口带出的一次性背景（如籍贯、当下心情、路过提到的经历）、"
        "当前任务的临时细节、你推测而非用户明说的信息。"
        "**宁缺毋滥——拿不准、或只是普通事实陈述而非会长期用到的偏好，就不记。**"
        "**特例必须记：用户明确推翻/更改了以前的偏好或决定时，把最新状态记为一条**"
        "（写成现行结论，如「用户已放弃X方案，改用Y」），以便覆盖旧记忆；"
        "只输出最新结论，不要把被否决的旧内容单独记成一条。"
        "不要把本轮生成了某个文件当成长期偏好。"
        "忽略个人病史、政治或宗教身份、个人财务数额及联系方式、证件、账号凭据；"
        "普通教学、学科和职业信息不等于个人敏感属性。"
        "只输出 JSON 数组，每项 {\"type\":\"preference|fact|skills|interests|work_info|context\","
        "\"content\":\"...\",\"stability\":\"stable|temporary\","
        "\"grounding\":\"user_stated\",\"source_quote\":\"逐字引用用户原话\","
        "\"identity\":{\"subject\":\"对象\",\"scope\":\"适用范围\",\"attribute\":\"单值属性\"}}；"
        + IDENTITY_INSTRUCTIONS +
        "没有明显值得长期记住的就输出 []。"
    )
    from app.services.agent_harness.compaction_budget import bounded_excerpt
    evidence_input = bounded_excerpt(evidence_text, 4_000)
    wire_payload = {"model": model, "stream": False, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": evidence_input},
    ]}
    audit, logical, attempt = await _begin_memory_model_audit(
        purpose="memory_extract",
        purpose_detail="post_turn_extract",
        run_id=str(run_id or ""),
        thread_id=thread_id,
        root_run_id=root_run_id,
        model=model,
        api_key=api_key,
        payload=wire_payload,
    )
    try:
        response_payload: Dict[str, Any] = {}
        resp = None
        try:
            _endpoint = f"{get_model_base_url().rstrip('/')}/chat/completions"
            logger.info("memory_extract 请求 endpoint=%s model=%s", _endpoint, model)
            async with httpx.AsyncClient(timeout=MEMORY_MODEL_TIMEOUT) as client:
                resp = await client.post(
                    _endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=wire_payload,
                )
            response_payload = resp.json()
            if resp.status_code >= 400:
                await _finish_memory_model_audit(
                    audit, logical, attempt,
                    terminal_status="failed",
                    response_payload=response_payload,
                    http_status=resp.status_code,
                    committed=False,
                )
                return
            if str((response_payload.get("choices") or [{}])[0].get("finish_reason") or "") != "stop":
                await _finish_memory_model_audit(
                    audit, logical, attempt, terminal_status="incomplete",
                    response_payload=response_payload, http_status=resp.status_code, committed=False,
                )
                return
            content = (
                ((response_payload.get("choices") or [{}])[0].get("message") or {}).get("content")
                or "[]"
            )
            items = _loads_json_lenient(content)
        except asyncio.CancelledError:
            await _finish_memory_model_audit(
                audit, logical, attempt,
                terminal_status="cancelled",
                response_payload=response_payload,
                http_status=getattr(resp, "status_code", None),
                committed=False,
            )
            raise
        except Exception as exc:
            await _finish_memory_model_audit(
                audit, logical, attempt,
                terminal_status="failed",
                response_payload=response_payload,
                http_status=getattr(resp, "status_code", None),
                error=exc,
                committed=False,
            )
            raise
        if not isinstance(items, list):
            await _finish_memory_model_audit(
                audit, logical, attempt,
                terminal_status="incomplete",
                response_payload=response_payload,
                http_status=resp.status_code,
                committed=False,
            )
            return
        await _finish_memory_model_audit(
            audit, logical, attempt,
            terminal_status="completed",
            response_payload=response_payload,
            http_status=resp.status_code,
            committed=True,
        )
        from app.services.agent_harness import (
            MemoryCandidate,
            MemoryController,
            MemoryGrounding,
            MemoryStability,
        )

        controller = MemoryController()
        stored: List[str] = []
        for item in items[:10]:
            if isinstance(item, dict):
                text = str(item.get("content") or "").strip()
                quote = grounded_quote(item.get("source_quote"), evidence_input)
                if not grounded_quote(quote, evidence_text) or item.get("grounding") != "user_stated":
                    continue
                metadata, source_ids = memory_source_provenance(
                    source, quote, run_id=str(run_id or ""), kind="user_stated",
                )
                if memory_identity({"identity": item.get("identity")}):
                    metadata["identity"] = item["identity"]
                try:
                    candidate = MemoryCandidate(
                        candidate_id=uuid.uuid4().hex,
                        user_id=user_id,
                        memory_type=str(item.get("type") or ""),
                        content=text,
                        grounding=MemoryGrounding(str(item.get("grounding") or "inferred")),
                        stability=MemoryStability(str(item.get("stability") or "temporary")),
                        source_thread_id=thread_id,
                        confidence=_EXTRACT_CONFIDENCE,
                    )
                except Exception:  # noqa: BLE001
                    continue

                is_new = False

                async def _write(approved: MemoryCandidate) -> Optional[str]:
                    nonlocal is_new
                    mid, is_new = await store_memory(
                        user_id=approved.user_id,
                        mem_type=approved.memory_type,
                        content=approved.content,
                        source_thread_id=approved.source_thread_id,
                        source_message_ids=source_ids or None,
                        structured_value=metadata,
                        return_new=True,
                        confidence=approved.confidence,
                        audit_run_id=str(run_id or ""),
                        audit_thread_id=thread_id,
                        audit_root_run_id=root_run_id,
                    )
                    return mid

                decision = await controller.commit(candidate, _write)
                if decision.accepted and is_new and text:
                    stored.append(text)  # 仅真正新建的入待推池，去重命中不出 chip
        _push_pending(user_id, stored)
        # v3.0 摘要自动刷新：落库新记忆后延迟触发（冷却 + 单飞 + stale 判定在函数内）
        if stored:
            try:
                if run_id or thread_id or root_run_id:
                    _guarded_auto_summarize(
                        user_id,
                        model=model,
                        api_key=api_key,
                        run_id=str(run_id or ""),
                        thread_id=thread_id,
                        root_run_id=root_run_id,
                    )
                else:
                    _guarded_auto_summarize(user_id, model=model, api_key=api_key)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        # TimeoutError 一类异常的 str(e) 是空串，只打 %s 等于没有信息——排查时
        # 完全看不出是超时、连错地址还是鉴权失败。带上类型与目标端点。
        logger.info(
            "记忆抽取失败（不影响对话）: %s: %s [endpoint=%s]",
            type(e).__name__, e, get_model_base_url(),
        )


async def generate_summary(
    user_id: str,
    *,
    model: str,
    api_key: str,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
) -> str:
    """把用户全部 active 记忆交给模型生成分区摘要（复刻 ChatGPT「记忆摘要」）。

    输出纯文本：首段「概览」+ 按主题分区（每区一行标题 + 一段描述），供管理页展示。
    记忆为空或调用失败返回空串（前端显示空态/错误提示）。
    """
    memories = await list_memories(user_id, limit=500)
    if not memories:
        return ""
    lines = "\n".join(f"- [{m['type']}] {m['content']}" for m in memories)
    prompt = (
        "下面是从与用户的历史对话中记录的长期记忆条目。请把它们整理成一份「记忆摘要」，"
        "以第二人称（\"你…\"）描述用户。要求：\n"
        "1. 第一节标题固定为「概览」，用两三句话概括用户的总体关注点；\n"
        "2. 之后按主题聚类分节（如兴趣方向、项目、工作、学习、偏好等，按实际内容起名），"
        "每节一个短标题，标题独占一行，下面跟一段连贯描述；\n"
        "3. 只依据给出的记忆条目，不得虚构；相互矛盾的以更近期的为准；\n"
        "4. 输出纯文本（标题行不加 #/* 等符号），不要开场白和结尾寒暄。"
    )
    import httpx
    wire_payload = {"model": model, "stream": False, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": lines[:8000]},
    ]}
    audit, logical, attempt = await _begin_memory_model_audit(
        purpose="memory_summary",
        purpose_detail="memory_profile_summary",
        run_id=run_id,
        thread_id=thread_id,
        root_run_id=root_run_id,
        model=model,
        api_key=api_key,
        payload=wire_payload,
    )
    response_payload: Dict[str, Any] = {}
    resp = None
    try:
        async with httpx.AsyncClient(timeout=MEMORY_MODEL_TIMEOUT) as client:
            resp = await client.post(
                f"{get_model_base_url().rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=wire_payload,
            )
        response_payload = resp.json()
        if resp.status_code >= 400:
            await _finish_memory_model_audit(
                audit, logical, attempt,
                terminal_status="failed",
                response_payload=response_payload,
                http_status=resp.status_code,
                committed=False,
            )
            return ""
        content = (
            ((response_payload.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()[:8000]
        terminal_status = "completed" if content else "incomplete"
        await _finish_memory_model_audit(
            audit, logical, attempt,
            terminal_status=terminal_status,
            response_payload=response_payload,
            http_status=resp.status_code,
            committed=bool(content),
        )
        return content
    except asyncio.CancelledError:
        await _finish_memory_model_audit(
            audit, logical, attempt,
            terminal_status="cancelled",
            response_payload=response_payload,
            http_status=getattr(resp, "status_code", None),
            committed=False,
        )
        raise
    except Exception as e:  # noqa: BLE001
        await _finish_memory_model_audit(
            audit, logical, attempt,
            terminal_status="failed",
            response_payload=response_payload,
            http_status=getattr(resp, "status_code", None),
            error=e,
            committed=False,
        )
        logger.warning("生成记忆摘要失败: %s", e)
        return ""


async def update_from_text(user_id: str, text: str, *, model: str, api_key: str) -> List[str]:
    """自然语言「添加或更新」记忆（摘要页底部输入框）：走与轮后抽取同一套抽取管线，
    抽不出结构化条目时整句按 fact 兜底入库（仍受治理管线约束）。返回入库内容列表。"""
    text = (text or "").strip()
    if not text:
        return []
    import httpx
    prompt = (
        "用户想让你记住/更新下面这句话里的信息。请抽取为长期记忆条目，"
        "只输出 JSON 数组，每项 {\"type\":\"preference|fact|skills|interests|work_info|context\","
        "\"content\":\"...\",\"source_quote\":\"逐字引用用户原话\",\"identity\":{"
        "\"subject\":\"对象\",\"scope\":\"适用范围\",\"attribute\":\"单值属性\"}}；"
        + IDENTITY_INSTRUCTIONS +
        "内容用第三人称客观陈述（如「用户偏好…」）；没有可记的就输出 []。"
    )
    items: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=MEMORY_MODEL_TIMEOUT) as client:
            resp = await client.post(
                f"{get_model_base_url().rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "stream": False, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:1000]},
                ]},
            )
        content = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "[]"
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)
        if isinstance(parsed, list):
            items = [x for x in parsed if isinstance(x, dict)]
    except Exception as e:  # noqa: BLE001
        logger.info("自然语言记忆抽取失败，整句兜底: %s", e)
    if not items:
        items = [{"type": "fact", "content": text, "source_quote": text}] if len(text) <= 1000 else []
    stored: List[str] = []
    for item in items[:10]:
        content_text = str(item.get("content") or "").strip()
        quote = grounded_quote(item.get("source_quote"), text)
        if not quote:
            continue
        metadata = {"provenance": {"source": "user_requested", "quote": quote}}
        if memory_identity({"identity": item.get("identity")}):
            metadata["identity"] = item["identity"]
        mid, _ = await store_memory(
            user_id=user_id, mem_type=str(item.get("type") or "fact"),
            content=content_text, return_new=True,
            structured_value=metadata,
        )
        if mid and content_text:
            stored.append(content_text)
    return stored
