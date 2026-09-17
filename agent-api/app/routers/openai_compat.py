"""OpenAI-compatible facade for a publisher's own released Agent API."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, File, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.sse_utils import SSE_HEADERS, with_sse_keepalive
from app.services.agent_api.access_service import (
    AgentApiPrincipal,
    ApiAuthError,
    ApiConcurrencyLimitError,
    ApiReleaseInactiveError,
    ApiRateLimitError,
    authenticate_bearer,
    execution_slot,
)
from app.services.agent_api.execution_service import (
    ApiExecutionError,
    ApiExecutionStreamEvent,
    ApiQuotaError,
    execute_published_api_workflow,
    load_active_api_version,
    stream_published_api_workflow,
)
from app.services.agent_api.external_session_service import (
    ExternalSessionAccessError,
    get_or_create_external_session,
    resolve_external_file,
    save_external_file,
)
from app.services.agent_api.invocation_service import (
    InvocationHandle,
    InvocationResult,
    begin_invocation,
    finish_invocation,
    trusted_runtime_usage,
)
from app.services.agent_api.openai_adapter import (
    OpenAIChatRequest,
    OpenAIRequestError,
    completion_id,
    make_chat_completion,
    make_chunk,
    openai_error,
    parse_chat_completion_request,
    request_error_response,
    trusted_usage,
    virtual_model_id,
)


router = APIRouter(prefix="/openai/v1", tags=["publisher-agent-openai"])


async def authenticate_openai_bearer(authorization: str) -> AgentApiPrincipal:
    scheme, separator, token = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or token.strip() != token:
        raise ApiAuthError("invalid_api_key")
    return await authenticate_bearer(token)


def _safe_execution_error(error: Exception) -> tuple[int, str, str, str]:
    if isinstance(error, (ApiAuthError,)):
        return 401, "Invalid API key.", "invalid_request_error", "invalid_api_key"
    if isinstance(error, ApiRateLimitError):
        return 429, "Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded"
    if isinstance(error, ApiConcurrencyLimitError):
        return 429, "Too many concurrent requests.", "rate_limit_error", "concurrency_limit_exceeded"
    if isinstance(error, ApiReleaseInactiveError):
        return 403, "This agent API release is inactive.", "invalid_request_error", "api_release_inactive"
    if isinstance(error, ApiQuotaError):
        return 402, "Publisher account has insufficient quota.", "insufficient_quota", "insufficient_quota"
    if isinstance(error, ApiExecutionError):
        if error.code == "api_release_inactive":
            return 403, "This agent API release is inactive.", "invalid_request_error", "api_release_inactive"
        if error.code == "api_model_access_denied":
            return 402, "Publisher account has insufficient quota.", "insufficient_quota", "insufficient_quota"
    if isinstance(error, ExternalSessionAccessError):
        return 400, "External session or file is unavailable.", "invalid_request_error", error.code
    return 500, "Agent execution failed.", "api_error", "api_execution_failed"


def _error_response(error: Exception) -> JSONResponse:
    status, message, error_type, code = _safe_execution_error(error)
    return openai_error(status, message, error_type, None, code)


def _result_error(result: Mapping[str, Any]) -> ApiExecutionError | None:
    if str(result.get("status") or "success") == "success":
        return None
    return ApiExecutionError("api_execution_failed")


async def begin_openai_invocation(
    principal: AgentApiPrincipal, request_user: str, *, external_session_id: str = ""
) -> InvocationHandle:
    """Persist billing attribution before the adapter enters the workflow runtime."""
    version = await load_active_api_version(principal.app_id, principal.owner_user_id)
    return await begin_invocation(
        principal, str(version.id or ""), "openai_api", request_user=request_user,
        external_session_id=external_session_id,
    )


async def _external_execution_request(
    principal: AgentApiPrincipal, parsed: OpenAIChatRequest, session_id: str
):
    session = await get_or_create_external_session(principal, session_id)
    for file_id in parsed.execution.file_ids:
        await resolve_external_file(session, file_id)
    return replace(
        parsed.execution,
        session_id=session_id,
        external_session=session,
    )


async def _finish(handle: InvocationHandle | None, *, result: Mapping[str, Any] | None = None,
                  error: Exception | None = None, http_status: int = 200) -> None:
    if handle is None:
        return
    usage = trusted_usage(result)
    if error is None:
        await finish_invocation(handle, InvocationResult(
            status=("waiting" if str((result or {}).get("status") or "") == "waiting_external_input" else "success"),
            http_status=http_status,
            input_tokens=usage["prompt_tokens"] if usage else None,
            output_tokens=usage["completion_tokens"] if usage else None,
            usage_known=usage is not None,
        ))
        return
    status, message, _error_type, code = _safe_execution_error(error)
    await finish_invocation(handle, InvocationResult(
        status="failed", http_status=status, error_code=code, error_message=message,
        input_tokens=usage["prompt_tokens"] if usage else None,
        output_tokens=usage["completion_tokens"] if usage else None,
        usage_known=usage is not None,
    ))


async def _with_runtime_usage(result: Mapping[str, Any], handle: InvocationHandle | None) -> Mapping[str, Any]:
    if trusted_usage(result) is not None:
        return result
    usage = await trusted_runtime_usage(handle)
    if usage is None:
        return result
    return {**result, "usage": usage}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _pending_response(result: Mapping[str, Any]) -> JSONResponse:
    interaction = result.get("interaction")
    required_action: dict[str, Any] = {}
    if interaction is not None:
        required_action = {
            "interaction_id": getattr(interaction, "id", ""),
            "type": getattr(interaction, "kind", "input"),
            "params": (getattr(interaction, "schema", {}) or {}).get("params") or {},
        }
    return JSONResponse({
        "object": "run_pending",
        "run_id": str(result.get("runId") or ""),
        "status": "requires_action",
        "required_action": required_action,
    })


async def _openai_stream(
    principal: AgentApiPrincipal,
    request: OpenAIChatRequest,
    handle: InvocationHandle | None,
) -> AsyncGenerator[str, None]:
    model = virtual_model_id(principal.app_id)
    completion = ""
    created = int(time.time())
    finalized = False
    try:
        execution = replace(request.execution, attribution=handle.attribution if handle else None)
        async with execution_slot(principal.key_id):
            async for event in stream_published_api_workflow(principal, execution):
                if event.type == "delta" and event.delta:
                    if not completion:
                        completion = completion_id(None)
                        yield _sse(make_chunk(completion, model, delta={"role": "assistant"}, created=created))
                    yield _sse(make_chunk(completion, model, delta={"content": event.delta}, created=created))
                    continue
                if event.type == "completed":
                    result = await _with_runtime_usage(event.result or {}, handle)
                    result_error = _result_error(result)
                    if result_error is not None:
                        raise result_error
                    if not completion:
                        completion = completion_id(result)
                        yield _sse(make_chunk(completion, model, delta={"role": "assistant"}, created=created))
                    yield _sse(make_chunk(completion, model, delta={}, finish_reason="stop", created=created))
                    usage = trusted_usage(result)
                    if request.include_usage and usage is not None:
                        yield _sse(make_chunk(completion, model, usage=usage, created=created))
                    await _finish(handle, result=result)
                    finalized = True
                    break
                if event.type == "error":
                    raise event.error or ApiExecutionError("api_execution_failed")
            else:
                raise ApiExecutionError("api_execution_failed")
    except Exception as error:  # streaming errors already have a 200 transport response
        status, message, error_type, code = _safe_execution_error(error)
        yield _sse({"error": {"message": message, "type": error_type, "param": None, "code": code}})
        await _finish(handle, error=error, http_status=status)
        finalized = True
    finally:
        if not finalized:
            await _finish(handle, error=ApiExecutionError("api_execution_failed"), http_status=500)
        yield "data: [DONE]\n\n"


@router.get("/models")
async def list_models(authorization: str = Header(default="", alias="Authorization")) -> JSONResponse:
    try:
        principal = await authenticate_openai_bearer(authorization)
    except Exception as error:
        return _error_response(error)
    return JSONResponse({"object": "list", "data": [{
        "id": virtual_model_id(principal.app_id), "object": "model", "created": 0,
        "owned_by": "publisher-agent-api",
    }]})


@router.post("/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(default="", alias="Authorization")):
    try:
        principal = await authenticate_openai_bearer(authorization)
    except Exception as error:
        return _error_response(error)
    try:
        payload = await request.json()
        parsed = parse_chat_completion_request(payload, principal)
    except OpenAIRequestError as error:
        return request_error_response(error)
    except Exception:
        return openai_error(400, "Request body must be valid JSON.", "invalid_request_error", None, "invalid_json")
    try:
        session_id = str(request.query_params.get("session_id") or parsed.execution.session_id)
        execution = await _external_execution_request(principal, parsed, session_id)
        handle = await begin_openai_invocation(
            principal, parsed.request_user, external_session_id=execution.external_session.id,
        )
    except Exception as error:
        return _error_response(error)
    if parsed.stream:
        return StreamingResponse(
            with_sse_keepalive(_openai_stream(principal, replace(parsed, execution=execution), handle)), media_type="text/event-stream", headers=SSE_HEADERS,
        )
    try:
        async with execution_slot(principal.key_id):
            result = await execute_published_api_workflow(
                principal, replace(execution, attribution=handle.attribution if handle else None),
            )
        result = await _with_runtime_usage(result, handle)
        if str(result.get("status") or "") == "waiting_external_input":
            await _finish(handle, result=result)
            return _pending_response(result)
        result_error = _result_error(result)
        if result_error is not None:
            raise result_error
        await _finish(handle, result=result)
        return JSONResponse(make_chat_completion(result, virtual_model_id(principal.app_id)))
    except Exception as error:
        await _finish(handle, error=error)
        return _error_response(error)


@router.post("/files")
async def upload_openai_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Query(default=""),
    authorization: str = Header(default="", alias="Authorization"),
) -> JSONResponse:
    try:
        principal = await authenticate_openai_bearer(authorization)
        session = await get_or_create_external_session(principal, session_id)
        uploaded = await save_external_file(session, file.filename or "file", file.content_type or "", file)
    except Exception as error:
        return _error_response(error)
    return JSONResponse({
        "id": uploaded.id,
        "object": "file",
        "bytes": uploaded.size_bytes,
        "filename": uploaded.original_name,
        "purpose": "assistants",
    })
