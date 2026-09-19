"""auth-api 用户资料 → agent-api `sys_user` 的单向同步（广场「创建人」姓名/头像）。

两边为什么会不通：认证与个人资料在 auth-api（SQLite 的 users/administrator 表），
`GET /sys/user/login/setting/getUserData` 读、`userEdit` 改，头像存相对路径
`uploads/<id>.png`，前端经 `/api/sys/common/static/` 显示；而智能体广场卡片的
「创建人」由 agent-api 自己 MySQL 里的 `sys_user` 表出（`_load_creator_profiles`），
那张表只有启动时播种的 admin 一行。用户在个人资料里改名、传头像，广场永远不知道。

做法：鉴权回源 `_verify_token_with_auth_api()` 拿到 userInfo（含 realname/avatar）时，
把对应行 upsert 进 `sys_user`。任何走过鉴权的用户都会被同步——只更新
username/realname/avatar 三列，其余列不碰。avatar 原样存 auth-api 给的相对路径，
前端 `getProxyStaticFileUrl` 会拼成 `/api/sys/common/static/uploads/...`，与个人资料
弹窗里显示头像用的是同一条规则。

节流：`AUTH_TOKEN_CACHE_TTL_SECONDS` 默认 0，每个请求都回源；不能每次都写库。
进程内字典记「上次同步时间」，同一 user 每 SYNC_INTERVAL_SECONDS 最多写一次。
写库失败只记日志、绝不影响鉴权主流程——这只是展示信息。
"""
import logging
import time

from app.core.database import async_session
from app.models import SysUser

logger = logging.getLogger(__name__)

# 同一用户两次写库的最小间隔（秒）。改完资料最多等这么久广场才刷新，够用且不放大写压力。
SYNC_INTERVAL_SECONDS = 300
# 节流表上限：超过就整体清空重来。它只用于降重，不保证正确性，清了顶多多写一次。
_MAX_TRACKED_USERS = 10_000

_last_synced_at: dict[str, float] = {}


def _profile_from_user_info(info: dict) -> tuple[str, str, str, str] | None:
    """从 auth-api userInfo 抽 (user_id, username, realname, avatar)；缺 id/username 视为无效。"""
    if not isinstance(info, dict):
        return None
    user_id = str(info.get("id") or info.get("userId") or "").strip()
    username = str(info.get("username") or "").strip()
    if not user_id or not username:
        return None
    realname = str(info.get("realname") or "").strip()
    avatar = str(info.get("avatar") or "").strip()
    return user_id, username, realname, avatar


def _should_sync(user_id: str, now: float) -> bool:
    last = _last_synced_at.get(user_id)
    return last is None or now - last >= SYNC_INTERVAL_SECONDS


async def sync_sys_user_profile(info: dict, *, now: float | None = None) -> bool:
    """按 auth-api userInfo upsert `sys_user` 一行。

    返回 True = 本次真的写了库；False = 节流跳过 / 资料不完整 / 写库失败（已记日志）。
    `now` 只供测试注入时间。
    """
    profile = _profile_from_user_info(info)
    if profile is None:
        return False
    user_id, username, realname, avatar = profile
    ts = time.time() if now is None else now
    if not _should_sync(user_id, ts):
        return False
    if len(_last_synced_at) >= _MAX_TRACKED_USERS:
        _last_synced_at.clear()
    # 先占位再写库：同一用户并发到达的请求不会一起冲进写路径（进程内有效，够用）
    _last_synced_at[user_id] = ts
    try:
        async with async_session() as session:
            row = await session.get(SysUser, user_id)
            if row is None:
                session.add(SysUser(id=user_id, username=username, realname=realname, avatar=avatar))
            elif (row.username, row.realname or "", row.avatar or "") == (username, realname, avatar):
                return False  # 已一致：不发 UPDATE，也不算「写了」
            else:
                row.username = username
                row.realname = realname
                row.avatar = avatar
            await session.commit()
    except Exception:  # noqa: BLE001 - 展示信息同步失败不能拖垮鉴权
        # 多 worker 并发首插可能撞主键、库瞬时不可用……都放到下一次请求再试
        _last_synced_at.pop(user_id, None)
        logger.warning("同步 sys_user 资料失败 user_id=%s", user_id, exc_info=True)
        return False
    logger.info("已同步 sys_user 资料 user_id=%s username=%s realname=%s avatar=%s",
                user_id, username, realname, avatar or "<空>")
    return True
