"""One validated model round, without a planner, tool dispatcher or execution loop."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.services.agents.agent_service import agent_service
from app.services.platform.text_protocol_guard import StreamingProtocolScrubber
from . import model_usage_audit
from .conversation_compact import is_context_overflow_error
from .model_stream import ModelProviderHTTPError, _iter_model_stream_with_reconnect
from .responses_protocol import (
    ResponsesRoundState, ResponsesTerminalError, ResponsesUnsupportedError,
    messages_to_responses_input, model_uses_responses_transport, tools_to_responses,
    assistant_message as build_assistant_message,
)


class ContextWindowExceeded(RuntimeError):
    pass


@dataclass
class FunctionRound:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    text_deltas: list[str] = field(default_factory=list)
    native_output: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    logical_call_id: str = ""
    reasoning_content: str = ""

    def assistant_message(self) -> dict[str, Any]:
        return build_assistant_message(self.content, tool_calls=self.tool_calls,
                                       reasoning=self.reasoning_content, responses_output_items=self.native_output)


class FunctionRoundClient:
    """Freeze selection per execution; negotiate only before the first successful round.

    Transport retry/terminal policy is shared with main Agent. Text is staged until the
    terminal is validated: Chat Completions cannot identify a final answer from its first
    content delta (the same round may still produce tool calls).
    """

    def __init__(self, *, model: str, api_key: str, ctx: Any, parent_logical_call_id: str = "",
                 purpose: str = "subagent_model", purpose_detail: str = "", scope_key: str = ""):
        self.model = model
        self.api_key = api_key
        capability = agent_service.model_responses_capability(model, api_key)
        self.transport = "responses" if model_uses_responses_transport(
            model, aliases=agent_service.model_transport_aliases(model, api_key),
            supports_responses=capability,
        ) else "chat_completions"
        self.fallback_allowed = self.transport == "responses" and capability is None
        self.owner = {
            "run_id": str(getattr(ctx, "audit_run_id", "") or getattr(ctx, "run_id", "") or ""),
            "thread_id": str(getattr(ctx, "thread_id", "") or ""),
            "root_run_id": str(getattr(ctx, "audit_root_run_id", "") or ""),
            "parent_tool_call_id": str(getattr(ctx, "audit_parent_tool_call_id", "") or ""),
            "parent_logical_call_id": str(parent_logical_call_id or getattr(ctx, "audit_parent_logical_call_id", "") or ""),
        }
        self.purpose = str(getattr(ctx, "audit_purpose", "") or purpose)
        self.purpose_detail = purpose_detail
        self.scope_key = scope_key or self.purpose
        self.execution_segment = str(getattr(ctx, "audit_execution_segment", "") or "")
        self.rounds = 0
        self.usage: dict[str, int] = {}

    def payload(self, messages: list[dict], tools: list[dict], *, temperature=None,
                max_tokens: int = 4096, top_p=None, stop=None) -> dict:
        responses = self.transport == "responses"
        payload: dict[str, Any] = {"model": self.model, "stream": True}
        if responses:
            payload.update(input=messages_to_responses_input(messages), store=False,
                           max_output_tokens=max_tokens, include=["reasoning.encrypted_content"])
        else:
            payload.update(messages=[{k: v for k, v in m.items() if not k.startswith("_")}
                                     for m in messages], max_tokens=max_tokens,
                           stream_options={"include_usage": True})
        if tools:
            payload.update(tools=tools_to_responses(tools) if responses else tools, tool_choice="auto")
        if isinstance(temperature, (int, float)):
            payload["temperature"] = float(temperature)
        if isinstance(top_p, (int, float)) and 0 < top_p <= 1:
            payload["top_p"] = float(top_p)
        if not responses and isinstance(stop, str) and stop.strip():
            payload["stop"] = [part for part in stop.split("|") if part]
        return payload

    async def complete(self, client: httpx.AsyncClient, messages: list[dict], tools: list[dict],
                       **options) -> FunctionRound:
        while True:  # At most one zero-output protocol negotiation, not a model/tool loop.
            try:
                deadline = asyncio.timeout(120)
                async with deadline:
                    result = await self._complete(client, messages, tools, deadline_guard=deadline, **options)
                self.fallback_allowed = False
                self.rounds += 1
                for key, value in result.usage.items():
                    if isinstance(value, int):
                        self.usage[key] = self.usage.get(key, 0) + value
                return result
            except ResponsesUnsupportedError:
                if not self.fallback_allowed:
                    raise
                self.transport = "chat_completions"
                self.fallback_allowed = False

    async def _complete(self, client, messages, tools, *, deadline_guard=None, **options) -> FunctionRound:
        payload = self.payload(messages, tools, **options)
        responses = self.transport == "responses"
        logical = await model_usage_audit.begin_logical_call(
            **self.owner, model=self.model, transport=self.transport, purpose=self.purpose,
            purpose_detail=(self.purpose_detail or f"function_call_loop:step_{self.rounds + 1}")[:200],
            scope_key=self.scope_key[:255], provider_api_key=self.api_key,
        )
        current_attempt = None
        pending_terminal = None
        response_state = ResponsesRoundState()
        deltas: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict] = {}
        raw_response_arguments: dict[str, str] = {}
        response_deltas: list[dict] = []
        usage: dict = {}
        finish_reason = ""
        scrubber = StreamingProtocolScrubber()

        async def before_attempt():
            nonlocal current_attempt
            current_attempt = await model_usage_audit.begin_attempt(
                logical, wire_payload=payload, attempt_kind="http_responses_stream" if responses else "http_chat_stream",
                execution_segment=self.execution_segment,
            )
            return current_attempt

        async def after_attempt(handle, facts):
            nonlocal pending_terminal
            # Successful transport alone is not a committed model round. Validate the protocol
            # terminal, call envelopes and final text before closing this exact audit attempt.
            if facts.get("committed") or facts.get("terminal_status") == "cancelled":
                pending_terminal = (handle, facts)
            else:
                await model_usage_audit.finish_attempt(handle, **facts)

        try:
            stream = _iter_model_stream_with_reconnect(
                client=client, url=f"{settings.NEWAPI_BASE_URL.rstrip('/')}/{'responses' if responses else 'chat/completions'}",
                payload=payload, headers={"Authorization": f"Bearer {self.api_key}"},
                use_responses_transport=responses, responses_fallback_allowed=self.fallback_allowed,
                before_attempt=before_attempt, after_attempt=after_attempt,
            )
            async with aclosing(stream):
                async for envelope in stream:
                    if envelope["kind"] != "chunk":
                        continue
                    chunk = envelope["chunk"]
                    if chunk.get("error"):
                        raise ResponsesTerminalError("provider_error", str(chunk["error"])[:400])
                    if responses:
                        response_deltas.extend(event for event in response_state.ingest(chunk) if event["type"] == "output_text_delta")
                        item = chunk.get("item") or {}
                        if chunk.get("type") == "response.output_item.done" and item.get("type") == "function_call":
                            if item.get("status") not in (None, "completed"):
                                raise ResponsesTerminalError("incomplete_tool_item", "工具调用项未完整生成")
                            raw_response_arguments[str(item.get("call_id") or item.get("id") or "")] = str(
                                item.get("arguments") or "".join(response_state.argument_parts.get(str(item.get("id") or ""), []))
                            )
                        elif chunk.get("type") == "response.function_call_arguments.done":
                            item_id = str(chunk.get("item_id") or "")
                            meta = response_state.item_meta.get(item_id) or {}
                            raw_response_arguments[str(meta.get("call_id") or item_id)] = str(
                                chunk.get("arguments") or "".join(response_state.argument_parts.get(item_id, []))
                            )
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        reasoning.append(str(delta["reasoning_content"]))
                    text = scrubber.feed(str(delta.get("content") or ""))
                    if text:
                        deltas.append(text)
                    for part in delta.get("tool_calls") or []:
                        index = int(part.get("index") or 0)
                        call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if part.get("id"):
                            call["id"] = part["id"]
                        function = part.get("function") or {}
                        for key in ("name", "arguments"):
                            call["function"][key] += str(function.get(key) or "")
            if responses:
                response_state.require_completed()
                tool_calls = response_state.tool_calls
                for call in tool_calls:
                    call["function"]["arguments"] = raw_response_arguments.get(call["id"], "")
                text = response_state.assistant_context_text if tool_calls else response_state.final_text
                safe = scrubber.feed(text) + scrubber.flush()
                # Native item phases are retained for the next Responses request, but never
                # exposed as final output when this is a tool round.
                result = FunctionRound(safe, tool_calls, [safe] if safe else [],
                                       response_state.native_output_items, response_state.usage)
                if not tool_calls:
                    final_parts = [event["text"] for event in response_deltas
                                   if (response_state.item_meta.get(event["item_id"]) or {}).get("phase") != "commentary"]
                    if "".join(final_parts) == safe:
                        result.text_deltas = final_parts
            else:
                tail = scrubber.flush()
                if tail:
                    deltas.append(tail)
                if finish_reason not in {"stop", "tool_calls"}:
                    raise ResponsesTerminalError(
                        "response_incomplete" if finish_reason in {"length", "content_filter"} else "response_terminal_missing",
                        f"模型响应未完整结束（{finish_reason or 'missing finish_reason'}），不执行工具或提交终答",
                    )
                result = FunctionRound("".join(deltas), [calls[i] for i in sorted(calls)], deltas, usage=usage)
                result.reasoning_content = "".join(reasoning)
                if finish_reason == "tool_calls" and not result.tool_calls:
                    raise ResponsesTerminalError("missing_tool_calls", "模型工具终态缺少完整调用")
            ids = [call.get("id") for call in result.tool_calls]
            if any(not value for value in ids) or len(ids) != len(set(ids)) or any(
                not (call.get("function") or {}).get("name") for call in result.tool_calls
            ):
                raise ResponsesTerminalError("invalid_tool_envelope", "模型工具调用缺少或重复调用 ID/名称")
            if not result.tool_calls and (not result.content.strip() or scrubber.leaked):
                raise ResponsesTerminalError("invalid_final_answer", "模型没有生成有效的最终回答")
            if pending_terminal:
                await model_usage_audit.finish_attempt(pending_terminal[0], **pending_terminal[1])
            result.logical_call_id = str(getattr(logical, "logical_call_id", "") or "")
            await model_usage_audit.finish_logical_call(
                logical, terminal_status="completed", committed=True,
                selected_attempt_id=str(getattr(current_attempt, "attempt_id", "") or ""),
            )
            return result
        except BaseException as exc:
            timed_out = isinstance(exc, asyncio.CancelledError) and deadline_guard is not None and deadline_guard.expired()
            cancelled = isinstance(exc, asyncio.CancelledError) and not timed_out
            if pending_terminal:
                facts = {**pending_terminal[1], "terminal_status": "cancelled" if cancelled else "failed", "committed": False,
                         "error_code": "model_request_timeout" if timed_out else getattr(exc, "code", type(exc).__name__)}
                await model_usage_audit.finish_attempt(pending_terminal[0], **facts)
            await model_usage_audit.finish_logical_call(
                logical, terminal_status="cancelled" if cancelled else "failed",
                committed=False,
            )
            if timed_out:
                raise TimeoutError("单次模型请求超时，未提交终答或执行本轮工具") from exc
            if (isinstance(exc, ModelProviderHTTPError) and exc.status_code in {400, 413, 422}
                    and is_context_overflow_error(exc.status_code, exc.body.replace("max_tokens", ""))):
                raise ContextWindowExceeded(str(exc)) from exc
            raise
