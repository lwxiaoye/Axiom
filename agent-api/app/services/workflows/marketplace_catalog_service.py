"""智能体广场里「用户自建、已发布」的智能体目录。

为什么需要这个模块：广场（AgentMarketTab）原本从 Java 的 `/app/appInfo/my/all/list`
取自建应用，而本地 auth-api 把那条接口固定返回空数组，于是审核通过的自建智能体
只写进了 app_info 表，广场上永远看不到——「发布」这一步在用户眼里等于没发生。

这里不再绕道 app_info，而是直接以 agent-api 的发布事实源（agent_workflow_app +
approved 版本可见性）出目录，可见性判定复用委派召回层的 list_callable_subagent_ids：
owner 可见自己的已发布应用，其他人必须命中线上版本的可见角色/部门——和运行页
（/run/app → user_can_run_published_app）用同一把尺子，广场上看得见的就一定跑得起来。

返回结构对齐 app_info 目录行（appName/appRemark/pcUrl/createBy…），前端广场组件
不必区分来源。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import bindparam, select, text

from app.core.auth import UserContext
from app.core.database import async_session
from app.models import WorkflowApp, WorkflowDefinition
from app.services.agents.subagent_service import list_callable_subagent_ids

logger = logging.getLogger(__name__)

# 与内置智能体的 runtimeKind（harness_builtin）区分：广场卡片打开的是 /agent/run/:appId
WORKFLOW_RUNTIME_KIND = "workflow_app"


def _runtime_url(app_id: str) -> str:
    return f"/agent/run/{app_id}"


async def _load_creator_profiles(session, user_ids: list[str]) -> dict[str, tuple[str, str]]:
    """owner_user_id → (显示名, 头像)。查不到时回退空，卡片显示「未知」而不是报错。"""
    ids = sorted({str(value or "").strip() for value in user_ids if str(value or "").strip()})
    if not ids:
        return {}
    profiles: dict[str, tuple[str, str]] = {}
    try:
        rows = (
            await session.execute(
                text("SELECT id, username, realname, avatar FROM sys_user WHERE id IN :user_ids").bindparams(
                    bindparam("user_ids", expanding=True)
                ),
                {"user_ids": ids},
            )
        ).mappings().all()
        for raw in rows:
            row = dict(raw)
            user_id = str(row.get("id") or "").strip()
            name = str(row.get("realname") or row.get("username") or user_id).strip()
            avatar = str(row.get("avatar") or "").strip()
            if user_id:
                profiles[user_id] = (name, avatar)
    except Exception:  # noqa: BLE001
        # 创建人资料只是展示信息，查不到不能让整个广场目录失败
        logger.warning("加载自建智能体创建人资料失败，回退到 owner_username", exc_info=True)
    return profiles


def _to_marketplace_row(
    app: WorkflowApp,
    published_version: int,
    creator: tuple[str, str] | None,
) -> dict[str, Any]:
    app_id = str(app.id)
    owner_id = str(app.owner_user_id or "")
    creator_name, creator_avatar = creator or (str(app.owner_username or "") or "未知", "")
    created_at = getattr(app, "create_time", None)
    published_at = getattr(app, "published_at", None)
    form_options = {
        "aiAppType": app.ai_app_type,
        "sourceAppId": app_id,
        "publishedVersion": int(published_version or 0),
    }
    return {
        "id": app_id,
        "appName": app.name,
        "appRemark": app.description or "",
        "appIcon": app.app_icon or "",
        "appCategory": app.app_category or "",
        # 运行时类型：广场按 aiAppType 识别对话 Agent / 工作流（getAiAppKind 读 formOptions.aiAppType）
        "appType": "agent",
        "aiAppType": app.ai_app_type,
        "pcUrl": _runtime_url(app_id),
        "h5Url": _runtime_url(app_id),
        "openType": "_blank",
        "status": 1,
        "formOptions": json.dumps(form_options, ensure_ascii=False, separators=(",", ":")),
        "runtimeKind": WORKFLOW_RUNTIME_KIND,
        "createBy": owner_id,
        "createByName": creator_name,
        "createByAvatar": creator_avatar,
        "createTime": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        "publishedAt": published_at.isoformat() if hasattr(published_at, "isoformat") else None,
    }


async def list_published_marketplace_apps(user: UserContext) -> list[dict[str, Any]]:
    """当前用户在广场上可见（= 可运行）的自建已发布智能体，按发布时间倒序。"""
    allowed = await list_callable_subagent_ids(user)
    if not allowed:
        return []
    async with async_session() as session:
        rows = (
            await session.execute(
                select(WorkflowApp, WorkflowDefinition.published_version)
                .join(WorkflowDefinition, WorkflowDefinition.app_id == WorkflowApp.id)
                .where(WorkflowApp.id.in_(list(allowed)))
                .order_by(WorkflowApp.published_at.desc(), WorkflowApp.update_time.desc())
            )
        ).all()
        creators = await _load_creator_profiles(session, [str(app.owner_user_id or "") for app, _ in rows])
    return [
        _to_marketplace_row(app, int(published_version or 0), creators.get(str(app.owner_user_id or "")))
        for app, published_version in rows
    ]
