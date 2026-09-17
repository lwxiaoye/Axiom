import logging
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)


def pick_display_name(user_id: str, row: dict[str, Any] | None = None, fallback_name: str = "") -> str:
    row = row or {}
    realname = str(row.get("realname") or "").strip()
    username = str(row.get("username") or "").strip()
    fallback = str(fallback_name or "").strip()
    return realname or username or fallback or str(user_id or "").strip()


async def load_user_display_names(session, user_ids: list[str], fallback_names: dict[str, str] | None = None) -> dict[str, str]:
    fallback_names = fallback_names or {}
    ids = sorted({str(user_id or "").strip() for user_id in user_ids if str(user_id or "").strip()})
    if not ids:
        return {}

    names: dict[str, str] = {}
    try:
        result = await session.execute(
            text("SELECT id, username, realname FROM sys_user WHERE id IN :user_ids").bindparams(
                bindparam("user_ids", expanding=True)
            ),
            {"user_ids": ids},
        )
        for row in result.mappings():
            user_id = str(row.get("id") or "").strip()
            if user_id:
                names[user_id] = pick_display_name(user_id, dict(row), fallback_names.get(user_id, ""))
    except Exception:
        logger.warning("加载 sys_user.realname 失败，回退到已存用户名", exc_info=True)

    for user_id in ids:
        names.setdefault(user_id, pick_display_name(user_id, {}, fallback_names.get(user_id, "")))
    return names
