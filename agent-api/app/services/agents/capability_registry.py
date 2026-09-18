"""Capability Registry（Phase 7，§12）—— agent-api 自托管版。

按发布态把工作台智能体（chatAgent/workflow）的能力与路由元数据登记到 `agent_capability_registry`，
供自动路由做候选强过滤（source_system/execution_scope/runtime_type/enabled/health/published_version）。
权威版是 Java/MySQL `app_info_capability_registry`（跨团队）——本表是内部候选来源，external_app
第三方条目由外部注册（R6 兜底就绪）。
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models import CapabilityRegistry, WorkflowApp, WorkflowDefinition

logger = logging.getLogger(__name__)

_ROUTABLE_TYPES = ("chatAgent", "workflow")

# 路由索引契约版本（复审 #6）：Qdrant payload / 路由文本口径变更时 +1。
# 启动期 backfill_if_stale 发现 registry 行的 index_version 落后即自动整体重建
# （重写 routing_json/route_text_hash + 重建 Qdrant 点位），存量环境只跑 SQL 迁移
# 也能在下次启动补齐新 payload 字段，不再依赖人工执行回填。
ROUTE_INDEX_VERSION = 2

# 候选元数据消毒（描述按不可信内容处理）：剔除控制字符、压缩空白、限长
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _sanitize_meta(value: Any, limit: int) -> str:
    text = _CTRL_RE.sub("", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _extract_capabilities(published_json: Optional[str]) -> Dict[str, Any]:
    """从已发布画布尽力抽取能力摘要（工具/知识/技能/节点类型），用于展示与能力检索。"""
    caps: Dict[str, Any] = {"tools": [], "knowledge": [], "skills": [], "nodeTypes": []}
    if not published_json:
        return caps
    try:
        graph = json.loads(published_json)
    except (json.JSONDecodeError, TypeError):
        return caps
    fastgpt = graph.get("fastgpt") if isinstance(graph, dict) else None
    fastgpt = fastgpt or graph
    nodes = (fastgpt or {}).get("nodes") or []
    node_types = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("flowNodeType") or ""
        if node_type:
            node_types.add(node_type)
        if node_type == "tool":
            tool_config = node.get("toolConfig") or {}
            for key in ("systemTool", "httpTool", "mcpTool"):
                sub = tool_config.get(key)
                if isinstance(sub, dict) and sub.get("toolId"):
                    caps["tools"].append(str(sub.get("toolId")))
        for item in node.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            key, value = item.get("key"), item.get("value")
            if key in ("datasetIds", "knowledgeIds") and isinstance(value, list):
                caps["knowledge"] += [str(x) for x in value]
            elif key in ("skillIds", "selectedSkills") and isinstance(value, list):
                caps["skills"] += [str(x.get("id") if isinstance(x, dict) else x) for x in value]
    caps["nodeTypes"] = sorted(node_types)
    for key in ("tools", "knowledge", "skills"):
        caps[key] = sorted({c for c in caps[key] if c})
    return caps


def _to_dict(row: CapabilityRegistry) -> Dict[str, Any]:
    try:
        capabilities = json.loads(row.capabilities_json or "{}")
    except (json.JSONDecodeError, TypeError):
        capabilities = {}
    return {
        "appId": row.app_id,
        "name": row.name,
        "description": row.description or "",
        "aiAppType": row.ai_app_type,
        "sourceSystem": row.source_system,
        "executionScope": row.execution_scope,
        "runtimeType": row.runtime_type,
        "publishedVersion": row.published_version or 0,
        "enabled": bool(row.enabled),
        "health": row.health,
        "capabilities": capabilities,
        "ownerUserId": row.owner_user_id,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------- 向量化路由索引（Phase 7 §12.2，全部 best-effort，异常不影响发布/路由） ----------

def _route_text(name: str, description: str, caps: Dict[str, Any],
                routing: Optional[Dict[str, Any]] = None,
                capability_names: Optional[List[str]] = None) -> str:
    """组织用于 embedding 的路由文本（语义发现升级 §十）。

    组成：名称 + routeDescription + 原 description + tags + triggerExamples +
    能力名称（尽量用可读名而非不透明 ID）。严格限总长。

    negativeExamples **不进正向 embedding**（复审 #5）：向量模型不理解「不适用」这种
    否定标注，负例文本只会拉高与负例场景的相似度、适得其反。负例只在排序端做扣分
    （_lexical_rank/_rrf_merge 的 negative penalty）。
    """
    routing = routing or {}
    parts = [name or ""]
    route_desc = str(routing.get("routeDescription") or "").strip()
    if route_desc:
        parts.append(route_desc)
    if description and description.strip() != route_desc:
        parts.append(description)
    tags = [str(t).strip() for t in (routing.get("tags") or []) if str(t or "").strip()]
    if tags:
        parts.append("标签：" + "、".join(tags[:20]))
    triggers = [str(t).strip() for t in (routing.get("triggerExamples") or [])
                if str(t or "").strip()]
    if triggers:
        parts.append("适用：" + "；".join(t[:100] for t in triggers[:20]))
    names = [str(n).strip() for n in (capability_names or []) if str(n or "").strip()]
    if not names and isinstance(caps, dict):
        # 未做名称解析时退回原始摘要（tools id 多为可读 code；纯不透明 ID 价值有限但无害）
        for key in ("tools", "skills", "knowledge"):
            names += [str(v) for v in (caps.get(key) or [])[:8]]
    if names:
        parts.append("能力：" + "、".join(names[:16]))
    return "。".join(p for p in parts if p).strip()[:1500]


async def _resolve_capability_names(session, caps: Dict[str, Any]) -> List[str]:
    """尽力把能力 ID 解析成可读名称（§十：不要只嵌入不透明 ID）。失败静默返回已有可读项。"""
    names: List[str] = []
    if not isinstance(caps, dict):
        return names
    names += [str(t) for t in (caps.get("tools") or [])[:8]]  # 工具 code 通常本身可读
    skill_ids = [str(s) for s in (caps.get("skills") or []) if s][:8]
    if skill_ids:
        try:
            from sqlalchemy import bindparam, text
            stmt = text(
                "SELECT name FROM agent_skill WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            rows = (await session.execute(stmt, {"ids": skill_ids})).scalars().all()
            names += [str(r) for r in rows if r]
        except Exception:  # noqa: BLE001
            names += skill_ids
    kb_ids = [str(k) for k in (caps.get("knowledge") or []) if k][:8]
    if kb_ids:
        try:
            from sqlalchemy import bindparam, text
            stmt = text(
                "SELECT name FROM agent_knowledge_base WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            rows = (await session.execute(stmt, {"ids": kb_ids})).scalars().all()
            names += [str(r) for r in rows if r]
        except Exception:  # noqa: BLE001
            pass  # 知识库表结构漂移/不存在：不塞不透明 ID
    return names


async def _reindex_route(app_id: str, name: str, description: str, caps: Dict[str, Any],
                         execution_scope: str, enabled: bool, *,
                         tenant_id: str = "0", runtime_type: str = "python_workflow",
                         published_version: int = 0, health: str = "healthy",
                         routing: Optional[Dict[str, Any]] = None,
                         capability_names: Optional[List[str]] = None,
                         route_text: Optional[str] = None) -> None:
    """把一个已发布子智能体的路由描述写入 Qdrant 路由索引。embedding 未配置/失败即跳过。"""
    try:
        from app.services.knowledge import embedding_service, vector_service
        cfg = await embedding_service.get_active_embedding_config()
        if not cfg:
            return
        text = route_text or _route_text(name, description, caps, routing, capability_names)
        vec = await embedding_service.embed_query(text, config=cfg)
        if len(vec) != cfg.dimension:
            return
        collection = vector_service.route_collection_name(cfg.model, cfg.dimension)
        await vector_service.ensure_collection(collection, cfg.dimension)
        await vector_service.upsert_routes(collection, [{
            "app_id": app_id, "tenant_id": str(tenant_id or "0"),
            "name": name, "description": description,
            "execution_scope": execution_scope, "source_system": "agent_workbench",
            "runtime_type": runtime_type, "published_version": int(published_version or 0),
            "enabled": enabled, "health": str(health or "healthy"),
            "vector": vec,
        }])
    except Exception:  # noqa: BLE001
        logger.debug("路由索引写入跳过 app_id=%s", app_id, exc_info=True)


async def _deindex_route(app_id: str) -> None:
    try:
        from app.services.knowledge import embedding_service, vector_service
        cfg = await embedding_service.get_active_embedding_config()
        if not cfg:
            return
        collection = vector_service.route_collection_name(cfg.model, cfg.dimension)
        await vector_service.delete_route(collection, app_id)
    except Exception:  # noqa: BLE001
        logger.debug("路由索引删除跳过 app_id=%s", app_id, exc_info=True)


async def sync_from_app(app_id: str) -> None:
    """按应用当前**线上发布态**同步一行 registry；未发布 / 非可路由类型 / 无 published_json
    则移除该行（含 Qdrant 点位）。降级安全（异常不抛）。

    路由元数据纪律（语义发现升级 §九）：
    - routing_json 只取**线上 approved 版本**冻结的快照（待审核版本不影响线上 Registry）；
      版本无元数据时以 app.description 兜底 routeDescription；
    - 同步已有行**不重置** enabled/health（运维停用/降级状态保留，重新启用须显式操作）；
      仅新建行给 enabled=1/health=healthy 初值。
    """
    try:
        import hashlib

        async with async_session() as session:
            app = await session.get(WorkflowApp, app_id)
            existing = await session.get(CapabilityRegistry, app_id)
            definition = (
                await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
            ).scalar_one_or_none()
            live_ok = (
                app is not None and app.ai_app_type in _ROUTABLE_TYPES
                and app.status == "published"
                and definition is not None and bool(definition.published_json)
                and int(definition.published_version or 0) > 0
            )
            if not live_ok:
                if existing:
                    await session.delete(existing)
                    await session.commit()
                await _deindex_route(app_id)
                from app.services.agents.recommendation_index import request_rebuild
                request_rebuild()
                return
            caps = _extract_capabilities(definition.published_json)
            # 线上 approved 版本的路由元数据快照（待审核版本不可见于此查询）
            from app.services.agents.published_visibility import load_published_visibility_version
            live_version = await load_published_visibility_version(session, app_id)
            routing: Dict[str, Any] = {}
            if live_version is not None and getattr(live_version, "routing_json", None):
                try:
                    parsed = json.loads(live_version.routing_json)
                    routing = parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    routing = {}
            if not str(routing.get("routeDescription") or "").strip():
                routing["routeDescription"] = app.description or ""
            routing.setdefault("triggerExamples", [])
            routing.setdefault("negativeExamples", [])
            routing.setdefault("tags", [])

            capability_names = await _resolve_capability_names(session, caps)
            route_text = _route_text(app.name, app.description or "", caps, routing, capability_names)
            route_hash = hashlib.sha256(route_text.encode("utf-8")).hexdigest()

            row = existing or CapabilityRegistry(app_id=app_id)
            row.name = app.name
            row.description = app.description or ""
            row.ai_app_type = app.ai_app_type
            row.source_system = "agent_workbench"
            row.execution_scope = "campus_internal"
            row.runtime_type = "python_workflow"
            row.published_version = definition.published_version
            if not existing:
                row.enabled = 1
                row.health = "healthy"
            row.capabilities_json = json.dumps(caps, ensure_ascii=False)
            row.owner_user_id = app.owner_user_id
            if hasattr(row, "tenant_id"):
                row.tenant_id = str(app.tenant_id or "0")
            if hasattr(row, "routing_json"):
                row.routing_json = json.dumps(routing, ensure_ascii=False)
            if hasattr(row, "route_text_hash"):
                row.route_text_hash = route_hash
            if hasattr(row, "index_version"):
                row.index_version = ROUTE_INDEX_VERSION
            if not existing:
                session.add(row)
            enabled_now = bool(int(row.enabled or 0))
            health_now = str(row.health or "healthy")
            published_version_now = int(definition.published_version or 0)
            await session.commit()
        await _reindex_route(
            app_id, app.name, app.description or "", caps, "campus_internal", enabled_now,
            tenant_id=str(app.tenant_id or "0"), published_version=published_version_now,
            health=health_now, routing=routing, capability_names=capability_names,
            route_text=route_text,
        )
        from app.services.agents.recommendation_index import request_rebuild
        request_rebuild()
    except Exception:  # noqa: BLE001
        logger.warning("Capability Registry 同步失败 app_id=%s", app_id, exc_info=True)


async def remove(app_id: str) -> None:
    async with async_session() as session:
        existing = await session.get(CapabilityRegistry, app_id)
        if existing:
            await session.delete(existing)
            await session.commit()
    await _deindex_route(app_id)
    from app.services.agents.recommendation_index import request_rebuild
    request_rebuild()


async def list_registry(source_system: Optional[str] = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
    async with async_session() as session:
        query = select(CapabilityRegistry)
        if source_system:
            query = query.where(CapabilityRegistry.source_system == source_system)
        if enabled_only:
            query = query.where(CapabilityRegistry.enabled == 1)
        query = query.where(CapabilityRegistry.health != "down")
        rows = (await session.execute(query.order_by(CapabilityRegistry.updated_at.desc()))).scalars().all()
        return [_to_dict(r) for r in rows]


async def external_candidates(*, tenant_id: str) -> List[Dict[str, Any]]:
    """R6 外部兜底候选（§8.1）：委托权威表 `app_info_capability_registry` 的 external_app 条目
    （来自 app_info 广场应用同步）。返回 {id,name,description,url,source:external}，用于「前往使用」
    推荐卡，不派发 call_subagent。

    tenant_id 必传（深扫 P0-3）：按调用者租户过滤，语义同 published_visibility.tenant_matches。"""
    from app.services.agents import app_capability_registry
    return await app_capability_registry.external_candidates(tenant_id=tenant_id)


async def backfill_all() -> int:
    async with async_session() as session:
        ids = (
            await session.execute(
                select(WorkflowApp.id).where(
                    WorkflowApp.ai_app_type.in_(_ROUTABLE_TYPES), WorkflowApp.status == "published"
                )
            )
        ).scalars().all()
    for app_id in ids:
        await sync_from_app(app_id)
    return len(ids)


async def backfill_if_empty() -> int:
    """启动期回填/重建（幂等、best-effort）：

    - Registry 空表：按已发布应用建齐（含路由向量索引）；
    - Registry 非空但存在 index_version < ROUTE_INDEX_VERSION 的行（复审 #6：存量环境
      只跑了 SQL 迁移、旧 Qdrant 点位缺 tenant/runtime/health 等新 payload 字段）：
      自动整体 backfill_all 重建，避免新硬过滤把旧点位全部滤空、向量召回静默失效；
    - 租户对账（2026-07-27 真机故障）：应用租户被迁移/修正而未走发布同步时，registry 行
      tenant_id 陈旧，_registry_candidates 的租户闸会把候选整体滤空、call_subagent 不注册；
      而 index_version/route_text_hash 均不含租户，上面的变更检测发现不了。这里直接与
      WorkflowApp 联表比对（'0'/NULL/'' 统一按全局占位归一），漂移行逐个 sync_from_app
      重同步（连带重写 Qdrant 点位 payload 的 tenant_id，向量召回同样按租户过滤）。
    """
    try:
        drifted: List[str] = []
        async with async_session() as session:
            has_any = (await session.execute(select(CapabilityRegistry.app_id))).scalars().first()
            stale = 0
            if has_any:
                try:
                    from sqlalchemy import func as sa_func, or_ as sa_or
                    stale = (
                        await session.execute(
                            select(sa_func.count()).select_from(CapabilityRegistry).where(
                                sa_or(
                                    CapabilityRegistry.index_version.is_(None),
                                    CapabilityRegistry.index_version < ROUTE_INDEX_VERSION,
                                )
                            )
                        )
                    ).scalar() or 0
                except Exception:  # noqa: BLE001
                    # index_version 列尚未迁移：视为过期，触发重建前先由迁移补列，
                    # 这里不因缺列崩掉启动
                    stale = 0
                if not stale:
                    try:
                        from sqlalchemy import func as sa_func
                        norm_reg = sa_func.coalesce(
                            sa_func.nullif(CapabilityRegistry.tenant_id, ""), "0")
                        norm_app = sa_func.coalesce(
                            sa_func.nullif(WorkflowApp.tenant_id, ""), "0")
                        drifted = (
                            await session.execute(
                                select(CapabilityRegistry.app_id)
                                .join(WorkflowApp, WorkflowApp.id == CapabilityRegistry.app_id)
                                .where(norm_reg != norm_app)
                            )
                        ).scalars().all()
                    except Exception:  # noqa: BLE001
                        logger.warning("Capability Registry 租户对账查询失败（跳过本次自愈）",
                                       exc_info=True)
                        drifted = []
        if has_any and not stale:
            if not drifted:
                return 0
            for app_id in drifted:
                await sync_from_app(app_id)
            logger.warning(
                "【Capability Registry 租户对账】%d 条 registry 行 tenant_id 与应用不一致，"
                "已重同步归一（含路由向量点位）：%s",
                len(drifted), list(drifted)[:20],
            )
            return len(drifted)
        n = await backfill_all()
        if n:
            logger.info(
                "Capability Registry 启动%s %d 条（index_version→%d）",
                "重建" if has_any else "回填", n, ROUTE_INDEX_VERSION,
            )
        return n
    except Exception:  # noqa: BLE001
        # 明确报警（复审二轮 #2）：Registry 是 call_subagent 候选的硬依赖，启动自检失败
        # 意味着主对话可能发现不了任何子智能体——运维必须处理（跑迁移后重启，或手动
        # POST /workflow/registry/backfill）。运行期另有「全局未初始化才回退 ACL」兜底。
        logger.error(
            "【Capability Registry 启动自检失败】主对话候选发现可能不可用："
            "请确认 mysql 迁移已执行（alembic -n mysql upgrade head）后重启，"
            "或手动调用 /workflow/registry/backfill 重建", exc_info=True,
        )
        return 0


async def internal_candidates(allowed_ids: set) -> List[Dict[str, Any]]:
    """R3 内部候选：Registry 强过滤（agent_workbench + campus_internal + enabled + health!=down）
    ∩ 用户 ACL 允许集（allowed_ids，来自 list_subagents）。返回 {id,name,description} 供 R4 精选。

    Registry 为空（未回填/降级）时返回空——调用方据此回退到 list_subagents 兜底，路由不中断。
    """
    if not allowed_ids:
        return []
    rows = await list_registry(source_system="agent_workbench", enabled_only=True)
    out = []
    for r in rows:
        if r.get("appId") in allowed_ids and r.get("executionScope") == "campus_internal":
            out.append({"id": r["appId"], "name": r["name"], "description": r.get("description", "")})
    return out


# ---------- call_subagent 候选语义发现（2026-07-22，主路径统一入口） ----------
# 流程：完整 ACL 允许集（MySQL 权威）→ Registry 运行状态硬过滤 → 语义+关键词混合召回
# → Top K 注册为 call_subagent 候选。召回层只决定主模型「看得到谁」，绝不替主模型
# 决定调用谁；实际执行前 subagent_service._resolve_accessible 仍实时重查 MySQL 权限。
# 独立于 AUTO_ROUTE_ENABLED（旧整轮路由默认关，不因此启用）。

def _tenant_of(user: Any) -> str:
    return str(getattr(user, "tenant_id", "0") or "0")


def _parse_routing(row: Any) -> Dict[str, Any]:
    """解析 Registry 行的 routing_json（Phase B 列；缺列/空值安全降级为空元数据）。"""
    raw = getattr(row, "routing_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _candidate_shape(
    *, app_id: str, name: str, description: str, route_description: str,
    ai_app_type: str, owner_user_id: str, user: Any, published_version: int,
    tags: Optional[list] = None, trigger_examples: Optional[list] = None,
    negative_examples: Optional[list] = None, updated_at: Any = None,
) -> Dict[str, Any]:
    return {
        "id": str(app_id),
        "name": _sanitize_meta(name, 64),
        "description": _sanitize_meta(description, 200),
        "route_description": _sanitize_meta(route_description, 200),
        "type": str(ai_app_type or "workflow"),
        "scope": "owned" if owner_user_id == getattr(user, "user_id", None) else "shared",
        "published_version": int(published_version or 0),
        "tags": [_sanitize_meta(t, 32) for t in (tags or [])[:20] if str(t or "").strip()],
        "trigger_examples": [_sanitize_meta(t, 200) for t in (trigger_examples or [])[:20]
                             if str(t or "").strip()],
        # 内部排序字段（负例扣分用）：_public_candidate 不外发
        "negative_examples": [_sanitize_meta(t, 200) for t in (negative_examples or [])[:20]
                              if str(t or "").strip()],
        "updated_at": updated_at,
    }


async def _registry_candidates(allowed_ids: set, tenant_id: str, user: Any) -> List[Dict[str, Any]]:
    """Registry 硬过滤 ∩ ACL 允许集：source/scope/runtime/enabled/health/version/tenant。"""
    async with async_session() as session:
        query = (
            select(CapabilityRegistry)
            .where(
                CapabilityRegistry.app_id.in_(list(allowed_ids)),
                CapabilityRegistry.source_system == "agent_workbench",
                CapabilityRegistry.execution_scope == "campus_internal",
                CapabilityRegistry.runtime_type == "python_workflow",
                CapabilityRegistry.enabled == 1,
                CapabilityRegistry.health != "down",
                CapabilityRegistry.published_version > 0,
            )
        )
        tenant_col = getattr(CapabilityRegistry, "tenant_id", None)
        if tenant_col is not None:
            # 智能体/工作流候选与运行入口使用同一个临时租户开关；恢复时该处会自动
            # 重新应用 tenant_visible_clause，避免“可推荐但不可运行”。
            from app.services.agents.published_visibility import workflow_tenant_visible_clause
            query = query.where(workflow_tenant_visible_clause(tenant_col, tenant_id))
        rows = (await session.execute(query)).scalars().all()
    out = []
    for row in rows:
        routing = _parse_routing(row)
        out.append(_candidate_shape(
            app_id=row.app_id, name=row.name, description=row.description or "",
            route_description=str(routing.get("routeDescription") or ""),
            ai_app_type=row.ai_app_type, owner_user_id=row.owner_user_id or "",
            user=user, published_version=row.published_version or 0,
            tags=routing.get("tags") or [],
            trigger_examples=routing.get("triggerExamples") or [],
            negative_examples=routing.get("negativeExamples") or [],
            updated_at=row.updated_at,
        ))
    return out


async def _registry_has_any_row() -> bool:
    """Registry 是否已初始化（全局存在任意行，不带过滤）。用于区分两种「空」：
    全局无行=未初始化（迁移后回填未跑），允许临时回退 ACL；有行但过滤为空=正常结果。"""
    async with async_session() as session:
        first = (await session.execute(
            select(CapabilityRegistry.app_id).limit(1)
        )).scalars().first()
    return first is not None


async def _acl_app_candidates(allowed_ids: set, user: Any) -> List[Dict[str, Any]]:
    """降级路径（Registry 不可用）：完整 ACL 应用集合做 name/description 关键词候选。"""
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    WorkflowApp.id, WorkflowApp.name, WorkflowApp.description,
                    WorkflowApp.ai_app_type, WorkflowApp.owner_user_id, WorkflowApp.update_time,
                ).where(WorkflowApp.id.in_(list(allowed_ids)))
            )
        ).all()
    return [
        _candidate_shape(
            app_id=app_id, name=name, description=description or "",
            route_description="", ai_app_type=ai_app_type, owner_user_id=owner or "",
            user=user, published_version=0, updated_at=update_time,
        )
        for app_id, name, description, ai_app_type, owner, update_time in rows
    ]


def _query_tokens(query_text: str) -> List[str]:
    """轻量分词：ASCII 词（≥2 字符）+ 中文 bigram（中文子串匹配即可，不引入分词依赖）。"""
    q = (query_text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", q))
    cjk = re.findall(r"[一-鿿]+", q)
    for seg in cjk:
        if len(seg) == 1:
            tokens.add(seg)
        for i in range(len(seg) - 1):
            tokens.add(seg[i:i + 2])
    return list(tokens)


def _negative_hits(query_lower: str, tokens: List[str], candidate: Dict[str, Any]) -> int:
    """负例匹配计数（复审 #5）：负例整句被消息包含记重，token 命中记轻。只用于扣分。"""
    hits = 0
    for neg in candidate.get("negative_examples") or []:
        nl = str(neg or "").strip().lower()
        if not nl:
            continue
        if nl in query_lower or query_lower in nl:
            hits += 3
        else:
            hits += sum(1 for t in tokens if t in nl) > 0
    return hits


def _lexical_rank(query_text: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """关键词召回：名称精确/包含命中最高，route_description/description/tags/
    trigger_examples 子串命中累计。negativeExamples 单独计分并**扣分**（§14.18/复审 #5）。"""
    q = (query_text or "").strip().lower()
    if not q:
        return []
    tokens = _query_tokens(q)
    scored: List[tuple] = []
    for c in candidates:
        score = 0.0
        name = (c.get("name") or "").lower()
        if name:
            if name == q:
                score += 200.0
            elif name in q:
                score += 120.0  # 消息里出现完整名称
            elif q in name:
                score += 60.0
        fields = [
            ((c.get("route_description") or "").lower(), 3.0),
            ((c.get("description") or "").lower(), 2.0),
            (" ".join(c.get("trigger_examples") or []).lower(), 2.0),
        ]
        for text, weight in fields:
            if not text:
                continue
            hit = sum(1 for t in tokens if t in text)
            score += weight * hit
        for tag in c.get("tags") or []:
            tl = str(tag).lower()
            if tl and tl in q:
                score += 15.0
            elif tl and any(t in tl for t in tokens):
                score += 4.0
        score -= 8.0 * _negative_hits(q, tokens, c)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _s, c in scored]


def _build_query_text(query: str, history: Optional[List[dict]]) -> str:
    """检索文本：当前消息为主，附最近 2~4 条对话辅助识别指代（严格限长）。"""
    parts = [(query or "").strip()[:400]]
    recent = []
    for m in (history or [])[-4:]:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip().replace("\n", " ")
        if text:
            recent.append(text[:120])
    if recent:
        parts.append("最近对话：" + " / ".join(recent))
    return "\n".join(p for p in parts if p)[:800]


async def _vector_route_hits(
    query_text: str,
    candidate_ids: List[str],
    tenant_id: str,
    recall_k: int,
    *,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """向量召回（带 score）。embedding/Qdrant 不可用返回 None（降级关键词）。"""
    if not query_text or not candidate_ids:
        return None
    try:
        from app.services.knowledge import embedding_service, vector_service
        cfg = await embedding_service.get_active_embedding_config()
        if not cfg:
            return None
        vec = await embedding_service.embed_query(
            query_text,
            config=cfg,
            audit_run_id=audit_run_id,
            audit_thread_id=audit_thread_id,
            audit_root_run_id=audit_root_run_id,
            audit_purpose_detail="subagent_candidate_discovery",
        )
        if len(vec) != cfg.dimension:
            return None
        collection = vector_service.route_collection_name(cfg.model, cfg.dimension)
        return await vector_service.search_routes(
            collection, vec, candidate_ids, recall_k, tenant_id=tenant_id
        )
    except Exception:  # noqa: BLE001
        logger.debug("候选发现向量召回不可用，降级关键词", exc_info=True)
        return None


def _rrf_merge(
    candidates: List[Dict[str, Any]],
    vector_hits: List[Dict[str, Any]],
    lexical_ranked: List[Dict[str, Any]],
    query_text: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    """稳定混合排序（RRF）：向量排名 + 关键词排名 + 名称/标签精确命中额外加权，
    负例命中统一扣分（向量召回无法感知负例，在此兜底，复审 #5）；
    不足 top_k 时按 updated_at 补齐（保证模型仍有候选目录可看）。"""
    k = 60.0
    by_id = {c["id"]: c for c in candidates}
    scores: Dict[str, float] = {}
    for rank, hit in enumerate(vector_hits or []):
        cid = str(hit.get("id") or "")
        if cid in by_id:
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    for rank, c in enumerate(lexical_ranked or []):
        cid = c["id"]
        if cid in by_id:
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    q = (query_text or "").lower()
    tokens = _query_tokens(q)
    for c in candidates:
        name = (c.get("name") or "").lower()
        if name and (name == q or name in q):
            scores[c["id"]] = scores.get(c["id"], 0.0) + 1.5 / k
        elif any(str(t).lower() and str(t).lower() in q for t in (c.get("tags") or [])):
            scores[c["id"]] = scores.get(c["id"], 0.0) + 0.5 / k
        neg = _negative_hits(q, tokens, c)
        if neg and c["id"] in scores:
            scores[c["id"]] -= (0.75 / k) * min(neg, 3)
    ordered = [by_id[cid] for cid, _s in sorted(scores.items(), key=lambda x: -x[1])]
    if len(ordered) < top_k:
        seen = {c["id"] for c in ordered}
        rest = [c for c in candidates if c["id"] not in seen]
        rest.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)
        ordered.extend(rest[: top_k - len(ordered)])
    return ordered[:top_k]


def _public_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    """对外候选对象（进工具目录/挂起快照）：剥离内部排序字段。"""
    public = {
        "id": c["id"],
        "name": c["name"],
        "description": c.get("description") or "",
        "route_description": c.get("route_description") or "",
        "type": c.get("type") or "workflow",
        "scope": c.get("scope") or "shared",
        "published_version": int(c.get("published_version") or 0),
    }
    if c.get("icon"):
        public["icon"] = str(c["icon"])
    return public


async def _attach_current_icons(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把候选快照补成工作台当前头像；头像不是 Registry 的可路由元数据。"""
    ids = [str(c.get("id") or "") for c in candidates if c.get("id")]
    if not ids:
        return candidates
    async with async_session() as session:
        rows = (await session.execute(
            select(WorkflowApp.id, WorkflowApp.app_icon).where(WorkflowApp.id.in_(ids))
        )).all()
    icons = {str(app_id): str(icon or "") for app_id, icon in rows}
    for candidate in candidates:
        candidate["icon"] = icons.get(str(candidate.get("id") or ""), "")
    return candidates


async def discover_for_call(
    user: Any,
    query: str,
    history: Optional[List[dict]] = None,
    top_k: int = 0,
    *,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
) -> List[Dict[str, Any]]:
    """主对话 call_subagent 候选统一发现入口（§五）。

    降级顺序（§十二）：Registry+混合召回 → 向量不可用走关键词 → Registry 不可用走
    完整 ACL 应用集关键词 → 全部失败返回 []（不注册 call_subagent，主对话不受影响）。
    任何异常都不抛出——检索失败绝不能拖垮 /chat。
    """
    t0 = time.monotonic()
    top_k = int(top_k or settings.SUBAGENT_DISCOVERY_TOP_K or 12)
    fallback_reason = ""
    vector_hits: List[Dict[str, Any]] = []
    lexical_ranked: List[Dict[str, Any]] = []
    discovery_mode = str(settings.SUBAGENT_DISCOVERY_MODE or "hybrid")
    try:
        from app.services.agents import subagent_service
        try:
            allowed_ids = await subagent_service.list_callable_subagent_ids(user)
        except Exception:  # noqa: BLE001
            logger.warning("候选发现 ACL 全集查询失败，本轮不注册 call_subagent", exc_info=True)
            return []
        tenant = _tenant_of(user)
        if not allowed_ids:
            logger.info(
                "subagent_discovery user_id=%s tenant_id=%s allowed_count=0 registry_count=0 "
                "vector_hit_count=0 lexical_hit_count=0 final_candidate_ids=[] discovery_mode=%s "
                "fallback_reason=no_allowed latency_ms=%d",
                getattr(user, "user_id", ""), tenant, discovery_mode,
                int((time.monotonic() - t0) * 1000),
            )
            return []

        registry_count = 0
        registry_ok = True
        try:
            candidates = await _registry_candidates(allowed_ids, tenant, user)
            registry_count = len(candidates)
        except Exception:  # noqa: BLE001
            logger.warning("Registry 候选查询失败，降级 ACL 应用集关键词", exc_info=True)
            registry_ok = False
            candidates = []
            fallback_reason = "registry_unavailable"
        if not registry_ok:
            # 只有 Registry **不可用**（查询异常/表缺失）才允许回退 ACL 应用集
            candidates = await _acl_app_candidates(allowed_ids, user)
            discovery_mode = "acl_lexical"
        elif not candidates:
            # 过滤为空的两种「空」必须区分（复审二轮 #2）：
            # - 全局无任何行 = Registry 未初始化（迁移后回填未跑/被跳过）：临时回退 ACL
            #   并 warning 报警，等启动自检/手动 backfill 补齐——否则新环境主对话一个
            #   子智能体都发现不了；
            # - 全局有行、本次过滤为空（候选全部停用/health=down/版本无效）：如实返回空，
            #   绝不回退 ACL——那会绕过 enabled/health 运维闸把停用智能体放回候选。
            uninitialized = False
            try:
                uninitialized = not await _registry_has_any_row()
            except Exception:  # noqa: BLE001
                uninitialized = False
            if uninitialized:
                logger.warning(
                    "Capability Registry 全局为空（未初始化），临时回退 ACL 候选 "
                    "user_id=%s reason_code=registry_uninitialized——请执行 registry backfill",
                    getattr(user, "user_id", ""),
                )
                candidates = await _acl_app_candidates(allowed_ids, user)
                discovery_mode = "acl_lexical"
                fallback_reason = "registry_uninitialized"
            else:
                logger.info(
                    "subagent_discovery user_id=%s tenant_id=%s allowed_count=%d registry_count=0 "
                    "vector_hit_count=0 lexical_hit_count=0 final_candidate_ids=[] discovery_mode=%s "
                    "fallback_reason=registry_filtered_empty latency_ms=%d",
                    getattr(user, "user_id", ""), tenant, len(allowed_ids), discovery_mode,
                    int((time.monotonic() - t0) * 1000),
                )
                return []

        if len(candidates) <= top_k:
            final = list(candidates)
        else:
            query_text = _build_query_text(query, history)
            if discovery_mode == "hybrid" and str(settings.SUBAGENT_DISCOVERY_MODE) != "lexical":
                recall_k = max(top_k, int(settings.SUBAGENT_DISCOVERY_RECALL_K or top_k * 3))
                hits = await _vector_route_hits(
                    query_text,
                    [c["id"] for c in candidates],
                    tenant,
                    recall_k,
                    audit_run_id=audit_run_id,
                    audit_thread_id=audit_thread_id,
                    audit_root_run_id=audit_root_run_id,
                )
                if hits is None:
                    fallback_reason = (fallback_reason + "+" if fallback_reason else "") + "vector_unavailable"
                else:
                    vector_hits = hits
            lexical_ranked = _lexical_rank(query_text, candidates)
            final = _rrf_merge(candidates, vector_hits, lexical_ranked, query_text, top_k)
            if not vector_hits and not lexical_ranked:
                # 双召回皆空的兜底补齐已在 _rrf_merge 内按 updated_at 发生：显式记录
                fallback_reason = (fallback_reason + "+" if fallback_reason else "") + "padded_recent"
                logger.warning(
                    "候选发现无任何召回命中，按 updated_at 兜底补齐 user_id=%s reason_code=padded_recent",
                    getattr(user, "user_id", ""),
                )

        result = [_public_candidate(c) for c in await _attach_current_icons(final)]
        logger.info(
            "subagent_discovery user_id=%s tenant_id=%s allowed_count=%d registry_count=%d "
            "vector_hit_count=%d lexical_hit_count=%d final_candidate_ids=%s discovery_mode=%s "
            "fallback_reason=%s latency_ms=%d",
            getattr(user, "user_id", ""), tenant, len(allowed_ids), registry_count,
            len(vector_hits), len(lexical_ranked), [c["id"] for c in result], discovery_mode,
            fallback_reason or "none", int((time.monotonic() - t0) * 1000),
        )
        return result
    except Exception:  # noqa: BLE001
        logger.warning("候选发现整体失败，本轮不注册 call_subagent", exc_info=True)
        return []


async def vector_recall(
    query: str,
    allowed_ids: set,
    top_k: int,
    *,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """向量召回内部候选（§8 R3「返回少量内部候选」）。

    返回 top-K 候选（已按 execution_scope/enabled/ACL 强过滤）；embedding/Qdrant 不可用或
    无命中时返回 None，调用方回退全量 internal_candidates。best-effort，绝不抛。
    """
    if not query or not query.strip() or not allowed_ids:
        return None
    try:
        from app.services.knowledge import embedding_service, vector_service
        cfg = await embedding_service.get_active_embedding_config()
        if not cfg:
            return None
        vec = await embedding_service.embed_query(
            query,
            config=cfg,
            audit_run_id=audit_run_id,
            audit_thread_id=audit_thread_id,
            audit_root_run_id=audit_root_run_id,
            audit_purpose_detail="auto_route_candidate_recall",
        )
        if len(vec) != cfg.dimension:
            return None
        collection = vector_service.route_collection_name(cfg.model, cfg.dimension)
        hits = await vector_service.search_routes(collection, vec, list(allowed_ids), top_k)
        return hits or None
    except Exception:  # noqa: BLE001
        logger.debug("路由向量召回跳过", exc_info=True)
        return None
