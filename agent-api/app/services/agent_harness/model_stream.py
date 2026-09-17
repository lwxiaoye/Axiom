"""Replay-safe SSE transport shared by main and workflow Agent model rounds."""

import asyncio
import json
import logging
import random
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from app.core.config import settings
from .public_errors import SENSITIVE_WORDS_REJECTION_MESSAGE, TerminalRunError
from .responses_protocol import (
    ResponsesUnsupportedError,
    provider_policy_rejection,
    responses_api_is_unsupported,
)

logger = logging.getLogger(__name__)


class ModelStreamRetriesExhausted(RuntimeError):
    """A replay-safe model stream failed after the configured reconnect budget."""


class ModelProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"模型调用失败: {status_code} {body[:200]}")


class ModelProviderPolicyRejected(TerminalRunError):
    """A structured gateway policy decision that replay cannot change."""

    public_message = SENSITIVE_WORDS_REJECTION_MESSAGE
    exclude_run_messages_from_future_context = True

    def __init__(self, status_code: int, code: str, body: str):
        self.status_code = int(status_code or 0)
        self.code = str(code or "provider_policy_rejected")
        self.body = str(body or "")
        super().__init__(self.public_message)


class ModelStreamReplayUnsafe(RuntimeError):
    """A stream failed after a Provider event, so replay could duplicate charge or output."""


class _RetryableModelStreamError(RuntimeError):
    pass


def _model_stream_retry_delay(attempt: int) -> float:
    base = max(
        0.0,
        float(getattr(settings, "MODEL_STREAM_RETRY_BASE_SECONDS", 0.2) or 0.0),
    )
    ceiling = max(
        base,
        float(getattr(settings, "MODEL_STREAM_RETRY_MAX_SECONDS", 3.2) or 0.0),
    )
    raw = min(ceiling, base * (2 ** max(0, int(attempt) - 1)))
    return raw * random.uniform(0.9, 1.1) if raw else 0.0


def _provider_chunk_has_partial_text(chunk: Dict[str, Any]) -> bool:
    event_type = str(chunk.get("type") or "")
    if event_type.endswith("output_text.delta") and str(chunk.get("delta") or ""):
        return True
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if str(delta.get("content") or ""):
            return True
    return False


async def _iter_model_stream_with_reconnect(
    *,
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    use_responses_transport: bool,
    responses_fallback_allowed: bool,
    before_attempt: Optional[Callable[[], Awaitable[Any]]] = None,
    after_attempt: Optional[Callable[[Any, Dict[str, Any]], Awaitable[None]]] = None,
    retry_delay: Optional[Callable[[int], float]] = None,
):
    """Yield parsed SSE chunks and retry only replay-safe transport/service failures.

    The main loop does not execute function calls until a successful terminal event.  A request
    is replay-safe only while it has produced no Provider event: after any event it may already
    be chargeable, and after a text delta it may also be user-visible.  Only the zero-event case
    emits ``retrying`` and resets the consumer's attempt-local parser.
    """
    max_retries = max(
        0,
        int(getattr(settings, "MODEL_STREAM_MAX_RETRIES", 5) or 0),
    )
    retries = 0
    recovery_pending = False
    provider_event_seen = False
    while True:
        terminal_seen = False
        terminal_status = ""
        terminal_usage: Dict[str, Any] = {}
        response_id = ""
        attempt_provider_event_seen = False
        attempt_partial_text_seen = False
        attempt_handle = None
        attempt_finished = False

        async def _finish_attempt(**facts: Any) -> None:
            nonlocal attempt_finished
            if attempt_finished:
                return
            attempt_finished = True
            facts.setdefault("partial_text_seen", attempt_partial_text_seen)
            if after_attempt is None:
                return
            try:
                await after_attempt(attempt_handle, facts)
            except Exception:  # noqa: BLE001 - accounting must remain fail-open
                logger.warning("model attempt terminal audit failed open", exc_info=True)

        try:
            if before_attempt is not None:
                attempt_handle = await before_attempt()
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    raw_body = (await resp.aread()).decode("utf-8", "ignore")
                    body = raw_body[:400]
                    if (
                        use_responses_transport
                        and responses_fallback_allowed
                        and not provider_event_seen
                        and responses_api_is_unsupported(resp.status_code, raw_body)
                    ):
                        await _finish_attempt(
                            terminal_status="incompatible",
                            usage=None,
                            provider_event_seen=False,
                            terminal_seen=True,
                            http_status=resp.status_code,
                            error_code="responses_unsupported",
                            error_detail=body,
                            unknown_provider_charge=False,
                            committed=False,
                        )
                        raise ResponsesUnsupportedError(
                            f"Responses unsupported: {resp.status_code}"
                        )
                    policy_rejection = provider_policy_rejection(
                        resp.status_code, raw_body
                    )
                    if policy_rejection is not None:
                        policy_code, _policy_message = policy_rejection
                        await _finish_attempt(
                            terminal_status="failed",
                            usage=None,
                            provider_event_seen=False,
                            terminal_seen=True,
                            http_status=resp.status_code,
                            error_code=policy_code,
                            error_detail=body,
                            unknown_provider_charge=False,
                            committed=False,
                        )
                        raise ModelProviderPolicyRejected(
                            resp.status_code, policy_code, body
                        )
                    if resp.status_code in {408, 425} or 500 <= resp.status_code <= 599:
                        await _finish_attempt(
                            terminal_status="interrupted",
                            usage=None,
                            provider_event_seen=False,
                            terminal_seen=True,
                            http_status=resp.status_code,
                            error_code="retryable_http_status",
                            error_detail=body,
                            unknown_provider_charge=True,
                            committed=False,
                        )
                        raise _RetryableModelStreamError(
                            f"retryable model service status: {resp.status_code}"
                        )
                    await _finish_attempt(
                        terminal_status="failed",
                        usage=None,
                        provider_event_seen=False,
                        terminal_seen=True,
                        http_status=resp.status_code,
                        error_code="provider_http_error",
                        error_detail=body,
                        unknown_provider_charge=False,
                        committed=False,
                    )
                    raise ModelProviderHTTPError(resp.status_code, body)

                async for raw_line in resp.aiter_lines():
                    line = (raw_line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        terminal_seen = True
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    provider_event_seen = True
                    attempt_provider_event_seen = True
                    attempt_partial_text_seen = (
                        attempt_partial_text_seen or _provider_chunk_has_partial_text(chunk)
                    )
                    direct_usage = chunk.get("usage")
                    if isinstance(direct_usage, dict) and direct_usage:
                        terminal_usage = dict(direct_usage)
                    response_obj = chunk.get("response")
                    if isinstance(response_obj, dict):
                        response_id = str(response_obj.get("id") or response_id)
                        response_usage = response_obj.get("usage")
                        if isinstance(response_usage, dict) and response_usage:
                            terminal_usage = dict(response_usage)
                    elif chunk.get("id"):
                        response_id = str(chunk.get("id") or response_id)
                    if recovery_pending:
                        yield {
                            "kind": "recovered",
                            "attempt": retries,
                            "max_retries": max_retries,
                        }
                        recovery_pending = False
                    event_type = str(chunk.get("type") or "")
                    if event_type in {
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                    }:
                        terminal_seen = True
                        terminal_status = {
                            "response.completed": "completed",
                            "response.incomplete": "incomplete",
                            "response.failed": "failed",
                        }[event_type]
                    if any(
                        choice.get("finish_reason")
                        for choice in (chunk.get("choices") or [])
                        if isinstance(choice, dict)
                    ):
                        terminal_seen = True
                        finish_reasons = [
                            str(choice.get("finish_reason") or "")
                            for choice in (chunk.get("choices") or [])
                            if isinstance(choice, dict) and choice.get("finish_reason")
                        ]
                        terminal_status = (
                            "incomplete"
                            if any(reason in {"length", "content_filter"} for reason in finish_reasons)
                            else "completed"
                        )
                    yield {"kind": "chunk", "chunk": chunk}
                if not terminal_seen:
                    raise _RetryableModelStreamError(
                        "model stream closed before a terminal event"
                    )
                if recovery_pending:
                    yield {
                        "kind": "recovered",
                        "attempt": retries,
                        "max_retries": max_retries,
                    }
                await _finish_attempt(
                    terminal_status=terminal_status or "completed",
                    usage=terminal_usage or None,
                    response_id=response_id,
                    provider_event_seen=attempt_provider_event_seen,
                    terminal_seen=True,
                    committed=(terminal_status or "completed") == "completed",
                )
                return
        except (asyncio.CancelledError, GeneratorExit):
            await _finish_attempt(
                terminal_status="cancelled",
                usage=terminal_usage or None,
                response_id=response_id,
                provider_event_seen=attempt_provider_event_seen,
                terminal_seen=terminal_seen,
                committed=False,
            )
            raise
        except ResponsesUnsupportedError:
            await _finish_attempt(
                terminal_status="incompatible",
                usage=terminal_usage or None,
                response_id=response_id,
                provider_event_seen=attempt_provider_event_seen,
                terminal_seen=terminal_seen,
                unknown_provider_charge=attempt_provider_event_seen,
                committed=False,
            )
            raise
        except (httpx.TransportError, _RetryableModelStreamError) as exc:
            replay_unsafe = attempt_provider_event_seen or attempt_partial_text_seen
            retry_exhausted = retries >= max_retries or replay_unsafe
            await _finish_attempt(
                terminal_status="interrupted",
                usage=terminal_usage or None,
                response_id=response_id,
                provider_event_seen=attempt_provider_event_seen,
                terminal_seen=terminal_seen,
                error_code=exc.__class__.__name__,
                error_detail=str(exc),
                unknown_provider_charge=True,
                committed=False,
                retry_exhausted=retry_exhausted,
            )
            if replay_unsafe:
                # Once the Provider has emitted anything, its side of the request may already
                # be chargeable.  A same-payload reconnect is no longer a zero-side-effect
                # transport retry.  Preserve the original exception after public text so the
                # outer driver can flush that partial exactly once; otherwise use an explicit
                # sentinel that also blocks the non-stream compatibility fallback.
                if attempt_partial_text_seen:
                    raise
                raise ModelStreamReplayUnsafe(
                    "模型流在收到 Provider 事件后中断；为避免重复计费，未自动重放"
                ) from exc
            if retry_exhausted:
                yield {
                    "kind": "failed",
                    "attempt": retries,
                    "max_retries": max_retries,
                }
                raise ModelStreamRetriesExhausted(
                    f"模型连接连续重试 {max_retries} 次仍未恢复，已停止当前执行循环"
                ) from exc
            retries += 1
            delay = (retry_delay or _model_stream_retry_delay)(retries)
            logger.warning(
                "模型流连接中断，将重连 %s/%s（%.2fs）: %s",
                retries,
                max_retries,
                delay,
                str(exc)[:200],
            )
            yield {
                "kind": "retrying",
                "attempt": retries,
                "max_retries": max_retries,
                "delay_seconds": delay,
            }
            recovery_pending = True
            if delay > 0:
                await asyncio.sleep(delay)
        except Exception as exc:
            # A non-retryable failure can still occur after ``before_attempt`` allocated the
            # physical row (for example a client/context-manager error before response headers).
            # Close that exact attempt before the outer driver decides whether a distinct
            # logical fallback is safe.  With no trustworthy terminal usage, charge remains
            # unknown rather than being reported as zero.
            await _finish_attempt(
                terminal_status="failed",
                usage=terminal_usage or None,
                response_id=response_id,
                provider_event_seen=attempt_provider_event_seen,
                terminal_seen=terminal_seen,
                error_code=exc.__class__.__name__,
                error_detail=str(exc),
                unknown_provider_charge=True,
                committed=False,
                retry_exhausted=True,
            )
            raise
