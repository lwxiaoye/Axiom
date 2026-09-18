"""Draft / validate / publish / rollback for the campus assistant."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models import CampusAssistantConfig, CampusAssistantRelease, CampusAssistantReleaseKb, ChatModel
from . import knowledge_access
from .domain_policy import (
    DomainPolicyError,
    normalize_official_domains,
)
from app.services.campus_assistant.main_chat_skin_service import (
    MainChatSkinError,
    validate_skin_selection,
)
from .policy import CAMPUS_POLICY_VERSION

logger = logging.getLogger(__name__)

STATUS_DRAFT = "DRAFT"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_ABANDONED = "ABANDONED"
RETRIEVAL_PERMISSIONS = frozenset({"VIEWER", "EDITOR", "OWNER"})
DOC_WARNING_STATUSES = frozenset({"PARSING", "EMBEDDING", "FAILED", "SPLITTING", "QA_GENERATING"})


class CampusConfigError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _tenant_id(user) -> str:
    return str(getattr(user, "tenant_id", None) or "0")


def _user_id(user) -> str:
    return str(getattr(user, "user_id", None) or "")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    return data if isinstance(data, list) else []


def compute_config_hash(
    *,
    model_id: str,
    official_domains: list[dict],
    knowledge_bindings: list[dict],
    policy_version: str,
    main_chat_skin_id: Optional[str] = None,
) -> str:
    payload = {
        "model_id": str(model_id or "").strip(),
        "main_chat_skin_id": str(main_chat_skin_id or "").strip(),
        "policy_version": str(policy_version or "").strip(),
        "official_domains": [
            {
                "host": str(item.get("host") or ""),
                "include_subdomains": bool(item.get("include_subdomains")),
            }
            for item in official_domains
        ],
        "knowledge_bindings": sorted(
            [
                {
                    "knowledge_id": str(item.get("knowledge_id") or ""),
                    "category": str(item.get("category") or ""),
                    "department": str(item.get("department") or ""),
                    "priority": int(item.get("priority") or 100),
                    "enabled": bool(item.get("enabled", True)),
                }
                for item in knowledge_bindings
            ],
            key=lambda row: row["knowledge_id"],
        ),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _kb_rows(release_id: str, bindings: list[dict]) -> list[CampusAssistantReleaseKb]:
    rows = []
    for item in bindings:
        kid = str(item.get("knowledge_id") or "").strip()
        if not kid:
            continue
        rows.append(CampusAssistantReleaseKb(
            id=_new_id("ckb"),
            release_id=release_id,
            knowledge_id=kid,
            knowledge_name_snapshot=str(item.get("knowledge_name_snapshot") or "")[:255],
            category=str(item.get("category") or "")[:64],
            department=str(item.get("department") or "")[:128],
            priority=int(item.get("priority") or 100),
            enabled=1 if item.get("enabled", True) else 0,
        ))
    return rows


async def _load_bindings(session: AsyncSession, release_id: str) -> list[dict]:
    rows = (await session.execute(
        select(CampusAssistantReleaseKb)
        .where(CampusAssistantReleaseKb.release_id == release_id)
        .order_by(CampusAssistantReleaseKb.priority.asc(), CampusAssistantReleaseKb.knowledge_id.asc())
    )).scalars().all()
    return [
        {
            "knowledge_id": row.knowledge_id,
            "knowledge_name_snapshot": row.knowledge_name_snapshot or "",
            "category": row.category or "",
            "department": row.department or "",
            "priority": int(row.priority or 100),
            "enabled": bool(row.enabled),
        }
        for row in rows
    ]


def serialize_release(release: Optional[CampusAssistantRelease], bindings: Optional[list] = None) -> Optional[dict]:
    if release is None:
        return None
    return {
        "id": release.id,
        "status": release.status,
        "version_no": release.version_no,
        "base_release_id": release.base_release_id,
        "rollback_from_release_id": release.rollback_from_release_id,
        "model_id": release.model_id,
        "main_chat_skin_id": release.main_chat_skin_id,
        "official_domains": _loads_list(release.official_domains_json),
        "policy_version": release.policy_version,
        "change_note": release.change_note or "",
        "config_hash": release.config_hash,
        "created_by": release.created_by,
        "published_by": release.published_by,
        "created_at": release.created_at.isoformat() if release.created_at else None,
        "published_at": release.published_at.isoformat() if release.published_at else None,
        "knowledge_bindings": bindings or [],
    }


async def _get_config(session: AsyncSession, tenant_id: str) -> Optional[CampusAssistantConfig]:
    return (await session.execute(
        select(CampusAssistantConfig).where(CampusAssistantConfig.tenant_id == tenant_id)
    )).scalar_one_or_none()


async def _empty_draft(session: AsyncSession, config: CampusAssistantConfig, user) -> CampusAssistantRelease:
    draft = CampusAssistantRelease(
        id=_new_id("crel"),
        config_id=config.id,
        status=STATUS_DRAFT,
        version_no=None,
        model_id="",
        main_chat_skin_id=None,
        official_domains_json="[]",
        policy_version=CAMPUS_POLICY_VERSION,
        change_note="",
        created_by=_user_id(user),
    )
    session.add(draft)
    config.draft_release_id = draft.id
    config.updated_by = _user_id(user)
    await session.flush()
    return draft


async def _clone_release(
    session: AsyncSession,
    config: CampusAssistantConfig,
    source: CampusAssistantRelease,
    user,
    *,
    rollback_from: Optional[str] = None,
) -> CampusAssistantRelease:
    bindings = await _load_bindings(session, source.id)
    draft = CampusAssistantRelease(
        id=_new_id("crel"),
        config_id=config.id,
        status=STATUS_DRAFT,
        version_no=None,
        base_release_id=source.id,
        rollback_from_release_id=rollback_from,
        model_id=source.model_id,
        main_chat_skin_id=source.main_chat_skin_id,
        official_domains_json=source.official_domains_json,
        policy_version=source.policy_version or CAMPUS_POLICY_VERSION,
        change_note="",
        created_by=_user_id(user),
    )
    session.add(draft)
    await session.flush()
    for row in _kb_rows(draft.id, bindings):
        session.add(row)
    config.draft_release_id = draft.id
    config.updated_by = _user_id(user)
    await session.flush()
    return draft


async def get_or_create_config(user) -> dict:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        config = await _get_config(session, tenant_id)
        created = False
        if config is None:
            config = CampusAssistantConfig(
                id=_new_id("ccfg"),
                tenant_id=tenant_id,
                revision=0,
                enabled=1,
                created_by=_user_id(user),
                updated_by=_user_id(user),
            )
            session.add(config)
            await session.flush()
            await _empty_draft(session, config, user)
            created = True
        elif not config.draft_release_id:
            current = await session.get(CampusAssistantRelease, config.current_release_id) if config.current_release_id else None
            if current:
                await _clone_release(session, config, current, user)
            else:
                await _empty_draft(session, config, user)
            created = True
        await session.commit()
        return await _serialize_config(session, config, created=created)


async def ensure_draft(user) -> dict:
    return await get_or_create_config(user)


async def _list_enabled_models(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        select(ChatModel)
        .where(ChatModel.enabled == 1)
        .order_by(ChatModel.is_default.desc(), ChatModel.sort_order.asc(), ChatModel.id.asc())
    )).scalars().all()
    return [
        {
            "id": str(row.model_id or "").strip(),
            "name": str(row.display_name or row.model_id or "").strip(),
            "is_default": bool(row.is_default),
        }
        for row in rows
        if str(row.model_id or "").strip()
    ]


async def _serialize_config(session: AsyncSession, config: CampusAssistantConfig, created: bool = False) -> dict:
    current = await session.get(CampusAssistantRelease, config.current_release_id) if config.current_release_id else None
    draft = await session.get(CampusAssistantRelease, config.draft_release_id) if config.draft_release_id else None
    current_bindings = await _load_bindings(session, current.id) if current else []
    draft_bindings = await _load_bindings(session, draft.id) if draft else []
    return {
        "id": config.id,
        "tenant_id": config.tenant_id,
        "revision": int(config.revision or 0),
        "enabled": bool(config.enabled),
        "created": created,
        "current_release": serialize_release(current, current_bindings),
        "draft": serialize_release(draft, draft_bindings),
        "available_models": await _list_enabled_models(session),
    }


def _require_revision(config: CampusAssistantConfig, expected: int) -> None:
    if int(config.revision or 0) != int(expected):
        raise CampusConfigError(409, "配置已被他人更新，请刷新后重试")


async def save_draft(user, body) -> dict:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        config = await _get_config(session, tenant_id)
        if config is None:
            raise CampusConfigError(404, "尚未创建校园百事通配置")
        _require_revision(config, body.expected_revision)
        if not config.draft_release_id:
            raise CampusConfigError(409, "没有可编辑草稿")
        draft = await session.get(CampusAssistantRelease, config.draft_release_id)
        if draft is None or draft.status != STATUS_DRAFT:
            raise CampusConfigError(409, "草稿不存在或已发布")
        incoming_domains = [item.model_dump() for item in (body.official_domains or [])]
        if incoming_domains:
            try:
                domains = normalize_official_domains(incoming_domains)
            except DomainPolicyError as exc:
                raise CampusConfigError(422, str(exc)) from exc
        else:
            domains = _loads_list(draft.official_domains_json)
        bindings = [item.model_dump() for item in body.knowledge_bindings]
        ids = [str(item.get("knowledge_id") or "").strip() for item in bindings]
        if not [i for i in ids if i]:
            raise CampusConfigError(422, "请至少选择一个知识库")
        if len([i for i in ids if i]) != len(set(i for i in ids if i)):
            raise CampusConfigError(422, "同一知识库不能重复绑定")
        model_id = str(body.model_id or draft.model_id or "").strip()
        if not model_id:
            current = await session.get(CampusAssistantRelease, config.current_release_id) if config.current_release_id else None
            model_id = str((current.model_id if current else "") or "").strip()
        if not model_id:
            row = (await session.execute(
                select(ChatModel).where(ChatModel.enabled == 1).order_by(ChatModel.is_default.desc(), ChatModel.sort_order.asc())
            )).scalars().first()
            model_id = str(getattr(row, "model_id", "") or "")
        draft.model_id = model_id
        if "main_chat_skin_id" in getattr(body, "model_fields_set", set()):
            requested_skin_id = str(body.main_chat_skin_id or "").strip() or None
            try:
                await validate_skin_selection(
                    session,
                    tenant_id,
                    requested_skin_id,
                    for_update=True,
                )
            except MainChatSkinError as exc:
                raise CampusConfigError(exc.status_code, exc.detail) from exc
            draft.main_chat_skin_id = requested_skin_id
        draft.official_domains_json = _dumps(domains)
        draft.change_note = str(body.change_note or "")[:1024]
        draft.policy_version = CAMPUS_POLICY_VERSION
        existing = (await session.execute(
            select(CampusAssistantReleaseKb).where(CampusAssistantReleaseKb.release_id == draft.id)
        )).scalars().all()
        for row in existing:
            await session.delete(row)
        await session.flush()
        for row in _kb_rows(draft.id, bindings):
            session.add(row)
        if body.enabled is not None:
            config.enabled = 1 if body.enabled else 0
        config.revision = int(config.revision or 0) + 1
        config.updated_by = _user_id(user)
        await session.commit()
        return await _serialize_config(session, config)


async def _model_available(session: AsyncSession, model_id: str) -> bool:
    model_id = str(model_id or "").strip()
    if not model_id:
        return False
    row = (await session.execute(
        select(ChatModel).where(ChatModel.model_id == model_id)
    )).scalar_one_or_none()
    if row is None:
        # Dev catalogs may have no local rows; still require a non-empty id.
        count = (await session.execute(select(ChatModel.id))).first()
        return count is None
    return bool(row.enabled)


async def _kb_tenant(session: AsyncSession, knowledge_id: str) -> Optional[str]:
    try:
        row = (await session.execute(
            text("select tenant_id from agent_knowledge_base where id = :id limit 1"),
            {"id": knowledge_id},
        )).first()
    except Exception:  # noqa: BLE001
        return None
    return str(row[0]) if row and row[0] is not None else None


async def validate_payload(
    user,
    *,
    model_id: str,
    official_domains: list[dict],
    knowledge_bindings: list[dict],
    main_chat_skin_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    token = str(getattr(user, "access_token", None) or "")
    tenant_id = _tenant_id(user)

    try:
        domains = normalize_official_domains(official_domains)
    except DomainPolicyError as exc:
        domains = []
        errors.append(str(exc))
    if not domains:
        errors.append("至少配置一个学校官方域名；官网检索与官网配图只允许来自该白名单")

    model_id = str(model_id or "").strip()

    enabled_bindings = [
        item for item in knowledge_bindings
        if str(item.get("knowledge_id") or "").strip() and item.get("enabled", True)
    ]
    ids = [str(item.get("knowledge_id") or "").strip() for item in enabled_bindings]
    if not ids:
        errors.append("至少绑定一个可用知识库")
    if len(ids) != len(set(ids)):
        errors.append("同一知识库不能重复绑定")

    own_session = session is None
    if own_session:
        session = async_session()
        await session.__aenter__()
    try:
        if model_id and not await _model_available(session, model_id):
            errors.append("固定模型不存在或已停用")
        try:
            await validate_skin_selection(
                session,
                tenant_id,
                main_chat_skin_id,
                for_update=True,
            )
        except MainChatSkinError as exc:
            errors.append(exc.detail)
        names: list[str] = []
        categories: list[str] = []
        for item in enabled_bindings:
            kid = str(item.get("knowledge_id") or "").strip()
            kb = await knowledge_access.fetch_knowledge_base(token, tenant_id, kid)
            if not kb:
                errors.append(f"知识库不存在或不可访问：{kid}")
                continue
            status = str(kb.get("status") or "").upper()
            if status == "DISABLED":
                errors.append(f"知识库已停用：{kb.get('name') or kid}")
            chunk_count = int(kb.get("chunkCount") or kb.get("chunk_count") or 0)
            if chunk_count <= 0:
                errors.append(f"知识库尚无可检索片段：{kb.get('name') or kid}")
            kb_tenant = str(kb.get("tenantId") or kb.get("tenant_id") or "") or await _kb_tenant(session, kid)
            if kb_tenant and str(kb_tenant) != str(tenant_id):
                errors.append(f"知识库不属于当前租户：{kb.get('name') or kid}")
            permission = knowledge_access.permission_of(kb)
            if permission and permission not in RETRIEVAL_PERMISSIONS:
                errors.append(f"管理员对知识库 {kb.get('name') or kid} 没有查看权限")
            docs = await knowledge_access.fetch_documents(token, tenant_id, kid)
            if any(str(doc.get("status") or "").upper() in DOC_WARNING_STATUSES for doc in docs):
                warnings.append(f"知识库存在处理中或失败文档：{kb.get('name') or kid}")
            acls = await knowledge_access.fetch_acl(token, tenant_id, kid)
            has_retrieval_acl = any(
                knowledge_access.permission_of(acl) in RETRIEVAL_PERMISSIONS
                for acl in acls
            )
            if not has_retrieval_acl:
                warnings.append(f"知识库可能缺少面向师生的 VIEWER ACL：{kb.get('name') or kid}")
            names.append(str(kb.get("name") or item.get("knowledge_name_snapshot") or kid))
            categories.append(str(item.get("category") or kb.get("name") or ""))
        if len(set(names)) < len(names):
            warnings.append("多个知识库名称相同，可能存在重复召回")
        if len([c for c in categories if c]) >= 2 and len(set(categories)) == 1:
            warnings.append("多个知识库分类相同，可能存在重复召回")
    finally:
        if own_session:
            await session.__aexit__(None, None, None)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "official_domains": domains,
    }


async def validate_draft(user, body=None) -> dict:
    async with async_session() as session:
        config = await _get_config(session, _tenant_id(user))
        if config is None or not config.draft_release_id:
            raise CampusConfigError(404, "没有可验证的草稿")
        draft = await session.get(CampusAssistantRelease, config.draft_release_id)
        if draft is None:
            raise CampusConfigError(404, "草稿不存在")
        bindings = await _load_bindings(session, draft.id)
        model_id = draft.model_id
        main_chat_skin_id = draft.main_chat_skin_id
        domains = _loads_list(draft.official_domains_json)
        if body is not None:
            if body.model_id is not None:
                model_id = body.model_id
            if "main_chat_skin_id" in getattr(body, "model_fields_set", set()):
                main_chat_skin_id = str(body.main_chat_skin_id or "").strip() or None
            if body.official_domains is not None:
                domains = [item.model_dump() for item in body.official_domains]
            if body.knowledge_bindings is not None:
                bindings = [item.model_dump() for item in body.knowledge_bindings]
        result = await validate_payload(
            user,
            model_id=model_id,
            official_domains=domains,
            knowledge_bindings=bindings,
            main_chat_skin_id=main_chat_skin_id,
            session=session,
        )
        result["revision"] = int(config.revision or 0)
        return result


async def publish_draft(user, body) -> dict:
    tenant_id = _tenant_id(user)
    async with async_session() as session:
        config = await _get_config(session, tenant_id)
        if config is None:
            raise CampusConfigError(404, "尚未创建校园百事通配置")
        await session.execute(
            select(CampusAssistantConfig).where(CampusAssistantConfig.id == config.id).with_for_update()
        )
        config = await session.get(CampusAssistantConfig, config.id)
        _require_revision(config, body.expected_revision)
        if not config.enabled:
            raise CampusConfigError(422, "校园百事通已关闭，不能发布")
        draft = await session.get(CampusAssistantRelease, config.draft_release_id) if config.draft_release_id else None
        if draft is None or draft.status != STATUS_DRAFT:
            raise CampusConfigError(409, "没有可发布的草稿")
        bindings = await _load_bindings(session, draft.id)
        domains = _loads_list(draft.official_domains_json)
        result = await validate_payload(
            user,
            model_id=draft.model_id,
            official_domains=domains,
            knowledge_bindings=bindings,
            main_chat_skin_id=draft.main_chat_skin_id,
            session=session,
        )
        if result["errors"]:
            raise CampusConfigError(422, "；".join(result["errors"]))
        if result["warnings"] and not body.confirm_warnings:
            raise CampusConfigError(409, "存在发布警告，需 confirm_warnings=true 后才能发布")
        max_version = (await session.execute(
            select(CampusAssistantRelease.version_no)
            .where(CampusAssistantRelease.config_id == config.id)
            .where(CampusAssistantRelease.version_no.is_not(None))
            .order_by(CampusAssistantRelease.version_no.desc())
        )).scalars().first()
        next_version = int(max_version or 0) + 1
        draft.status = STATUS_PUBLISHED
        draft.version_no = next_version
        draft.published_by = _user_id(user)
        draft.published_at = datetime.utcnow()
        draft.change_note = str(body.change_note or draft.change_note or "")[:1024]
        draft.official_domains_json = _dumps(result["official_domains"])
        draft.config_hash = compute_config_hash(
            model_id=draft.model_id,
            official_domains=result["official_domains"],
            knowledge_bindings=bindings,
            policy_version=draft.policy_version or CAMPUS_POLICY_VERSION,
            main_chat_skin_id=draft.main_chat_skin_id,
        )
        config.current_release_id = draft.id
        config.draft_release_id = None
        config.revision = int(config.revision or 0) + 1
        config.updated_by = _user_id(user)
        await session.commit()
        return await _serialize_config(session, config)


async def abandon_draft(user, expected_revision: int) -> dict:
    async with async_session() as session:
        config = await _get_config(session, _tenant_id(user))
        if config is None:
            raise CampusConfigError(404, "尚未创建校园百事通配置")
        _require_revision(config, expected_revision)
        draft = await session.get(CampusAssistantRelease, config.draft_release_id) if config.draft_release_id else None
        if draft and draft.status == STATUS_DRAFT:
            draft.status = STATUS_ABANDONED
        config.draft_release_id = None
        config.revision = int(config.revision or 0) + 1
        config.updated_by = _user_id(user)
        await session.commit()
        return await _serialize_config(session, config)


async def list_releases(user, limit: int = 20, offset: int = 0) -> dict:
    async with async_session() as session:
        config = await _get_config(session, _tenant_id(user))
        if config is None:
            return {"total": 0, "items": []}
        stmt = (
            select(CampusAssistantRelease)
            .where(CampusAssistantRelease.config_id == config.id)
            .where(CampusAssistantRelease.status == STATUS_PUBLISHED)
            .order_by(CampusAssistantRelease.version_no.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        items = []
        for row in rows[offset: offset + max(1, min(limit, 100))]:
            items.append(serialize_release(row, await _load_bindings(session, row.id)))
        return {"total": len(rows), "items": items}


async def get_release(user, release_id: str) -> dict:
    async with async_session() as session:
        config = await _get_config(session, _tenant_id(user))
        if config is None:
            raise CampusConfigError(404, "尚未创建校园百事通配置")
        release = await session.get(CampusAssistantRelease, release_id)
        if release is None or release.config_id != config.id:
            raise CampusConfigError(404, "版本不存在")
        return serialize_release(release, await _load_bindings(session, release.id))


async def rollback_release(user, release_id: str, body) -> dict:
    async with async_session() as session:
        config = await _get_config(session, _tenant_id(user))
        if config is None:
            raise CampusConfigError(404, "尚未创建校园百事通配置")
        await session.execute(
            select(CampusAssistantConfig).where(CampusAssistantConfig.id == config.id).with_for_update()
        )
        config = await session.get(CampusAssistantConfig, config.id)
        _require_revision(config, body.expected_revision)
        source = await session.get(CampusAssistantRelease, release_id)
        if source is None or source.config_id != config.id or source.status != STATUS_PUBLISHED:
            raise CampusConfigError(404, "只能回滚到已发布版本")
        if config.draft_release_id:
            old = await session.get(CampusAssistantRelease, config.draft_release_id)
            if old and old.status == STATUS_DRAFT:
                old.status = STATUS_ABANDONED
        draft = await _clone_release(session, config, source, user, rollback_from=source.id)
        draft.change_note = str(body.change_note or f"回滚自版本 {source.version_no}")[:1024]
        config.revision = int(config.revision or 0) + 1
        await session.commit()
        publish_body = type("P", (), {
            "expected_revision": config.revision,
            "change_note": draft.change_note,
            "confirm_warnings": True,
        })()
    return await publish_draft(user, publish_body)
