"""Static key iframe execution endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Header, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.services.agent_api.access_service import (
    AgentApiPrincipal,
    ApiAuthError,
    ApiConcurrencyLimitError,
    ApiRateLimitError,
    ApiReleaseInactiveError,
    authenticate_embed_key,
    execution_slot,
    resolve_embed_key_frame,
)
from app.services.agent_api.embed_ticket_service import EmbedTicketError, embed_ticket_service
from app.services.agent_api.embed_page import frame_content_security_policy, render_embed_page
from app.services.agent_api.execution_service import (
    ApiExecutionError,
    ApiExecutionRequest,
    ApiQuotaError,
    execute_published_api_workflow,
    resume_published_api_workflow,
)
from app.services.agent_api.external_interaction_service import (
    mark_interaction_completed,
    submit_external_interaction,
)
from app.services.agent_api.external_session_service import (
    get_or_create_external_session,
    resolve_external_file,
    save_external_file,
)
from app.services.agent_api.invocation_service import (
    InvocationResult,
    begin_invocation,
    finish_invocation,
)


router = APIRouter(prefix="/embed/v1", tags=["publisher-agent-embed"])
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _response(status: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers=PRIVATE_HEADERS)


def _error(status: int, code: str) -> JSONResponse:
    messages = {
        "invalid_api_key": "Invalid API key.",
        "invalid_embed_token": "Invalid embed session.",
        "session_expired": "Embed session has expired.",
        "origin_mismatch": "Embed origin is not allowed.",
        "origin_not_allowed": "Embed origin is not allowed.",
        "api_release_inactive": "This agent API release is inactive.",
        "embed_key_revoked": "Embed session is no longer active.",
        "insufficient_quota": "Publisher account has insufficient quota.",
        "unsupported_parameter": "This parameter is not supported by embed runs.",
    }
    return _response(status, {"error": {"code": code, "message": messages.get(code, "Embed request failed.")}})


def _embed_ticket_error(error: EmbedTicketError) -> JSONResponse:
    if error.code in {"origin_invalid", "origin_missing", "origin_mismatch", "origin_not_allowed"}:
        return _error(403, error.code)
    if error.code in {"api_release_inactive", "embed_key_revoked"}:
        return _error(403, error.code)
    if error.code in {"invalid_embed_token", "session_expired"}:
        return _error(401, error.code)
    return _error(400, error.code)


def _access_error(error: Exception) -> JSONResponse:
    if isinstance(error, ApiAuthError):
        return _error(401, "invalid_api_key")
    if isinstance(error, ApiRateLimitError):
        return _error(429, "rate_limit_exceeded")
    if isinstance(error, ApiConcurrencyLimitError):
        return _error(429, "concurrency_limit_exceeded")
    if isinstance(error, ApiReleaseInactiveError):
        return _error(403, "api_release_inactive")
    if isinstance(error, ApiExecutionError) and error.code == "api_release_inactive":
        return _error(403, "api_release_inactive")
    return _error(500, "embed_request_failed")


async def _json_object(request: Request) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _embed_session_token(authorization: str) -> str:
    scheme, separator, token = str(authorization or "").partition(" ")
    if scheme != "Embed" or not separator or not token or token.strip() != token:
        raise EmbedTicketError("invalid_embed_token")
    return token


@router.get("/frame/{app_id}")
async def render_embed_frame(
    app_id: str, embedKeyId: str = Query(default=""),
) -> HTMLResponse:
    """Serve the isolated frame with a CSP bound to its static qze key origin."""
    try:
        parent_origin = (await resolve_embed_key_frame(embedKeyId, app_id)).origin
    except (EmbedTicketError, ApiAuthError, ApiReleaseInactiveError):
        # Avoid revealing ticket state from a URL that may have been copied.
        return HTMLResponse("Not Found", status_code=404, headers=PRIVATE_HEADERS)
    headers = {
        **PRIVATE_HEADERS,
        "Content-Security-Policy": frame_content_security_policy(parent_origin),
    }
    return HTMLResponse(render_embed_page(parent_origin), headers=headers)


@router.post("/agents/{app_id}/direct-sessions")
async def create_direct_embed_session(
    app_id: str,
    authorization: str = Header(default="", alias="Authorization"),
) -> JSONResponse:
    scheme, separator, token = str(authorization or "").partition(" ")
    if scheme != "Embed-Key" or not separator:
        return _error(401, "invalid_embed_key")
    try:
        principal = await authenticate_embed_key(token, app_id)
        session = await embed_ticket_service.create_direct_session(principal)
    except ApiRateLimitError:
        return _error(429, "rate_limit_exceeded")
    except ApiReleaseInactiveError:
        return _error(403, "api_release_inactive")
    except Exception:
        return _error(401, "invalid_embed_key")
    return _response(200, {"token": session.token, "expiresAt": session.expires_at.isoformat()})


@router.post("/runs")
async def run_embed_agent(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
) -> JSONResponse:
    try:
        token = _embed_session_token(authorization)
    except EmbedTicketError as error:
        return _embed_ticket_error(error)
    payload = await _json_object(request)
    if payload is None:
        return _error(400, "invalid_request")
    forbidden = next((key for key in ("model", "tools", "files", "file", "workflowJson", "apiKey", "token") if key in payload), None)
    if forbidden is not None:
        return _error(400, "unsupported_parameter")
    if set(payload) - {"input", "sessionId", "fileIds"}:
        return _error(400, "invalid_request")
    if not isinstance(payload.get("input"), str) or not payload["input"].strip():
        return _error(400, "invalid_request")
    session_id = payload.get("sessionId") or "embed-session"
    if not isinstance(session_id, str):
        return _error(400, "invalid_request")
    file_ids = payload.get("fileIds") or []
    if not isinstance(file_ids, list) or any(not isinstance(file_id, str) for file_id in file_ids):
        return _error(400, "invalid_request")
    try:
        # The platform-hosted iframe document cannot prove its parent through
        # HTTP Origin; the qze key's exact origin and frame CSP do that.
        session = await embed_ticket_service.authenticate_session(token)
    except EmbedTicketError as error:
        return _embed_ticket_error(error)
    except ApiRateLimitError:
        return _error(429, "rate_limit_exceeded")

    principal = AgentApiPrincipal(session.key_id, session.app_id, session.owner_user_id, "embed")
    handle = None
    try:
        async with execution_slot(principal.key_id):
            external_session = await get_or_create_external_session(principal, session_id)
            for file_id in file_ids:
                await resolve_external_file(external_session, file_id)
            handle = await begin_invocation(
                principal, session.version_id, "embed", external_session_id=external_session.id,
            )
            result = await execute_published_api_workflow(
                principal,
                ApiExecutionRequest(
                    input_text=payload["input"], histories=[], session_id=session_id,
                    source="embed", attribution=handle.attribution, external_session=external_session,
                    file_ids=file_ids,
                ),
            )
        result_status = str(result.get("status") or "success")
        if result_status not in {"success", "waiting_external_input"}:
            raise ApiExecutionError("api_execution_failed")
        await finish_invocation(handle, InvocationResult(status="waiting" if result_status == "waiting_external_input" else "success", http_status=200))
        content = {
            "runId": str(result.get("runId") or ""), "status": result_status,
            "output": str(result.get("output") or ""),
        }
        interaction = result.get("interaction")
        if interaction is not None:
            content["interaction"] = {
                "id": interaction.id,
                "type": interaction.kind,
                "params": interaction.schema.get("params") or {},
            }
        return _response(200, content)
    except ApiQuotaError:
        if handle is not None:
            await finish_invocation(handle, InvocationResult(status="failed", http_status=402, error_code="insufficient_quota"))
        return _error(402, "insufficient_quota")
    except ApiExecutionError as error:
        if handle is not None:
            await finish_invocation(handle, InvocationResult(status="failed", http_status=500, error_code=error.code))
        status = 403 if error.code == "api_release_inactive" else 500
        return _error(status, error.code)
    except ApiConcurrencyLimitError:
        return _error(429, "concurrency_limit_exceeded")
    except Exception:
        if handle is not None:
            await finish_invocation(handle, InvocationResult(status="failed", http_status=500, error_code="api_execution_failed"))
        return _error(500, "api_execution_failed")


@router.post("/files")
async def upload_embed_file(
    file: UploadFile = File(...),
    session_id: str = Query(default=""),
    authorization: str = Header(default="", alias="Authorization"),
) -> JSONResponse:
    try:
        token = _embed_session_token(authorization)
        session = await embed_ticket_service.authenticate_session(token)
        principal = AgentApiPrincipal(session.key_id, session.app_id, session.owner_user_id, "embed")
        external_session = await get_or_create_external_session(principal, session_id)
        uploaded = await save_external_file(
            external_session, file.filename or "file", file.content_type or "", file,
        )
    except EmbedTicketError as error:
        return _embed_ticket_error(error)
    except ApiRateLimitError:
        return _error(429, "rate_limit_exceeded")
    except Exception:
        return _error(400, "invalid_request")
    return _response(200, {
        "id": uploaded.id,
        "filename": uploaded.original_name,
        "bytes": uploaded.size_bytes,
    })


@router.post("/runs/{run_id}/inputs")
async def submit_embed_run_input(
    run_id: str,
    request: Request,
    session_id: str = Query(default=""),
    authorization: str = Header(default="", alias="Authorization"),
) -> JSONResponse:
    payload = await _json_object(request)
    if payload is None or not isinstance(payload.get("interactionId"), str) or "value" not in payload:
        return _error(400, "invalid_request")
    try:
        token = _embed_session_token(authorization)
        session = await embed_ticket_service.authenticate_session(token)
        principal = AgentApiPrincipal(session.key_id, session.app_id, session.owner_user_id, "embed")
        external_session = await get_or_create_external_session(principal, session_id)
        interaction = await submit_external_interaction(
            external_session.id, run_id, payload["interactionId"], payload["value"],
        )
        result = await resume_published_api_workflow(
            principal,
            ApiExecutionRequest(input_text="", session_id=session_id, source="embed", external_session=external_session),
            resume_id=interaction.resume_id,
            value=payload["value"],
        )
        await mark_interaction_completed(interaction.id)
    except EmbedTicketError as error:
        return _embed_ticket_error(error)
    except Exception:
        return _error(500, "embed_request_failed")
    interaction_value = result.get("interaction")
    content = {"runId": str(result.get("runId") or ""), "status": str(result.get("status") or "success"),
               "output": str(result.get("output") or "")}
    if interaction_value is not None:
        content["interaction"] = {
            "id": interaction_value.id,
            "type": interaction_value.kind,
            "params": interaction_value.schema.get("params") or {},
        }
    return _response(200, content)
