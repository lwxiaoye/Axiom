"""Durable ownership checks for an external workflow's LangGraph interrupt."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from app.core.database import async_session
from app.models import ExternalAgentInteraction
from app.services.agent_api.external_session_service import ExternalSessionAccessError


@dataclass(frozen=True)
class ExternalInteraction:
    id: str
    external_session_id: str
    run_id: str
    resume_id: str
    kind: str
    schema: dict[str, Any]
    status: str


def _as_handle(row: ExternalAgentInteraction) -> ExternalInteraction:
    try:
        schema = json.loads(str(row.schema_json or "{}"))
    except json.JSONDecodeError:
        schema = {}
    return ExternalInteraction(
        id=str(row.id), external_session_id=str(row.external_session_id), run_id=str(row.run_id),
        resume_id=str(schema.get("resumeId") or ""), kind=str(row.kind), schema=schema,
        status=str(row.status),
    )


async def create_external_interaction(
    external_session_id: str, run_id: str, interactive: dict[str, Any], version_id: str
) -> ExternalInteraction:
    payload = {key: value for key, value in dict(interactive or {}).items() if key in {"resumeId", "type", "params"}}
    payload["versionId"] = str(version_id or "")[:64]
    resume_id = str(payload.get("resumeId") or "")[:96]
    if not external_session_id or not run_id or not resume_id:
        raise ExternalSessionAccessError("external_interaction_invalid")
    row = ExternalAgentInteraction(
        id=f"exti_{uuid.uuid4().hex}", external_session_id=str(external_session_id)[:64],
        run_id=str(run_id)[:64], kind=str(payload.get("type") or "input")[:32],
        schema_json=json.dumps(payload, ensure_ascii=False), status="pending",
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _as_handle(row)


async def get_external_interaction(
    external_session_id: str, run_id: str, interaction_id: str | None = None
) -> ExternalInteraction:
    async with async_session() as session:
        query = select(ExternalAgentInteraction).where(
            ExternalAgentInteraction.external_session_id == str(external_session_id or "")[:64],
            ExternalAgentInteraction.run_id == str(run_id or "")[:64],
        )
        if interaction_id:
            query = query.where(ExternalAgentInteraction.id == str(interaction_id)[:64])
        row = await session.scalar(query.order_by(ExternalAgentInteraction.created_at.desc()))
    if row is None:
        raise ExternalSessionAccessError("external_interaction_not_found")
    return _as_handle(row)


async def submit_external_interaction(
    external_session_id: str, run_id: str, interaction_id: str, value: Any
) -> ExternalInteraction:
    async with async_session() as session:
        row = await session.scalar(
            select(ExternalAgentInteraction)
            .where(
                ExternalAgentInteraction.external_session_id == str(external_session_id or "")[:64],
                ExternalAgentInteraction.run_id == str(run_id or "")[:64],
                ExternalAgentInteraction.id == str(interaction_id or "")[:64],
            )
            .with_for_update()
        )
        if row is None:
            raise ExternalSessionAccessError("external_interaction_not_found")
        if str(row.status) != "pending":
            raise ExternalSessionAccessError("external_interaction_not_pending")
        row.status = "submitted"
        row.submitted_value_json = json.dumps(value, ensure_ascii=False)
        await session.commit()
        await session.refresh(row)
    return _as_handle(row)


async def mark_interaction_waiting(interaction_id: str, interactive: dict[str, Any], version_id: str) -> ExternalInteraction:
    payload = {key: value for key, value in dict(interactive or {}).items() if key in {"resumeId", "type", "params"}}
    payload["versionId"] = str(version_id or "")[:64]
    async with async_session() as session:
        await session.execute(
            update(ExternalAgentInteraction)
            .where(ExternalAgentInteraction.id == str(interaction_id or "")[:64])
            .values(status="pending", schema_json=json.dumps(payload, ensure_ascii=False), submitted_value_json=None)
        )
        await session.commit()
    return await get_external_interaction_by_id(interaction_id)


async def mark_interaction_completed(interaction_id: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(ExternalAgentInteraction)
            .where(ExternalAgentInteraction.id == str(interaction_id or "")[:64])
            .values(status="completed")
        )
        await session.commit()


async def get_external_interaction_by_id(interaction_id: str) -> ExternalInteraction:
    async with async_session() as session:
        row = await session.get(ExternalAgentInteraction, str(interaction_id or "")[:64])
    if row is None:
        raise ExternalSessionAccessError("external_interaction_not_found")
    return _as_handle(row)
