"""Key-authenticated lifecycle endpoints for externally paused Agent runs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.routers.openai_compat import authenticate_openai_bearer
from app.services.agent_api.execution_service import (
    ApiExecutionRequest,
    execute_published_api_workflow,
    resume_published_api_workflow,
)
from app.services.agent_api.external_interaction_service import (
    get_external_interaction,
    mark_interaction_completed,
    submit_external_interaction,
)
from app.services.agent_api.external_session_service import (
    get_or_create_external_session,
    resolve_external_file,
)


router = APIRouter(prefix="/external/v1", tags=["publisher-agent-external-runs"])


def _interaction_payload(interaction) -> dict[str, Any]:  # noqa: ANN001
    schema = dict(interaction.schema or {})
    return {
        "id": interaction.id,
        "type": interaction.kind,
        "params": schema.get("params") or {},
        "status": interaction.status,
    }


async def _principal_and_session(authorization: str, session_id: str):
    principal = await authenticate_openai_bearer(authorization)
    session = await get_or_create_external_session(principal, session_id)
    return principal, session


@router.post("/runs")
async def start_external_run(request: Request, authorization: str = Header(default="", alias="Authorization")):
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), str):
        raise HTTPException(400, "invalid_request")
    session_id = str(payload.get("sessionId") or "")
    file_ids = payload.get("fileIds") or []
    if not isinstance(file_ids, list) or any(not isinstance(file_id, str) for file_id in file_ids):
        raise HTTPException(400, "invalid_file_ids")
    principal, session = await _principal_and_session(authorization, session_id)
    for file_id in file_ids:
        await resolve_external_file(session, file_id)
    result = await execute_published_api_workflow(
        principal,
        ApiExecutionRequest(
            input_text=payload["input"], session_id=session_id, source="openai_api",
            external_session=session, file_ids=file_ids,
        ),
    )
    if result.get("interaction"):
        result = {**result, "interaction": _interaction_payload(result["interaction"])}
    return result


@router.get("/runs/{run_id}")
async def get_external_run(
    run_id: str, session_id: str, authorization: str = Header(default="", alias="Authorization"),
):
    _principal, session = await _principal_and_session(authorization, session_id)
    interaction = await get_external_interaction(session.id, run_id)
    return {
        "runId": run_id,
        "status": "waiting_external_input" if interaction.status == "pending" else interaction.status,
        "interaction": _interaction_payload(interaction),
    }


@router.post("/runs/{run_id}/inputs", status_code=202)
async def submit_external_run_input(
    run_id: str, request: Request, session_id: str, authorization: str = Header(default="", alias="Authorization"),
):
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("interactionId"), str) or "value" not in payload:
        raise HTTPException(400, "invalid_request")
    principal, session = await _principal_and_session(authorization, session_id)
    interaction = await submit_external_interaction(session.id, run_id, payload["interactionId"], payload["value"])
    result = await resume_published_api_workflow(
        principal,
        ApiExecutionRequest(
            input_text="", session_id=session_id, source="openai_api", external_session=session,
        ),
        resume_id=interaction.resume_id,
        value=payload["value"],
    )
    await mark_interaction_completed(interaction.id)
    if result.get("interaction"):
        result = {**result, "interaction": _interaction_payload(result["interaction"])}
    return result
