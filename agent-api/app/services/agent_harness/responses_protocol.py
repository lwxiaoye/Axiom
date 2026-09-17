"""OpenAI Responses transport helpers for the main Agent Harness loop.

The Harness keeps its durable conversation cursor in the legacy chat-message
shape because tool execution and resume already use that shape.  This module is
the narrow protocol boundary: it translates the cursor to Responses input
items and normalizes streamed Responses output items back into the fields the
loop consumes.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

_UNPAIRED_FUNCTION_CALL_OUTPUT = (
    "工具结果未写入（上次运行在回执落盘前中断）。请根据已有上下文继续，不要重复这一次调用。"
)


def _normalized_protocol_token(value: Any) -> str:
    return "-".join(
        part
        for part in "".join(
            ch.lower() if ch.isalnum() else " "
            for ch in str(value or "").strip()
        ).split()
        if part
    )


def model_is_deepseek(model: str, *, aliases: Iterable[str] = ()) -> bool:
    """Recognize DeepSeek even when NewAPI exposes an opaque model id."""
    return any(
        "deepseek" in _normalized_protocol_token(candidate)
        for candidate in (model, *aliases)
    )


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "1", "supported", "enabled"}:
        return True
    if normalized in {"false", "no", "0", "unsupported", "disabled"}:
        return False
    return None


def responses_capability_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> Optional[bool]:
    """Read an explicit Responses capability from common model-catalog shapes.

    OpenAI-compatible gateways do not expose one universal capability schema.  Absence is
    therefore ``None`` (probe safely), never an invented ``False``.  A declared endpoint list,
    however, is authoritative: a non-empty list containing only Chat endpoints means Chat.
    """
    if not isinstance(metadata, Mapping):
        return None

    bool_keys = (
        "supports_responses",
        "supports_responses_api",
        "responses_supported",
        "response_api_supported",
    )
    for key in bool_keys:
        if key in metadata:
            parsed = _optional_bool(metadata.get(key))
            if parsed is not None:
                return parsed

    for container_key in ("capabilities", "features"):
        container = metadata.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in (*bool_keys, "responses", "response_api"):
            if key in container:
                parsed = _optional_bool(container.get(key))
                if parsed is not None:
                    return parsed

    endpoint_fields = (
        "supported_endpoint_types",
        "supported_endpoints",
        "endpoint_types",
        "endpoints",
        "endpoint_type",
    )
    for key in endpoint_fields:
        if key not in metadata:
            continue
        raw = metadata.get(key)
        if isinstance(raw, Mapping):
            values = [name for name, enabled in raw.items() if _optional_bool(enabled) is not False]
        elif isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = [part for part in str(raw or "").replace(",", " ").split() if part]
        tokens = [_normalized_protocol_token(value) for value in values]
        tokens = [token for token in tokens if token]
        if not tokens:
            continue
        if any(
            token in {"responses", "response", "openai-response", "openai-responses"}
            or token.startswith("openai-response-")
            or token.endswith("-responses")
            for token in tokens
        ):
            return True
        # Only endpoint-specific fields are closed-world enumerations. Generic capability
        # lists may omit Responses while listing unrelated features such as vision.
        return False
    return None


def assistant_message(
    content: str,
    *,
    tool_calls=None,
    reasoning: str = "",
    responses_output_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """拼装回传 API 的 assistant 消息（）。

    deepseek-v4 思考模式要求：上一轮若产出过 reasoning_content，多轮/工具回填时
    必须原样带回，否则网关 400「reasoning_content in the thinking mode must be
    passed back」，工具循环整段失败并回退 plain——Word 假完成即由此而来。
    """
    msg: Dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    rc = str(reasoning or "").strip()
    if rc:
        msg["reasoning_content"] = rc
    if responses_output_items:
        # Internal cursor only. messages_to_responses_input replays these native
        # items instead of reconstructing lossy Chat-shaped messages.
        msg["_responses_output_items"] = deepcopy(responses_output_items)
    return msg


def model_uses_responses_transport(
    model: str,
    *,
    aliases: Iterable[str] = (),
    supports_responses: Optional[bool] = None,
) -> bool:
    """Prefer Responses whenever the selected model can use it.

    DeepSeek remains an explicit product-level ``True`` even when the catalog omits
    capability metadata. Other explicit catalog values are honored. Unknown models
    optimistically try Responses; callers may fall back to Chat Completions only when
    the endpoint rejects the request before any output or tool side effect.
    """
    if model_is_deepseek(model, aliases=aliases):
        return True
    if supports_responses is not None:
        return bool(supports_responses)
    return True


class ResponsesUnsupportedError(RuntimeError):
    """The Responses endpoint rejected the protocol before producing any output."""


def _structured_response_error(body: Any) -> tuple[str, str]:
    """Return a gateway's structured ``(code, message)`` without guessing from prose.

    New API may return the OpenAI error envelope either as a decoded mapping or as JSON text.
    A plain exception string is deliberately not treated as structured: classifying every 500
    containing ``not implemented`` as protocol incompatibility would turn real provider outages
    into an unsafe cross-protocol replay.
    """
    candidate = body
    if isinstance(candidate, (bytes, bytearray)):
        candidate = bytes(candidate).decode("utf-8", "ignore")
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "", ""
    if not isinstance(candidate, Mapping):
        return "", ""
    error = candidate.get("error")
    envelope = error if isinstance(error, Mapping) else candidate
    code = str(envelope.get("code") or "").strip().lower()
    message = str(envelope.get("message") or "").strip().lower()
    return code, message


_PROVIDER_POLICY_REJECTION_CODES = frozenset({"sensitive_words_detected"})


def provider_policy_rejection(
    status_code: int, body: Any,
) -> tuple[str, str] | None:
    """Return an exact structured gateway policy rejection, never a prose guess."""
    if int(status_code or 0) < 400:
        return None
    code, message = _structured_response_error(body)
    if code not in _PROVIDER_POLICY_REJECTION_CODES:
        return None
    return code, message


def _exception_http_payload(exc: BaseException) -> tuple[int, Any]:
    response = getattr(exc, "response", None)
    status = int(
        getattr(exc, "status_code", 0)
        or getattr(response, "status_code", 0)
        or 0
    )
    body: Any = getattr(exc, "body", None)
    if body is None and response is not None:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - SDK/httpx response variants are best-effort here
            body = getattr(response, "text", None)
    return status, body


def provider_policy_rejection_from_exception(
    exc: BaseException,
) -> tuple[int, str, str] | None:
    """Find a structured policy rejection through common SDK wrapper chains."""
    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        status, body = _exception_http_payload(current)
        rejection = provider_policy_rejection(status, body)
        if rejection is not None:
            code, message = rejection
            return status, code, message
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return None


def responses_api_is_unsupported(status_code: int, body: Any) -> bool:
    """Conservatively classify a zero-output Responses rejection as protocol-incompatible."""
    status = int(status_code or 0)
    if status in {404, 405, 501}:
        return True
    if status == 500:
        # New API's DeepSeek adaptor reports a deterministic request-conversion gap as 500.
        # This is not a transient provider failure, but only the exact structured signature is
        # safe to short-circuit before the ordinary 5xx reconnect policy.
        code, message = _structured_response_error(body)
        return code == "convert_request_failed" and "not implemented" in message
    if status not in {400, 415, 422}:
        return False
    text = str(body or "").strip().lower()
    if not text:
        return False
    explicit = (
        "not implemented",
        "responses api is not supported",
        "response api is not supported",
        "responses endpoint is not supported",
        "does not support responses",
        "unsupported responses",
        "unknown endpoint",
        "unsupported endpoint",
        "endpoint not found",
    )
    if any(marker in text for marker in explicit):
        return True
    if "messages" in text and "required" in text:
        # A supposed /responses route validating a Chat-only body is a common proxy/adaptor
        # failure mode; rebuilding the untouched request as Chat is safe before output.
        return True
    if any(marker in text for marker in ("unknown parameter", "unsupported parameter")):
        return any(
            f"'{field}'" in text or f'"{field}"' in text or field in text
            for field in ("input", "include", "store", "max_output_tokens")
        )
    return False


def responses_exception_is_unsupported(exc: BaseException) -> bool:
    """Extract status/body from OpenAI SDK or HTTP client exceptions without leaking it."""
    response = getattr(exc, "response", None)
    status = int(
        getattr(exc, "status_code", 0)
        or getattr(response, "status_code", 0)
        or 0
    )
    body: Any = getattr(exc, "body", None)
    if body is None and response is not None:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - SDK/httpx response variants are best-effort here
            body = getattr(response, "text", None)
    if body is None:
        body = str(exc or "")
    if not status:
        match = re.search(
            r"(?:status(?:_code)?|error code)\s*[:=]?\s*(\d{3})",
            str(body or ""),
            re.I,
        )
        status = int(match.group(1)) if match else 0
    return responses_api_is_unsupported(status, body)


def model_stream_exception_is_retryable(exc: BaseException) -> bool:
    """Classify transport/service failures without retrying semantic model errors.

    ``ChatOpenAI`` may expose either httpx exceptions directly or wrap them in OpenAI SDK
    exceptions. Inspect the causal chain, but keep the allow-list narrow: connection/timeout,
    HTTP 408/425, and server-side 5xx. Authentication, quota/rate-limit, invalid request,
    context overflow, and Responses terminal failures intentionally return ``False``.
    """
    if provider_policy_rejection_from_exception(exc) is not None:
        return False

    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))

        if isinstance(current, (asyncio.TimeoutError, httpx.TransportError)):
            return True

        response = getattr(current, "response", None)
        status = int(
            getattr(current, "status_code", 0)
            or getattr(response, "status_code", 0)
            or 0
        )
        if status in {408, 425} or 500 <= status <= 599:
            return True
        if status:
            return False

        class_name = type(current).__name__.lower()
        if class_name in {
            "apiconnectionerror",
            "apitimeouterror",
            "connecterror",
            "connecttimeout",
            "readerror",
            "readtimeout",
            "remoteprotocolerror",
            "writetimeout",
        }:
            return True

        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return False


class ResponsesTerminalError(Exception):
    """A Responses round ended without the sole successful terminal event."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "responses_not_completed")
        super().__init__(message)


def _input_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind in {"text", "input_text"}:
            text = str(block.get("text") or "")
            if text:
                blocks.append({"type": "input_text", "text": text})
            continue
        if kind in {"image_url", "input_image"}:
            raw_url = block.get("image_url")
            if isinstance(raw_url, dict):
                url = str(raw_url.get("url") or "")
                detail = raw_url.get("detail")
            else:
                url = str(raw_url or "")
                detail = block.get("detail")
            if not url:
                continue
            item: dict[str, Any] = {"type": "input_image", "image_url": url}
            if detail:
                item["detail"] = detail
            blocks.append(item)
            continue
        # Keep already-native Responses blocks intact.  Unknown Chat
        # Completions extensions are intentionally not forwarded.
        if kind.startswith("input_"):
            blocks.append(dict(block))
    return blocks


def _responses_call_id(item: Mapping[str, Any]) -> str:
    return str(item.get("call_id") or item.get("id") or "")


def _is_function_call_group_item(item: Mapping[str, Any]) -> bool:
    kind = str(item.get("type") or "")
    return kind in {"function_call", "function_call_output", "reasoning"}


def pair_responses_function_call_outputs(items: Iterable[Dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert a synthetic output before the next user/assistant turn when a call has none.

    Responses rejects the whole request with 400 ``No tool output found for function call``
    if a prior attempt checkpointed native ``function_call`` items and then died before the
    matching ``function_call_output`` landed. Worker recovery would otherwise retry the same
    illegal payload until the backoff window expires, even when the user is not sending a
    new task.
    """
    source = [item for item in items if isinstance(item, dict)]
    have_output: set[str] = set()
    earlier_calls: set[str] = set()
    for item in source:
        call_id = _responses_call_id(item)
        if item.get("type") == "function_call" and call_id:
            earlier_calls.add(call_id)
        elif item.get("type") == "function_call_output" and call_id in earlier_calls:
            have_output.add(call_id)
    result: list[dict[str, Any]] = []
    pending: list[str] = []
    seen_calls: set[str] = set()
    seen_outputs: set[str] = set()
    filled = 0
    dropped = 0

    def flush_pending() -> None:
        nonlocal filled
        for call_id in pending:
            if call_id in have_output:
                continue
            result.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _UNPAIRED_FUNCTION_CALL_OUTPUT,
            })
            have_output.add(call_id)
            seen_outputs.add(call_id)
            filled += 1
        pending.clear()

    for item in source:
        kind = str(item.get("type") or "")
        if kind == "function_call":
            call_id = _responses_call_id(item)
            if call_id:
                if call_id in seen_calls:
                    dropped += 1
                    continue
                seen_calls.add(call_id)
                pending.append(call_id)
            result.append(item)
            continue
        if kind == "function_call_output":
            call_id = _responses_call_id(item)
            if not call_id or call_id not in seen_calls or call_id in seen_outputs:
                # Keep the durable receipt, but never invent a call to replay it.
                # Stateless Responses requires one earlier call for each output.
                dropped += 1
                continue
            seen_outputs.add(call_id)
            pending = [item_id for item_id in pending if item_id != call_id]
            result.append(item)
            continue
        if pending and not _is_function_call_group_item(item):
            flush_pending()
        result.append(item)
    flush_pending()
    if filled:
        logger.warning("paired %s dangling Responses function_call item(s) before provider request", filled)
    if dropped:
        logger.warning("omitted %s orphan/duplicate Responses item(s) before provider request", dropped)
    return result


def messages_to_responses_input(messages: Iterable[Dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the committed chat cursor to stateless Responses input items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id:
                items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(message.get("content") or ""),
                })
            continue

        content = message.get("content")
        native_items = message.get("_responses_output_items")
        native_call_ids: set[str] = set()
        if role == "assistant" and isinstance(native_items, list) and native_items:
            # Stateless Responses continuation must replay the provider-native
            # output items, especially encrypted reasoning.  Reconstructing an
            # assistant message plus function calls would duplicate those items. Message IDs are
            # server-side references and are invalid with store=false, so replay the full message
            # item without its ID. Reasoning is independently replayable only with its encrypted
            # cursor; summary/id-only reasoning is deliberately dropped.
            for native_item in native_items:
                if not isinstance(native_item, dict):
                    continue
                item_type = str(native_item.get("type") or "")
                if not item_type:
                    continue
                if item_type == "reasoning" and not native_item.get(
                    "encrypted_content"
                ):
                    continue
                replay = deepcopy(native_item)
                if item_type == "message":
                    replay.pop("id", None)
                if item_type == "function_call":
                    native_call_ids.add(_responses_call_id(replay))
                items.append(replay)
        elif role in {"system", "developer", "user", "assistant"} and content not in (None, "", []):
            items.append({
                "role": "developer" if role == "system" else role,
                "content": _input_content(content),
            })

        if role != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            call_id = str(call.get("id") or "")
            name = str(fn.get("name") or "")
            if not call_id or not name or call_id in native_call_ids:
                continue
            # Text-protocol recovery can add a local call after the native
            # response was saved. Replay that committed call alongside opaque
            # native items so its real receipt does not become an orphan.
            items.append({
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": str(fn.get("arguments") or "{}"),
            })
    return pair_responses_function_call_outputs(items)


def tools_to_responses(tools: Iterable[Dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Chat Completions function declarations to Responses tools."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            converted.append(dict(tool))
            continue
        fn = tool.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        row: dict[str, Any] = {
            "type": "function",
            "name": name,
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if fn.get("strict") is not None:
            row["strict"] = bool(fn.get("strict"))
        converted.append(row)
    return converted


def response_output_text(payload: Any) -> str:
    """Extract assistant message text from a completed Responses object."""
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "output_text":
                parts.append(str(block.get("text") or ""))
    return "".join(parts).strip()


def _message_text(item: dict[str, Any]) -> str:
    return "".join(
        str(block.get("text") or "")
        for block in item.get("content") or []
        if isinstance(block, dict) and block.get("type") == "output_text"
    )


def _chat_tool_call(item: dict[str, Any], fallback_id: str = "") -> dict[str, Any]:
    return {
        "id": str(item.get("call_id") or item.get("id") or fallback_id),
        "type": "function",
        "function": {
            "name": str(item.get("name") or ""),
            "arguments": str(item.get("arguments") or "{}"),
        },
    }


@dataclass
class ResponsesRoundState:
    """Incrementally collect one streamed Response without reordering its items."""

    item_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    text_parts: dict[str, list[str]] = field(default_factory=dict)
    argument_parts: dict[str, list[str]] = field(default_factory=dict)
    output_items: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    message_rows: list[tuple[int, str, str]] = field(default_factory=list)
    tool_rows: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    native_commentary: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    terminal_event: str = ""
    terminal_status: str = ""
    terminal_error: str = ""

    def ingest(self, event: Any) -> list[dict[str, Any]]:
        """Consume one Responses SSE event and return immediately publishable events."""
        if not isinstance(event, dict):
            return []
        event_type = str(event.get("type") or "")
        emitted: list[dict[str, Any]] = []

        if event_type == "response.output_item.added":
            item = event.get("item") or {}
            item_id = str(item.get("id") or event.get("item_id") or f"output-{event.get('output_index', 0)}")
            self.item_meta[item_id] = dict(item)
            if item.get("type") == "function_call":
                self.argument_parts.setdefault(item_id, [])
            else:
                self.text_parts.setdefault(item_id, [])
            return emitted

        if event_type == "response.output_text.delta":
            item_id = str(event.get("item_id") or f"output-{event.get('output_index', 0)}")
            delta = str(event.get("delta") or "")
            self.text_parts.setdefault(item_id, []).append(delta)
            if delta:
                emitted.append({
                    "type": "output_text_delta",
                    "text": delta,
                    "phase": str((self.item_meta.get(item_id) or {}).get("phase") or ""),
                    "item_id": item_id,
                })
            return emitted

        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            delta = str(event.get("delta") or "")
            if delta:
                self.reasoning_parts.append(delta)
                emitted.append({
                    "type": "reasoning_delta",
                    "text": delta,
                    "item_id": str(event.get("item_id") or ""),
                })
            return emitted

        if event_type == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or f"output-{event.get('output_index', 0)}")
            self.argument_parts.setdefault(item_id, []).append(str(event.get("delta") or ""))
            return emitted

        if event_type == "response.output_item.done":
            item = event.get("item") or {}
            item_id = str(item.get("id") or event.get("item_id") or f"output-{event.get('output_index', 0)}")
            output_index = int(event.get("output_index") or 0)
            self.item_meta[item_id] = dict(item)
            if isinstance(item, dict) and item.get("type"):
                self.output_items[item_id] = (output_index, deepcopy(item))
            if item.get("type") == "message":
                text = _message_text(item) or "".join(self.text_parts.get(item_id) or [])
                phase = str(item.get("phase") or "")
                self.message_rows.append((output_index, phase, text))
                if phase == "commentary" and text.strip():
                    clean = text.strip()
                    self.native_commentary.append(clean)
                    emitted.append({"type": "commentary", "text": clean, "item_id": item_id})
            elif item.get("type") == "function_call":
                row = dict(item)
                if not row.get("arguments"):
                    row["arguments"] = "".join(self.argument_parts.get(item_id) or []) or "{}"
                self.tool_rows.append((output_index, _chat_tool_call(row, item_id)))
            elif item.get("type") == "reasoning":
                texts = []
                for block in (item.get("summary") or []) + (item.get("content") or []):
                    if not isinstance(block, dict):
                        continue
                    text = str(block.get("text") or block.get("reasoning") or "")
                    if text:
                        texts.append(text)
                if texts and not self.reasoning_parts:
                    self.reasoning_parts.extend(texts)
            return emitted

        if event_type == "response.function_call_arguments.done":
            item_id = str(event.get("item_id") or f"output-{event.get('output_index', 0)}")
            meta = dict(self.item_meta.get(item_id) or {})
            meta.update({
                "id": meta.get("id") or item_id,
                "call_id": meta.get("call_id") or item_id,
                "name": event.get("name") or meta.get("name"),
                "arguments": event.get("arguments") or "".join(self.argument_parts.get(item_id) or []) or "{}",
            })
            output_index = int(event.get("output_index") or 0)
            if not any(call.get("id") == str(meta.get("call_id") or item_id) for _, call in self.tool_rows):
                self.tool_rows.append((output_index, _chat_tool_call(meta, item_id)))
            return emitted

        if event_type in {"response.completed", "response.incomplete", "response.failed"}:
            response = event.get("response") or {}
            if isinstance(response.get("usage"), dict):
                self.usage = dict(response["usage"])
            status = str(response.get("status") or "")
            self.terminal_event = event_type
            self.terminal_status = status
            details = response.get("incomplete_details") or {}
            if event_type == "response.incomplete" or status == "incomplete":
                self.finish_reason = str(details.get("reason") or "length")
                self.terminal_error = self.finish_reason
            elif event_type == "response.failed" or status == "failed":
                error = response.get("error") or {}
                self.finish_reason = str(error.get("code") or "failed")
                self.terminal_error = str(
                    error.get("message") or error.get("code") or "Responses request failed"
                )
            else:
                self.finish_reason = "stop"
        return emitted

    def require_completed(self) -> None:
        """Accept only ``response.completed``; DONE/EOF are transport markers."""
        if self.terminal_event == "response.completed" and self.terminal_status in {
            "",
            "completed",
        }:
            return
        if self.terminal_event == "response.incomplete" or self.terminal_status == "incomplete":
            reason = self.terminal_error or self.finish_reason or "unknown"
            raise ResponsesTerminalError(
                "response_incomplete",
                f"模型响应未完整生成（{reason}），本轮不会提交正文或执行工具",
            )
        if self.terminal_event == "response.failed" or self.terminal_status == "failed":
            reason = self.terminal_error or self.finish_reason or "unknown"
            raise ResponsesTerminalError(
                "response_failed",
                f"模型响应失败（{reason}），本轮不会提交正文或执行工具",
            )
        raise ResponsesTerminalError(
            "response_terminal_missing",
            "模型 Responses 流在 response.completed 之前结束，本轮不会提交正文或执行工具",
        )

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        unique: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, call in self.tool_rows:
            unique[str(call.get("id") or f"tool-{index}")] = (index, call)
        return [row for _, row in sorted(unique.values(), key=lambda value: value[0])]

    @property
    def final_text(self) -> str:
        return "".join(
            text for _, phase, text in sorted(self.message_rows, key=lambda row: row[0])
            if phase != "commentary"
        )

    @property
    def assistant_context_text(self) -> str:
        return "".join(text for _, _, text in sorted(self.message_rows, key=lambda row: row[0]))

    @property
    def native_output_items(self) -> list[dict[str, Any]]:
        """Provider-native output cursor, including opaque encrypted reasoning."""
        return [
            deepcopy(item)
            for _, item in sorted(self.output_items.values(), key=lambda row: row[0])
        ]
