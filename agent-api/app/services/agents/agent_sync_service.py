import asyncio
import hashlib
import logging
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.database import async_session
from app.models import AgentIndexEvent
from app.schemas.schemas import AgentEvent, AgentSyncAgent
from app.services.knowledge import embedding_service, vector_service

logger = logging.getLogger(__name__)


async def _get_embedding_config() -> Optional[tuple[str, int, str]]:
    config = await embedding_service.get_active_embedding_config()
    if not config or not config.dimension:
        return None
    return (
        config.model,
        config.dimension,
        vector_service.collection_name(config.model, config.dimension),
    )


def _content_hash(name: str, description: str) -> str:
    return hashlib.sha1(f"{name}\n{description}".encode()).hexdigest()


def _agent_payload(agent: AgentSyncAgent, content_hash: str, source_version: int) -> dict:
    return {
        "agent_id": agent.id,
        # 租户维度（深扫 P0）：空/缺省归一为全局占位 '0'（对所有租户可见）
        "tenant_id": str(agent.tenant_id or "0").strip() or "0",
        "owner_user_id": agent.owner_user_id,
        "name": agent.name,
        "description": agent.description or "",
        "icon": agent.icon or "",
        "category": agent.category or "",
        "status": agent.status,
        "published": agent.published,
        "role_ids": agent.role_ids,
        "dept_ids": agent.dept_ids,
        "is_public": agent.is_public,
        "content_hash": content_hash,
        "source_version": source_version,
    }


async def _latest_event_version(session, agent_id: str) -> int:
    value = await session.scalar(
        select(func.max(AgentIndexEvent.source_version)).where(
            AgentIndexEvent.agent_id == agent_id,
        )
    )
    return int(value or -1)


def _record_event(session, event: AgentEvent, agent: AgentSyncAgent) -> None:
    session.add(AgentIndexEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        agent_id=agent.id,
        source_version=event.source_version,
    ))


async def process_event(event: AgentEvent) -> dict:
    config = await _get_embedding_config()
    if not config:
        raise HTTPException(status_code=503, detail="无活跃的 Embedding 配置")
    model_id, dimension, collection = config

    agent = event.agent
    if not agent:
        raise HTTPException(status_code=422, detail="缺少 agent 信息")
    if event.event_type not in {"upsert", "delete", "disable", "unpublish"}:
        raise HTTPException(status_code=422, detail="不支持的事件类型")

    await vector_service.ensure_collection(collection, dimension)

    async with async_session() as session:
        if await session.get(AgentIndexEvent, event.event_id):
            return {"status": "skipped", "message": "事件已处理"}

        latest_version = await _latest_event_version(session, agent.id)
        if event.source_version <= latest_version:
            _record_event(session, event, agent)
            await session.commit()
            return {"status": "skipped", "message": "旧版本事件已忽略"}

        existing = await vector_service.get_agent_metadata(collection, agent.id)
        existing_version = int((existing or {}).get("source_version", -1))
        if event.source_version <= existing_version:
            _record_event(session, event, agent)
            await session.commit()
            return {"status": "skipped", "message": "旧版本事件已忽略"}

        if event.event_type in {"delete", "disable", "unpublish"} or agent.status != 1 or not agent.published:
            await vector_service.delete_agent(collection, agent.id)
            _record_event(session, event, agent)
            await session.commit()
            return {"status": "ok", "action": "deleted"}

        content_hash = _content_hash(agent.name, agent.description or "")
        payload = _agent_payload(agent, content_hash, event.source_version)
        if existing and existing.get("content_hash") == content_hash:
            await vector_service.update_agent_payload(collection, payload)
            action = "metadata_updated"
        else:
            embed_config = await embedding_service.get_active_embedding_config()
            if not embed_config or not embed_config.dimension:
                raise HTTPException(status_code=503, detail="未配置平台 Embedding 模型")
            vector = await embedding_service.embed_query(
                f"{agent.name}\n{agent.description or ''}",
                config=embed_config,
            )
            if len(vector) != dimension:
                raise HTTPException(
                    status_code=502,
                    detail=f"向量维度不匹配: 期望 {dimension}, 实际 {len(vector)}",
                )
            payload["vector"] = vector
            await vector_service.upsert_agents(collection, [payload])
            action = "indexed"

        _record_event(session, event, agent)
        await session.commit()
        return {"status": "ok", "action": action}


async def process_bulk_sync(agents: List[AgentSyncAgent]) -> dict:
    config = await _get_embedding_config()
    if not config:
        raise HTTPException(status_code=503, detail="无活跃的 Embedding 配置")
    model_id, dimension, collection = config
    await vector_service.ensure_collection(collection, dimension)

    metadata = await asyncio.gather(*[
        vector_service.get_agent_metadata(collection, agent.id)
        for agent in agents
    ])

    indexed = 0
    skipped = 0
    deleted = 0
    failed = 0
    errors: List[str] = []
    changed_items: list[tuple[AgentSyncAgent, dict]] = []
    upserts = []

    for agent, existing in zip(agents, metadata):
        incoming_version = int(agent.source_version or 0)
        existing_version = int((existing or {}).get("source_version", -1))
        if incoming_version < existing_version:
            skipped += 1
            continue
        if agent.status != 1 or not agent.published:
            await vector_service.delete_agent(collection, agent.id)
            deleted += 1
            continue

        content_hash = _content_hash(agent.name, agent.description or "")
        payload = _agent_payload(agent, content_hash, incoming_version)
        if existing and existing.get("content_hash") == content_hash:
            await vector_service.update_agent_payload(collection, payload)
            skipped += 1
            continue
        changed_items.append((agent, payload))

    # 使用平台 embedding 配置
    embed_config = await embedding_service.get_active_embedding_config()
    if not embed_config or not embed_config.dimension:
        failed += len(changed_items)
        errors.extend(f"{agent.id}: 未配置平台 Embedding 模型" for agent, _ in changed_items)
    else:
        texts = [f"{agent.name}\n{agent.description or ''}" for agent, _ in changed_items]
        vectors = await embedding_service.embed_texts(
            texts,
            config=embed_config,
            return_exceptions=True,
        )

        for ((agent, payload), vector) in zip(changed_items, vectors):
            if isinstance(vector, Exception):
                failed += 1
                errors.append(f"{agent.id}: {vector}")
                continue
            if len(vector) != dimension:
                failed += 1
                errors.append(f"{agent.id}: 维度不匹配 {len(vector)} != {dimension}")
                continue
            payload["vector"] = vector
            upserts.append(payload)

    if upserts:
        await vector_service.upsert_agents(collection, upserts)
        indexed = len(upserts)

    return {
        "status": "partial" if failed else "ok",
        "indexed": indexed,
        "skipped": skipped,
        "deleted": deleted,
        "failed": failed,
        "errors": errors[:10],
    }
