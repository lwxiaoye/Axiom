"""Resolve the published campus snapshot for a tenant Run."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import CampusAssistantConfig, CampusAssistantRelease, CampusAssistantReleaseKb
from .policy import CAMPUS_POLICY_VERSION
from .config_service import (
    CampusConfigError,
    STATUS_PUBLISHED,
    _loads_list,
)


def snapshot_from_release(release: CampusAssistantRelease, knowledge_ids: list[str], domains: list[dict]) -> dict:
    return {
        "code": "campus_services",
        "release_id": release.id,
        "version_no": release.version_no,
        "policy_version": release.policy_version or CAMPUS_POLICY_VERSION,
        "knowledge_ids": list(knowledge_ids),
        "official_domains": [dict(item) for item in domains],
        "model_id": release.model_id,
        "config_hash": release.config_hash,
    }


async def resolve_published_snapshot(tenant_id: str) -> dict:
    tenant_id = str(tenant_id or "0")
    async with async_session() as session:
        config = (await session.execute(
            select(CampusAssistantConfig).where(CampusAssistantConfig.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if config is None or not config.enabled:
            raise CampusConfigError(503, "校园百事通尚未启用或没有已发布配置")
        if not config.current_release_id:
            raise CampusConfigError(503, "校园百事通尚未发布配置")
        release = await session.get(CampusAssistantRelease, config.current_release_id)
        if release is None or release.status != STATUS_PUBLISHED:
            raise CampusConfigError(503, "校园百事通当前发布版本不可用")
        rows = (await session.execute(
            select(CampusAssistantReleaseKb)
            .where(CampusAssistantReleaseKb.release_id == release.id)
            .where(CampusAssistantReleaseKb.enabled == 1)
            .order_by(CampusAssistantReleaseKb.priority.asc(), CampusAssistantReleaseKb.knowledge_id.asc())
        )).scalars().all()
        knowledge_ids = [row.knowledge_id for row in rows if row.knowledge_id]
        domains = _loads_list(release.official_domains_json)
        if not knowledge_ids:
            raise CampusConfigError(503, "校园百事通尚未绑定知识库")
        return snapshot_from_release(release, knowledge_ids, domains)


def snapshot_from_state(value: object) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    if str(value.get("code") or "") != "campus_services":
        return None
    if not value.get("release_id") or not value.get("knowledge_ids"):
        return None
    return value
