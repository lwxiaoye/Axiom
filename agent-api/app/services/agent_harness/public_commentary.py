"""Small non-reasoning model calls for user-visible Harness commentary."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.core.config import settings
from app.services.agent_harness import model_usage_audit
from app.services.agent_harness.responses_protocol import response_output_text
from app.services.chat import turn_finalizer


# This request sits before the authoritative model stream for reasoning-first providers.
# It is optional UX and therefore owns exactly one physical Provider attempt.  A malformed or
# incomplete sentence is cheaper and safer to omit than to replay with a larger output budget.
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 1

logger = logging.getLogger(__name__)

_COMPLETE_END_RE = re.compile(r"[\u3002！？!?\.](?:[\u201d’\"'）\)\]\u3011》〉]*)$")


def _completed_public_text(raw: str, *, max_chars: int) -> str:
    """Return only a visibly complete sentence, never a character-sliced fragment."""
    text = turn_finalizer.scrub_public_runtime_text(raw).strip()
    if not text:
        return ""
    limit = max(32, int(max_chars or 360))
    if len(text) > limit:
        bounded = text[:limit]
        ends = list(re.finditer(r"[\u3002！？!?\.]", bounded))
        if not ends:
            return ""
        text = bounded[:ends[-1].end()].rstrip()
    if not _COMPLETE_END_RE.search(text):
        return ""
    return text


def _response_is_complete(payload: Any) -> tuple[bool, str]:
    """Keep optional-provider compatibility while rejecting explicit incomplete Responses."""
    if not isinstance(payload, dict):
        return False, "invalid_payload"
    status = str(payload.get("status") or "").strip().lower()
    details = payload.get("incomplete_details") or {}
    reason = str(details.get("reason") or status or "unknown") if isinstance(details, dict) else status
    if payload.get("error") or details or (status and status != "completed"):
        return False, reason or "incomplete"
    return True, status or "status_omitted"


async def generate_public_commentary(
    *,
    model: str,
    api_key: str,
    developer_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 180,
    max_chars: int = 360,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
    purpose: str = "public_preamble",
    purpose_detail: str = "",
) -> str:
    """Return one bounded model-authored commentary item, or an empty fallback."""
    if not model or not api_key or not str(developer_prompt or "").strip():
        return ""
    body: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "developer", "content": str(developer_prompt).strip()},
            {"role": "user", "content": str(user_prompt or "写一条公开进度说明。").strip()},
        ],
        "stream": False,
        "store": False,
        "max_output_tokens": max(32, int(max_output_tokens or 180)),
        # DeepSeek Responses uses the Responses reasoning contract.  ``thinking`` belongs to
        # Chat Completions and may be silently ignored on /responses, leaving hidden reasoning
        # enabled for this otherwise tiny public sentence.
        "reasoning": {"effort": "none"},
    }

    async def _request(
        *, request_body: dict[str, Any], request_timeout: float,
    ) -> tuple[Any, dict[str, Any]]:
        bounded_timeout = max(0.1, float(request_timeout))
        timeout = httpx.Timeout(
            bounded_timeout,
            connect=min(3.0, bounded_timeout),
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.NEWAPI_BASE_URL.rstrip('/')}/responses",
                json=request_body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            try:
                payload = response.json()
            except Exception:  # noqa: BLE001 - HTTP status still needs an audit terminal
                payload = {}
            return response, payload if isinstance(payload, dict) else {}

    # Respect the caller's explicit output ceiling.  An incomplete sentence is deliberately
    # discarded below instead of being repaired by a second paid request.
    first_budget = max(32, int(max_output_tokens or 180))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, float(timeout_seconds))
    logical_call = await model_usage_audit.begin_logical_call(
        run_id=str(run_id or ""),
        thread_id=str(thread_id or ""),
        root_run_id=str(root_run_id or ""),
        model=str(model or ""),
        transport="responses",
        purpose=str(purpose or "public_preamble"),
        purpose_detail=str(purpose_detail or ""),
        provider_api_key=api_key,
    )
    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - loop.time()
        if remaining <= 0.05:
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="not_sent",
                committed=False,
            )
            break
        output_tokens = first_budget
        request_body = dict(body)
        request_body["max_output_tokens"] = output_tokens
        attempt_handle = await model_usage_audit.begin_attempt(
            logical_call,
            wire_payload=request_body,
            attempt_kind="initial",
            legacy_compatible=False,
        )
        remaining = deadline - loop.time()
        if remaining <= 0.05:
            await model_usage_audit.finish_attempt(
                attempt_handle,
                terminal_status="not_sent",
                provider_event_seen=False,
                terminal_seen=False,
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="not_sent",
                selected_attempt_id=str(
                    getattr(attempt_handle, "attempt_id", "") or ""
                ),
                committed=False,
            )
            return ""
        try:
            response, payload = await asyncio.wait_for(
                _request(
                    request_body=request_body,
                    request_timeout=remaining,
                ),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            await model_usage_audit.finish_attempt(
                attempt_handle,
                terminal_status="cancelled",
                provider_event_seen=False,
                terminal_seen=False,
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="cancelled",
                selected_attempt_id=str(getattr(attempt_handle, "attempt_id", "") or ""),
                committed=False,
            )
            raise
        except Exception:  # noqa: BLE001 - commentary never blocks the authoritative task
            await model_usage_audit.finish_attempt(
                attempt_handle,
                terminal_status="failed",
                provider_event_seen=False,
                terminal_seen=False,
                error_code="request_failed",
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="failed",
                selected_attempt_id=str(getattr(attempt_handle, "attempt_id", "") or ""),
                committed=False,
            )
            logger.debug(
                "public commentary request failed model=%s attempt=%s",
                model, attempt + 1, exc_info=True,
            )
            return ""
        status_code = int(getattr(response, "status_code", 200) or 200)
        usage = payload.get("usage") if isinstance(payload, dict) else None
        response_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        if status_code >= 400:
            await model_usage_audit.finish_attempt(
                attempt_handle,
                terminal_status="failed",
                usage=usage,
                response_id=response_id,
                provider_event_seen=True,
                terminal_seen=True,
                http_status=status_code,
                error_code="http_error",
                error_detail=str(getattr(response, "text", "") or "")[:2_000],
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="failed",
                selected_attempt_id=str(getattr(attempt_handle, "attempt_id", "") or ""),
                committed=False,
            )
            logger.debug(
                "public commentary HTTP failure model=%s status=%s",
                model,
                status_code,
            )
            return ""
        response_complete, response_reason = _response_is_complete(payload)
        raw = response_output_text(payload)
        text = _completed_public_text(raw, max_chars=max_chars) if response_complete else ""
        response_status = str(payload.get("status") or "").strip().lower()
        terminal_status = (
            "failed"
            if payload.get("error") or response_status == "failed"
            else "incomplete"
            if not response_complete
            else "completed"
        )
        await model_usage_audit.finish_attempt(
            attempt_handle,
            terminal_status=terminal_status,
            usage=usage,
            response_id=response_id,
            provider_event_seen=True,
            # A non-streaming HTTP response is itself the terminal Provider event even when a
            # compatibility gateway omits the optional top-level ``status`` field.
            terminal_seen=True,
            http_status=status_code,
            error_code=(response_reason if terminal_status != "completed" else ""),
            committed=bool(text),
        )
        await model_usage_audit.finish_logical_call(
            logical_call,
            terminal_status=(terminal_status if terminal_status != "completed" else "completed"),
            selected_attempt_id=str(getattr(attempt_handle, "attempt_id", "") or ""),
            committed=bool(text),
        )
        if text:
            return text
        logger.warning(
            "public commentary rejected model=%s attempt=%s response=%s text_chars=%s",
            model, attempt + 1, response_reason, len(raw),
        )
    return ""
