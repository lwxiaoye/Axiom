"""用户个性化设置服务（复刻 ChatGPT Personalization，2026-07-10）。

三块能力：
- 「关于你」（昵称/职业/详情）+ 自定义指令 → prompt_block 注入系统提示词；
- autoManage 记忆自动管理开关：关闭后轮后自动抽取（extract_and_store）停，
  用户显式的 remember_fact 与手动添加不受影响；
- 读写走独立新表 agent_user_personalization（JSON 单列，create_all 零迁移）。

与 memory_service 的 _ENABLED_CACHE 同思路做 60s 进程内 TTL 缓存：prompt_block
在每轮对话热路径上，不能每轮多一次 DB 往返。
"""
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Tuple

from app.core.runtime_db import runtime_session

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "autoManage": True,        # 记忆自动管理：关=轮后自动抽取停（显式 remember/手动添加仍可用）
    "nickname": "",            # 希望被如何称呼
    "occupation": "",          # 职业
    "about": "",               # 详情：兴趣、价值观、偏好等自述
    "customInstructions": "",  # 自定义指令：行为/风格/语调偏好
    "memorySummary": "",       # 模型生成的记忆摘要（管理页展示，不注入 prompt）
    "memorySummaryAt": "",     # 摘要生成时间 ISO 串
}
_LIMITS = {
    "nickname": 40, "occupation": 60, "about": 600, "customInstructions": 1500,
    "memorySummary": 8000, "memorySummaryAt": 40,
}

_CACHE: "OrderedDict[str, Tuple[Dict[str, Any], float]]" = OrderedDict()
_CACHE_TTL_SEC = 60
_CACHE_MAX = 2000
# 写后竞态哨兵（与 memory_service._ENABLED_GEN 同思路）：save_personalization 每次成功
# 提交后 +1。get_personalization 在发起 DB 查询前记下版本号，查询期间若版本号被改动过
# （说明读到的是覆盖前的旧值），放弃写回缓存，避免旧值又在缓存里存活 60s。
_CACHE_GEN: Dict[str, int] = {}


def _normalize(stored: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in stored:
            continue
        if key == "autoManage":
            merged[key] = bool(stored[key])
        else:
            merged[key] = str(stored[key] or "").strip()[: _LIMITS[key]]
    return merged


async def get_personalization(user_id: str) -> Dict[str, Any]:
    """读取用户个性化设置（缺省补齐）。Runtime 未配置返回默认值。"""
    now = time.time()
    hit = _CACHE.get(user_id)
    if hit and hit[1] > now:
        _CACHE.move_to_end(user_id)
        return dict(hit[0])

    gen_before = _CACHE_GEN.get(user_id, 0)  # 查询开始前的版本号，写回缓存前核对
    data = dict(DEFAULTS)
    factory = runtime_session()
    if factory is not None:
        from app.runtime_models import AgentUserPersonalization
        try:
            async with factory() as session:
                row = await session.get(AgentUserPersonalization, user_id)
            if row is not None and row.config_json:
                parsed = json.loads(row.config_json)
                if isinstance(parsed, dict):
                    data = _normalize(parsed)
        except Exception as e:  # noqa: BLE001
            logger.info("读取个性化设置失败（用默认值）: %s", e)

    if _CACHE_GEN.get(user_id, 0) != gen_before:
        # 查询期间 save_personalization 已经改过版本号：这次读到的值可能是覆盖前的
        # 旧值，不写回缓存，让下一次调用重新查库
        return dict(data)
    _CACHE[user_id] = (data, now + _CACHE_TTL_SEC)
    _CACHE.move_to_end(user_id)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return dict(data)


async def save_personalization(user_id: str, incoming: Dict[str, Any]) -> Dict[str, Any]:
    """保存（仅覆盖提交的已知字段，长度裁剪）。返回保存后的完整配置。

    **未真正落库必须抛**（复审修订，与 memory_service.set_enabled 同口径）：此前 Runtime
    未配置或写库异常都会静默吞掉、仍返回合并后的新值，路由层原样 200 回给前端，UI 显示
    「已保存」但配置实际没落库——下次读到的还是旧值。抛出后由路由层转 500，不再伪装成功。
    """
    current = await get_personalization(user_id)
    for key in DEFAULTS:
        if key not in incoming:
            continue
        if key == "autoManage":
            current[key] = bool(incoming[key])
        else:
            current[key] = str(incoming[key] or "").strip()[: _LIMITS[key]]

    factory = runtime_session()
    if factory is None:
        raise ValueError("个性化设置存储未配置，无法保存")
    from app.runtime_models import AgentUserPersonalization
    try:
        async with factory() as session:
            row = await session.get(AgentUserPersonalization, user_id)
            payload = json.dumps(current, ensure_ascii=False)
            if row is None:
                session.add(AgentUserPersonalization(user_id=user_id, config_json=payload))
            else:
                row.config_json = payload
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("保存个性化设置失败: %s", e)
        raise
    _CACHE_GEN[user_id] = _CACHE_GEN.get(user_id, 0) + 1  # 先推进版本号，供并发 get_personalization 探测竞态
    _CACHE.pop(user_id, None)  # 立即失效，避免旧值 60s 滞后
    while len(_CACHE_GEN) > 5000:  # 版本号表只增不减会缓慢泄漏；淘汰最早条目，最坏影响
        # 是该用户一次竞态探测失手（旧值多缓存 60s），可接受
        _CACHE_GEN.pop(next(iter(_CACHE_GEN)))
    return current


async def auto_manage_enabled(user_id: str) -> bool:
    """记忆自动管理是否开启（默认开）。供 extract_and_store 门禁。"""
    conf = await get_personalization(user_id)
    return bool(conf.get("autoManage", True))


async def prompt_block(user_id: str) -> str:
    """渲染注入系统提示词的个性化段；全空返回空串。

    自定义指令带「不得违反系统与安全约束」的口径，防止用户指令越权覆盖平台规则。
    """
    conf = await get_personalization(user_id)
    profile_lines = []
    if conf["nickname"]:
        profile_lines.append(f"- 称呼用户为：{conf['nickname']}")
    if conf["occupation"]:
        profile_lines.append(f"- 用户职业：{conf['occupation']}")
    if conf["about"]:
        profile_lines.append(f"- 用户自述（兴趣/价值观/偏好）：{conf['about']}")

    parts = []
    if profile_lines:
        parts.append("关于用户（用户主动填写，回答时自然利用，无需刻意复述）：\n" + "\n".join(profile_lines))
    if conf["customInstructions"]:
        parts.append(
            "用户自定义指令（在不违反系统规则与安全约束的前提下遵循）：\n"
            + conf["customInstructions"]
        )
    return "\n\n".join(parts)
