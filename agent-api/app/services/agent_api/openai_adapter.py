"""Strict, provider-safe OpenAI Chat Completions wire adapter."""
from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_api.execution_service import ApiExecutionRequest


_SUPPORTED_FIELDS = frozenset({"model", "messages", "n", "user", "stream", "stream_options"})


class OpenAIRequestError(ValueError):
    def __init__(self, message: str, *, param: str | None = None, code: str = "invalid_request_error"):
        self.message = message
        self.param = param
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class OpenAIChatRequest:
    execution: ApiExecutionRequest
    stream: bool
    include_usage: bool
    request_user: str


def virtual_model_id(app_id: str) -> str:
    return f"agent_{str(app_id or '')}"


def openai_error(status: int, message: str, error_type: str, param: str | None, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=int(status),
        content={"error": {"message": message, "type": error_type, "param": param, "code": code}},
    )


def request_error_response(error: OpenAIRequestError) -> JSONResponse:
    return openai_error(400, error.message, "invalid_request_error", error.param, error.code)


def _unsupported_parameter(name: str) -> OpenAIRequestError:
    return OpenAIRequestError(
        f"Parameter '{name}' is not supported by publisher agent API.",
        param=name,
        code="unsupported_parameter",
    )


def _safe_session_id(request_user: str) -> str:
    if request_user:
        # ``user`` is opaque audit metadata, not an untrusted thread identifier.
        return f"chat_{hashlib.sha256(request_user.encode('utf-8')).hexdigest()[:32]}"
    return f"chat_{uuid.uuid4().hex}"


def _parse_stream_options(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, Mapping):
        raise OpenAIRequestError("Parameter 'stream_options' must be an object.", param="stream_options")
    unknown = next((str(key) for key in value if key != "include_usage"), None)
    if unknown is not None:
        raise _unsupported_parameter(f"stream_options.{unknown}")
    include_usage = value.get("include_usage", False)
    if not isinstance(include_usage, bool):
        raise OpenAIRequestError(
            "Parameter 'stream_options.include_usage' must be a boolean.",
            param="stream_options.include_usage",
        )
    return include_usage


def parse_chat_completion_request(payload: Any, principal: AgentApiPrincipal) -> OpenAIChatRequest:
    """Validate the intentionally small API subset before allocating a run."""
    if not isinstance(payload, Mapping):
        raise OpenAIRequestError("Request body must be a JSON object.")
    unsupported = next((str(key) for key in payload if str(key) not in _SUPPORTED_FIELDS), None)
    if unsupported is not None:
        raise _unsupported_parameter(unsupported)

    expected_model = virtual_model_id(principal.app_id)
    if payload.get("model") != expected_model:
        raise OpenAIRequestError(
            f"Model must be '{expected_model}'.", param="model", code="model_not_found",
        )
    n = payload.get("n", 1)
    if isinstance(n, bool) or not isinstance(n, int) or n != 1:
        raise OpenAIRequestError("Only n=1 is supported.", param="n", code="unsupported_parameter")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise OpenAIRequestError("Parameter 'stream' must be a boolean.", param="stream")
    request_user = payload.get("user", "")
    if request_user is None:
        request_user = ""
    if not isinstance(request_user, str):
        raise OpenAIRequestError("Parameter 'user' must be a string.", param="user")
    if len(request_user) > 255:
        raise OpenAIRequestError("Parameter 'user' must be at most 255 characters.", param="user")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise OpenAIRequestError("Parameter 'messages' must be a non-empty array.", param="messages", code="invalid_messages")
    normalized: list[dict[str, str]] = []
    final_file_ids: list[str] = []
    for index, message in enumerate(messages):
        path = f"messages[{index}]"
        if not isinstance(message, Mapping):
            raise OpenAIRequestError("Every message must be an object.", param=path, code="invalid_messages")
        extra_key = next((str(key) for key in message if key not in {"role", "content"}), None)
        if extra_key is not None:
            raise _unsupported_parameter(f"{path}.{extra_key}")
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise _unsupported_parameter(f"{path}.role")
        content = message.get("content")
        if isinstance(content, str):
            normalized.append({"role": role, "content": content})
            continue
        if role != "user" or not isinstance(content, list):
            raise OpenAIRequestError(
                "Only plain text or user input parts are supported by publisher agent API.",
                param=f"{path}.content", code="unsupported_parameter",
            )
        texts: list[str] = []
        file_ids: list[str] = []
        for part_index, part in enumerate(content):
            part_path = f"{path}.content[{part_index}]"
            if not isinstance(part, Mapping):
                raise OpenAIRequestError("Content part must be an object.", param=part_path)
            part_type = part.get("type")
            if part_type == "input_text" and set(part) == {"type", "text"} and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part_type == "input_file" and set(part) == {"type", "file_id"} and isinstance(part.get("file_id"), str):
                file_id = part["file_id"].strip()
                if not file_id:
                    raise OpenAIRequestError("input_file.file_id must not be empty.", param=f"{part_path}.file_id")
                file_ids.append(file_id)
            else:
                raise OpenAIRequestError("Unsupported content part.", param=part_path, code="unsupported_parameter")
        if not texts:
            raise OpenAIRequestError("A user message must include input_text.", param=f"{path}.content")
        normalized.append({"role": role, "content": "\n".join(texts)})
        if file_ids:
            if index != len(messages) - 1:
                raise OpenAIRequestError(
                    "input_file is supported only in the final user message.", param=f"{path}.content",
                    code="unsupported_parameter",
                )
            final_file_ids = list(dict.fromkeys(file_ids))
    if normalized[-1]["role"] != "user":
        raise OpenAIRequestError(
            "The final message must have role 'user'.", param=f"messages[{len(normalized) - 1}].role", code="invalid_messages",
        )

    return OpenAIChatRequest(
        execution=ApiExecutionRequest(
            input_text=normalized[-1]["content"], histories=normalized[:-1],
            session_id=_safe_session_id(request_user), source="openai_api", file_ids=final_file_ids,
        ),
        stream=stream,
        include_usage=_parse_stream_options(payload.get("stream_options")),
        request_user=request_user,
    )


def trusted_usage(result: Mapping[str, Any] | None) -> dict[str, int] | None:
    """Return OpenAI usage only when upstream supplied a trusted value."""
    candidate = result.get("usage") if isinstance(result, Mapping) else None
    if not isinstance(candidate, Mapping) or not bool(candidate.get("trusted") or candidate.get("usage_known")):
        return None
    prompt = candidate.get("prompt_tokens", candidate.get("input_tokens"))
    completion = candidate.get("completion_tokens", candidate.get("output_tokens"))
    if (isinstance(prompt, bool) or isinstance(completion, bool)
            or not isinstance(prompt, int) or not isinstance(completion, int)
            or prompt < 0 or completion < 0):
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def completion_id(result: Mapping[str, Any] | None) -> str:
    run_id = str(result.get("runId") or "") if isinstance(result, Mapping) else ""
    safe_run_id = "".join(char for char in run_id if char.isalnum() or char in "_-")[:96]
    return f"chatcmpl_{safe_run_id or uuid.uuid4().hex}"


def make_chat_completion(result: Mapping[str, Any], model: str, *, created: int | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": completion_id(result), "object": "chat.completion", "created": int(created or time.time()), "model": model,
        "choices": [{
            "index": 0, "message": {"role": "assistant", "content": str(result.get("output") or "")},
            "logprobs": None, "finish_reason": "stop",
        }],
    }
    usage = trusted_usage(result)
    if usage is not None:
        response["usage"] = usage
    return response


def make_chunk(
    completion: str, model: str, *, delta: dict[str, str] | None = None,
    finish_reason: str | None = None, usage: dict[str, int] | None = None, created: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": completion, "object": "chat.completion.chunk", "created": int(created or time.time()), "model": model,
        "choices": [] if usage is not None else [{
            "index": 0, "delta": delta or {}, "logprobs": None, "finish_reason": finish_reason,
        }],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload
