import hashlib
import logging
import re
from typing import Dict, List, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings


def _resolve_is_public(a: dict) -> bool:
    """智能体推荐/检索的公开性判定（§4.4 风险 3：空 ACL 判公开会越权曝光）。

    显式 `is_public`（Java 同步契约字段）优先。否则按 `AGENT_ACL_STRICT`：
    - 严格（True）：空/未知 ACL = 私有（deny-by-default，只有 admin 或命中 role/dept 可见）；
    - 兼容（False，默认）：空 ACL = 公开（旧行为）。

    默认保持兼容以免在 Java 补齐 ACL/is_public 前把广场清空；Java 契约就绪后置 True 即启用严格。
    """
    explicit = a.get("is_public")
    if isinstance(explicit, bool):
        return explicit
    empty_acl = not a.get("role_ids") and not a.get("dept_ids")
    if getattr(settings, "AGENT_ACL_STRICT", False):
        return False
    return empty_acl

logger = logging.getLogger(__name__)

_client: Optional[AsyncQdrantClient] = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.QDRANT_URL)
    return _client


def _stable_point_id(agent_id: str) -> int:
    return int(hashlib.sha256(agent_id.encode()).hexdigest()[:16], 16)


# 广场智能体索引的 payload 口径版本（同 capability_registry.ROUTE_INDEX_VERSION 的作用）。
# **改 payload 字段集/语义必须 bump**：集合名里带版本 → bump 后是一个全新的空集合，
# 启动钩子 auto_backfill_if_empty 与登录自愈入口 /embedding-config/ensure-indexed 都以
# 「集合为空」为触发条件，于是自动全量回填出带新字段的点位。
# 不 bump 而原地改口径的后果（历史教训）：旧点位没有新字段，Qdrant 的 MatchAny 对缺字段
# 一律不命中 → 过滤后召回恒空 → 推荐/系统提示词里的智能体目录整体消失。
# v4（2026-07-26）：payload 增加 tenant_id，search() 起按租户过滤。
AGENT_INDEX_VERSION = 4


def collection_name(model_id: str, dimension: int) -> str:
    version = hashlib.sha256(f"{model_id}:{dimension}".encode()).hexdigest()[:12]
    return f"agents_v{AGENT_INDEX_VERSION}_{version}"


def _normalize_tenant(value) -> str:
    """租户归一：NULL/''/空白 → '0'（全局占位）。与 published_visibility.tenant_matches 同口径。"""
    return str(value).strip() if value is not None and str(value).strip() else "0"


def _agent_workflow_tenant_values(tenant_id) -> Optional[list[str]]:
    """智能体/工作流索引的临时租户开关（知识库检索不使用此函数）。"""
    if not settings.AGENT_WORKFLOW_TENANT_ISOLATION_ENABLED:
        return None
    tenant = _normalize_tenant(tenant_id)
    return ["0"] if tenant == "0" else ["0", tenant]


async def ensure_collection(collection: str, dimension: int) -> None:
    client = _get_client()
    collections = await client.get_collections()
    names = {c.name for c in collections.collections}
    if collection not in names:
        await client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s' (dim=%d)", collection, dimension)
    else:
        logger.info("Qdrant collection '%s' already exists", collection)


async def upsert_agents(collection: str, agents: List[dict]) -> None:
    if not agents:
        return
    client = _get_client()
    points = [
        PointStruct(
            id=_stable_point_id(a["agent_id"]),
            vector=a["vector"],
            payload={
                "agent_id": a["agent_id"],
                # 租户维度（深扫 P0）：缺省 '0' = 全局占位，对所有租户可见；见 search() 过滤口径
                "tenant_id": _normalize_tenant(a.get("tenant_id")),
                "owner_user_id": a.get("owner_user_id", ""),
                "name": a["name"],
                "description": a.get("description", ""),
                "icon": a.get("icon", ""),
                "category": a.get("category", ""),
                "status": a.get("status", 1),
                "published": a.get("published", True),
                "is_public": _resolve_is_public(a),
                "role_ids": a.get("role_ids", []),
                "dept_ids": a.get("dept_ids", []),
                "content_hash": a.get("content_hash", ""),
                "source_version": a.get("source_version", 0),
            },
        )
        for a in agents
    ]
    await client.upsert(collection_name=collection, points=points)


async def delete_agent(collection: str, agent_id: str) -> None:
    client = _get_client()
    await client.delete(
        collection_name=collection,
        points_selector=[_stable_point_id(agent_id)],
    )


async def list_agent_ids(collection: str) -> set:
    """列出集合内全部 agent_id（用于轮询删除对账）。"""
    client = _get_client()
    ids: set = set()
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=collection,
            with_payload=["agent_id"],
            with_vectors=False,
            limit=1000,
            offset=offset,
        )
        for p in points:
            aid = (p.payload or {}).get("agent_id")
            if aid:
                ids.add(str(aid))
        if offset is None:
            break
    return ids


async def delete_by_agent_ids(collection: str, agent_ids: List[str]) -> None:
    """按 agent_id 批量删除，用于清理已删除的应用。"""
    if not agent_ids:
        return
    client = _get_client()
    await client.delete(
        collection_name=collection,
        points_selector=Filter(must=[FieldCondition(key="agent_id", match=MatchAny(any=agent_ids))]),
    )


async def get_agent_metadata(collection: str, agent_id: str) -> Optional[dict]:
    client = _get_client()
    points = await client.retrieve(
        collection_name=collection,
        ids=[_stable_point_id(agent_id)],
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None
    return dict(points[0].payload or {})


async def update_agent_payload(collection: str, agent: dict) -> None:
    client = _get_client()
    await client.set_payload(
        collection_name=collection,
        payload={
            "agent_id": agent["agent_id"],
            # 与 upsert_agents 保持同一 payload 口径：内容未变、仅元数据更新的分支也要写租户，
            # 否则「改名/改描述以外的编辑」会让点位停留在旧租户值上
            "tenant_id": _normalize_tenant(agent.get("tenant_id")),
            "owner_user_id": agent.get("owner_user_id", ""),
            "name": agent["name"],
            "description": agent.get("description", ""),
            "icon": agent.get("icon", ""),
            "category": agent.get("category", ""),
            "status": agent.get("status", 1),
            "published": agent.get("published", True),
            "is_public": _resolve_is_public(agent),
            "role_ids": agent.get("role_ids", []),
            "dept_ids": agent.get("dept_ids", []),
            "content_hash": agent.get("content_hash", ""),
            "source_version": agent.get("source_version", 0),
        },
        points=[_stable_point_id(agent["agent_id"])],
    )


async def search(
    collection: str,
    query_vec: List[float],
    user_role_ids: List[str],
    user_dept_ids: List[str],
    is_admin_user: bool,
    top_k: int,
    tenant_id: str = "0",
) -> List[dict]:
    """按租户 + ACL 检索智能体。两者都在 Qdrant filter 中执行。

    租户口径与 published_visibility.tenant_visible_clause / search_routes 逐字对齐：
    应用侧 '0' = 全局占位（历史/未分配），对任何租户可见；非 0 租户 = 本租户 ∪ 全局占位。

    tenant_id 缺省 '0'，且空值/解析失败一律归一为 '0' —— 退化成「只看得见全局应用」，
    宁可丢召回也不越权（Java 回源的租户解析本就不稳，同一 token 相邻请求可能解析成
    '0' 或 '1000'）。**租户过滤先于 ACL**：is_admin 只豁免 ACL，不豁免租户隔离——
    A 租户的管理员不该在推荐里看到 B 租户的应用。
    """
    client = _get_client()

    must = [
        FieldCondition(key="status", match=MatchValue(value=1)),
        FieldCondition(key="published", match=MatchValue(value=True)),
    ]
    tenant_values = _agent_workflow_tenant_values(tenant_id)
    if tenant_values is not None:
        must.insert(0, FieldCondition(key="tenant_id", match=MatchAny(any=tenant_values)))

    if not is_admin_user:
        # ACL：公开 OR 角色匹配 OR 部门匹配
        acl_should = [
            Filter(must=[FieldCondition(key="is_public", match=MatchValue(value=True))]),
        ]
        if user_role_ids:
            acl_should.append(
                Filter(must=[
                    FieldCondition(key="role_ids", match=MatchAny(any=user_role_ids))
                ])
            )
        if user_dept_ids:
            acl_should.append(
                Filter(must=[
                    FieldCondition(key="dept_ids", match=MatchAny(any=user_dept_ids))
                ])
            )
        must.append(Filter(should=acl_should))

    results = await client.search(
        collection_name=collection,
        query_vector=query_vec,
        query_filter=Filter(must=must),
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "id": r.payload.get("agent_id", ""),
            "name": r.payload.get("name", ""),
            "description": r.payload.get("description", ""),
            "score": float(r.score),   # 相似度分数：推荐时按阈值筛「相关」智能体
        }
        for r in results
        if r.payload
    ]


async def count_collection(collection: str) -> int:
    client = _get_client()
    result = await client.count(collection_name=collection)
    return result.count


async def delete_collection(collection: str) -> None:
    client = _get_client()
    await client.delete_collection(collection_name=collection)
    logger.info("Deleted Qdrant collection '%s'", collection)


# 广场智能体集合命名口径：agents_v{版本号}_{embedding配置哈希后缀}（见 collection_name）。
# 版本化之前存在一份裸集合 `agents`（无版本、无 embedding 后缀），一并当陈旧副本清理。
_AGENT_COLLECTION_RE = re.compile(r"^agents_v(\d+)_([0-9a-f]+)$")
_LEGACY_AGENT_COLLECTION = "agents"


async def cleanup_stale_agent_collections(current_collection: str) -> List[str]:
    """全量回填成功后，删除被新版本取代的陈旧广场智能体集合，避免每次 bump 版本堆积整份副本。

    删除范围（严格收窄，宁可漏删也不误删）：
      - 与当前集合**同 embedding 配置哈希后缀**、但**版本号更低**的 `agents_v{N}_{后缀}`；
      - 版本化之前的裸集合 `agents`。
    绝不触碰：当前集合本身、相同/更高版本、不同后缀（属另一套 embedding 配置的集合）、
    以及路由索引 `agent_route_v1_*`（前缀是 `agent_route_`，正则天然不命中）。

    安全阀：**仅当当前集合已验证非空时**才执行删除——否则一旦回填异常把当前集合灌空，
    又把旧副本删掉，广场就彻底没有可召回的数据了。调用方（回填流程）本就在回填成功后
    才调用；这里再自校验一次数量，双保险。单个集合删除失败不影响其余（下次回填再补删）。

    返回实际删除的集合名列表。
    """
    m = _AGENT_COLLECTION_RE.match(current_collection)
    if not m:
        # 当前集合名不符合 agents_v{N}_{后缀} 口径：无法据此推断“更低版本” —— 直接跳过，绝不猜
        logger.warning(
            "[cleanup] 当前集合名 '%s' 非预期格式，跳过陈旧集合清理", current_collection
        )
        return []
    current_version = int(m.group(1))
    current_suffix = m.group(2)

    client = _get_client()

    # 安全阀：当前集合非空才允许删旧副本
    try:
        current_count = (await client.count(collection_name=current_collection)).count
    except Exception as e:
        logger.warning(
            "[cleanup] 读取当前集合 '%s' 数量失败，跳过清理：%s", current_collection, e
        )
        return []
    if current_count <= 0:
        logger.warning(
            "[cleanup] 当前集合 '%s' 为空，跳过陈旧集合清理（不删任何旧副本）", current_collection
        )
        return []

    try:
        existing = {c.name for c in (await client.get_collections()).collections}
    except Exception as e:
        logger.warning("[cleanup] 列举 Qdrant 集合失败，跳过清理：%s", e)
        return []

    stale: List[str] = []
    for name in existing:
        if name == current_collection:
            continue  # 绝不删当前集合
        if name == _LEGACY_AGENT_COLLECTION:
            stale.append(name)  # 版本化之前的裸集合
            continue
        sm = _AGENT_COLLECTION_RE.match(name)
        if not sm:
            continue  # 非 agents_v* —— 含路由索引 agent_route_v1_*，前缀不同不会命中
        version = int(sm.group(1))
        suffix = sm.group(2)
        # 只清同一 embedding 配置、且严格更低版本的（相同/更高版本一律保留）
        if suffix == current_suffix and version < current_version:
            stale.append(name)

    deleted: List[str] = []
    for name in sorted(stale):
        try:
            await delete_collection(name)  # delete_collection 内已记 info 日志
            deleted.append(name)
        except Exception as e:
            logger.warning("[cleanup] 删除陈旧集合 '%s' 失败（下次回填重试）：%s", name, e)

    if deleted:
        logger.info(
            "[cleanup] 已清理 %d 个陈旧广场集合（当前=%s 非空 %d 条）：%s",
            len(deleted), current_collection, current_count, deleted,
        )
    return deleted


# ---------- 路由索引（Phase 7 §12.2：向量化召回内部可执行 Capability） ----------
# 与广场智能体索引（agents_v3_*）隔离：这里只存工作台已发布子智能体的路由描述副本，
# payload 只放路由所需字段（app_id/name/execution_scope/enabled），不存 apiKey/敏感数据。

def route_collection_name(model_id: str, dimension: int) -> str:
    version = hashlib.sha256(f"{model_id}:{dimension}".encode()).hexdigest()[:12]
    return f"agent_route_v1_{version}"


async def upsert_routes(collection: str, routes: List[dict]) -> None:
    """写入/更新路由点。routes: [{app_id, tenant_id, name, description, execution_scope,
    runtime_type, published_version, enabled, health, vector}]。payload 是候选发现的
    Qdrant 侧过滤副本——**不是**权限权威源（权威在 MySQL allowed_ids）。"""
    if not routes:
        return
    client = _get_client()
    points = [
        PointStruct(
            id=_stable_point_id(r["app_id"]),
            vector=r["vector"],
            payload={
                "app_id": r["app_id"],
                "tenant_id": str(r.get("tenant_id") or "0"),
                "name": r.get("name", ""),
                "description": r.get("description", ""),
                "execution_scope": r.get("execution_scope", "campus_internal"),
                "source_system": r.get("source_system", "agent_workbench"),
                "runtime_type": r.get("runtime_type", "python_workflow"),
                "published_version": int(r.get("published_version") or 0),
                "enabled": bool(r.get("enabled", True)),
                "health": str(r.get("health") or "healthy"),
            },
        )
        for r in routes
    ]
    await client.upsert(collection_name=collection, points=points)


async def delete_route(collection: str, app_id: str) -> None:
    client = _get_client()
    await client.delete(collection_name=collection, points_selector=[_stable_point_id(app_id)])


async def search_routes(
    collection: str, query_vec: List[float], allowed_ids: List[str], top_k: int,
    tenant_id: str = "0",
) -> List[dict]:
    """向量召回内部候选（带 score）。Qdrant 侧硬过滤：tenant_id + source_system=
    agent_workbench + execution_scope=campus_internal + runtime_type=python_workflow +
    enabled + health!=down + app_id ∈ ACL 允许集。

    Qdrant 只是过滤副本，**不是权限权威源**：即使存在越权/过期点位，也被 app_id
    白名单（调用方来自 MySQL list_callable_subagent_ids ∩ Registry）强行截住。
    allowed_ids 为空则不召回（返回空）。
    """
    if not allowed_ids:
        return []
    client = _get_client()
    must = [
        FieldCondition(key="source_system", match=MatchValue(value="agent_workbench")),
        FieldCondition(key="execution_scope", match=MatchValue(value="campus_internal")),
        FieldCondition(key="runtime_type", match=MatchValue(value="python_workflow")),
        FieldCondition(key="enabled", match=MatchValue(value=True)),
        FieldCondition(key="app_id", match=MatchAny(any=list(allowed_ids))),
    ]
    tenant_values = _agent_workflow_tenant_values(tenant_id)
    if tenant_values is not None:
        must.insert(0, FieldCondition(key="tenant_id", match=MatchAny(any=tenant_values)))
    query_filter = Filter(
        must=must,
        must_not=[
            FieldCondition(key="health", match=MatchValue(value="down")),
        ],
    )
    results = await client.search(
        collection_name=collection,
        query_vector=query_vec,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "id": r.payload.get("app_id", ""),
            "name": r.payload.get("name", ""),
            "description": r.payload.get("description", ""),
            "score": float(r.score),
        }
        for r in results
        if r.payload
    ]
