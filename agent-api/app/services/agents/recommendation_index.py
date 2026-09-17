"""Dedicated high-precision marketplace-agent recommendation index.

The Java ``app_info`` table remains the catalogue/visibility source of truth.  Qdrant is only a
semantic recall accelerator: every result is rehydrated and authorized from MySQL before it can be
returned to the Harness tool.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import bindparam, select, text

from app.core.auth import UserContext
from app.core.config import settings
from app.core.database import async_session
from app.models import CapabilityRegistry
from app.services.agents.published_visibility import (
    load_user_relation_ids,
    tenant_matches,
    workflow_tenant_matches,
)
from app.services.chat.builtin_app_access import (
    CATALOG_APP_TYPE,
    builtin_preset_for_catalog_routes,
)
from app.services.knowledge import embedding_service

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1
RANKING_VERSION = 2
ALIAS_NAME = "agent_recommend_current"
MANIFEST_POINT_ID = 0
RECORD_TYPE = "catalog_agent"
_LOCK_NAME = "agent_recommendation_index_v1"
_READY_CACHE_TTL_SECONDS = 10.0
_GENERIC_NEGATIVE_QUERIES = (
    "帮我写一封简短的通知邮件",
    "总结下面这段文字",
    "把这句话翻译成英文",
    "今天心情不错随便聊聊",
    "帮我润色这段话",
)
_PRODUCT_GOLDEN_QUERIES = (
    ("帮我模拟一场产品经理面试", "面试助手"),
    ("识别这份扫描文档并提取内容", "智能文档识别助手"),
    ("把会议内容整理成会议纪要", "会议纪要助手"),
)
_EXPLICIT_DISCOVERY_RE = re.compile(
    r"(?:智能体广场|(?:推荐|找(?:一)?个|介绍(?:一)?个|有没有|有哪些|哪个|用什么)"
    r".{0,18}(?:智能体|agent|助手|应用|工具)|"
    r"(?:智能体|agent|助手|应用|工具).{0,18}(?:推荐|有没有|哪个|哪些|用什么))",
    re.I,
)
_GENERIC_OFFICE_RE = re.compile(
    r"(?:^(?:帮我|请)?(?:总结|润色|改写|修改)|"
    r"^(?:帮我|请)?写.{0,18}(?:邮件|文字|文案|句子|段落|通知)|"
    r"^(?:帮我|请)?把.{0,30}翻译)"
)
_QUERY_STOP_TOKENS = {
    # 推荐请求的外壳词不能算作候选能力命中，否则每个说明中带
    # “智能体/助手”的记录都会被误判为 description 信号。
    "agent", "智能", "能体", "助手", "应用", "工具", "推荐", "介绍",
    "一个", "有没", "哪个", "哪些", "帮我", "请帮", "想用", "用什",
}

_client: Optional[AsyncQdrantClient] = None
_ready_cache: tuple[float, Optional[dict[str, Any]]] = (0.0, None)
_requested_rebuild: Optional[asyncio.Task] = None
_rebuild_generation = 0


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.QDRANT_URL)
    return _client


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _list(value: Any) -> list[str]:
    raw = _json(value, []) if not isinstance(value, list) else value
    result: list[str] = []
    for item in raw:
        normalized = str(item or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return _list(value)
    return [item for item in re.split(r"[,，;；\s]+", str(value or "").strip()) if item][:20]


def _source_version(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except (TypeError, ValueError):
        return 1


def _point_id(catalog_id: str) -> int:
    value = int(hashlib.sha256(str(catalog_id).encode()).hexdigest()[:16], 16)
    return value or 1


def _embedding_signature(model_id: str, dimension: int) -> str:
    return hashlib.sha256(f"{model_id}:{dimension}".encode()).hexdigest()[:12]


def _physical_collection(model_id: str, dimension: int, digest: str) -> str:
    return f"agent_recommend_v{INDEX_SCHEMA_VERSION}_{_embedding_signature(model_id, dimension)}_{digest[:10]}"


def _query_tokens(value: str) -> set[str]:
    lowered = str(value or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(segment) == 1:
            tokens.add(segment)
        tokens.update(segment[index:index + 2] for index in range(max(0, len(segment) - 1)))
    return {token for token in tokens if token not in _QUERY_STOP_TOKENS}


def _capability_names(raw: Any) -> list[str]:
    caps = _json(raw, {})
    names: list[str] = []
    for key in ("tools", "skills", "knowledge"):
        for item in caps.get(key) or []:
            value = str(item or "").strip()
            if not value or len(value) > 80:
                continue
            if re.fullmatch(r"[0-9a-f-]{24,}", value, re.I):
                continue
            if value not in names:
                names.append(value)
    return names[:16]


def _embedding_text(record: dict[str, Any]) -> str:
    parts = [str(record.get("name") or "").strip()]
    description = str(record.get("route_description") or record.get("description") or "").strip()
    if description:
        parts.append(description)
    triggers = record.get("trigger_examples") or []
    if triggers:
        parts.append("适用任务：" + "；".join(str(item)[:100] for item in triggers[:20]))
    tags = record.get("tags") or []
    if tags:
        parts.append("标签：" + "、".join(tags[:20]))
    category = str(record.get("category") or "").strip()
    if category:
        parts.append("分类：" + category)
    names = record.get("capability_names") or []
    if names:
        parts.append("能力：" + "、".join(names[:16]))
    # negative_examples intentionally stay out of positive embeddings.
    return "。".join(item for item in parts if item)[:1500]


async def _app_info_columns(session) -> set[str]:
    rows = (
        await session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'app_info'"
        ))
    ).scalars().all()
    return {str(item) for item in rows}


def _select_expr(columns: set[str], column: str, fallback_sql: str) -> str:
    return f"a.`{column}`" if column in columns else fallback_sql


def _visibility_allows(
    *,
    user: UserContext,
    tenant_id: str,
    owner_user_id: str,
    role_ids: list[str],
    dept_ids: list[str],
    user_roles: set[str],
    user_depts: set[str],
    internal_workflow: bool,
    global_catalog: bool = False,
) -> bool:
    if global_catalog:
        tenant_allowed = True
    else:
        tenant_allowed = (
            workflow_tenant_matches(tenant_id, user.tenant_id)
            if internal_workflow else tenant_matches(tenant_id, user.tenant_id)
        )
    if not tenant_allowed:
        return False
    if internal_workflow and owner_user_id == user.user_id:
        return True
    if role_ids or dept_ids:
        return bool(set(role_ids) & user_roles or set(dept_ids) & user_depts)
    # 工作流发布语义中空 ACL = 仅 owner，外部广场存量记录空 ACL = 公开。
    return not internal_workflow


async def fetch_catalog_records(user: Optional[UserContext] = None) -> list[dict[str, Any]]:
    """Return current eligible catalogue rows, optionally filtered for a concrete user."""
    async with async_session() as session:
        columns = await _app_info_columns(session)
        if not columns:
            return []
        wanted = {
            "id": "''", "app_name": "''", "app_remark": "''", "app_icon": "''",
            "app_category": "''", "status": "0", "tenant_id": "'0'", "pc_url": "''",
            "h5_url": "''",
            "app_type": "''", "ai_app_type": "''", "is_recommend": "0",
            "form_options": "'{}'", "create_by": "''", "del_flag": "'0'", "update_time": "NULL",
        }
        select_list = ", ".join(
            f"{_select_expr(columns, column, fallback)} AS `{column}`"
            for column, fallback in wanted.items()
        )
        rows = (
            await session.execute(text(f"SELECT {select_list} FROM app_info a"))
        ).mappings().all()
        registries = (
            await session.execute(
                select(CapabilityRegistry).where(
                    CapabilityRegistry.enabled == 1,
                    CapabilityRegistry.health != "down",
                )
            )
        ).scalars().all()
        registry_by_id = {str(row.app_id): row for row in registries}
        relation_roles: list[str] = []
        relation_depts: list[str] = []
        builtin_role_acl: dict[str, list[str]] = {}
        builtin_dept_acl: dict[str, list[str]] = {}
        if user is not None:
            relation_roles, relation_depts = await load_user_relation_ids(session, user)
            builtin_ids = [
                str(raw.get("id") or "").strip()
                for raw in rows
                if str(raw.get("app_type") or "").strip().lower() == CATALOG_APP_TYPE
                and builtin_preset_for_catalog_routes(raw.get("pc_url"), raw.get("h5_url"))
                and str(raw.get("id") or "").strip()
            ]
            if builtin_ids:
                role_rows = (
                    await session.execute(
                        text("SELECT app_id, role_id FROM app_role WHERE app_id IN :ids").bindparams(
                            bindparam("ids", expanding=True)
                        ),
                        {"ids": builtin_ids},
                    )
                ).all()
                dept_rows = (
                    await session.execute(
                        text("SELECT app_id, dept_id FROM app_dept WHERE app_id IN :ids").bindparams(
                            bindparam("ids", expanding=True)
                        ),
                        {"ids": builtin_ids},
                    )
                ).all()
                for app_id, role_id in role_rows:
                    normalized = str(role_id or "").strip()
                    if normalized:
                        builtin_role_acl.setdefault(str(app_id), []).append(normalized)
                for app_id, dept_id in dept_rows:
                    normalized = str(dept_id or "").strip()
                    if normalized:
                        builtin_dept_acl.setdefault(str(app_id), []).append(normalized)

    user_roles = set(user.role_ids or []) | set(relation_roles) if user is not None else set()
    user_depts = set(user.dept_ids or []) | set(relation_depts) if user is not None else set()
    records: list[dict[str, Any]] = []
    for raw in rows:
        if str(raw.get("status") or "0") != "1":
            continue
        if str(raw.get("del_flag") or "0").strip().lower() not in {"", "0", "false", "none"}:
            continue
        catalog_id = str(raw.get("id") or "").strip()
        name = str(raw.get("app_name") or "").strip()
        pc_url = str(raw.get("pc_url") or "").strip()
        if not catalog_id or not name or not pc_url:
            continue
        form = _json(raw.get("form_options"), {})
        source_app_id = str(form.get("sourceAppId") or catalog_id).strip()
        builtin_preset = (
            builtin_preset_for_catalog_routes(raw.get("pc_url"), raw.get("h5_url"))
            if str(raw.get("app_type") or "").strip().lower() == CATALOG_APP_TYPE
            else None
        )
        if builtin_preset:
            # The two code-owned pages are ordinary administrator-created external applications.
            # Their app_role/app_dept relations are authoritative just like the management form;
            # no marker is written into form_options and no tenant copy is generated.
            role_ids = builtin_role_acl.get(catalog_id, [])
            dept_ids = builtin_dept_acl.get(catalog_id, [])
        else:
            role_ids = _list(form.get("visibleRoleIds"))
            dept_ids = _list(form.get("visibleDeptIds"))
        tenant_id = str(raw.get("tenant_id") or "0").strip() or "0"
        owner_user_id = str(raw.get("create_by") or "").strip()
        internal_workflow = pc_url.startswith("/agent/run/") or bool(form.get("sourceAppId"))
        registry = registry_by_id.get(source_app_id) or registry_by_id.get(catalog_id)
        # 内部工作流的 Registry 启用/健康态是“当前有效”的一部分；
        # 索引漂移时宁可暂时不推，不推一张点开后无法执行的卡。
        if internal_workflow and registry is None:
            continue
        if user is not None:
            if not _visibility_allows(
                user=user,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                role_ids=role_ids,
                dept_ids=dept_ids,
                user_roles=user_roles,
                user_depts=user_depts,
                internal_workflow=internal_workflow,
                global_catalog=bool(builtin_preset),
            ):
                continue
        routing = _json(getattr(registry, "routing_json", None), {}) if registry else {}
        description = str(raw.get("app_remark") or "").strip()
        route_description = str(routing.get("routeDescription") or "").strip()
        triggers = _list(routing.get("triggerExamples"))
        negatives = _list(routing.get("negativeExamples"))
        tags = _split_tags(routing.get("tags"))
        capability_names = _capability_names(
            getattr(registry, "capabilities_json", None) if registry else None
        )
        semantic_ready = bool(description or route_description or triggers or tags or capability_names)
        record = {
            "record_type": RECORD_TYPE,
            "catalog_id": catalog_id,
            "source_app_id": source_app_id or None,
            "owner_user_id": owner_user_id or None,
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "route_description": route_description,
            "trigger_examples": triggers,
            "negative_examples": negatives,
            "tags": tags,
            "capability_names": capability_names,
            "category": str(raw.get("app_category") or "").strip(),
            "icon": str(raw.get("app_icon") or "").strip(),
            "pc_url": pc_url,
            "app_type": str(raw.get("app_type") or "").strip(),
            "ai_app_type": str(raw.get("ai_app_type") or "").strip(),
            "terminal_type": str(form.get("terminalType") or "").strip(),
            "role_ids": role_ids,
            "dept_ids": dept_ids,
            "is_public": not internal_workflow and not role_ids and not dept_ids,
            "status": 1,
            "is_recommend": str(raw.get("is_recommend") or "0") in {"1", "true", "True"},
            "semantic_ready": semantic_ready,
            "source_version": _source_version(raw.get("update_time")),
            "index_schema_version": INDEX_SCHEMA_VERSION,
        }
        record["embedding_text"] = _embedding_text(record)
        hash_payload = {key: value for key, value in record.items() if key != "content_hash"}
        record["content_hash"] = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        records.append(record)
    records.sort(key=lambda item: item["catalog_id"])
    return records


def _snapshot_digest(records: Iterable[dict[str, Any]]) -> str:
    rows = [(item["catalog_id"], item["content_hash"]) for item in records]
    return hashlib.sha256(json.dumps(
        {"ranking_version": RANKING_VERSION, "records": rows},
        ensure_ascii=False,
    ).encode()).hexdigest()


async def _ensure_collection(name: str, dimension: int) -> None:
    client = _get_client()
    existing = {item.name for item in (await client.get_collections()).collections}
    if name not in existing:
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )


async def _replace_alias(collection: str) -> None:
    client = _get_client()
    operations: list[Any] = []
    try:
        aliases = (await client.get_aliases()).aliases
        if any(str(item.alias_name) == ALIAS_NAME for item in aliases):
            operations.append(models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=ALIAS_NAME)
            ))
    except Exception:  # noqa: BLE001
        logger.debug("Unable to inspect Qdrant aliases before cutover", exc_info=True)
    operations.append(models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=collection, alias_name=ALIAS_NAME)
    ))
    await client.update_collection_aliases(change_aliases_operations=operations)


async def _manifest(collection: str) -> Optional[dict[str, Any]]:
    try:
        points = await _get_client().retrieve(
            collection_name=collection,
            ids=[MANIFEST_POINT_ID],
            with_payload=True,
            with_vectors=False,
        )
        return dict(points[0].payload or {}) if points else None
    except Exception:  # noqa: BLE001
        return None


async def readiness(*, refresh: bool = False) -> Optional[dict[str, Any]]:
    global _ready_cache
    now = time.monotonic()
    if not refresh and now - _ready_cache[0] < _READY_CACHE_TTL_SECONDS:
        return dict(_ready_cache[1]) if _ready_cache[1] else None
    payload = await _manifest(ALIAS_NAME)
    config = await embedding_service.get_active_embedding_config() if payload else None
    ready = payload if (
        payload
        and payload.get("record_type") == "manifest"
        and payload.get("ready") is True
        and int(payload.get("index_schema_version") or 0) == INDEX_SCHEMA_VERSION
        and int(payload.get("ranking_version") or 0) == RANKING_VERSION
        and bool((payload.get("quality") or {}).get("passed"))
        and config is not None
        and str(payload.get("embedding_model") or "") == str(config.model or "")
        and int(payload.get("embedding_dimension") or 0) == int(config.dimension or 0)
    ) else None
    _ready_cache = (now, dict(ready) if ready else None)
    return dict(ready) if ready else None


async def _raw_vector_hits(
    collection: str,
    query: str,
    *,
    allowed_ids: Optional[list[str]] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    cfg = await embedding_service.get_active_embedding_config()
    if not cfg:
        return []
    vector = await embedding_service.embed_query(query, config=cfg)
    must: list[Any] = [
        models.FieldCondition(key="record_type", match=models.MatchValue(value=RECORD_TYPE)),
        models.FieldCondition(key="status", match=models.MatchValue(value=1)),
    ]
    if allowed_ids is not None:
        if not allowed_ids:
            return []
        must.append(models.FieldCondition(
            key="catalog_id", match=models.MatchAny(any=allowed_ids)
        ))
    rows = await _get_client().search(
        collection_name=collection,
        query_vector=vector,
        query_filter=models.Filter(must=must),
        limit=max(1, min(int(limit), 50)),
        with_payload=True,
    )
    return [
        {"id": str((row.payload or {}).get("catalog_id") or ""), "score": float(row.score)}
        for row in rows if row.payload and (row.payload or {}).get("catalog_id")
    ]


async def _calibrate(collection: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(item["name"]): item for item in records}
    positives: list[tuple[str, str, bool]] = []
    for query, expected_name in _PRODUCT_GOLDEN_QUERIES:
        if expected_name in by_name:
            positives.append((query, str(by_name[expected_name]["catalog_id"]), False))
    for item in records:
        item_queries = list((item.get("trigger_examples") or [])[:3])
        explicit_case = False
        if not item_queries and item.get("semantic_ready"):
            description = str(item.get("route_description") or item.get("description") or "").strip()
            tags = "、".join(item.get("tags") or [])
            item_queries.append(f"{item['name']} {description or tags}"[:180])
            explicit_case = True
        for query in item_queries:
            positives.append((str(query), str(item["catalog_id"]), explicit_case))
    positives = list(dict.fromkeys(positives))
    if not positives:
        return {"passed": False, "reason": "no_positive_golden_queries", "threshold": None}

    positive_hits: list[tuple[str, str, bool, list[dict[str, Any]]]] = []
    for query, expected_id, explicit_case in positives:
        hits = await _raw_vector_hits(collection, query, limit=20)
        positive_hits.append((query, expected_id, explicit_case, hits))
    negative_hits = [
        (query, await _raw_vector_hits(collection, query, limit=20))
        for query in _GENERIC_NEGATIVE_QUERIES
    ]

    explicit_name_cases: list[tuple[str, str, list[dict[str, Any]]]] = []
    for item in records:
        query = f"我想用{item['name']}"
        explicit_name_cases.append((
            query,
            str(item["catalog_id"]),
            await _raw_vector_hits(collection, query, limit=20),
        ))

    selected: Optional[float] = None
    metrics: dict[str, float] = {}
    for step in range(45, 86):
        threshold = step / 100
        explicit_name_hits = 0
        for query, expected_id, hits in explicit_name_cases:
            ranked = _rank_candidates(query, records, hits, threshold=threshold)
            choices = _explicit_choices(ranked)
            explicit_name_hits += any(
                str(item["catalog_id"]) == expected_id for item in choices[:3]
            )
        explicit_recall_at_3 = (
            explicit_name_hits / len(explicit_name_cases) if explicit_name_cases else 1.0
        )
        implicit_cases = [item for item in positive_hits if not item[2]]
        implicit_displayed = 0
        implicit_correct = 0
        for query, expected_id, _explicit_case, hits in implicit_cases:
            ranked = _rank_candidates(query, records, hits, threshold=threshold)
            chosen, _reason_code = _implicit_choice(ranked)
            if chosen is not None:
                implicit_displayed += 1
                implicit_correct += str(chosen["catalog_id"]) == expected_id
        precision = implicit_correct / implicit_displayed if implicit_displayed else 1.0
        recall = implicit_correct / len(implicit_cases) if implicit_cases else 1.0

        # 评测最终的“是否展示”，不用单一 cosine 代替服务端的
        # 通用办公拦截+多信号+分差拒绝；否则门禁与真实产品逻辑不同口径。
        negative_displayed = 0
        for query, hits in negative_hits:
            if len(query) <= 80 and _GENERIC_OFFICE_RE.search(query):
                continue
            ranked = _rank_candidates(query, records, hits, threshold=threshold)
            chosen, _reason_code = _implicit_choice(ranked)
            negative_displayed += chosen is not None
        false_rate = negative_displayed / len(negative_hits) if negative_hits else 0.0
        if (
            precision >= 0.90
            and recall >= 0.80
            and false_rate <= 0.02
            and explicit_recall_at_3 >= 0.80
        ):
            selected = threshold
            metrics = {
                "precision_at_1": precision,
                "recall_at_1": recall,
                "explicit_recall_at_3": explicit_recall_at_3,
                "negative_false_rate": false_rate,
            }
            break
    if selected is None:
        return {
            "passed": False,
            "reason": "golden_metrics_below_gate",
            "threshold": None,
            "positive_cases": len(positive_hits),
        }
    return {
        "passed": True,
        # 阈值由当前 embedding + schema 的黄金集分布决定；再套一个
        # 静态下限会让“已通过”的 Recall 在真实查询时反而失效。
        "threshold": selected,
        "positive_cases": len(positive_hits),
        "negative_cases": len(negative_hits),
        **metrics,
    }


async def rebuild_index() -> dict[str, Any]:
    """Build a complete physical snapshot, quality-gate it, then atomically move the alias."""
    if not settings.AGENT_RECOMMEND_ENABLED:
        return {"status": "disabled"}
    async with async_session() as lock_session:
        acquired = int((await lock_session.scalar(
            text("SELECT GET_LOCK(:name, 0)"), {"name": _LOCK_NAME}
        )) or 0)
        if acquired != 1:
            return {"status": "locked"}
        try:
            cfg = await embedding_service.get_active_embedding_config()
            if not cfg or not cfg.dimension:
                return {"status": "not_ready", "reason": "embedding_unavailable"}
            records = await fetch_catalog_records()
            if not records:
                return {"status": "not_ready", "reason": "empty_catalog"}
            digest = _snapshot_digest(records)
            collection = _physical_collection(cfg.model, cfg.dimension, digest)
            await _ensure_collection(collection, cfg.dimension)
            existing_manifest = await _manifest(collection)
            if existing_manifest and existing_manifest.get("digest") == digest and existing_manifest.get("ready") is True:
                await _replace_alias(collection)
                await readiness(refresh=True)
                return {"status": "ready", "collection": collection, "indexed": len(records), "reused": True}

            vectors = await embedding_service.embed_texts(
                [str(item["embedding_text"]) for item in records],
                config=cfg,
                return_exceptions=True,
            )
            points: list[Any] = []
            errors: list[str] = []
            for record, vector in zip(records, vectors):
                if isinstance(vector, Exception) or len(vector) != cfg.dimension:
                    errors.append(str(record["catalog_id"]))
                    continue
                payload = {key: value for key, value in record.items() if key != "embedding_text"}
                points.append(models.PointStruct(
                    id=_point_id(record["catalog_id"]), vector=vector, payload=payload
                ))
            if errors or len(points) != len(records):
                return {"status": "not_ready", "reason": "embedding_failed", "failed_ids": errors[:20]}
            for index in range(0, len(points), 100):
                await _get_client().upsert(collection_name=collection, points=points[index:index + 100])

            calibration = await _calibrate(collection, records)
            if not calibration.get("passed"):
                return {"status": "not_ready", "reason": calibration.get("reason"), "quality": calibration}
            manifest_payload = {
                "record_type": "manifest",
                "ready": True,
                "digest": digest,
                "collection": collection,
                "embedding_model": cfg.model,
                "embedding_dimension": cfg.dimension,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "ranking_version": RANKING_VERSION,
                "threshold": float(calibration["threshold"]),
                "record_count": len(records),
                "quality": calibration,
                "built_at": int(time.time()),
            }
            await _get_client().upsert(
                collection_name=collection,
                points=[models.PointStruct(
                    id=MANIFEST_POINT_ID,
                    vector=[0.0] * cfg.dimension,
                    payload=manifest_payload,
                )],
            )
            count = int((await _get_client().count(collection_name=collection, exact=True)).count)
            if count != len(records) + 1:
                return {"status": "not_ready", "reason": "count_mismatch", "count": count}
            await _replace_alias(collection)
            await readiness(refresh=True)
            return {
                "status": "ready", "collection": collection, "indexed": len(records),
                "quality": calibration,
            }
        finally:
            try:
                await lock_session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK_NAME})
            except Exception:  # noqa: BLE001
                logger.warning("Failed to release recommendation rebuild lock", exc_info=True)


async def run_sync_loop() -> None:
    interval = max(60, int(settings.AGENT_RECOMMEND_SYNC_INTERVAL or 300))
    while True:
        await asyncio.sleep(interval)
        try:
            result = await rebuild_index()
            if result.get("status") not in {"ready", "locked", "disabled"}:
                logger.warning("Recommendation index reconcile did not become ready: %s", result)
            elif result.get("status") == "ready":
                await cleanup_old_collections()
        except Exception:  # noqa: BLE001
            logger.warning("Recommendation index reconcile failed", exc_info=True)


def request_rebuild(*, delay_seconds: float = 0.5) -> None:
    """Coalesce publish/delete/ACL/route changes into a blue-green snapshot reconcile."""
    global _requested_rebuild, _rebuild_generation
    _rebuild_generation += 1
    if _requested_rebuild is not None and not _requested_rebuild.done():
        return

    async def _run() -> None:
        await asyncio.sleep(max(0.0, float(delay_seconds)))
        while True:
            observed_generation = _rebuild_generation
            try:
                result = await rebuild_index()
                if result.get("status") not in {"ready", "locked", "disabled"}:
                    logger.warning("Requested recommendation reconcile not ready: %s", result)
                elif result.get("status") == "ready":
                    await cleanup_old_collections()
            except Exception:  # noqa: BLE001
                logger.warning("Requested recommendation reconcile failed", exc_info=True)
            if observed_generation == _rebuild_generation:
                break

    try:
        _requested_rebuild = asyncio.create_task(_run())
    except RuntimeError:
        # Import-time / migration callers may not own an event loop; periodic reconcile remains.
        _requested_rebuild = None


async def cleanup_old_collections() -> int:
    """Delete only expired, formerly-ready recommendation snapshots; route/v4 indexes are untouchable."""
    retention = max(3600, int(settings.AGENT_RECOMMEND_RETENTION_SECONDS or 86400))
    active = await _manifest(ALIAS_NAME)
    active_name = str((active or {}).get("collection") or "")
    prefix = f"agent_recommend_v{INDEX_SCHEMA_VERSION}_"
    deleted = 0
    try:
        collections = (await _get_client().get_collections()).collections
        for item in collections:
            name = str(item.name or "")
            if not name.startswith(prefix) or name == active_name:
                continue
            manifest = await _manifest(name)
            built_at = int((manifest or {}).get("built_at") or 0)
            if not manifest or manifest.get("ready") is not True or not built_at:
                continue
            if int(time.time()) - built_at < retention:
                continue
            await _get_client().delete_collection(collection_name=name)
            deleted += 1
    except Exception:  # noqa: BLE001
        logger.warning("Recommendation snapshot cleanup failed", exc_info=True)
    return deleted


def detect_explicit_request(message: str) -> bool:
    text_value = str(message or "").strip().lower()
    return bool(_EXPLICIT_DISCOVERY_RE.search(text_value))


def _recall_groups(
    query: str,
    records: list[dict[str, Any]],
) -> tuple[list[tuple[float, dict[str, Any], set[str], bool]], dict[str, int], dict[str, int]]:
    """Return independent metadata/trigger Top-20 ranks plus reusable lexical features."""
    feature_rows: list[tuple[float, dict[str, Any], set[str], bool]] = []
    metadata_rows: list[tuple[float, str]] = []
    trigger_rows: list[tuple[float, str]] = []
    for record in records:
        score, signals, veto = _lexical_features(query, record)
        feature_rows.append((score, record, signals, veto))
        metadata_score = 0.0
        if "exact_name" in signals:
            metadata_score += 200.0
        if "tag" in signals:
            metadata_score += 25.0
        if "category" in signals:
            metadata_score += 12.0
        if "description" in signals:
            metadata_score += 10.0
        if metadata_score > 0:
            metadata_rows.append((metadata_score, str(record["catalog_id"])))
        if "trigger" in signals:
            trigger_score = 100.0 if any(
                str(item or "").strip().lower() in query.lower()
                or query.lower() in str(item or "").strip().lower()
                for item in (record.get("trigger_examples") or [])
                if str(item or "").strip()
            ) else 25.0
            trigger_rows.append((trigger_score, str(record["catalog_id"])))
    metadata_rows.sort(key=lambda item: (-item[0], item[1]))
    trigger_rows.sort(key=lambda item: (-item[0], item[1]))
    return (
        feature_rows,
        {catalog_id: rank for rank, (_, catalog_id) in enumerate(metadata_rows[:20])},
        {catalog_id: rank for rank, (_, catalog_id) in enumerate(trigger_rows[:20])},
    )


def _rank_candidates(
    query: str,
    records: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    """Fuse three independent ranks and classify confidence without mixing raw source scores."""
    vector_rank = {item["id"]: index for index, item in enumerate(hits[:20])}
    vector_scores = {item["id"]: float(item["score"]) for item in hits[:20]}
    lexical_rows, metadata_rank, trigger_rank = _recall_groups(query, records)
    ranked: list[dict[str, Any]] = []
    for lexical_score, record, signals, veto in lexical_rows:
        catalog_id = str(record["catalog_id"])
        if catalog_id not in vector_rank and catalog_id not in metadata_rank and catalog_id not in trigger_rank:
            continue
        exact = "exact_name" in signals
        if veto or (not record.get("semantic_ready") and not exact):
            continue
        vector_score = vector_scores.get(catalog_id, 0.0)
        rrf = 0.0
        if catalog_id in vector_rank:
            rrf += 1.0 / (60 + vector_rank[catalog_id] + 1)
        if catalog_id in metadata_rank:
            rrf += 1.0 / (60 + metadata_rank[catalog_id] + 1)
        if catalog_id in trigger_rank:
            rrf += 1.0 / (60 + trigger_rank[catalog_id] + 1)
        if exact:
            rrf += 1.0
        if record.get("is_recommend"):
            rrf += 0.0001
        corroborating = signals & {"trigger", "tag", "description", "category"}
        if exact:
            confidence = "exact"
        elif vector_score >= threshold and corroborating:
            confidence = "strong"
        elif vector_score >= threshold or lexical_score >= 25:
            confidence = "medium"
        else:
            confidence = "weak"
        ranked.append({
            **record,
            "vector_score": vector_score,
            "rank_score": rrf,
            "confidence": confidence,
            "matched_signals": sorted(
                (signals - {"negative_penalty"})
                | ({"vector"} if vector_score >= threshold else set())
            ),
            "reason": _reason(record, signals),
        })
    ranked.sort(key=lambda item: (-float(item["rank_score"]), -float(item["vector_score"]), item["name"]))
    return ranked


def _implicit_choice(ranked: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], str]:
    qualified = [item for item in ranked if item["confidence"] == "strong"]
    if not qualified:
        return None, "confidence_too_low"
    top = qualified[0]
    if len(qualified) > 1:
        rank_margin = float(top["rank_score"]) - float(qualified[1]["rank_score"])
        vector_margin = float(top["vector_score"]) - float(qualified[1]["vector_score"])
        if (
            rank_margin < float(settings.AGENT_RECOMMEND_IMPLICIT_MARGIN)
            and vector_margin < float(settings.AGENT_RECOMMEND_IMPLICIT_VECTOR_MARGIN)
        ):
            return None, "ambiguous_top_candidates"
    return top, ""


def _explicit_choices(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return relevant choices without filling the three-card allowance with weak neighbours."""
    qualified = [
        item for item in ranked
        if item["confidence"] in {"exact", "strong", "medium"}
    ]
    exact = [item for item in qualified if item["confidence"] == "exact"]
    if exact:
        return exact[:3]
    if not qualified:
        return []

    top = qualified[0]
    # medium 只有单一语义信号：明确查找时可以给第一名，但不用它凑满三张卡片。
    if top["confidence"] == "medium":
        return [top]

    chosen = [top]
    top_vector = float(top.get("vector_score") or 0.0)
    for item in qualified[1:]:
        if len(chosen) >= 3:
            break
        if item["confidence"] != "strong":
            continue
        # 后续选项必须与第一名接近；“最多三个”不等于必须给满。
        if top_vector - float(item.get("vector_score") or 0.0) > 0.10:
            continue
        chosen.append(item)
    return chosen


def _lexical_features(query: str, record: dict[str, Any]) -> tuple[float, set[str], bool]:
    lowered = query.lower()
    tokens = _query_tokens(lowered)
    score = 0.0
    signals: set[str] = set()
    name = str(record.get("name") or "").lower()
    if name and (name == lowered or name in lowered):
        score += 200.0
        signals.add("exact_name")
    for trigger in record.get("trigger_examples") or []:
        candidate = str(trigger or "").strip().lower()
        if candidate and (candidate in lowered or lowered in candidate):
            score += 80.0
            signals.add("trigger")
        elif candidate and sum(token in candidate for token in tokens) >= 2:
            score += 18.0
            signals.add("trigger")
    for tag in record.get("tags") or []:
        candidate = str(tag or "").strip().lower()
        if candidate and candidate in lowered:
            score += 25.0
            signals.add("tag")
    category = str(record.get("category") or "").lower()
    if category and category in lowered:
        score += 12.0
        signals.add("category")
    description = " ".join((
        str(record.get("route_description") or ""),
        str(record.get("description") or ""),
        " ".join(record.get("capability_names") or []),
    )).lower()
    description_hits = sum(token in description for token in tokens) if description else 0
    if description_hits:
        score += min(description_hits, 8) * 3.0
        signals.add("description")
    veto = False
    partial_negative_hits = 0
    for negative in record.get("negative_examples") or []:
        candidate = str(negative or "").strip().lower()
        if not candidate:
            continue
        if candidate in lowered or lowered in candidate:
            veto = True
            break
        if any(token in candidate for token in tokens):
            partial_negative_hits += 1
    if partial_negative_hits:
        score -= 30.0 * partial_negative_hits
        signals.add("negative_penalty")
    return score, signals, veto


def _reason(record: dict[str, Any], signals: set[str]) -> str:
    if "trigger" in signals and record.get("trigger_examples"):
        return f"适合{str(record['trigger_examples'][0])[:48]}"
    if "tag" in signals and record.get("tags"):
        return f"专注于{'、'.join(record['tags'][:3])}"
    description = str(record.get("route_description") or record.get("description") or "").strip()
    return description[:72] if description else "与当前任务匹配的专业智能体"


@dataclass(frozen=True)
class RecommendationResult:
    status: str
    intent: str
    recommendations: list[dict[str, Any]]
    confidence: str = "weak"
    matched_signals: tuple[str, ...] = ()
    suppressed_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "intent": self.intent,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
            "matched_signals": list(self.matched_signals),
            "suppressed_reason": self.suppressed_reason,
        }


async def recommend(
    *,
    raw_message: str,
    task: str,
    requested_intent: str,
    recent_user_messages: list[str],
    attachment_types: list[str],
    user: UserContext,
    excluded_ids: Iterable[str] = (),
) -> RecommendationResult:
    manifest = await readiness()
    if not manifest:
        return RecommendationResult("suppressed", "capability_gap", [], suppressed_reason="index_not_ready")
    explicit = detect_explicit_request(raw_message)
    intent = "explicit_request" if explicit else "capability_gap"
    if requested_intent == "explicit_request" and not explicit:
        intent = "capability_gap"
    if (
        intent == "capability_gap"
        and not attachment_types
        and len(str(raw_message or "").strip()) <= 80
        and _GENERIC_OFFICE_RE.search(str(raw_message or "").strip())
    ):
        return RecommendationResult("suppressed", intent, [], suppressed_reason="main_agent_can_handle")

    context_parts = [str(raw_message or "").strip()]
    context_parts.extend(str(item or "").strip()[:160] for item in recent_user_messages[-2:])
    if attachment_types:
        context_parts.append("附件类型：" + "、".join(dict.fromkeys(attachment_types)))
    if task and task.strip() not in context_parts[0]:
        context_parts.append("任务摘要：" + task.strip()[:240])
    query = "\n".join(item for item in context_parts if item)[:800]
    records = await fetch_catalog_records(user)
    excluded = {str(item) for item in excluded_ids}
    records = [item for item in records if item["catalog_id"] not in excluded]
    if not records:
        return RecommendationResult("suppressed", intent, [], suppressed_reason="no_visible_candidates")
    # 用户直接说出当前可见智能体的完整名称也属于 exact；名称只能
    # 来自数据库回源，不接受模型传入候选 id 或自报名称。
    current_text = str(raw_message or "").strip().lower()
    if any(str(item.get("name") or "").strip().lower() in current_text for item in records):
        intent = "explicit_request"

    vector_task = asyncio.create_task(_raw_vector_hits(
        ALIAS_NAME, query, allowed_ids=[str(item["catalog_id"]) for item in records], limit=20
    ))
    hits = await vector_task
    threshold = float(manifest.get("threshold") or settings.AGENT_RECOMMEND_DEFAULT_MIN_SCORE)
    ranked = _rank_candidates(query, records, hits, threshold=threshold)

    if intent == "capability_gap":
        top, suppressed_reason = _implicit_choice(ranked)
        if top is None:
            return RecommendationResult("suppressed", intent, [], suppressed_reason=suppressed_reason)
        chosen = [top]
    else:
        chosen = _explicit_choices(ranked)
        if not chosen:
            return RecommendationResult("suppressed", intent, [], suppressed_reason="confidence_too_low")

    recommendations = [
        {
            "id": item["catalog_id"],
            "name": item["name"],
            "reason": item["reason"],
            "confidence": item["confidence"],
            "matched_signals": item["matched_signals"],
        }
        for item in chosen
    ]
    confidence = str(chosen[0]["confidence"])
    all_signals = tuple(dict.fromkeys(
        signal for item in chosen for signal in item["matched_signals"]
    ))
    return RecommendationResult("matched", intent, recommendations, confidence, all_signals)
