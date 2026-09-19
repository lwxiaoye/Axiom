"""
工作台工作流 API：应用 CRUD/发布/授权 + 定义草稿/发布/调试/执行。

响应字段与前端 src/views/workflow/api/workflow.api.ts 的类型完全对齐（camelCase），
前端经 /agent-api 代理调用（isTransformResponse: false，无 jeecg Result 包装）。
"""
import json
import logging
import re
import uuid
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import and_, func, or_, select, text

from app.core.auth import UserContext, current_user, is_platform_admin, is_reviewer
from app.core.config import settings
from app.core.database import async_session
from app.models import ChatMessage, ChatThread, EmbeddingModel, WorkflowAcl, WorkflowAdminAudit, WorkflowApp, WorkflowDefinition, WorkflowVersion
from app.services.agent_time import format_agent_now
from app.services.agents import app_capability_registry, capability_registry
from app.services.gateway import tool_gateway
from app.services.chat.turn_context_builder import _attachments_meta
from app.services.agents.app_info_publish_service import (
    normalize_visible_ids,
    retire_app_info_catalog_entry,
    retire_orphaned_agent_catalog_entries,
    resolve_workflow_required_model_ids,
    set_app_info_catalog_status,
    sync_app_info_for_approved_version,
)
from app.services.agents.published_visibility import load_published_visibility_version, user_can_run_published_app
from app.services.platform.user_display_name import load_user_display_names
from app.services.workflows.workflow_model_requirements import missing_required_models
from app.services.workflows import marketplace_catalog_service
from app.services.workflows.builtin_app_admin_service import (
    build_builtin_admin_detail,
    get_managed_builtin,
    is_managed_builtin_id,
    list_managed_builtins,
)
from app.services.workflows.admin_governance_service import (
    build_admin_app_summary,
    build_version_diff,
    record_app_audit,
)
from app.services.workflows.workflow_engine import (
    RunContext,
    SUPPORTED_NODE_TYPES,
    VARIABLE_NODE_ID,
    WorkflowExecutionError,
    execute_workflow,
    parse_graph,
)
from app.services.workflows.debug_session_service import (
    DebugSessionExpiredError,
    DebugSessionForbiddenError,
    DebugSessionNotFoundError,
    DebugSessionUnsupportedGraphError,
    WorkflowDebugSession,
    workflow_debug_sessions,
)
from app.services.workflows.prompt_debug_service import (
    complete_prompt_experiment,
    interpolate_prompt,
    prompt_generation_messages,
)
from app.services.workflows.evaluation_run_service import (
    get_workflow_evaluation_run,
    list_workflow_evaluation_runs,
    record_workflow_evaluation_run,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflow", tags=["workflow"])

AI_APP_TYPES = {"simple", "chatAgent", "workflow", "workflowTool", "httpToolSet", "mcpToolSet"}
TOOL_APP_TYPES = {"workflowTool", "httpToolSet", "mcpToolSet"}
REVIEW_MENU_PATH = "/workflow/review"
AGENT_MANAGE_MENU_PATH = "/workflow/manage"


# ---------- Schemas ----------

class AppUpsertRequest(BaseModel):
    id: Optional[str] = None
    aiAppType: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    appCategory: Optional[str] = None
    appIcon: Optional[str] = None
    configJson: Optional[str] = None


class RouteMetadata(BaseModel):
    """发布弹窗可选的路由元数据（语义发现升级 §九）：随版本冻结，approved 上线后才进召回。"""
    routeDescription: Optional[str] = None
    triggerExamples: list[str] = Field(default_factory=list)
    negativeExamples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def _normalize_route_metadata(meta: Optional[RouteMetadata]) -> Optional[str]:
    """严格校验并归一 routeMetadata → routing_json 字符串；未提供返回 None。

    限制：routeDescription ≤1000 字；trigger/negative 各 ≤20 条、单条 ≤200 字；
    tags ≤20 个、单个 ≤32 字。超限 400，不静默截断（把选择权留给发布者）。
    """
    if meta is None:
        return None

    def _clean_list(items: list, *, label: str, max_items: int, max_len: int) -> list[str]:
        cleaned = []
        for raw in items or []:
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > max_len:
                raise HTTPException(400, f"{label}单条不能超过 {max_len} 字")
            if text not in cleaned:
                cleaned.append(text)
        if len(cleaned) > max_items:
            raise HTTPException(400, f"{label}最多 {max_items} 条")
        return cleaned

    desc = str(meta.routeDescription or "").strip()
    if len(desc) > 1000:
        raise HTTPException(400, "能力描述最长 1000 字")
    normalized = {
        "routeDescription": desc,
        "triggerExamples": _clean_list(meta.triggerExamples, label="触发示例",
                                       max_items=20, max_len=200),
        "negativeExamples": _clean_list(meta.negativeExamples, label="不适用示例",
                                        max_items=20, max_len=200),
        "tags": _clean_list(meta.tags, label="业务标签", max_items=20, max_len=32),
    }
    if not desc and not any(normalized[k] for k in ("triggerExamples", "negativeExamples", "tags")):
        return None
    return json.dumps(normalized, ensure_ascii=False)


class DefinitionSaveRequest(BaseModel):
    appId: Optional[str] = None
    appInfoId: Optional[str] = None
    workflowJson: str
    # 提交发布审核时的变更说明（WS2）
    changeNote: Optional[str] = None
    visibleRoleIds: Any = Field(default_factory=list)
    visibleDeptIds: Any = Field(default_factory=list)
    # 可选路由元数据（语义发现升级 §九）：冻结进 WorkflowVersion.routing_json
    routeMetadata: Optional[RouteMetadata] = None


class ReviewActionRequest(BaseModel):
    versionId: str
    comment: Optional[str] = None


class RollbackRequest(BaseModel):
    appId: str
    versionNo: int


class AdminRestoreRequest(BaseModel):
    appId: str
    reason: str = Field(default="", max_length=512)


class AdminTransferOwnerRequest(BaseModel):
    appId: str
    targetUserId: str = Field(min_length=1, max_length=64)
    retainPreviousOwnerAsEditor: bool = True
    reason: str = Field(default="", max_length=512)


class DebugRequest(BaseModel):
    appId: Optional[str] = None
    appInfoId: Optional[str] = None
    previewVersionId: Optional[str] = None
    # 草稿预览：按 appId 取最新 draft_json 运行（编辑态产物，要求编辑权限）
    previewDraft: Optional[bool] = None
    input: Optional[str] = ""
    workflowJson: Optional[str] = None
    variables: Optional[dict] = None
    # 多轮对话历史 [{role: user|assistant, content}]，agent 节点按 maxHistories 截取
    histories: Optional[list] = None
    # Standalone sub-agent session that owns generated deliverables.  The
    # endpoint validates ownership before it is passed into the workflow.
    sessionId: Optional[str] = None


class DebugSessionStepRequest(BaseModel):
    """Variables intentionally patch only the paused debug context."""
    variables: Optional[dict] = None


class PromptDebugRequest(BaseModel):
    """One stateless prompt experiment; never executes a workflow node."""
    appId: str = Field(min_length=1, max_length=64)
    prompt: str = Field(default="", max_length=100_000)
    question: str = Field(min_length=1, max_length=20_000)
    model: str = Field(default="", max_length=255)
    variables: dict[str, Any] = Field(default_factory=dict)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    maxToken: Optional[int] = Field(default=None, ge=1, le=128_000)
    topP: Optional[float] = Field(default=None, gt=0, le=1)
    stopSign: str = Field(default="", max_length=2_000)
    responseFormat: str = Field(default="", max_length=64)
    jsonSchema: str = Field(default="", max_length=100_000)


class PromptGenerateRequest(BaseModel):
    appId: str = Field(min_length=1, max_length=64)
    currentPrompt: str = Field(default="", max_length=100_000)
    goal: str = Field(default="", max_length=10_000)
    model: str = Field(default="", max_length=255)
    variableKeys: list[str] = Field(default_factory=list, max_length=100)


# ---------- Helpers ----------

def _review_summary_dict(version: Optional[WorkflowVersion], share_permission: str) -> Optional[dict]:
    if not version or version.status not in {"pending_review", "rejected"}:
        return None

    data = {
        "versionId": version.id,
        "versionNo": version.version_no,
        "status": version.status,
        "submittedAt": version.submitted_at.isoformat() if version.submitted_at else None,
        "reviewedAt": version.reviewed_at.isoformat() if version.reviewed_at else None,
        "reviewedByName": version.reviewed_by_name or "",
    }
    if str(share_permission or "").upper() in {"OWNER", "EDITOR", "ADMIN", "REVIEWER"}:
        data["reviewComment"] = version.review_comment or ""
    return data


def _json_values_are_equal(left: Optional[str], right: Optional[str]) -> bool:
    """Compare persisted JSON without treating key order as a user change."""
    if left == right:
        return True
    try:
        return json.loads(left or "null") == json.loads(right or "null")
    except (json.JSONDecodeError, TypeError):
        return False


def _has_unpublished_definition_changes(definition: Optional[WorkflowDefinition]) -> bool:
    if (
        not definition
        or not getattr(definition, "published_json", None)
        or not getattr(definition, "draft_json", None)
    ):
        return False
    return not _json_values_are_equal(
        getattr(definition, "draft_json", None),
        getattr(definition, "published_json", None),
    )


def _reject_unchanged_published_definition(
    app: WorkflowApp,
    definition: Optional[WorkflowDefinition],
    workflow_json: str,
) -> None:
    if (
        app.status == "published"
        and definition
        and getattr(definition, "published_json", None)
        and _json_values_are_equal(workflow_json, definition.published_json)
    ):
        raise HTTPException(409, "当前草稿与线上版本一致，没有可发布的修改")


def _app_dict(
    app: WorkflowApp,
    share_permission: str = "OWNER",
    *,
    review_version: Optional[WorkflowVersion] = None,
    definition: Optional[WorkflowDefinition] = None,
) -> dict:
    data = {
        "id": app.id,
        "appInfoId": None,
        "aiAppType": app.ai_app_type,
        "name": app.name,
        "description": app.description or "",
        "appCategory": app.app_category,
        "appIcon": app.app_icon or "",
        "configJson": app.config_json or "{}",
        "status": app.status,
        "ownerUserId": app.owner_user_id,
        "sharePermission": share_permission,
        "appInfoStatus": "1" if app.status == "published" else "0",
        "publishedAt": app.published_at.isoformat() if app.published_at else None,
        "publishedBy": app.published_by,
        "hasUnpublishedChanges": app.status == "published" and _has_unpublished_definition_changes(definition),
    }
    review_summary = _review_summary_dict(review_version, share_permission)
    if review_summary:
        data["reviewSummary"] = review_summary
    return data


def _app_audit_snapshot(app: WorkflowApp) -> dict[str, Any]:
    """Return the non-sensitive application fields suitable for an audit diff."""
    return {
        "name": getattr(app, "name", "") or "",
        "description": getattr(app, "description", "") or "",
        "appCategory": getattr(app, "app_category", "") or "",
        "appIcon": getattr(app, "app_icon", "") or "",
        "status": getattr(app, "status", "") or "",
        "aiAppType": getattr(app, "ai_app_type", "") or "",
    }


async def _latest_review_versions(session, app_ids: list[str]) -> dict[str, WorkflowVersion]:
    if not app_ids:
        return {}

    latest_version_numbers = (
        select(
            WorkflowVersion.app_id.label("app_id"),
            func.max(WorkflowVersion.version_no).label("version_no"),
        )
        .where(WorkflowVersion.app_id.in_(app_ids))
        .group_by(WorkflowVersion.app_id)
        .subquery()
    )
    versions = (
        await session.execute(
            select(WorkflowVersion)
            .join(
                latest_version_numbers,
                and_(
                    WorkflowVersion.app_id == latest_version_numbers.c.app_id,
                    WorkflowVersion.version_no == latest_version_numbers.c.version_no,
                ),
            )
        )
    ).scalars().all()
    return {version.app_id: version for version in versions}


async def _definitions_by_app_id(session, app_ids: list[str]) -> dict[str, WorkflowDefinition]:
    if not app_ids:
        return {}
    rows = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id.in_(app_ids)))
    ).scalars().all()
    return {definition.app_id: definition for definition in rows}


def _definition_dict(definition: WorkflowDefinition) -> dict:
    return {
        "id": definition.id,
        "appId": definition.app_id,
        "appInfoId": None,
        "draftJson": definition.draft_json,
        "publishedJson": definition.published_json,
        "publishedVersion": definition.published_version or 0,
        "status": definition.status,
    }


def _parse_routing_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _version_dict(v: WorkflowVersion, *, with_json: bool = False) -> dict:
    data = {
        "id": v.id,
        "appId": v.app_id,
        "versionNo": v.version_no,
        "aiAppType": v.ai_app_type,
        "status": v.status,
        "changeNote": v.change_note or "",
        "visibleRoleIds": normalize_visible_ids(v.visible_role_ids),
        "visibleDeptIds": normalize_visible_ids(v.visible_dept_ids),
        "publishChannels": ["marketplace"],
        "embedOrigins": [],
        "submittedBy": v.submitted_by,
        "submittedByName": v.submitted_by_name or "",
        "submittedAt": v.submitted_at.isoformat() if v.submitted_at else None,
        "reviewedBy": v.reviewed_by,
        "reviewedByName": v.reviewed_by_name or "",
        "reviewedAt": v.reviewed_at.isoformat() if v.reviewed_at else None,
        "reviewComment": v.review_comment or "",
        "publishedAt": v.published_at.isoformat() if v.published_at else None,
        "routeMetadata": _parse_routing_json(getattr(v, "routing_json", None)),
    }
    if with_json:
        data["definitionJson"] = v.definition_json
        data["configJson"] = v.config_json
    return data


async def _next_version_no(session, app_id: str) -> int:
    current = (
        await session.execute(
            select(func.max(WorkflowVersion.version_no)).where(WorkflowVersion.app_id == app_id)
        )
    ).scalar()
    return int(current or 0) + 1


def _require_reviewer(user: UserContext) -> None:
    if not is_reviewer(user):
        raise HTTPException(403, "需要审核员权限")


def _require_platform_admin(user: UserContext) -> None:
    if not is_platform_admin(user):
        raise HTTPException(403, "需要平台管理员权限")


def _normalize_menu_path(path: Optional[str]) -> str:
    value = str(path or "").strip()
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def _menu_tree_contains_path(menu_nodes: Any, target_path: str) -> bool:
    target = _normalize_menu_path(target_path)
    if not target:
        return False
    if isinstance(menu_nodes, dict):
        menu_nodes = [menu_nodes]
    if not isinstance(menu_nodes, list):
        return False
    for node in menu_nodes:
        if not isinstance(node, dict):
            continue
        if _normalize_menu_path(node.get("path")) == target:
            return True
        meta = node.get("meta") or {}
        if isinstance(meta, dict) and _normalize_menu_path(meta.get("url")) == target:
            return True
        if _menu_tree_contains_path(node.get("children"), target):
            return True
    return False


async def _auth_api_user_has_menu_path(access_token: str, menu_path: str) -> bool:
    token = str(access_token or "").strip()
    if not token:
        return False
    url = f"{settings.AUTH_API_BASE}/sys/permission/getUserPermissionByToken"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers={"X-Access-Token": token})
        if resp.status_code >= 500:
            raise HTTPException(503, "权限服务不可用")
        if resp.status_code >= 400:
            return False
        data = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth-api menu permission lookup failed", exc_info=True)
        raise HTTPException(503, "权限服务不可用") from exc
    if not data.get("success"):
        return False
    result = data.get("result") or {}
    return _menu_tree_contains_path(result.get("menu") or [], menu_path)


async def _require_agent_manage_permission(user: UserContext) -> None:
    if is_platform_admin(user):
        return
    if await _auth_api_user_has_menu_path(getattr(user, "access_token", ""), AGENT_MANAGE_MENU_PATH):
        return
    raise HTTPException(403, "需要智能体管理页面权限")


async def _require_review_permission(user: UserContext) -> None:
    if is_reviewer(user):
        return
    if await _auth_api_user_has_menu_path(getattr(user, "access_token", ""), REVIEW_MENU_PATH):
        return
    raise HTTPException(403, "需要发布审核页面权限")


async def _get_app(session, app_id: str) -> WorkflowApp:
    app = await session.get(WorkflowApp, app_id)
    if not app:
        raise HTTPException(404, "应用不存在")
    return app


async def _share_permission(session, app: WorkflowApp, user: UserContext) -> Optional[str]:
    """返回 OWNER/EDITOR/VIEWER；无权限返回 None。"""
    if app.owner_user_id == user.user_id:
        return "OWNER"
    result = await session.execute(select(WorkflowAcl).where(WorkflowAcl.app_id == app.id))
    permission = None
    for acl in result.scalars():
        matched = (
            (acl.subject_type == "USER" and acl.subject_id == user.user_id)
            or (acl.subject_type == "ROLE" and acl.subject_id in user.role_ids)
            or (acl.subject_type == "DEPARTMENT" and acl.subject_id in user.dept_ids)
        )
        if matched:
            if acl.permission == "EDITOR":
                return "EDITOR"
            permission = "VIEWER"
    return permission


async def _require_permission(session, app_id: str, user: UserContext, *, edit: bool = False, owner: bool = False):
    app = await _get_app(session, app_id)
    permission = await _share_permission(session, app, user)
    if permission is None:
        raise HTTPException(403, "没有该应用的访问权限")
    if owner and permission != "OWNER":
        raise HTTPException(403, "仅应用拥有者可执行该操作")
    if edit and permission not in ("OWNER", "EDITOR"):
        raise HTTPException(403, "没有该应用的编辑权限")
    return app, permission


async def _require_run_access(session, app_id: str, user: UserContext) -> WorkflowApp:
    app = await _get_app(session, app_id)
    if app.status != "published":
        raise HTTPException(403, "应用尚未发布，无法使用")
    if not await user_can_run_published_app(session, app, user):
        raise HTTPException(403, "没有该应用的使用权限")
    return app


def _resolve_app_id(app_id: Optional[str], app_info_id: Optional[str]) -> str:
    """Python 运行时没有 Java app_info 概念，appInfoId 兼容按 appId 解析。"""
    resolved = app_id or app_info_id
    if not resolved:
        raise HTTPException(400, "缺少应用 ID")
    return resolved


def _reject_if_unpublished(app) -> None:
    """H3：下架即停止对外提供。运行入口（execute/executeStream/resume）拒绝已下架应用。

    仅拦截显式 unpublished 状态；草稿态经 published_json 运行的既有行为不受影响。
    """
    if getattr(app, "status", None) == "unpublished":
        raise HTTPException(403, "应用已下架，暂停对外提供")


def _reject_missing_required_models(required_models: list[str], available_models: list[str]) -> None:
    missing_models = missing_required_models(required_models, available_models)
    if missing_models:
        raise HTTPException(403, f"当前用户无权使用工作流所需模型：{','.join(missing_models)}")


def _merge_available_model_ids(chat_models: list[Any], embedding_models: list[Any]) -> list[str]:
    values: list[str] = []
    for item in [*chat_models, *embedding_models]:
        model_id = getattr(item, "id", item)
        model_id = str(model_id or "").strip()
        if model_id and model_id not in values:
            values.append(model_id)
    return values


async def _available_embedding_model_ids(session) -> list[str]:
    rows = (
        await session.execute(
            select(EmbeddingModel.model_id).where(or_(EmbeddingModel.enabled == 1, EmbeddingModel.is_active == 1))
        )
    ).scalars().all()
    return [str(model_id).strip() for model_id in rows if str(model_id or "").strip()]


async def _require_published_workflow_model_access(workflow_json: Optional[str], user: UserContext) -> None:
    """Re-check model access using the caller's key before executing a published definition."""
    try:
        async with async_session() as session:
            required_models = await resolve_workflow_required_model_ids(session, workflow_json)
            embedding_models = await _available_embedding_model_ids(session)
        if not required_models:
            return
        from app.services.platform.key_service import key_service

        user_key = await key_service.get_user_key(user.user_id)
        if not user_key:
            raise RuntimeError("missing user key")
        from app.services.agents.agent_service import agent_service

        chat_models = await agent_service.get_models(user_key=user_key, raise_on_lookup_failure=True)
    except Exception as exc:
        logger.warning("confirm required workflow models failed", exc_info=True)
        raise HTTPException(403, "无法确认当前用户的模型权限") from exc
    _reject_missing_required_models(required_models, _merge_available_model_ids(chat_models, embedding_models))


def _first_workflow_chat_model(graph: dict) -> str:
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("flowNodeType") or "") not in {"chatNode", "agent", "tools"}:
            continue
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict) or inp.get("key") != "model":
                continue
            value = str(inp.get("value") or "").strip()
            if value and value != "default":
                return value
    return ""


def _extract_run_chat_config(workflow_json: Optional[str], user: Optional[UserContext] = None) -> dict:
    empty = {"welcomeText": "", "quickQuestions": [], "inspirationScenes": []}
    if not workflow_json:
        return empty
    try:
        graph = json.loads(workflow_json)
    except json.JSONDecodeError:
        return empty
    if not isinstance(graph, dict):
        return empty
    chat_config = graph.get("chatConfig") or (graph.get("fastgpt") or {}).get("chatConfig") or {}
    if not isinstance(chat_config, dict):
        return empty
    guide = chat_config.get("chatInputGuide") or {}
    guide_enabled = isinstance(guide, dict) and guide.get("open") is not False
    quick_questions = guide.get("textList") if guide_enabled else []
    if not isinstance(quick_questions, list):
        quick_questions = []
    quick_questions = [str(item).strip() for item in quick_questions if str(item).strip()]
    inspiration_scenes = []
    raw_scenes = guide.get("sceneList") if guide_enabled else []
    if isinstance(raw_scenes, list):
        used_keys: set[str] = set()
        for index, raw_scene in enumerate(raw_scenes):
            if not isinstance(raw_scene, dict):
                continue
            text_list = raw_scene.get("textList")
            if not isinstance(text_list, list):
                text_list = []
            tasks = list(dict.fromkeys(str(item).strip() for item in text_list if str(item).strip()))
            if not tasks:
                continue
            label = str(raw_scene.get("label") or "").strip() or f"场景 {index + 1}"
            key = str(raw_scene.get("key") or "").strip() or f"scene-{index + 1}"
            while key in used_keys:
                key = f"{key}-{index + 1}"
            used_keys.add(key)
            inspiration_scenes.append({"key": key, "label": label, "tasks": tasks})
    if not inspiration_scenes and quick_questions:
        inspiration_scenes = [{"key": "recommended", "label": "推荐", "tasks": quick_questions}]
    # 开场白变量注入：与引擎 interpolate 一致（系统变量 + chatConfig.variables 默认值，未定义保留原样）
    welcome_text = _interpolate_welcome(str(chat_config.get("welcomeText") or ""), chat_config, user)
    file_select_config = chat_config.get("fileSelectConfig") if isinstance(chat_config.get("fileSelectConfig"), dict) else {}
    tts_source = chat_config.get("ttsConfig") if isinstance(chat_config.get("ttsConfig"), dict) else {}
    tts_type = "web" if tts_source.get("type") == "web" else "none"
    result = {
        "welcomeText": welcome_text,
        "quickQuestions": quick_questions,
        "inspirationScenes": inspiration_scenes,
        "variables": chat_config.get("variables") if isinstance(chat_config.get("variables"), list) else [],
        "fileSelectConfig": file_select_config,
        "ttsConfig": {"type": tts_type},
        "chatModel": _first_workflow_chat_model(graph),
    }
    # 运行页只有一套默认外观；presentation 仅保留受限长度的欢迎语 / 输入框占位符文案，
    # 不透传预设 key、CSS、HTML 或任意资源地址。没有文案时整个字段不返回。
    presentation_source = chat_config.get("presentation")
    copy_source = presentation_source.get("copy") if isinstance(presentation_source, dict) else None
    if isinstance(copy_source, dict):
        copy = {}
        for key, max_length in (("welcomeTitle", 80), ("composerPlaceholder", 120)):
            value = str(copy_source.get(key) or "").strip()
            if value:
                copy[key] = value[:max_length]
        if copy:
            result["presentation"] = {"copy": copy}
    return result


def _interpolate_welcome(text: str, chat_config: dict, user: Optional[UserContext]) -> str:
    """开场白 {{变量}} 注入，规则对齐 workflow_engine.interpolate：
    chatConfig.variables 默认值 -> 系统变量（userId/username/realname/cTime/appId），
    未定义的 {{key}} 保留原样（不残留空串），已定义但值为 None 替换为空串。
    """
    if not text or "{{" not in text:
        return text
    variables: dict = {}
    for item in chat_config.get("variables") or []:
        key = item.get("key") if isinstance(item, dict) else None
        if key and item.get("defaultValue") is not None:
            variables[key] = item.get("defaultValue")
    if user:
        variables.setdefault("userId", user.user_id)
        variables.setdefault("username", user.username)
        variables.setdefault("realname", user.real_name or user.username)
        variables.setdefault("appId", "")
    variables.setdefault("cTime", format_agent_now())

    def to_text(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key not in variables:
            return match.group(0)
        value = variables.get(key)
        return "" if value is None else to_text(value)

    return re.sub(r"\{\{([^{}]+)\}\}", repl, text)


async def _load_review_preview_workflow(session, app_id: str, version_id: Optional[str], user: UserContext) -> Optional[str]:
    if not version_id:
        return None
    await _require_review_permission(user)
    version = await session.get(WorkflowVersion, version_id)
    if not version or version.app_id != app_id:
        raise HTTPException(404, "待审版本不存在")
    if version.status != "pending_review":
        raise HTTPException(400, "仅待审核版本支持审核预览")
    if not version.definition_json:
        raise HTTPException(400, "待审版本没有定义快照")
    return version.definition_json


async def _load_draft_preview_workflow(session, app_id: str, user: UserContext) -> str:
    """草稿预览：按 appId 取最新 draft_json 运行。

    草稿是编辑态产物，可能含未审核的 code/http 节点（任意执行风险），因此要求编辑权限，
    权限模型对齐 debug 接口（H1 策略），不能让普通 RUNNER 跑别人的草稿。
    """
    await _require_permission(session, app_id, user, edit=True)
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
    ).scalar_one_or_none()
    if not definition or not definition.draft_json:
        raise HTTPException(400, "该应用还没有草稿内容，请先保存")
    return definition.draft_json


# ---------- 应用 CRUD ----------

@router.get("/app/page")
async def page_apps(
    pageNo: int = 1,
    pageSize: int = 20,
    keyword: Optional[str] = None,
    scope: str = "all",
    aiAppType: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    async with async_session() as session:
        try:
            await retire_orphaned_agent_catalog_entries(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("清理已删除智能体的广场目录残留失败")
        acl_subject = or_(
            (WorkflowAcl.subject_type == "USER") & (WorkflowAcl.subject_id == user.user_id),
            (WorkflowAcl.subject_type == "ROLE") & (WorkflowAcl.subject_id.in_(user.role_ids or [""])),
            (WorkflowAcl.subject_type == "DEPARTMENT") & (WorkflowAcl.subject_id.in_(user.dept_ids or [""])),
        )
        shared_ids = select(WorkflowAcl.app_id).where(acl_subject)

        owned = WorkflowApp.owner_user_id == user.user_id
        shared = WorkflowApp.id.in_(shared_ids)
        if scope == "owned":
            visibility = owned
        elif scope == "shared":
            visibility = shared & ~owned
        else:
            visibility = or_(owned, shared)

        query = select(WorkflowApp).where(visibility)
        if aiAppType:
            query = query.where(WorkflowApp.ai_app_type == aiAppType)
        if keyword:
            like = f"%{keyword}%"
            query = query.where(or_(WorkflowApp.name.like(like), WorkflowApp.description.like(like)))

        total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (
            await session.execute(
                query.order_by(WorkflowApp.create_time.desc()).offset((pageNo - 1) * pageSize).limit(pageSize)
            )
        ).scalars().all()

        app_ids = [app.id for app in rows]
        latest_review_versions = await _latest_review_versions(session, app_ids)
        definitions = await _definitions_by_app_id(session, app_ids)
        records = []
        for app in rows:
            permission = "OWNER" if app.owner_user_id == user.user_id else (await _share_permission(session, app, user) or "VIEWER")
            records.append(
                _app_dict(
                    app,
                    permission,
                    review_version=latest_review_versions.get(app.id),
                    definition=definitions.get(app.id),
                )
            )
        return {"records": records, "total": total, "size": pageSize, "current": pageNo}


@router.get("/app/marketplace")
async def marketplace_apps(user: UserContext = Depends(current_user)):
    """智能体广场：当前用户可运行的自建已发布智能体（内置智能体仍走 /chat/builtin-apps）。

    本地 auth-api 的 /app/appInfo/my/all/list 固定返回空，审核通过的智能体此前
    只写进 app_info 却没有任何界面能读到；广场现在直接从发布事实源取目录。
    """
    return await marketplace_catalog_service.list_published_marketplace_apps(user)


@router.get("/app/queryById")
async def query_app_by_id(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        app, permission = await _require_permission(session, id, user)
        return _app_dict(app, permission)


@router.get("/app/queryByAppInfoId")
async def query_app_by_app_info_id(
    appInfoId: str,
    aiAppType: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    async with async_session() as session:
        app, permission = await _require_permission(session, appInfoId, user)
        return _app_dict(app, permission)


@router.post("/app/add")
async def add_app(payload: AppUpsertRequest, user: UserContext = Depends(current_user)):
    if not (payload.name or "").strip():
        raise HTTPException(400, "应用名称不能为空")
    ai_app_type = payload.aiAppType or "workflow"
    if ai_app_type not in AI_APP_TYPES:
        raise HTTPException(400, f"不支持的应用类型: {ai_app_type}")
    async with async_session() as session:
        app = WorkflowApp(
            id=uuid.uuid4().hex,
            tenant_id=user.tenant_id,
            ai_app_type=ai_app_type,
            name=payload.name.strip(),
            description=(payload.description or "").strip(),
            app_category=payload.appCategory,
            app_icon=payload.appIcon or "",
            config_json=payload.configJson or "{}",
            status="published" if ai_app_type in TOOL_APP_TYPES else "draft",
            owner_user_id=user.user_id,
            owner_username=user.username,
        )
        session.add(app)
        record_app_audit(session, app=app, action="create", actor=user, after=_app_audit_snapshot(app))
        await session.commit()
        await session.refresh(app)
        return _app_dict(app)


@router.put("/app/edit")
async def edit_app(payload: AppUpsertRequest, user: UserContext = Depends(current_user)):
    if not payload.id:
        raise HTTPException(400, "缺少应用 ID")
    async with async_session() as session:
        app, permission = await _require_permission(session, payload.id, user, edit=True)
        before = _app_audit_snapshot(app)
        config_changed = payload.configJson is not None and payload.configJson != app.config_json
        if payload.name is not None:
            app.name = payload.name.strip() or app.name
        if payload.description is not None:
            app.description = payload.description.strip()
        if payload.appCategory is not None:
            app.app_category = payload.appCategory
        if payload.appIcon is not None:
            app.app_icon = payload.appIcon
        if payload.configJson is not None:
            app.config_json = payload.configJson
        after = _app_audit_snapshot(app)
        if before != after or config_changed:
            record_app_audit(
                session,
                app=app,
                action="update_app",
                actor=user,
                before={**before, "configChanged": False},
                after={**after, "configChanged": config_changed},
            )
        await session.commit()
        await session.refresh(app)
        return _app_dict(app, permission)


@router.post("/app/saveConfig")
async def save_app_config(payload: AppUpsertRequest, user: UserContext = Depends(current_user)):
    return await edit_app(payload, user)


@router.delete("/app/delete")
async def delete_app(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        app, _ = await _require_permission(session, id, user, owner=True)
        record_app_audit(session, app=app, action="delete", actor=user, before=_app_audit_snapshot(app))
        await retire_app_info_catalog_entry(session, id)
        await session.execute(sa_delete(WorkflowDefinition).where(WorkflowDefinition.app_id == id))
        await session.execute(sa_delete(WorkflowAcl).where(WorkflowAcl.app_id == id))
        await session.execute(sa_delete(WorkflowApp).where(WorkflowApp.id == id))
        try:
            await retire_orphaned_agent_catalog_entries(session)
        except Exception:
            logger.exception("删除后清理广场目录残留失败 app=%s", id)
        await session.commit()
    await _mark_agent_api_access_invalidation_after_commit(id, "app_deleted")
    await capability_registry.remove(id)
    return {"success": True}


@router.post("/app/publish")
async def publish_app(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        await _require_permission(session, id, user, owner=True)
    raise HTTPException(410, "该旧发布接口已停用，请使用工作流编辑器的发布流程")


async def _cancel_pending_versions_for_unpublish(session, app_id: str) -> int:
    """Ensure an in-flight update cannot republish an app that its owner has taken offline."""
    pending_versions = (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app_id,
                WorkflowVersion.status == "pending_review",
            )
        )
    ).scalars().all()
    for version in pending_versions:
        version.status = "cancelled"
        version.review_comment = "因应用下架自动撤回"
    return len(pending_versions)


@router.post("/app/cancelPublish")
async def cancel_publish_app(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        app, _ = await _require_permission(session, id, user, owner=True)
        before = {"status": app.status}
        await _cancel_pending_versions_for_unpublish(session, id)
        app.status = "unpublished"
        await set_app_info_catalog_status(session, id, enabled=False)
        record_app_audit(session, app=app, action="unpublish", actor=user, before=before, after={"status": app.status})
        await session.commit()
        await session.refresh(app)
        result = _app_dict(app)
    await _mark_agent_api_access_invalidation_after_commit(id, "app_unpublished")
    await capability_registry.sync_from_app(id)  # 下架 → 从路由候选移除
    return result


# ---------- 定义 ----------

@router.get("/definition/queryByAppInfoId")
async def query_definition(
    appInfoId: Optional[str] = None,
    appId: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    resolved = _resolve_app_id(appId, appInfoId)
    async with async_session() as session:
        await _require_permission(session, resolved, user)
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == resolved))
        ).scalar_one_or_none()
        if not definition:
            return {
                "id": None,
                "appId": resolved,
                "appInfoId": None,
                "draftJson": None,
                "publishedJson": None,
                "publishedVersion": 0,
                "status": "draft",
            }
        return _definition_dict(definition)


async def _upsert_definition(session, app_id: str, workflow_json: str, publish: bool) -> WorkflowDefinition:
    try:
        json.loads(workflow_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"工作流 JSON 不合法: {exc}")
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
    ).scalar_one_or_none()
    if not definition:
        definition = WorkflowDefinition(id=uuid.uuid4().hex, app_id=app_id, published_version=0)
        session.add(definition)
    definition.draft_json = workflow_json
    if publish:
        definition.published_json = workflow_json
        definition.published_version = (definition.published_version or 0) + 1
        definition.status = "published"
    await session.commit()
    await session.refresh(definition)
    return definition


@router.post("/definition/save")
async def save_definition(payload: DefinitionSaveRequest, user: UserContext = Depends(current_user)):
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    async with async_session() as session:
        app, _ = await _require_permission(session, app_id, user, edit=True)
        previous_definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
        ).scalar_one_or_none()
        had_draft = bool(previous_definition and previous_definition.draft_json)
        draft_changed = (previous_definition.draft_json if previous_definition else None) != payload.workflowJson
        if draft_changed:
            record_app_audit(
                session,
                app=app,
                action="save_draft",
                actor=user,
                before={"hasDraft": had_draft},
                after={"hasDraft": bool(payload.workflowJson)},
            )
        definition = await _upsert_definition(session, app_id, payload.workflowJson, publish=False)
        return _definition_dict(definition)


def _is_reference_value(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value)


def _raw_input_value(node: dict, key: str):
    for item in node.get("inputs") or []:
        if item.get("key") == key:
            return item.get("value")
    return None


# 无锚点的固定节点：不允许任何连线（蓝本 systemConfig showSourceHandle/showTargetHandle 均为 false）
_NO_HANDLE_NODE_TYPES = {"userGuide"}


def _allowed_source_keys(node: dict) -> set:
    """节点合法的普通出口分支 key（与前端 getNodeSourceHandleKeys 一致）。"""
    node_type = node.get("flowNodeType", "")
    if node_type == "ifElseNode":
        groups = _raw_input_value(node, "ifElseList") or []
        keys = {"IF" if i == 0 else f"ELSE IF {i}" for i in range(len(groups))}
        keys.add("ELSE")
        return keys
    if node_type == "classifyQuestion":
        agents = _raw_input_value(node, "agents") or []
        return {a.get("key") for a in agents if isinstance(a, dict) and a.get("key")}
    if node_type == "userSelect":
        options = _raw_input_value(node, "userSelectOptions") or []
        return {o.get("key") for o in options if isinstance(o, dict) and o.get("key")}
    return {"right"}


def _validate_list_schema(node: dict, problems: list[dict]) -> None:
    """分支/交互节点的列表 schema 校验：非空、key 非空且唯一（蓝本编辑器约束）。"""
    node_type = node.get("flowNodeType", "")
    name = node.get("name") or node.get("nodeId")
    specs = {
        "userSelect": ("userSelectOptions", "选项列表", "value"),
        "formInput": ("userInputForms", "表单项", "label"),
        "classifyQuestion": ("agents", "分类列表", "value"),
    }
    spec = specs.get(node_type)
    if spec is None:
        return
    input_key, label, text_field = spec
    rows = _raw_input_value(node, input_key)
    rows = rows if isinstance(rows, list) else []
    valid_rows = [r for r in rows if isinstance(r, dict)]
    if not valid_rows:
        problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"{label}不能为空"})
        return
    keys = [str(r.get("key") or "").strip() for r in valid_rows]
    if any(not k for k in keys):
        problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"{label}存在空 key 的条目"})
    if len(set(keys)) != len(keys):
        problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"{label}存在重复 key"})
    if any(not str(r.get(text_field) or "").strip() for r in valid_rows):
        problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"{label}存在未命名条目"})


def _validate_workflow_definition(workflow_json: str) -> list[dict]:
    """发布前原生校验（蓝本 SaveAndPublish 校验语义的 P0 子集）：
    - 节点类型必须在运行时支持范围内（EXECUTOR_METHODS ∪ 交互节点）；
    - 「流程开始」节点必须存在且唯一，系统配置节点至多一个；
    - required 输入必须有字面量值，或引用图内存在的节点与输出；
    - 分支/交互节点的选项、表单、分类列表非空且 key 唯一；
    - 连线两端必须指向存在的节点，出口 handle 必须是该节点真实存在的分支
      （判断器/分类/用户选择按当前配置计算，含 source_catch 错误边）；
    - 除开始/系统配置外，节点必须从开始节点可达。
    返回问题列表（空列表 = 通过）。
    """
    try:
        graph = parse_graph(workflow_json)
    except WorkflowExecutionError as exc:
        return [{"nodeId": "", "name": "", "reason": str(exc)}]

    problems: list[dict] = []
    nodes = {n.get("nodeId"): n for n in graph.get("nodes", []) if n.get("nodeId")}
    # A tool mounted to a ToolCall/Agent node receives its required arguments
    # from the model's function call at runtime.  Treating those inputs as
    # ordinary static node inputs makes every valid function-calling workflow
    # impossible to submit: the editor intentionally leaves them blank so the
    # model can provide source_file_id, field_values, etc. for each turn.
    tool_call_mounted_node_ids = {
        edge.get("target")
        for edge in graph.get("edges") or []
        if edge.get("sourceHandle") == "selectedTools"
        and nodes.get(edge.get("source"), {}).get("flowNodeType") in {"tools", "agent"}
    }

    start_ids = [nid for nid, n in nodes.items() if n.get("flowNodeType") == "workflowStart"]
    if not start_ids:
        problems.append({"nodeId": "", "name": "", "reason": "缺少「流程开始」节点"})
    elif len(start_ids) > 1:
        problems.append({"nodeId": start_ids[1], "name": "", "reason": "「流程开始」节点只能存在一个"})
    system_config_ids = [nid for nid, n in nodes.items() if n.get("flowNodeType") in _NO_HANDLE_NODE_TYPES]
    if len(system_config_ids) > 1:
        problems.append({"nodeId": system_config_ids[1], "name": "", "reason": "「系统配置」节点只能存在一个"})

    for node in nodes.values():
        node_type = node.get("flowNodeType", "")
        name = node.get("name") or node.get("nodeId")
        if node_type not in SUPPORTED_NODE_TYPES:
            problems.append(
                {"nodeId": node.get("nodeId"), "name": name, "reason": f"运行时不支持节点类型 {node_type}"}
            )
            continue
        # 动态节点结构检查：preview node 必带的来源字段缺失即拒绝（禁止前端自拼残缺节点）
        if node_type == "tool":
            tool_config = node.get("toolConfig") or {}
            if not any(tool_config.get(k) for k in ("systemTool", "httpTool", "mcpTool")):
                problems.append(
                    {"nodeId": node.get("nodeId"), "name": name, "reason": "tool 节点缺少 toolConfig（须经 previewNode 添加）"}
                )
        elif node_type in ("pluginModule", "appModule") and not node.get("pluginId"):
            problems.append(
                {"nodeId": node.get("nodeId"), "name": name, "reason": f"{node_type} 节点缺少 pluginId（须经 previewNode 添加）"}
            )
        _validate_list_schema(node, problems)
        if node_type == "workflowStart":
            continue  # 入口输入（userChatInput）由运行时注入
        is_model_invoked_tool = node_type == "tool" and node.get("nodeId") in tool_call_mounted_node_ids
        for item in node.get("inputs") or []:
            if not item.get("required"):
                continue
            value = item.get("value")
            label = item.get("label") or item.get("key")
            if _is_reference_value(value):
                ref_node_id, ref_key = value
                if ref_node_id == VARIABLE_NODE_ID:
                    continue  # 全局/系统变量运行时注入
                ref_node = nodes.get(ref_node_id)
                if ref_node is None:
                    problems.append(
                        {"nodeId": node.get("nodeId"), "name": name, "reason": f"必填输入「{label}」引用的节点已不存在"}
                    )
                elif not any(o.get("key") == ref_key for o in ref_node.get("outputs") or []):
                    problems.append(
                        {"nodeId": node.get("nodeId"), "name": name, "reason": f"必填输入「{label}」引用的输出 {ref_key} 不存在"}
                    )
            elif is_model_invoked_tool:
                # The model supplies this parameter when it invokes the tool.
                # Keep validating explicit references above, but allow a blank
                # literal value for a runtime function-call argument.
                continue
            elif value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, dict)) and not value):
                problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"必填输入「{label}」未填写"})

    for edge in graph.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes:
            problems.append({"nodeId": source or "", "name": "", "reason": "存在连接到不存在节点的连线"})
            continue
        source_name = nodes[source].get("name") or source
        target_name = nodes[target].get("name") or target
        if nodes[source].get("flowNodeType") in _NO_HANDLE_NODE_TYPES:
            problems.append({"nodeId": source, "name": source_name, "reason": "系统配置节点不能作为连线起点"})
            continue
        if nodes[target].get("flowNodeType") in _NO_HANDLE_NODE_TYPES:
            problems.append({"nodeId": target, "name": target_name, "reason": "系统配置节点不能作为连线终点"})
            continue
        source_handle = edge.get("sourceHandle") or ""
        # 工具边（蓝本裸 'selectedTools' handle）：源必须是 toolCall/agent，目标节点被挂载为工具
        if source_handle == "selectedTools":
            if nodes[source].get("flowNodeType") not in ("tools", "agent"):
                problems.append(
                    {"nodeId": source, "name": source_name, "reason": "工具锚点只能从工具调用/Agent 节点引出"}
                )
            if (edge.get("targetHandle") or "") != "selectedTools":
                problems.append(
                    {"nodeId": target, "name": target_name, "reason": "工具边目标必须是工具锚点（selectedTools）"}
                )
            continue
        catch_prefix = f"{source}-source_catch-"
        normal_prefix = f"{source}-source-"
        if source_handle.startswith(catch_prefix):
            if not nodes[source].get("catchError"):
                problems.append(
                    {"nodeId": source, "name": source_name, "reason": "存在错误捕获连线，但节点未开启报错捕获"}
                )
        elif source_handle.startswith(normal_prefix):
            branch_key = source_handle[len(normal_prefix):]
            if branch_key not in _allowed_source_keys(nodes[source]):
                problems.append(
                    {"nodeId": source, "name": source_name, "reason": f"连线出口分支 {branch_key} 在该节点当前配置中不存在"}
                )
        else:
            problems.append(
                {"nodeId": source, "name": source_name, "reason": f"连线出口 {source_handle} 不属于该节点"}
            )
        target_handle = edge.get("targetHandle") or ""
        if not target_handle.startswith(f"{target}-target-"):
            problems.append(
                {"nodeId": target, "name": target_name, "reason": f"连线入口 {target_handle} 不属于该节点"}
            )

    # 可达性：除开始/系统配置外，节点必须能从开始节点沿连线到达，否则永远不会执行
    if start_ids:
        reachable = {start_ids[0]}
        frontier = [start_ids[0]]
        adjacency: dict = {}
        for edge in graph.get("edges") or []:
            adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))
        while frontier:
            current = frontier.pop()
            for nxt in adjacency.get(current, []):
                if nxt in nodes and nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)
        for nid, node in nodes.items():
            if nid in reachable or node.get("flowNodeType") in _NO_HANDLE_NODE_TYPES | {"workflowStart"}:
                continue
            if node.get("parentNodeId"):
                continue  # 容器子节点由容器执行器驱动，不参与顶层可达性
            # 工具挂载节点经工具边可达（BFS 已含工具边），无需特判
            problems.append(
                {"nodeId": nid, "name": node.get("name") or nid, "reason": "节点未与流程连接（从开始节点不可达）"}
            )

    return problems


async def _validate_dynamic_node_resources(session, user, workflow_json: str, current_app_id: str) -> list[dict]:
    """动态节点资源校验（gap-audit §12.2 第 7/8 条）：目标存在、已发布、ACL 可见、禁自引用。"""
    from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP
    from app.services.gateway.tool_invoker import parse_config, parse_toolset_child_id

    try:
        graph = parse_graph(workflow_json)
    except WorkflowExecutionError:
        return []  # 结构校验已报错，这里不重复

    problems: list[dict] = []

    async def check_app(node: dict, name: str, target_id: str, *, need_published: bool = True) -> Optional[WorkflowApp]:
        if target_id == current_app_id:
            problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": "不能引用当前应用自身"})
            return None
        target = await session.get(WorkflowApp, target_id)
        if target is None:
            problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": "引用的应用/工具已不存在"})
            return None
        if await _share_permission(session, target, user) is None:
            problems.append(
                {"nodeId": node.get("nodeId"), "name": name, "reason": f"没有「{target.name}」的访问权限"}
            )
            return None
        if need_published and str(target.status) != "published":
            problems.append(
                {"nodeId": node.get("nodeId"), "name": name, "reason": f"「{target.name}」尚未发布，请先发布该资产"}
            )
            return None
        return target

    for node in graph.get("nodes") or []:
        node_type = node.get("flowNodeType", "")
        name = node.get("name") or node.get("nodeId")
        if node_type == "tool":
            tool_config = node.get("toolConfig") or {}
            if tool_config.get("systemTool"):
                tool_id = str(tool_config["systemTool"].get("toolId") or "")
                if tool_id not in BUILTIN_TOOL_MAP:
                    problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": f"系统工具 {tool_id} 不存在"})
            elif tool_config.get("httpTool") or tool_config.get("mcpTool"):
                conf = tool_config.get("httpTool") or tool_config.get("mcpTool")
                parsed = parse_toolset_child_id(str(conf.get("toolId") or ""))
                if not parsed:
                    problems.append({"nodeId": node.get("nodeId"), "name": name, "reason": "工具 ID 格式不合法"})
                    continue
                _, target_id, tool_name = parsed
                target = await check_app(node, name, target_id)
                if target is not None:
                    tool_names = {
                        str(i.get("name"))
                        for i in parse_config(target.config_json).get("toolList") or []
                        if isinstance(i, dict)
                    }
                    if tool_name not in tool_names:
                        problems.append(
                            {"nodeId": node.get("nodeId"), "name": name, "reason": f"工具「{tool_name}」已不在工具集清单中"}
                        )
        elif node_type in ("pluginModule", "appModule"):
            target_id = str(node.get("pluginId") or "")
            if target_id:
                await check_app(node, name, target_id)

    # 间接环检测（开发计划 Phase 3）：沿已发布定义 BFS 引用链（深度≤运行期上限），当前应用
    # 出现在任何下游链上即拒绝发布。直接自引用上面已挡；运行期 app_chain 守卫仍兜未发布态漂移。
    if not problems:
        problems.extend(await _detect_indirect_cycle(graph, current_app_id))

    return problems


def _collect_app_refs(graph: dict) -> list:
    """图里 appModule/pluginModule 引用的目标应用 id 列表。"""
    refs = []
    for node in graph.get("nodes") or []:
        if node.get("flowNodeType") in ("pluginModule", "appModule"):
            target = str(node.get("pluginId") or "")
            if target:
                refs.append(target)
    return refs


async def _detect_indirect_cycle(graph: dict, current_app_id: str) -> list:
    """有界 BFS：从当前草稿的直接引用出发，沿各目标的**已发布**定义展开引用链。

    深度 ≤ tool_invoker.MAX_SUB_WORKFLOW_DEPTH、总加载 ≤ 30 个应用（防慢校验）；
    引用链回到 current_app_id 即报环。定义缺失/解析失败跳过（运行期守卫兜底）。
    """
    from app.services.gateway.tool_invoker import MAX_SUB_WORKFLOW_DEPTH, load_published_definition

    frontier = [(rid, [current_app_id, rid]) for rid in _collect_app_refs(graph)]
    visited: set = set()
    loaded = 0
    while frontier and loaded < 30:
        next_frontier = []
        for target_id, chain in frontier:
            if target_id in visited or len(chain) > MAX_SUB_WORKFLOW_DEPTH + 1:
                continue
            visited.add(target_id)
            loaded += 1
            try:
                published = await load_published_definition(target_id)
                if not published:
                    continue
                sub_graph = parse_graph(published)
            except Exception:  # noqa: BLE001
                continue
            for ref in _collect_app_refs(sub_graph):
                if ref == current_app_id:
                    return [{
                        "nodeId": "", "name": "子应用引用链",
                        "reason": "检测到循环引用：" + " → ".join([*chain, ref]),
                    }]
                next_frontier.append((ref, [*chain, ref]))
        frontier = next_frontier
    return []


# 对话 Agent 不用画布编辑器，它的三节点图由配置页「保存草稿」时生成。用户还没保存过配置
# 就点发布，parse_graph 会报「工作流缺少画布模型（fastgpt 字段）」——对一个从没见过画布
# 的对话 Agent 作者来说这句话完全不可理解，这里换成能指路的说法。
_CHAT_AGENT_NOT_CONFIGURED_REASON = "对话 Agent 尚未完成配置：请先进入配置页选择模型、填写提示词并保存草稿"


def _is_missing_canvas_problem(problems: list[dict]) -> bool:
    """parse_graph 层面的失败（定义为空 / JSON 非法 / 无 fastgpt.nodes），而非节点级校验问题。"""
    if len(problems) != 1:
        return False
    reason = str(problems[0].get("reason") or "")
    return not problems[0].get("nodeId") and ("画布模型" in reason or "定义为空" in reason or "解析失败" in reason)


async def _validate_before_publish(session, user, workflow_json: str, app_id: str) -> None:
    app = await _get_app(session, app_id)
    problems = _validate_workflow_definition(workflow_json)
    if problems and app.ai_app_type == "chatAgent" and _is_missing_canvas_problem(problems):
        problems = [{"nodeId": "", "name": "", "reason": _CHAT_AGENT_NOT_CONFIGURED_REASON}]
    if not problems:
        problems = await _validate_dynamic_node_resources(session, user, workflow_json, app_id)
    if problems:
        # 硬拒绝（蓝本 SaveAndPublish 行为）：不提供「仍然发布」，前端按 problems 列表展示，
        # 并给「去配置」入口——400 不能是用户操作的终点。
        raise HTTPException(
            400,
            {"message": "工作流校验未通过，无法提交发布", "code": "definition_invalid", "problems": problems},
        )


async def _do_submit_review(
    session,
    app: WorkflowApp,
    workflow_json: str,
    note: Optional[str],
    user: UserContext,
    visible_role_ids: Any = None,
    visible_dept_ids: Any = None,
    routing_json: Optional[str] = None,
    publish_channels: tuple[str, ...] = ("marketplace",),
    embed_origins: tuple[str, ...] = (),
) -> WorkflowVersion:
    """WS2 强制审批：校验 → 存草稿 → 生成唯一 pending_review 版本快照。

    不触碰 published_json，线上运行版本保持不变（编辑/提交都不影响线上）。
    """
    app_id = app.id
    ai_app_type = app.ai_app_type
    config_snapshot = app.config_json
    before_status = app.status
    await _validate_before_publish(session, user, workflow_json, app_id)

    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
    ).scalar_one_or_none()
    if not definition:
        definition = WorkflowDefinition(id=uuid.uuid4().hex, app_id=app_id, published_version=0)
        session.add(definition)
    definition.draft_json = workflow_json

    # 同一应用只允许一个待审版本。作者必须先显式撤回，不能静默覆盖审核材料。
    prior_pending = (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app_id, WorkflowVersion.status == "pending_review"
            )
        )
    ).scalars().all()
    if prior_pending:
        raise HTTPException(409, "该智能体已有待审核版本，请先撤回审核后再重新提交")

    version = WorkflowVersion(
        id=uuid.uuid4().hex,
        app_id=app_id,
        version_no=await _next_version_no(session, app_id),
        ai_app_type=ai_app_type,
        definition_json=workflow_json,
        config_json=config_snapshot,
        status="pending_review",
        change_note=(note or "")[:1024],
        visible_role_ids=json.dumps(normalize_visible_ids(visible_role_ids), ensure_ascii=False),
        visible_dept_ids=json.dumps(normalize_visible_ids(visible_dept_ids), ensure_ascii=False),
        publish_channels=json.dumps(list(publish_channels), ensure_ascii=False),
        embed_origins_json=json.dumps(list(embed_origins), ensure_ascii=False),
        # 路由元数据随版本冻结（§九）：待审核期间不影响线上 Registry，approve 后才同步
        routing_json=routing_json,
        submitted_by=user.user_id,
        submitted_by_name=user.real_name or user.username,
    )
    session.add(version)
    # 草稿首次提交时显示待审核；已下架的应用要保留其下架态，不能在驳回/撤回
    # 时仅凭旧 published_json 又被误判为应恢复上线。待审事实以 version.status 为准。
    if app.status == "draft":
        app.status = "pending_review"
    record_app_audit(
        session,
        app=app,
        action="submit_review",
        actor=user,
        before={"status": before_status},
        after={"status": app.status, "versionNo": version.version_no},
    )
    await session.commit()
    await session.refresh(version)
    return version


async def _do_publish_now(
    session,
    app: WorkflowApp,
    workflow_json: str,
    note: Optional[str],
    user: UserContext,
    visible_role_ids: Any = None,
    visible_dept_ids: Any = None,
    routing_json: Optional[str] = None,
    publish_channels: tuple[str, ...] = ("marketplace",),
    embed_origins: tuple[str, ...] = (),
    review_comment: str = "审批关闭直接发布",
) -> WorkflowVersion:
    """直接发布：生成 approved 版本并即时上线。

    两种触发场景共用：
    - 审批关闭（PUBLISH_APPROVAL_REQUIRED=False）；
    - 审批开启但提交者本人就是审核员（见 _submit_or_publish 的自动通过分支），
      此时 review_comment 记为「审核员本人提交，自动通过」，版本记录里能看出不是人工审核。
    """
    before_status = app.status
    await _validate_before_publish(session, user, workflow_json, app.id)
    # 重新提交发布是明确的“上线”意图。app_info.status=0 只表示此前被下架，
    # 不能在新的已通过版本上继续把应用锁死为停用，否则前端会显示“通过”却始终“已下架”。
    definition = await _upsert_definition(session, app.id, workflow_json, publish=True)
    version = WorkflowVersion(
        id=uuid.uuid4().hex,
        app_id=app.id,
        version_no=definition.published_version,
        ai_app_type=app.ai_app_type,
        definition_json=workflow_json,
        config_json=app.config_json,
        status="approved",
        change_note=(note or "")[:1024],
        visible_role_ids=json.dumps(normalize_visible_ids(visible_role_ids), ensure_ascii=False),
        visible_dept_ids=json.dumps(normalize_visible_ids(visible_dept_ids), ensure_ascii=False),
        publish_channels=json.dumps(list(publish_channels), ensure_ascii=False),
        embed_origins_json=json.dumps(list(embed_origins), ensure_ascii=False),
        routing_json=routing_json,
        submitted_by=user.user_id,
        submitted_by_name=user.real_name or user.username,
        reviewed_by=user.user_id,
        reviewed_by_name=user.real_name or user.username,
        reviewed_at=func.now(),
        review_comment=review_comment,
        published_at=func.now(),
    )
    session.add(version)
    if app.status != "published":
        app.status = "published"
        app.published_at = func.now()
        app.published_by = user.user_id
    await sync_app_info_for_approved_version(session, app, version)
    record_app_audit(
        session,
        app=app,
        action="publish",
        actor=user,
        before={"status": before_status},
        after={"status": app.status, "versionNo": version.version_no},
    )
    await session.commit()
    await session.refresh(version)
    return version


async def _resolve_publish_visibility(
    session,
    app_id: str,
    provided_fields: set[str],
    visible_role_ids: Any,
    visible_dept_ids: Any,
) -> tuple[Any, Any]:
    """更新发布时继承线上版本的可见范围；显式提交的字段仍完全由本次请求决定。"""
    needs_roles = "visibleRoleIds" not in provided_fields
    needs_departments = "visibleDeptIds" not in provided_fields
    if not (needs_roles or needs_departments):
        return visible_role_ids, visible_dept_ids

    live = await load_published_visibility_version(session, app_id)
    if not live:
        return visible_role_ids, visible_dept_ids
    if needs_roles:
        visible_role_ids = normalize_visible_ids(live.visible_role_ids)
    if needs_departments:
        visible_dept_ids = normalize_visible_ids(live.visible_dept_ids)
    return visible_role_ids, visible_dept_ids


async def _resolve_publish_channels_and_origins(
    session,
    app_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Publishing always targets the marketplace; public exposure is app-level."""
    return ("marketplace",), ()


async def _mark_agent_api_access_invalidation_after_commit(app_id: str, reason: str) -> None:
    """Revoke durable API keys only after the caller has committed release state."""
    # 对外 Agent API 已整体移除：这里不再有 Key 可吊销，保留钩子位以维持发布流程调用点。
    logger.info("Agent API access invalidation skipped (feature removed) app=%s reason=%s", app_id, reason)


async def _submit_or_publish(payload: DefinitionSaveRequest, user: UserContext) -> dict:
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    # 复审二轮 #1：用 model_fields_set 区分「请求根本没带 routeMetadata」和「显式传入」。
    # - 未传（老前端/API 直调/弹窗未触碰路由字段）：继承当前线上版本快照，不清空；
    # - 显式传入：按本次内容保存——包括显式传空对象/null = 用户明确要求清空配置。
    route_meta_provided = "routeMetadata" in payload.model_fields_set
    routing_json = _normalize_route_metadata(payload.routeMetadata)
    async with async_session() as session:
        app, _ = await _require_permission(session, app_id, user, edit=True)
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
        ).scalar_one_or_none()
        _reject_unchanged_published_definition(app, definition, payload.workflowJson)
        if not route_meta_provided:
            live = await load_published_visibility_version(session, app_id)
            routing_json = getattr(live, "routing_json", None) if live else None
        visible_role_ids, visible_dept_ids = await _resolve_publish_visibility(
            session,
            app_id,
            payload.model_fields_set,
            payload.visibleRoleIds,
            payload.visibleDeptIds,
        )
        publish_channels, embed_origins = await _resolve_publish_channels_and_origins(
            session,
            app_id,
        )
        # 自动通过规则：平台只有 admin 一个管理员，同时也是唯一审核人。审核员自己
        # 提交再自己去「待审核」里点通过，纯属多一步；提交者本人具备审核权限
        # （is_reviewer：内置 admin / AGENT_ADMIN_ROLE_IDS / AGENT_REVIEWER_ROLE_IDS）
        # 时直接走发布，不进待审队列。普通用户仍然必须经审核员通过。
        self_reviewed = settings.PUBLISH_APPROVAL_REQUIRED and is_reviewer(user)
        if settings.PUBLISH_APPROVAL_REQUIRED and not self_reviewed:
            version = await _do_submit_review(
                session,
                app,
                payload.workflowJson,
                payload.changeNote,
                user,
                visible_role_ids,
                visible_dept_ids,
                routing_json=routing_json,
                publish_channels=publish_channels,
                embed_origins=embed_origins,
            )
            return {"version": _version_dict(version), "approvalRequired": True, "message": "已提交发布审核，等待审核员通过后上线"}
        if self_reviewed:
            # 别人（协作编辑者）提交的待审版本还挂着时，审核员直接发布会让那条待审记录
            # 变成「通过了也不会上线」的幽灵；要求先在待审核里处理掉，和 _do_submit_review
            # 的「同一应用只允许一个待审版本」保持一致。
            await _reject_if_pending_review_exists(session, app_id)
        version = await _do_publish_now(
            session,
            app,
            payload.workflowJson,
            payload.changeNote,
            user,
            visible_role_ids,
            visible_dept_ids,
            routing_json=routing_json,
            publish_channels=publish_channels,
            embed_origins=embed_origins,
            review_comment="审核员本人提交，自动通过" if self_reviewed else "审批关闭直接发布",
        )
        await capability_registry.sync_from_app(app_id)
        return {
            "version": _version_dict(version),
            "approvalRequired": False,
            "autoApproved": self_reviewed,
            "message": "你是审核员，已自动通过并上线" if self_reviewed else "已完成发布处理",
        }


async def _reject_if_pending_review_exists(session, app_id: str) -> None:
    """审核员直接发布前的守卫：应用已有待审版本时拒绝，提示先去待审核里处理。"""
    pending = (
        await session.execute(
            select(WorkflowVersion.id).where(
                WorkflowVersion.app_id == app_id, WorkflowVersion.status == "pending_review"
            ).limit(1)
        )
    ).scalar_one_or_none()
    if pending:
        raise HTTPException(409, "该智能体已有待审核版本，请先在「待审核」中通过或驳回后再发布")


@router.post("/definition/publish")
async def publish_definition(payload: DefinitionSaveRequest, user: UserContext = Depends(current_user)):
    """WS2：编辑器「提交发布」。强制审批开启时进入待审队列，不再即时上线。"""
    return await _submit_or_publish(payload, user)


@router.post("/definition/submitReview")
async def submit_review_definition(payload: DefinitionSaveRequest, user: UserContext = Depends(current_user)):
    """显式提交审核端点（语义同 publish；前端可任选其一）。"""
    return await _submit_or_publish(payload, user)


# ---------- 发布审批（WS2）：审核台 ----------

async def _reset_app_status_after_pending_clear(session, app: WorkflowApp) -> None:
    """撤回/驳回后：若应用仍是 pending_review 态，回落到 draft 或（有线上版本时）published。"""
    if app.status != "pending_review":
        return
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app.id))
    ).scalar_one_or_none()
    has_live = bool(definition and definition.published_json)
    app.status = "published" if has_live else "draft"


@router.get("/review/page")
async def review_page(
    pageNo: int = 1,
    pageSize: int = 20,
    status: str = "pending_review",
    aiAppType: Optional[str] = None,
    keyword: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    await _require_review_permission(user)
    async with async_session() as session:
        query = select(WorkflowVersion, WorkflowApp).join(WorkflowApp, WorkflowApp.id == WorkflowVersion.app_id)
        if status and status != "all":
            query = query.where(WorkflowVersion.status == status)
        if aiAppType:
            query = query.where(WorkflowVersion.ai_app_type == aiAppType)
        if keyword:
            like = f"%{keyword}%"
            query = query.where(
                or_(
                    WorkflowApp.name.like(like),
                    WorkflowApp.owner_username.like(like),
                    WorkflowVersion.submitted_by_name.like(like),
                )
            )
        total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (
            await session.execute(
                query.order_by(WorkflowVersion.submitted_at.desc()).offset((pageNo - 1) * pageSize).limit(pageSize)
            )
        ).all()
        fallback_names = {}
        user_ids = []
        for v, app in rows:
            owner_id = str(app.owner_user_id or "")
            submitter_id = str(v.submitted_by or "")
            user_ids.extend([owner_id, submitter_id])
            fallback_names[owner_id] = app.owner_username or ""
            fallback_names[submitter_id] = v.submitted_by_name or ""
        display_names = await load_user_display_names(session, user_ids, fallback_names)
        records = []
        for v, app in rows:
            version_data = _version_dict(v)
            version_data["submittedByName"] = display_names.get(v.submitted_by, version_data.get("submittedByName") or "")
            records.append(
                {
                    **version_data,
                    "appName": app.name,
                    "ownerUserId": app.owner_user_id,
                    "ownerUsername": display_names.get(app.owner_user_id, app.owner_username or ""),
                }
            )
        return {"records": records, "total": total, "size": pageSize, "current": pageNo}


@router.post("/review/approve")
async def review_approve(payload: ReviewActionRequest, user: UserContext = Depends(current_user)):
    await _require_review_permission(user)
    async with async_session() as session:
        version = await session.get(WorkflowVersion, payload.versionId)
        if not version:
            raise HTTPException(404, "版本不存在")
        if version.status != "pending_review":
            raise HTTPException(400, "该版本不在待审核状态")
        app = await session.get(WorkflowApp, version.app_id)
        if not app:
            raise HTTPException(404, "应用不存在")
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == version.app_id))
        ).scalar_one_or_none()
        if not definition:
            definition = WorkflowDefinition(id=uuid.uuid4().hex, app_id=version.app_id, published_version=0)
            session.add(definition)
        # 审核按钮的文案和业务含义都是“通过并上线”。此前的下架标记应被这次
        # 已通过版本覆盖，而不是让 app.status/app_info.status 永远维持 0。
        # 提升为线上：写回 published_json + 版本指针，并同步恢复目录可见状态。
        definition.published_json = version.definition_json
        definition.published_version = version.version_no
        definition.status = "published"
        version.status = "approved"
        version.reviewed_by = user.user_id
        version.reviewed_by_name = user.real_name or user.username
        version.reviewed_at = func.now()
        version.review_comment = (payload.comment or "")[:1024]
        version.published_at = func.now()
        app.status = "published"
        app.published_at = func.now()
        app.published_by = version.submitted_by
        await sync_app_info_for_approved_version(session, app, version)
        record_app_audit(
            session,
            app=app,
            action="approve_review",
            actor=user,
            before={"status": "pending_review", "versionNo": version.version_no},
            after={"status": app.status, "versionNo": version.version_no},
        )
        await session.commit()
        await session.refresh(version)
    # 同步 Capability Registry（Phase 7）：上线后登记路由候选与能力
    await capability_registry.sync_from_app(version.app_id)
    return {"version": _version_dict(version), "message": "已通过并上线"}


@router.post("/review/reject")
async def review_reject(payload: ReviewActionRequest, user: UserContext = Depends(current_user)):
    await _require_review_permission(user)
    if not (payload.comment or "").strip():
        raise HTTPException(400, "驳回必须填写意见")
    async with async_session() as session:
        version = await session.get(WorkflowVersion, payload.versionId)
        if not version:
            raise HTTPException(404, "版本不存在")
        if version.status != "pending_review":
            raise HTTPException(400, "该版本不在待审核状态")
        version.status = "rejected"
        version.reviewed_by = user.user_id
        version.reviewed_by_name = user.real_name or user.username
        version.reviewed_at = func.now()
        version.review_comment = payload.comment.strip()[:1024]
        app = await session.get(WorkflowApp, version.app_id)
        if app:
            await _reset_app_status_after_pending_clear(session, app)
            record_app_audit(
                session,
                app=app,
                action="reject_review",
                actor=user,
                reason=version.review_comment,
                before={"status": "pending_review", "versionNo": version.version_no},
                after={"status": app.status, "versionNo": version.version_no},
            )
        await session.commit()
        await session.refresh(version)
        return {"version": _version_dict(version), "message": "已驳回，已退回作者草稿"}


@router.post("/review/cancel")
async def review_cancel(payload: ReviewActionRequest, user: UserContext = Depends(current_user)):
    """作者撤回自己的待审提交（审核员也可撤回）。"""
    async with async_session() as session:
        version = await session.get(WorkflowVersion, payload.versionId)
        if not version:
            raise HTTPException(404, "版本不存在")
        if version.submitted_by != user.user_id and not is_reviewer(user):
            raise HTTPException(403, "只能撤回自己提交的版本")
        if version.status != "pending_review":
            raise HTTPException(400, "该版本不在待审核状态")
        version.status = "cancelled"
        app = await session.get(WorkflowApp, version.app_id)
        if app:
            await _reset_app_status_after_pending_clear(session, app)
            record_app_audit(
                session,
                app=app,
                action="cancel_review",
                actor=user,
                before={"status": "pending_review", "versionNo": version.version_no},
                after={"status": app.status, "versionNo": version.version_no},
            )
        await session.commit()
        return {"message": "已撤回提交"}


# ---------- 版本历史（WS3） ----------

@router.get("/version/page")
async def version_page(
    appId: str,
    pageNo: int = 1,
    pageSize: int = 20,
    user: UserContext = Depends(current_user),
):
    async with async_session() as session:
        # 平台管理员可查看任意应用的版本历史（后台管理台回滚需要）；否则须有应用权限
        if not is_platform_admin(user):
            await _require_permission(session, appId, user)
        query = select(WorkflowVersion).where(WorkflowVersion.app_id == appId)
        total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (
            await session.execute(
                query.order_by(WorkflowVersion.version_no.desc()).offset((pageNo - 1) * pageSize).limit(pageSize)
            )
        ).scalars().all()
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == appId))
        ).scalar_one_or_none()
        live_version = definition.published_version if definition else 0
        display_names = await load_user_display_names(
            session,
            [v.submitted_by for v in rows],
            {str(v.submitted_by or ""): v.submitted_by_name or "" for v in rows},
        )
        records = []
        for v in rows:
            version_data = _version_dict(v)
            version_data["submittedByName"] = display_names.get(v.submitted_by, version_data.get("submittedByName") or "")
            version_data["isLive"] = v.version_no == live_version and v.status == "approved"
            records.append(version_data)
        return {"records": records, "total": total, "size": pageSize, "current": pageNo, "liveVersion": live_version}


@router.get("/version/detail")
async def version_detail(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        version = await session.get(WorkflowVersion, id)
        if not version:
            raise HTTPException(404, "版本不存在")
        # 版本快照含 definitionJson/configJson（httpToolSet 类可能带工具鉴权配置，画布节点
        # 也可能带 system_header_secret）：仅审核员/平台管理员，或对该应用有访问权限
        # （OWNER/EDITOR/VIEWER）者可看，其余 403。
        if not is_reviewer(user):  # is_reviewer 已含平台管理员
            await _require_permission(session, version.app_id, user)
        data = _version_dict(version, with_json=True)
        display_names = await load_user_display_names(
            session,
            [version.submitted_by],
            {str(version.submitted_by or ""): version.submitted_by_name or ""},
        )
        data["submittedByName"] = display_names.get(version.submitted_by, data.get("submittedByName") or "")
        return data


async def _rollback_to_version(
    session,
    app: WorkflowApp,
    target_version_no: int,
    user: UserContext,
    *,
    review_comment: str,
) -> tuple[int, bool]:
    """Clone one approved history snapshot into a new live version inside the caller's transaction."""
    pending_version = (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app.id,
                WorkflowVersion.status == "pending_review",
            )
        )
    ).scalar_one_or_none()
    if pending_version:
        raise HTTPException(409, "当前有更新审核中，请先撤回审核后再回滚")

    target = (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app.id,
                WorkflowVersion.version_no == target_version_no,
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "目标版本不存在")
    if target.status not in ("approved", "archived"):
        raise HTTPException(400, "只能回滚到已通过的历史版本")
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app.id))
    ).scalar_one_or_none()
    if not definition:
        raise HTTPException(400, "该应用没有工作流定义")
    new_no = await _next_version_no(session, app.id)
    new_version = WorkflowVersion(
        id=uuid.uuid4().hex,
        app_id=app.id,
        version_no=new_no,
        ai_app_type=app.ai_app_type,
        definition_json=target.definition_json,
        config_json=target.config_json,
        status="approved",
        change_note=f"回滚自 v{target.version_no}",
        visible_role_ids=target.visible_role_ids,
        visible_dept_ids=target.visible_dept_ids,
        publish_channels='["marketplace"]',
        embed_origins_json='[]',
        routing_json=getattr(target, "routing_json", None),
        submitted_by=user.user_id,
        submitted_by_name=user.real_name or user.username,
        reviewed_by=user.user_id,
        reviewed_by_name=user.real_name or user.username,
        reviewed_at=func.now(),
        review_comment=review_comment,
        published_at=func.now(),
    )
    session.add(new_version)
    definition.published_json = target.definition_json
    definition.published_version = new_no
    definition.status = "published"
    app.config_json = target.config_json or "{}"
    app.status = "published"
    app.published_at = func.now()
    app.published_by = user.user_id
    await sync_app_info_for_approved_version(session, app, new_version)
    return new_no, False


@router.post("/version/rollback")
async def rollback_version(payload: RollbackRequest, user: UserContext = Depends(current_user)):
    """Allow an owner to restore an approved history snapshot without taking the app offline."""
    async with async_session() as session:
        app, _ = await _require_permission(session, payload.appId, user, owner=True)
        new_no, should_invalidate_api = await _rollback_to_version(
            session,
            app,
            payload.versionNo,
            user,
            review_comment="所有者回滚",
        )
        await session.commit()
    if should_invalidate_api:
        await _mark_agent_api_access_invalidation_after_commit(payload.appId, "api_channel_removed_by_rollback")
    await capability_registry.sync_from_app(payload.appId)
    return {"message": f"已回滚到 v{payload.versionNo}（新线上版本 v{new_no}）", "liveVersion": new_no}


# ---------- 后台管理（WS4，跨用户，平台管理员或智能体管理页面权限） ----------

def normalize_ai_app_types(aiAppType: Optional[str] = None, aiAppTypes: Optional[str] = None) -> list[str]:
    raw_values = [aiAppType] if aiAppType else (aiAppTypes or "").split(",")
    result: list[str] = []
    for item in raw_values:
        value = (item or "").strip()
        if value and value not in result:
            result.append(value)
    return result


@router.get("/admin/app/page")
async def admin_app_page(
    pageNo: int = 1,
    pageSize: int = 20,
    keyword: Optional[str] = None,
    aiAppType: Optional[str] = None,
    aiAppTypes: Optional[str] = None,
    status: Optional[str] = None,
    ownerUserId: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """统一分页工作台应用与已登记的定制助手，需智能体管理权限。"""
    await _require_agent_manage_permission(user)
    if pageNo < 1 or not 1 <= pageSize <= 100:
        raise HTTPException(400, "分页参数不合法")
    async with async_session() as session:
        query = select(WorkflowApp)
        app_types = normalize_ai_app_types(aiAppType, aiAppTypes)
        builtins = (
            await list_managed_builtins(session, keyword=keyword, status=status, owner_user_id=ownerUserId)
            if not app_types or "builtin" in app_types else []
        )
        if len(app_types) == 1:
            query = query.where(WorkflowApp.ai_app_type == app_types[0])
        elif app_types:
            query = query.where(WorkflowApp.ai_app_type.in_(app_types))
        if status:
            query = query.where(WorkflowApp.status == status)
        if ownerUserId:
            query = query.where(WorkflowApp.owner_user_id == ownerUserId)
        if keyword:
            like = f"%{keyword}%"
            query = query.where(
                or_(
                    WorkflowApp.name.like(like),
                    WorkflowApp.description.like(like),
                    WorkflowApp.owner_username.like(like),
                )
            )
        total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        # Registered assistants lead the directory. Offset both sources before reading
        # a page so search, totals and pagination remain consistent.
        offset = (pageNo - 1) * pageSize
        builtin_page = builtins[offset:offset + pageSize]
        rows = (
            await session.execute(
                query.order_by(WorkflowApp.update_time.desc(), WorkflowApp.id)
                .offset(max(0, offset - len(builtins))).limit(pageSize - len(builtin_page))
            )
        ).scalars().all()
        display_names = await load_user_display_names(
            session,
            [app.owner_user_id for app in rows],
            {str(app.owner_user_id or ""): app.owner_username or "" for app in rows},
        )
        records = [
            {**_app_dict(app, "ADMIN"), "ownerUsername": display_names.get(app.owner_user_id, app.owner_username or "")}
            for app in rows
        ]
        return {"records": builtin_page + records, "total": total + len(builtins), "size": pageSize, "current": pageNo}


async def _load_live_version(session, app_id: str, published_version: int | None = None) -> Optional[WorkflowVersion]:
    query = select(WorkflowVersion).where(WorkflowVersion.app_id == app_id)
    if published_version:
        query = query.where(WorkflowVersion.version_no == published_version)
    else:
        query = query.where(WorkflowVersion.status == "approved").order_by(WorkflowVersion.version_no.desc())
    return (await session.execute(query)).scalars().first()


async def _load_version_pair(session, app_id: str, base_version_no: int, target_version_no: int):
    rows = (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app_id,
                WorkflowVersion.version_no.in_([base_version_no, target_version_no]),
            )
        )
    ).scalars().all()
    versions = {int(row.version_no): row for row in rows}
    if base_version_no not in versions or target_version_no not in versions:
        raise HTTPException(400, "版本不属于该应用")
    return versions[base_version_no], versions[target_version_no]


@router.get("/admin/app/{app_id}/detail")
async def admin_app_detail(app_id: str, user: UserContext = Depends(current_user)):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        if is_managed_builtin_id(app_id):
            return build_builtin_admin_detail(await get_managed_builtin(session, app_id))
        app = await _get_app(session, app_id)
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
        ).scalars().first()
        live_version = await _load_live_version(session, app_id, getattr(definition, "published_version", None))
        audits = (
            await session.execute(
                select(WorkflowAdminAudit)
                .where(WorkflowAdminAudit.app_id == app_id)
                .order_by(WorkflowAdminAudit.create_time.desc())
                .limit(20)
            )
        ).scalars().all()
        versions = (
            await session.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.app_id == app_id)
                .order_by(WorkflowVersion.version_no.desc())
                .limit(50)
            )
        ).scalars().all()
        app_data = _app_dict(app, "ADMIN", definition=definition)
        app_data.pop("configJson", None)
        display_names = await load_user_display_names(
            session,
            [app.owner_user_id],
            {str(app.owner_user_id or ""): app.owner_username or ""},
        )
        return {
            "app": {
                **app_data,
                "ownerUsername": display_names.get(app.owner_user_id, app.owner_username or ""),
            },
            "summary": build_admin_app_summary(app, definition, live_version),
            "audits": [
                {
                    "id": row.id,
                    "action": row.action,
                    "actorUsername": row.actor_username,
                    "targetUserId": row.target_user_id,
                    "reason": row.reason,
                    "createdAt": row.create_time.isoformat() if row.create_time else None,
                }
                for row in audits
            ],
            "versions": [_version_dict(version) for version in versions],
        }


@router.get("/app/{app_id}/evaluation-runs")
async def owner_app_evaluation_runs(
    app_id: str,
    startAt: Optional[str] = None,
    endAt: Optional[str] = None,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    minDurationMs: Optional[int] = None,
    minTotalTokens: Optional[int] = None,
    pageNo: int = 1,
    pageSize: int = 20,
    user: UserContext = Depends(current_user),
):
    """List retained workflow executions for an editor's evaluation workbench."""
    async with async_session() as session:
        await _require_permission(session, app_id, user, edit=True)
    try:
        return await list_workflow_evaluation_runs(
            app_id=app_id,
            start_at=startAt,
            end_at=endAt,
            status=status,
            mode=mode,
            min_duration_ms=minDurationMs,
            min_total_tokens=minTotalTokens,
            page_no=pageNo,
            page_size=pageSize,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/app/{app_id}/evaluation-runs/{run_id}")
async def owner_app_evaluation_run_detail(
    app_id: str, run_id: str, user: UserContext = Depends(current_user),
):
    """Return node trace plus linked model-attempt usage for one workflow run."""
    async with async_session() as session:
        await _require_permission(session, app_id, user, edit=True)
    try:
        detail = await get_workflow_evaluation_run(app_id=app_id, run_id=run_id)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if detail is None:
        raise HTTPException(404, "评测运行记录不存在")
    return detail


@router.get("/admin/app/{app_id}/version-diff")
async def admin_app_version_diff(
    app_id: str,
    baseVersionNo: int,
    targetVersionNo: int,
    user: UserContext = Depends(current_user),
):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        await _get_app(session, app_id)
        base, target = await _load_version_pair(session, app_id, baseVersionNo, targetVersionNo)
        return build_version_diff(base, target)


@router.post("/admin/app/restore")
async def admin_restore(payload: AdminRestoreRequest, user: UserContext = Depends(current_user)):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        app = await _get_app(session, payload.appId)
        if app.status != "unpublished":
            raise HTTPException(400, "只有已下架的应用可以恢复上线")
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == payload.appId))
        ).scalars().first()
        if not definition or not definition.published_json:
            raise HTTPException(400, "该应用没有可恢复的线上版本")
        live_version = await _load_live_version(session, app.id, definition.published_version)
        if not live_version:
            raise HTTPException(400, "该应用没有可恢复的线上版本快照")
        before = {"status": app.status}
        app.status = "published"
        await sync_app_info_for_approved_version(session, app, live_version)
        record_app_audit(session, app=app, action="restore", actor=user, reason=payload.reason, before=before, after={"status": app.status})
        await session.commit()
    await capability_registry.sync_from_app(payload.appId)
    return {"success": True}


@router.post("/admin/app/transfer-owner")
async def admin_transfer_owner(payload: AdminTransferOwnerRequest, user: UserContext = Depends(current_user)):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        app = await _get_app(session, payload.appId)
        if app.owner_user_id == payload.targetUserId:
            raise HTTPException(400, "目标用户已经是当前负责人")
        target = (
            await session.execute(
                text("SELECT id, username, realname FROM sys_user WHERE id = :user_id "
                     "AND (del_flag = '0' OR del_flag IS NULL OR del_flag = '')"),
                {"user_id": payload.targetUserId},
            )
        ).mappings().first()
        if not target:
            raise HTTPException(400, "目标负责人不存在或已停用")
        previous_owner_id, previous_owner_name = app.owner_user_id, app.owner_username
        app.owner_user_id = str(target["id"])
        app.owner_username = str(target.get("username") or target.get("realname") or "")
        if payload.retainPreviousOwnerAsEditor and previous_owner_id:
            existing = (
                await session.execute(
                    select(WorkflowAcl).where(
                        WorkflowAcl.app_id == app.id,
                        WorkflowAcl.subject_type == "USER",
                        WorkflowAcl.subject_id == previous_owner_id,
                    )
                )
            ).scalars().first()
            if existing:
                existing.permission = "EDITOR"
            else:
                session.add(WorkflowAcl(
                    id=uuid.uuid4().hex,
                    app_id=app.id,
                    subject_type="USER",
                    subject_id=previous_owner_id,
                    permission="EDITOR",
                ))
        record_app_audit(
            session,
            app=app,
            action="transfer_owner",
            actor=user,
            reason=payload.reason,
            target_user_id=app.owner_user_id,
            before={"ownerUserId": previous_owner_id, "ownerUsername": previous_owner_name},
            after={"ownerUserId": app.owner_user_id, "ownerUsername": app.owner_username},
        )
        if app.status == "published":
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app.id))
            ).scalars().first()
            live_version = await _load_live_version(session, app.id, getattr(definition, "published_version", None))
            if not live_version:
                raise HTTPException(400, "已发布应用缺少线上版本，无法转移负责人")
            await sync_app_info_for_approved_version(session, app, live_version)
        await session.commit()
    await _mark_agent_api_access_invalidation_after_commit(payload.appId, "owner_changed")
    if app.status == "published":
        await capability_registry.sync_from_app(payload.appId)
    return {"success": True, "ownerUserId": app.owner_user_id, "ownerUsername": app.owner_username}


@router.post("/admin/app/unpublish")
async def admin_unpublish(id: str, user: UserContext = Depends(current_user)):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        app = await _get_app(session, id)
        before = {"status": app.status}
        await _cancel_pending_versions_for_unpublish(session, id)
        app.status = "unpublished"
        await set_app_info_catalog_status(session, id, enabled=False)
        record_app_audit(session, app=app, action="unpublish", actor=user, before=before, after={"status": app.status})
        await session.commit()
        await session.refresh(app)
        display_names = await load_user_display_names(
            session,
            [app.owner_user_id],
            {str(app.owner_user_id or ""): app.owner_username or ""},
        )
        result = {**_app_dict(app, "ADMIN"), "ownerUsername": display_names.get(app.owner_user_id, app.owner_username or "")}
    await _mark_agent_api_access_invalidation_after_commit(id, "app_unpublished")
    await capability_registry.sync_from_app(id)  # 下架 → 从路由候选移除
    return result


@router.delete("/admin/app/delete")
async def admin_delete(id: str, user: UserContext = Depends(current_user)):
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        app = await _get_app(session, id)
        record_app_audit(session, app=app, action="delete", actor=user, before=_app_audit_snapshot(app))
        await retire_app_info_catalog_entry(session, id)
        await session.execute(sa_delete(WorkflowVersion).where(WorkflowVersion.app_id == id))
        await session.execute(sa_delete(WorkflowDefinition).where(WorkflowDefinition.app_id == id))
        await session.execute(sa_delete(WorkflowAcl).where(WorkflowAcl.app_id == id))
        await session.execute(sa_delete(WorkflowApp).where(WorkflowApp.id == id))
        try:
            await retire_orphaned_agent_catalog_entries(session)
        except Exception:
            logger.exception("管理员删除后清理广场目录残留失败 app=%s", id)
        await session.commit()
    await _mark_agent_api_access_invalidation_after_commit(id, "app_deleted")
    await capability_registry.remove(id)
    return {"success": True}


@router.post("/admin/app/rollback")
async def admin_rollback(payload: RollbackRequest, user: UserContext = Depends(current_user)):
    """回滚到某历史已通过版本：克隆为新 approved 版本并立即上线（版本号单调递增、留审计）。"""
    await _require_agent_manage_permission(user)
    async with async_session() as session:
        app = await _get_app(session, payload.appId)
        new_no, should_invalidate_api = await _rollback_to_version(
            session,
            app,
            payload.versionNo,
            user,
            review_comment="管理员回滚",
        )
        record_app_audit(
            session,
            app=app,
            action="rollback",
            actor=user,
            before={"targetVersionNo": payload.versionNo},
            after={"liveVersion": new_no},
        )
        await session.commit()
    if should_invalidate_api:
        await _mark_agent_api_access_invalidation_after_commit(payload.appId, "api_channel_removed_by_rollback")
    await capability_registry.sync_from_app(payload.appId)
    return {"message": f"已回滚到 v{payload.versionNo}（新线上版本 v{new_no}）", "liveVersion": new_no}


# ---------- 独立 Agent 运行页会话（WS5，按 app 维度隔离，复用 ai_chat_threads/messages） ----------

class RunSessionCreate(BaseModel):
    appId: str
    title: Optional[str] = None


class RunMessageAppend(BaseModel):
    sessionId: str
    role: str
    content: str
    # 只存附件卡元数据（不存文档正文/原图），与主对话 attachments_json 同口径。
    attachments: Optional[list[dict[str, Any]]] = None
    runId: Optional[str] = None
    turnId: Optional[str] = Field(default=None, max_length=64)
    status: Optional[Literal["completed", "partial", "failed", "cancelled", "interrupted"]] = None
    # Assistant messages persist only owned file IDs; display metadata is
    # hydrated from 我的文件 when history is read back.
    generatedFiles: Optional[list[dict[str, Any]]] = None
    # 编辑重发：先删掉 fromId 及其之后的消息，再写入本条。
    truncateFromId: Optional[int] = None


_RUN_ATTACHMENT_EXTS = (
    "doc", "docx", "pdf", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "md", "markdown",
    "json", "png", "jpg", "jpeg", "webp", "bmp", "gif",
)
_LEGACY_QUOTED_FILE_RE = re.compile(
    rf"《([^\n《》]{{1,220}}?\.(?:{'|'.join(_RUN_ATTACHMENT_EXTS)}))》",
    re.IGNORECASE,
)


def _attachment_kind_from_filename(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"png", "jpg", "jpeg", "webp", "bmp", "gif"}:
        return "image"
    if ext in {"doc", "docx"}:
        return "docx"
    if ext in {"ppt", "pptx"}:
        return "pptx"
    if ext == "pdf":
        return "pdf"
    return "text"


def _legacy_run_attachments(content: str, origin: Optional[str]) -> Optional[list[dict]]:
    """兼容旧子智能体历史：过去只在文字里写「交付文件：《xxx.docx》」。

    仅委派会话或显式 [附件]/交付文件标记才推断，避免普通聊天提到文件名就误画卡。"""
    value = str(content or "")
    marked = origin == "delegation" or "交付文件" in value or "[附件]" in value
    if not marked:
        return None
    names = [m.group(1).strip() for m in _LEGACY_QUOTED_FILE_RE.finditer(value)]
    for line in value.splitlines():
        if line.strip().startswith("[附件]"):
            tail = line.split("]", 1)[-1]
            names.extend(part.strip() for part in re.split(r"[、,，]", tail) if "." in part)
    seen: set[str] = set()
    out = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"filename": name, "kind": _attachment_kind_from_filename(name), "status": "ok"})
    return out or None


def _run_message_attachments(row: ChatMessage, origin: Optional[str]) -> Optional[list[dict]]:
    if row.role != "user":
        return None
    if row.attachments_json:
        try:
            parsed = json.loads(row.attachments_json)
            if isinstance(parsed, list) and parsed:
                return _attachments_meta(parsed) or None
        except Exception:  # noqa: BLE001
            pass
    return _legacy_run_attachments(row.content, origin)


def _run_message_sender_type(row: ChatMessage, thread_origin: Optional[str]) -> Optional[str]:
    """返回用户角色消息的真实发送方。

    新数据以消息级 sender_type 为准。上线前的委派历史没有这个字段，只能暂时
    沿用会话 origin 回放；补列后的人类追问会显式写 human，不再被该降级误标。
    """
    if row.role != "user":
        return None
    if row.sender_type in ("human", "work_agent"):
        return row.sender_type
    return "work_agent" if thread_origin == "delegation" else "human"


def _attachment_file_ids(raw: Any) -> list[str]:
    """Extract opaque user-file identities from persisted attachment metadata."""
    if not isinstance(raw, list):
        return []
    return [
        str(item.get("file_id") or item.get("fileId") or "").strip()
        for item in raw
        if isinstance(item, dict) and str(item.get("file_id") or item.get("fileId") or "").strip()
    ]


async def _run_session_user_file_ids(session, session_id: str) -> list[str]:
    """Return prior user attachment IDs in session order without exposing metadata."""
    rows = (
        await session.execute(
            select(ChatMessage.attachments_json)
            .where(ChatMessage.thread_id == session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        )
    ).scalars().all()
    file_ids: list[str] = []
    for value in rows:
        try:
            file_ids.extend(_attachment_file_ids(json.loads(value or "[]")))
        except Exception:  # noqa: BLE001
            continue
    return list(dict.fromkeys(file_ids))[:5]


async def _run_message_generated_files(row: ChatMessage, user_id: str) -> list[dict]:
    if row.role != "assistant" or not row.attachments_json:
        return []
    try:
        entries = json.loads(row.attachments_json)
    except Exception:  # noqa: BLE001
        return []
    file_ids = [
        str(item.get("file_id") or item.get("id") or "").strip()
        for item in entries if isinstance(item, dict) and str(item.get("kind") or "") == "generated"
    ]
    if not file_ids:
        return []
    from app.services.files import user_file_service
    files = await user_file_service.get_files_by_ids(user_id, file_ids)
    return [
        {
            "id": str(item.get("id") or ""),
            "filename": str(item.get("filename") or ""),
            "mime": str(item.get("mime") or ""),
            "size": int(item.get("size") or 0),
            "source": str(item.get("source") or "generated"),
            "versionNo": int(item.get("versionNo") or 1),
            "deliverable": bool(item.get("deliverable", True)),
        }
        for item in files
        if item.get("id") and item.get("filename")
    ]


def _run_session_dict(t: ChatThread) -> dict:
    return {
        "id": t.id,
        "appId": t.app_id,
        "aiAppType": t.ai_app_type,
        "title": t.title or "新对话",
        # 主对话委派会话携带来源主对话 id：悬浮窗从某个主对话打开时优先选中对应会话。
        # origin='delegation' 是稳定的委派标记（删来源主对话会清空 parentThreadId，origin 不受影响）——
        # 悬浮窗据此判定「是否委派」，不再仅凭可空的 parentThreadId，避免孤儿委派被误当独立对话。
        "parentThreadId": t.parent_thread_id,
        "origin": t.origin,
        "pinned": bool(t.pinned),
        "createTime": t.created_at.isoformat() if t.created_at else None,
        "updateTime": t.updated_at.isoformat() if t.updated_at else None,
    }


async def _get_run_session(session, session_id: str, user: UserContext) -> ChatThread:
    thread = await session.get(ChatThread, session_id)
    if not thread or thread.user_id != user.user_id or not thread.app_id:
        raise HTTPException(404, "运行会话不存在")
    return thread


@router.get("/run/app")
async def run_app_meta(
    appId: str,
    previewVersionId: Optional[str] = None,
    previewDraft: Optional[bool] = None,
    user: UserContext = Depends(current_user),
):
    """运行页应用元信息 + 使用权限校验（按发布可见角色/部门控制）。"""
    async with async_session() as session:
        if previewDraft:
            # 草稿预览：用最新 draft_json，要求编辑权限
            app = await _get_app(session, appId)
            workflow_json = await _load_draft_preview_workflow(session, appId, user)
            data = _app_dict(app, "EDITOR")
            data["previewMode"] = True
            data.update(_extract_run_chat_config(workflow_json, user))
            return data
        if previewVersionId:
            app = await _get_app(session, appId)
            workflow_json = await _load_review_preview_workflow(session, appId, previewVersionId, user)
            data = _app_dict(app, "REVIEWER")
            data["previewMode"] = True
            data.update(_extract_run_chat_config(workflow_json, user))
            return data
        app = await _require_run_access(session, appId, user)
        permission = await _share_permission(session, app, user) or "RUNNER"
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == appId))
        ).scalar_one_or_none()
        data = _app_dict(app, permission)
        data.update(_extract_run_chat_config(definition.published_json if definition else None, user))
        return data


@router.get("/run/sessions")
async def run_sessions(appId: str, keyword: Optional[str] = None, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        await _require_run_access(session, appId, user)
        stmt = select(ChatThread).where(ChatThread.user_id == user.user_id, ChatThread.app_id == appId)
        if keyword:
            stmt = stmt.where(ChatThread.title.like(f"%{keyword}%"))
        rows = (await session.execute(stmt.order_by(ChatThread.pinned.desc(), ChatThread.updated_at.desc()))).scalars().all()
        return [_run_session_dict(t) for t in rows]


@router.post("/run/sessions")
async def create_run_session(payload: RunSessionCreate, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        app = await _require_run_access(session, payload.appId, user)
        thread = ChatThread(
            id=f"run_{user.user_id}_{uuid.uuid4().hex[:12]}",
            user_id=user.user_id,
            app_id=payload.appId,
            ai_app_type=app.ai_app_type,
            title=(payload.title or "新对话")[:255],
        )
        session.add(thread)
        await session.commit()
        await session.refresh(thread)
        return _run_session_dict(thread)


@router.delete("/run/sessions")
async def delete_run_session(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        await _get_run_session(session, id, user)
        await session.execute(sa_delete(ChatMessage).where(ChatMessage.thread_id == id))
        await session.execute(sa_delete(ChatThread).where(ChatThread.id == id))
        await session.commit()
        return {"success": True}


@router.get("/run/messages")
async def run_messages(sessionId: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        thread = await _get_run_session(session, sessionId, user)
        rows = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.thread_id == sessionId)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        ).scalars().all()
        payload = []
        for r in rows:
            payload.append({
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "turnId": r.turn_id,
                "status": r.status or "unknown",
                "feedback": r.feedback,
                "senderType": _run_message_sender_type(r, thread.origin),
                "attachments": _run_message_attachments(r, thread.origin),
                "generatedFiles": await _run_message_generated_files(r, user.user_id),
                "createTime": r.created_at.isoformat() if r.created_at else None,
            })
        return payload


@router.post("/run/messages")
async def append_run_message(payload: RunMessageAppend, user: UserContext = Depends(current_user)):
    if payload.role not in ("user", "assistant"):
        raise HTTPException(400, "非法角色")
    # client-authored assistant 正文入库前协议泄漏拦截（P0 DSML）：本端点正文由前端整条
    # 推送，完全绕过后端模型输出链路的源头清洗，是唯一需要在写入口兜底的路径
    if payload.role == "assistant" and payload.content:
        from app.services.platform.text_protocol_guard import scrub_text
        cleaned, leaked = scrub_text(payload.content)
        if leaked:
            payload.content = cleaned
    async with async_session() as session:
        thread = await _get_run_session(session, payload.sessionId, user)
        if payload.truncateFromId and payload.truncateFromId > 0:
            await session.execute(
                sa_delete(ChatMessage).where(
                    ChatMessage.thread_id == payload.sessionId,
                    ChatMessage.id >= int(payload.truncateFromId),
                )
            )
        attachments = _attachments_meta(payload.attachments) if payload.role == "user" else []
        if payload.role == "assistant" and payload.generatedFiles:
            from app.services.files import user_file_service
            requested = [str(item.get("id") or item.get("file_id") or "").strip()
                         for item in payload.generatedFiles if isinstance(item, dict)]
            owned = await user_file_service.get_files_by_ids(user.user_id, requested)
            attachments = [
                {"filename": item["filename"], "kind": "generated", "status": "ok", "file_id": item["id"]}
                for item in owned if item.get("id") and item.get("filename")
            ]
        msg = ChatMessage(
            thread_id=payload.sessionId,
            role=payload.role,
            content=payload.content or "",
            sender_type="human" if payload.role == "user" else None,
            attachments_json=json.dumps(attachments, ensure_ascii=False) if attachments else None,
            run_id=(payload.runId or None) if payload.role == "assistant" else None,
            turn_id=(payload.turnId or "").strip() or None,
            # 旧前端不会传 status，不能误写成完成；日志会明确标为未知状态。
            status=(payload.status or "unknown") if payload.role == "assistant" else None,
        )
        session.add(msg)
        if payload.role == "user" and (thread.title or "新对话") in ("", "新对话"):
            thread.title = (payload.content or "新对话").strip()[:30] or "新对话"
        thread.updated_at = func.now()
        await session.commit()
        await session.refresh(msg)
        return {"id": msg.id, "title": thread.title}


class RunMessageTruncate(BaseModel):
    sessionId: str
    fromId: int


@router.post("/run/messages/truncate")
async def truncate_run_messages(payload: RunMessageTruncate, user: UserContext = Depends(current_user)):
    """编辑重发：删除 fromId 及其之后的会话消息，再由前端 append 新用户消息。"""
    if payload.fromId <= 0:
        raise HTTPException(400, "非法消息")
    async with async_session() as session:
        thread = await _get_run_session(session, payload.sessionId, user)
        await session.execute(
            sa_delete(ChatMessage).where(
                ChatMessage.thread_id == payload.sessionId,
                ChatMessage.id >= int(payload.fromId),
            )
        )
        thread.updated_at = func.now()
        await session.commit()
        return {"success": True}


# ---------- 当前用户能力（前端据此显隐审核台/管理台入口） ----------

@router.get("/me/capabilities")
async def my_capabilities(user: UserContext = Depends(current_user)):
    reviewer = is_reviewer(user)
    return {
        "isReviewer": reviewer,
        "isPlatformAdmin": is_platform_admin(user),
        "approvalRequired": settings.PUBLISH_APPROVAL_REQUIRED,
        # 审核员本人提交发布时自动通过（见 _submit_or_publish），前端据此把「提交审核」
        # 文案改成「发布上线」，避免审核员看到「等待审核」却无人可等。
        "selfPublishAutoApproved": settings.PUBLISH_APPROVAL_REQUIRED and reviewer,
    }


# ---------- Capability Registry（Phase 7，§12） ----------

@router.get("/registry")
async def registry_list(sourceSystem: Optional[str] = None, user: UserContext = Depends(current_user)):
    """已发布智能体的能力与路由元数据（自动路由候选来源的自托管版）。"""
    return await capability_registry.list_registry(source_system=sourceSystem)


@router.post("/registry/backfill")
async def registry_backfill(user: UserContext = Depends(current_user)):
    _require_platform_admin(user)
    count = await capability_registry.backfill_all()
    return {"synced": count}


@router.post("/registry/external/backfill")
async def registry_external_backfill(user: UserContext = Depends(current_user)):
    """从 app_info（广场 external 应用）同步权威表的 external_app 候选（R6 外部兜底数据源，§12.1）。"""
    _require_platform_admin(user)
    count = await app_capability_registry.sync_external_from_app_info()
    return {"synced": count}


class RegistryChangeEvent(BaseModel):
    operation: str = "upsert"          # upsert / delete
    capability_code: str
    tenant_id: Optional[str] = "0"
    app_info_id: Optional[str] = None
    source_app_id: Optional[str] = None
    source_system: Optional[str] = None
    source_published_version: Optional[int] = None
    capability_name: Optional[str] = None
    route_description: Optional[str] = None
    trigger_examples: Optional[str] = None
    negative_examples: Optional[str] = None
    tags: Optional[str] = None
    capability_type: Optional[str] = None
    execution_scope: Optional[str] = None
    provider: Optional[str] = None
    launch_mode: Optional[str] = None
    runtime_type: Optional[str] = None
    endpoint: Optional[str] = None
    remote_app_id: Optional[str] = None
    protocol_version: Optional[str] = None
    risk_level: Optional[str] = None
    approval_policy: Optional[str] = None
    health_status: Optional[str] = None
    owner: Optional[str] = None
    enabled: Optional[Any] = None


@router.post("/registry/events")
async def registry_change_event(
    payload: RegistryChangeEvent,
    x_event_timestamp: str = Header("", alias="X-Event-Timestamp"),
    x_event_signature: str = Header("", alias="X-Event-Signature"),
):
    """消费 Java Capability Registry 签名变更事件（§12.2，服务间调用，HMAC 验签不走用户鉴权）。

    canonical = f"{operation}:{tenant_id}:{capability_code}"；签名 = HMAC-SHA256(INTERNAL_SYNC_SECRET,
    f"{canonical}\\n{timestamp}") base64url。secret 未配置（空/CHANGE_ME）时拒绝（失败关闭）。幂等 upsert/delete。
    """
    canonical = f"{payload.operation}:{payload.tenant_id or '0'}:{payload.capability_code}"
    try:
        app_capability_registry.verify_event_signature(canonical, x_event_timestamp, x_event_signature)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"事件验签失败：{exc}") from exc
    return await app_capability_registry.consume_change_event(payload.dict())


# ---------- Tool Gateway（Phase 7，§11）：幂等去重 + 敏感工具审批 ----------

class GatewayExecuteRequest(BaseModel):
    idempotencyKey: str
    toolName: str
    args: object = None
    sensitive: bool = False


class GatewayActionRequest(BaseModel):
    callId: str


@router.post("/gateway/execute")
async def gateway_execute(payload: GatewayExecuteRequest, user: UserContext = Depends(current_user)):
    """网关幂等 + 审批执行。演示执行器为 echo；真实业务工具由子智能体注入 executor（跨团队接入点）。"""

    async def _executor():
        return {
            "tool": payload.toolName,
            "args": payload.args,
            "note": "网关演示执行器（业务工具接入后替换为真实 executor）",
        }

    return await tool_gateway.execute(
        idempotency_key=payload.idempotencyKey,
        user_id=user.user_id,
        tool_name=payload.toolName,
        args=payload.args,
        sensitive=payload.sensitive,
        executor=_executor,
    )


@router.post("/gateway/approve")
async def gateway_approve(payload: GatewayActionRequest, user: UserContext = Depends(current_user)):
    return await tool_gateway.approve(payload.callId, user.user_id)


@router.post("/gateway/reject")
async def gateway_reject(payload: GatewayActionRequest, user: UserContext = Depends(current_user)):
    return await tool_gateway.reject(payload.callId, user.user_id)


@router.get("/gateway/calls")
async def gateway_calls(user: UserContext = Depends(current_user)):
    return await tool_gateway.list_calls(user.user_id)


# ---------- 执行 ----------

async def _prepare_llm(user: UserContext) -> tuple[str, str]:
    """尽力解析用户 LLM key 与默认模型；缺失时返回空串，由引擎在 LLM 节点报明确错误。"""
    try:
        from app.services.platform.key_service import key_service

        api_key = await key_service.get_user_key(user.user_id) or ""
    except Exception:
        api_key = ""
    default_model = ""
    if api_key:
        try:
            from app.services.agents.agent_service import agent_service

            models = await agent_service.get_models(user_key=api_key)
            default_model = next((m.id for m in models if getattr(m, "is_default", False)), None) or (
                models[0].id if models else ""
            )
        except Exception:
            logger.warning("resolve default model failed", exc_info=True)
    return api_key, default_model


async def _run(
    app_id: str,
    workflow_json: str,
    input_text: str,
    variables: Optional[dict],
    user: UserContext,
    token: str,
    thread_id: str = "",
    preview_only: bool = False,
    evaluation_mode: str = "execute",
) -> dict:
    api_key, default_model = await _prepare_llm(user)
    result = await execute_workflow(
        workflow_json,
        input_text,
        variables,
        token=token,
        user_id=user.user_id,
        user_name=user.real_name,
        username=user.username,
        app_id=app_id,
        thread_id=thread_id,
        preview_only=preview_only,
        llm_api_key=api_key,
        default_model=default_model,
    )
    await record_workflow_evaluation_run(
        app_id=app_id,
        user_id=user.user_id,
        workflow_json=workflow_json,
        mode=evaluation_mode,
        input_text=input_text,
        variables=variables,
        result=result,
        preview_only=preview_only,
    )
    return result


def _merge_histories(payload: DebugRequest) -> Optional[dict]:
    """histories 以变量形式传给引擎（agent 节点消费）。"""
    if not payload.histories:
        return payload.variables
    return {**(payload.variables or {}), "histories": payload.histories}


def _workflow_stream_variables(payload: DebugRequest, prior_file_ids: list[str]) -> dict:
    """Build one stream run's variables without replaying historical attachments.

    ``userFileIds`` used to contain both the files submitted with the current
    request and every attachment previously persisted in the session.  The
    workflow engine consequently treated an old image as a fresh upload on
    later text-only turns.  Keep the compatibility key scoped to the current
    request, and expose prior IDs separately for an explicit "look at the
    previous attachment" request.
    """
    variables = dict(_merge_histories(payload) or {})
    raw_current_ids = variables.get("userFileIds")
    current_file_ids = list(dict.fromkeys(
        str(item or "").strip()
        for item in (raw_current_ids if isinstance(raw_current_ids, list) else [])
        if str(item or "").strip()
    ))[:5]
    historical_file_ids = list(dict.fromkeys(
        str(item or "").strip()
        for item in prior_file_ids
        if str(item or "").strip() and str(item or "").strip() not in current_file_ids
    ))[:5]
    variables["currentTurnUserFileIds"] = current_file_ids
    variables["historicalUserFileIds"] = historical_file_ids
    variables["userFileIds"] = current_file_ids
    return variables


async def _workflow_stream_response(
    app_id: str,
    workflow_json: str,
    payload: DebugRequest,
    user: UserContext,
    token: str,
    preview_only: bool = False,
    evaluation_mode: str = "execute",
):
    """SSE 流式执行：delta 事件即时输出文本，result 事件保留完整运行结果。"""
    import asyncio as _asyncio
    import json as _json
    import time as _time
    import uuid as _uuid

    from sse_starlette.sse import EventSourceResponse

    from app.services.workflows.workflow_engine import (
        RunContext,
        WorkflowEngine,
        WorkflowExecutionError,
        parse_graph,
    )
    from app.services.workflow_runtime import CheckpointerRequiredError, UnsupportedGraphError
    from app.services.workflow_runtime.compiler import langgraph_enabled, stream_engine_with_langgraph

    api_key, default_model = await _prepare_llm(user)

    async def event_stream():
        queue = _asyncio.Queue()
        streamed_seqs: set[int] = set()
        run_id = _uuid.uuid4().hex
        thread_id = ""
        prior_file_ids: list[str] = []
        if payload.sessionId and not payload.previewDraft and not payload.previewVersionId:
            async with async_session() as db:
                thread = await _get_run_session(db, payload.sessionId, user)
                if thread.app_id != app_id:
                    raise HTTPException(400, "运行会话与智能体不匹配")
                thread_id = thread.id
                prior_file_ids = await _run_session_user_file_ids(db, thread_id)
        variables = _workflow_stream_variables(payload, prior_file_ids)
        ctx = RunContext(
            input_text=payload.input or "",
            variables=variables,
            token=token,
            user_id=user.user_id,
            user_name=user.real_name,
            username=user.username,
            app_id=app_id,
            run_id=run_id,
            thread_id=thread_id,
            preview_only=preview_only or bool(payload.previewDraft or payload.previewVersionId),
            llm_api_key=api_key,
            default_model=default_model,
        )
        emitted_parts = 0

        async def emit_stream_output(seq: int, text: str) -> None:
            if not text:
                return
            streamed_seqs.add(seq)
            await queue.put({"event": "delta", "data": _json.dumps({"text": text}, ensure_ascii=False)})

        ctx.stream_output = emit_stream_output

        async def emit_new_output_parts():
            nonlocal emitted_parts
            while emitted_parts < len(ctx.output_parts):
                _seq, text = ctx.output_parts[emitted_parts]
                emitted_parts += 1
                if text and _seq not in streamed_seqs:
                    await queue.put({"event": "delta", "data": _json.dumps({"text": text}, ensure_ascii=False)})

        async def run_workflow():
            started = _time.time()
            status, error_message, interactive = "success", None, None
            engine = None
            try:
                graph = parse_graph(workflow_json)
                engine = WorkflowEngine(graph, ctx)
                if langgraph_enabled():
                    try:
                        async for kind, data in stream_engine_with_langgraph(engine):
                            if kind == "node":
                                await queue.put({"event": "node", "data": _json.dumps(data, ensure_ascii=False)})
                                await emit_new_output_parts()
                            elif kind == "interactive":
                                interactive = data
                    except CheckpointerRequiredError as exc:
                        status, error_message = "failed", str(exc)
                    except UnsupportedGraphError:
                        await engine.run()
                        await emit_new_output_parts()
                else:
                    await engine.run()
                    await emit_new_output_parts()
            except WorkflowExecutionError as exc:
                status, error_message = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001
                status, error_message = "failed", f"执行异常: {exc}"

            output = "".join(text for _seq, text in sorted(ctx.output_parts, key=lambda p: p[0]) if text)
            if status == "success" and ctx.uncaught_errors:
                error_message = "；".join(ctx.uncaught_errors[:3])
                if not output:
                    status = "failed"
            if interactive and status == "success":
                status = "waiting"
            result = {
                "runId": run_id,
                "status": status,
                "output": output,
                "errorMessage": error_message,
                "durationMs": int((_time.time() - started) * 1000),
                "nodeRuns": [run.to_dict() for run in ctx.node_runs],
                "outputs": ctx.outputs,
                "edges": engine.edges_snapshot() if engine is not None else [],
                "files": ctx.generated_files,
                **({"interactive": interactive} if interactive else {}),
            }
            await record_workflow_evaluation_run(
                app_id=app_id,
                user_id=user.user_id,
                workflow_json=workflow_json,
                mode=evaluation_mode,
                input_text=payload.input or "",
                variables=variables,
                result=result,
                preview_only=preview_only,
            )
            await queue.put({"event": "result", "data": _json.dumps(result, ensure_ascii=False)})
            await queue.put(None)

        task = _asyncio.create_task(run_workflow())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await task
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_stream())


@router.post("/definition/debug/session")
async def create_debug_session(
    payload: DebugRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    """Start a paused, single-step debug session from the current draft graph.

    The caller must have edit permission whenever it supplies an unsaved graph,
    matching the existing full-flow debug endpoint's code/HTTP safety boundary.
    """
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    workflow_json = payload.workflowJson
    async with async_session() as session:
        await _require_permission(session, app_id, user, edit=bool(workflow_json))
        if not workflow_json:
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.draft_json if definition else None
    if not workflow_json:
        raise HTTPException(400, "没有可调试的工作流定义，请先保存草稿")

    api_key, default_model = await _prepare_llm(user)
    run_id = uuid.uuid4().hex
    ctx = RunContext(
        input_text=payload.input or "",
        variables=dict(_merge_histories(payload) or {}),
        token=x_access_token,
        user_id=user.user_id,
        user_name=user.real_name,
        username=user.username,
        app_id=app_id,
        run_id=run_id,
        audit_execution_segment=f"wf_debug_{run_id}",
        preview_only=True,
        llm_api_key=api_key,
        default_model=default_model,
    )
    try:
        debug_session = WorkflowDebugSession.create(
            parse_graph(workflow_json),
            ctx,
            owner_user_id=user.user_id,
            app_id=app_id,
        )
    except DebugSessionUnsupportedGraphError as exc:
        raise HTTPException(400, str(exc)) from exc
    except WorkflowExecutionError as exc:
        raise HTTPException(400, str(exc)) from exc
    await workflow_debug_sessions.create(debug_session)
    snapshot = debug_session.snapshot()
    await record_workflow_evaluation_run(
        app_id=app_id,
        user_id=user.user_id,
        workflow_json=workflow_json,
        mode="debug_step",
        input_text=payload.input or "",
        variables=debug_session.engine.ctx.variables,
        result=snapshot,
        preview_only=True,
    )
    return snapshot


async def _get_debug_session_or_http_error(session_id: str, user_id: str) -> WorkflowDebugSession:
    try:
        return await workflow_debug_sessions.get(session_id, user_id)
    except DebugSessionForbiddenError as exc:
        raise HTTPException(403, "无权访问此调试会话") from exc
    except DebugSessionExpiredError as exc:
        raise HTTPException(410, "调试会话已超时或服务已重启，请重新开始调试") from exc
    except DebugSessionNotFoundError as exc:
        raise HTTPException(404, "调试会话不存在") from exc


@router.get("/definition/debug/session/{session_id}")
async def get_debug_session(session_id: str, user: UserContext = Depends(current_user)):
    debug_session = await _get_debug_session_or_http_error(session_id, user.user_id)
    return debug_session.snapshot()


@router.post("/definition/debug/session/{session_id}/step")
async def step_debug_session(
    session_id: str,
    payload: DebugSessionStepRequest,
    user: UserContext = Depends(current_user),
):
    debug_session = await _get_debug_session_or_http_error(session_id, user.user_id)
    snapshot = await debug_session.step(payload.variables)
    await record_workflow_evaluation_run(
        app_id=debug_session.app_id,
        user_id=user.user_id,
        workflow_json=json.dumps(debug_session.engine.graph, ensure_ascii=False, sort_keys=True),
        mode="debug_step",
        input_text=debug_session.engine.ctx.input_text,
        variables=debug_session.engine.ctx.variables,
        result=snapshot,
        preview_only=True,
    )
    return snapshot


@router.delete("/definition/debug/session/{session_id}")
async def stop_debug_session(session_id: str, user: UserContext = Depends(current_user)):
    try:
        debug_session = await _get_debug_session_or_http_error(session_id, user.user_id)
        snapshot = await workflow_debug_sessions.stop(session_id, user.user_id)
        await record_workflow_evaluation_run(
            app_id=debug_session.app_id,
            user_id=user.user_id,
            workflow_json=json.dumps(debug_session.engine.graph, ensure_ascii=False, sort_keys=True),
            mode="debug_step",
            input_text=debug_session.engine.ctx.input_text,
            variables=debug_session.engine.ctx.variables,
            result=snapshot,
            preview_only=True,
        )
        return snapshot
    except DebugSessionForbiddenError as exc:
        raise HTTPException(403, "无权访问此调试会话") from exc
    except DebugSessionExpiredError as exc:
        raise HTTPException(410, "调试会话已超时或服务已重启，请重新开始调试") from exc
    except DebugSessionNotFoundError as exc:
        raise HTTPException(404, "调试会话不存在") from exc


@router.post("/definition/debug")
async def debug_definition(
    payload: DebugRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    workflow_json = payload.workflowJson
    async with async_session() as session:
        # H1：调用方自带 workflowJson（可含 code/http 节点 → 服务端任意代码执行）时要求编辑权限，
        # 只读 VIEWER 仅能调试服务端已存的草稿，不能提交任意定义执行。
        await _require_permission(session, app_id, user, edit=bool(workflow_json))
        if not workflow_json:
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.draft_json if definition else None
    if not workflow_json:
        raise HTTPException(400, "没有可调试的工作流定义，请先保存草稿")
    return await _run(
        app_id, workflow_json, payload.input or "", _merge_histories(payload),
        user, x_access_token, preview_only=True, evaluation_mode="debug",
    )


@router.post("/definition/debugStream")
async def debug_definition_stream(
    payload: DebugRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    workflow_json = payload.workflowJson
    async with async_session() as session:
        # H1：调用方自带 workflowJson 时要求编辑权限，和非流式 debug 保持一致。
        await _require_permission(session, app_id, user, edit=bool(workflow_json))
        if not workflow_json:
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.draft_json if definition else None
    if not workflow_json:
        raise HTTPException(400, "没有可调试的工作流定义，请先保存草稿")
    return await _workflow_stream_response(
        app_id, workflow_json, payload, user, x_access_token,
        preview_only=True, evaluation_mode="debug",
    )


@router.post("/definition/execute")
async def execute_definition(
    payload: DebugRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    async with async_session() as session:
        if payload.previewDraft:
            workflow_json = await _load_draft_preview_workflow(session, app_id, user)
        elif payload.previewVersionId:
            workflow_json = await _load_review_preview_workflow(session, app_id, payload.previewVersionId, user)
        else:
            if payload.workflowJson:
                app, _ = await _require_permission(session, app_id, user, edit=True)
            else:
                app = await _require_run_access(session, app_id, user)
            _reject_if_unpublished(app)
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.published_json if definition else None
            await _require_published_workflow_model_access(workflow_json, user)
    if not workflow_json:
        raise HTTPException(400, "该应用还没有已发布的工作流版本")
    return await _run(
        app_id, workflow_json, payload.input or "", _merge_histories(payload),
        user, x_access_token, preview_only=bool(payload.previewDraft or payload.previewVersionId),
    )


@router.post("/definition/executeStream")
async def execute_definition_stream(
    payload: DebugRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    """SSE 流式执行（P3，架构 §10.5.9 第 8 条）：node/delta 事件逐步推送，result 事件收尾。

    环路图（LangGraph 编译不支持）自动回退 legacy：运行完成后按输出片段发 delta，再发 result。
    """
    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    async with async_session() as session:
        if payload.previewDraft:
            workflow_json = await _load_draft_preview_workflow(session, app_id, user)
        elif payload.previewVersionId:
            workflow_json = await _load_review_preview_workflow(session, app_id, payload.previewVersionId, user)
        else:
            if payload.workflowJson:
                app, _ = await _require_permission(session, app_id, user, edit=True)
            else:
                app = await _require_run_access(session, app_id, user)
            _reject_if_unpublished(app)
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.published_json if definition else None
            await _require_published_workflow_model_access(workflow_json, user)
    if not workflow_json:
        raise HTTPException(400, "该应用还没有已发布的工作流版本")
    return await _workflow_stream_response(
        app_id, workflow_json, payload, user, x_access_token,
        preview_only=bool(payload.previewDraft or payload.previewVersionId),
    )


class ResumeRequest(BaseModel):
    appId: Optional[str] = None
    appInfoId: Optional[str] = None
    previewVersionId: Optional[str] = None
    # 草稿预览恢复挂起运行：继续使用 draft_json，不能退回已发布应用校验。
    previewDraft: Optional[bool] = None
    workflowJson: Optional[str] = None
    sessionId: Optional[str] = None
    resumeId: str
    # userSelect 传字符串（选项 value/key），formInput 传对象
    value: object = None


@router.post("/definition/resume")
async def resume_definition(
    payload: ResumeRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header(default="", alias="X-Access-Token"),
):
    """恢复交互挂起的运行（userSelect/formInput，LangGraph checkpoint）。"""
    from app.services.workflows.workflow_engine import resume_workflow
    from app.services.workflow_runtime import CheckpointAccessError

    app_id = _resolve_app_id(payload.appId, payload.appInfoId)
    thread_id = ""
    async with async_session() as session:
        if payload.workflowJson:
            # 调试态恢复必须使用首次挂起时的草稿定义；自带 workflowJson 与 debug 接口一样要求编辑权限。
            await _require_permission(session, app_id, user, edit=True)
            workflow_json = payload.workflowJson
        elif payload.previewDraft:
            workflow_json = await _load_draft_preview_workflow(session, app_id, user)
        elif payload.previewVersionId:
            workflow_json = await _load_review_preview_workflow(session, app_id, payload.previewVersionId, user)
        else:
            app = await _require_run_access(session, app_id, user)
            _reject_if_unpublished(app)
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            workflow_json = definition.published_json if definition else None
            await _require_published_workflow_model_access(workflow_json, user)
        if payload.sessionId and not payload.previewDraft and not payload.previewVersionId:
            thread = await _get_run_session(session, payload.sessionId, user)
            if thread.app_id != app_id:
                raise HTTPException(400, "运行会话与智能体不匹配")
            thread_id = thread.id
    if not workflow_json:
        raise HTTPException(400, "该应用还没有已发布的工作流版本")
    api_key, default_model = await _prepare_llm(user)
    try:
        return await resume_workflow(
            workflow_json,
            payload.resumeId,
            payload.value,
            token=x_access_token,
            user_id=user.user_id,
            user_name=user.real_name,
            username=user.username,
            app_id=app_id,
            llm_api_key=api_key,
            default_model=default_model,
            thread_id=thread_id,
        )
    except CheckpointAccessError as exc:
        # H2：resumeId 不属于当前用户/应用 → 403，不泄露他人挂起态
        raise HTTPException(403, str(exc)) from exc


# ---------- 四 Tab 节点模板目录与 preview node（gap-audit §7.3，蓝本两阶段协议） ----------

@router.get("/template/tags")
async def template_tags(user: UserContext = Depends(current_user)):
    """系统工具标签列表（builtin category 去重，蓝本 getPluginToolTags）。"""
    from app.services.workflows.node_template_service import list_system_tool_tags

    return list_system_tool_tags()


@router.get("/template/list")
async def template_list(
    tab: str,
    searchKey: Optional[str] = None,
    parentId: Optional[str] = None,
    tags: Optional[str] = None,
    excludeAppId: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """模板摘要（蓝本 NodeTemplateListItemType）：tab=systemTools|myTools|agent。

    摘要不可直接落图；动态节点必须再经 /template/previewNode 获取完整 schema。
    """
    from app.services.workflows.node_template_service import list_templates

    tag_list = [t for t in (tags or "").split(",") if t.strip()] or None
    async with async_session() as session:
        try:
            return await list_templates(
                session,
                user,
                tab=tab,
                search_key=searchKey,
                parent_id=parentId,
                tags=tag_list,
                exclude_app_id=excludeAppId,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))


@router.get("/template/previewNode")
async def template_preview_node(
    id: str,
    excludeAppId: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """完整 preview node（蓝本 getPreviewNode）：服务端 schema 生成 inputs/outputs/toolConfig。

    失败返回 400，前端必须放弃落图（蓝本「获取工具详情失败」语义）；密钥不回传明文。
    """
    from app.services.workflows.node_template_service import build_preview_node

    async with async_session() as session:
        try:
            return await build_preview_node(session, user, template_id=id, exclude_app_id=excludeAppId)
        except ValueError as exc:
            raise HTTPException(400, str(exc))


# ---------- 目录 ----------

@router.get("/tool/builtin")
async def builtin_tools(user: UserContext = Depends(current_user)):
    """系统工具目录：运行时内置、可被对话 Agent 挂载（执行入口在 agent 节点函数调用循环）。"""
    from app.services.skills.builtin_tools import BUILTIN_TOOLS

    return [
        {key: tool[key] for key in ("id", "name", "category", "description", "nodeType", "inputKeys", "outputKeys")}
        for tool in BUILTIN_TOOLS
    ]


async def _require_prompt_debug_model(api_key: str, requested_model: str, default_model: str) -> str:
    """Prompt experiments may only use a model visible to the current user key."""
    model = str(requested_model or default_model or "").strip()
    if not model:
        raise HTTPException(400, "请选择用于调试的模型")
    try:
        from app.services.agents.agent_service import agent_service

        models = await agent_service.get_models(user_key=api_key, raise_on_lookup_failure=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("load prompt debug models failed", exc_info=True)
        raise HTTPException(503, "无法确认模型使用权限") from exc
    allowed = {str(item.id) for item in models if getattr(item, "id", None)}
    if model not in allowed:
        raise HTTPException(403, "当前用户无权使用该模型")
    return model


def _prompt_debug_variables(values: dict[str, Any], user: UserContext, app_id: str) -> dict[str, Any]:
    """Use only explicit test values plus the same safe system variables as workflow text."""
    try:
        encoded = json.dumps(values, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "变量值必须可序列化") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise HTTPException(400, "变量值过大")
    result = dict(values)
    result.setdefault("userId", user.user_id)
    result.setdefault("username", user.username)
    result.setdefault("realname", user.real_name or user.username)
    result.setdefault("appId", app_id)
    result.setdefault("cTime", format_agent_now())
    return result


@router.post("/prompt/debug")
async def debug_prompt(
    payload: PromptDebugRequest,
    user: UserContext = Depends(current_user),
):
    """Test exactly one prompt/model completion without executing workflow tools or nodes."""
    async with async_session() as session:
        await _require_permission(session, payload.appId, user, edit=True)
    api_key, default_model = await _prepare_llm(user)
    if not api_key:
        raise HTTPException(403, "当前用户没有可用的模型调用凭证")
    model = await _require_prompt_debug_model(api_key, payload.model, default_model)
    variables = _prompt_debug_variables(payload.variables, user, payload.appId)
    messages = [
        {"role": "system", "content": interpolate_prompt(payload.prompt, variables)},
        {"role": "user", "content": interpolate_prompt(payload.question, variables)},
    ]
    try:
        return await complete_prompt_experiment(
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.maxToken,
            top_p=payload.topP,
            stop_sign=payload.stopSign,
            response_format=payload.responseFormat,
            json_schema=payload.jsonSchema,
        )
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/prompt/generate")
async def generate_prompt(
    payload: PromptGenerateRequest,
    user: UserContext = Depends(current_user),
):
    """Generate a candidate prompt only; the client must explicitly apply it."""
    async with async_session() as session:
        app, _permission = await _require_permission(session, payload.appId, user, edit=True)
    api_key, default_model = await _prepare_llm(user)
    if not api_key:
        raise HTTPException(403, "当前用户没有可用的模型调用凭证")
    model = await _require_prompt_debug_model(api_key, payload.model, default_model)
    variable_keys = [str(key).strip() for key in payload.variableKeys if str(key).strip()][:100]
    try:
        result = await complete_prompt_experiment(
            api_key=api_key,
            model=model,
            messages=prompt_generation_messages(
                app_name=app.name or "",
                app_description=app.description or "",
                current_prompt=payload.currentPrompt,
                goal=payload.goal,
                variable_keys=variable_keys,
            ),
            temperature=0.3,
            max_tokens=4_000,
        )
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"prompt": result["rawOutput"], "model": result["model"], "usage": result["usage"], "durationMs": result["durationMs"]}


@router.get("/model/options")
async def model_options(user: UserContext = Depends(current_user)):
    try:
        from app.services.platform.key_service import key_service

        api_key = await key_service.get_user_key(user.user_id)
        if not api_key:
            return []
        from app.services.agents.agent_service import agent_service

        models = await agent_service.get_models(user_key=api_key, raise_on_lookup_failure=True)
        return [
            {"label": getattr(m, "name", None) or m.id, "value": m.id, "available": True}
            for m in models
        ]
    except Exception as exc:
        logger.warning("load model options failed", exc_info=True)
        raise HTTPException(503, "模型列表暂不可用") from exc


@router.get("/marketplace/model/options")
async def marketplace_model_options(user: UserContext = Depends(current_user)):
    try:
        from app.services.platform.key_service import key_service

        api_key = await key_service.get_user_key(user.user_id)
        if not api_key:
            return []
        from app.services.agents.agent_service import agent_service
        from app.services.platform.marketplace_model_options import build_marketplace_model_options

        chat_models = await agent_service.get_models(user_key=api_key, raise_on_lookup_failure=True)
        async with async_session() as session:
            embedding_models = await _available_embedding_model_ids(session)
        return build_marketplace_model_options(chat_models, embedding_models)
    except Exception as exc:
        logger.warning("load marketplace model options failed", exc_info=True)
        raise HTTPException(503, "广场模型列表暂不可用") from exc
