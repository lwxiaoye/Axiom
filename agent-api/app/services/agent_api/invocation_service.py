"""Durable invocation attribution for publisher-owned Agent API requests."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, update

from app.core.database import async_session
from app.core.runtime_db import runtime_session
from app.models import AgentApiInvocation
from app.runtime_models import AgentModelAttemptAudit
from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_harness.model_usage_audit import ExternalAttribution


@dataclass(frozen=True)
class InvocationHandle:
    id: str
    principal: AgentApiPrincipal
    version_id: str
    source: str
    started_monotonic: float
    external_session_id: str = ""

    @property
    def attribution(self) -> ExternalAttribution:
        return ExternalAttribution(
            invocation_id=self.id,
            key_id=self.principal.key_id,
            app_id=self.principal.app_id,
            owner_user_id=self.principal.owner_user_id,
            external_session_id=self.external_session_id,
        )


@dataclass(frozen=True)
class InvocationResult:
    status: str
    http_status: int | None = None
    error_code: str = ""
    error_message: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    usage_known: bool = False
    provider_amount_raw: str | None = None
    provider_amount_unit: str | None = None
    first_byte_at: datetime | None = None


async def _insert_invocation(row: AgentApiInvocation) -> None:
    async with async_session() as session:
        session.add(row)
        await session.commit()


async def _update_invocation(handle: InvocationHandle, values: dict) -> None:
    async with async_session() as session:
        await session.execute(update(AgentApiInvocation).where(
            AgentApiInvocation.id == handle.id, AgentApiInvocation.status == "running"
        ).values(**values))
        await session.commit()


async def _load_runtime_usage(invocation_id: str) -> dict[str, object]:
    """Aggregate only trusted, attributed provider audit rows for this invocation."""
    async with runtime_session() as session:
        rows = (await session.execute(select(AgentModelAttemptAudit).where(
            AgentModelAttemptAudit.external_invocation_id == str(invocation_id or "")[:64],
            AgentModelAttemptAudit.trusted_usage.is_(True),
        ))).scalars().all()
    token_rows = [row for row in rows if row.input_tokens is not None or row.output_tokens is not None]
    amount_rows = [row for row in rows if row.provider_amount_raw is not None]
    result: dict[str, object] = {
        "usage_known": bool(token_rows),
        "input_tokens": sum(int(row.input_tokens or 0) for row in token_rows) if token_rows else None,
        "output_tokens": sum(int(row.output_tokens or 0) for row in token_rows) if token_rows else None,
        "reasoning_tokens": sum(int(row.reasoning_tokens or 0) for row in token_rows) if token_rows else None,
        "provider_amount_raw": None,
        "provider_amount_unit": None,
    }
    units = {str(row.provider_amount_unit or "") for row in amount_rows}
    if amount_rows and len(units) == 1:
        try:
            result["provider_amount_raw"] = format(
                sum((Decimal(str(row.provider_amount_raw)) for row in amount_rows), Decimal("0")), "f",
            )
            result["provider_amount_unit"] = units.pop() or None
        except (InvalidOperation, ValueError):
            # Multiple non-numeric provider amounts cannot be safely combined.
            if len(amount_rows) == 1:
                result["provider_amount_raw"] = str(amount_rows[0].provider_amount_raw)
                result["provider_amount_unit"] = str(amount_rows[0].provider_amount_unit or "") or None
    return result


async def trusted_runtime_usage(handle: InvocationHandle | None) -> dict[str, int] | None:
    """Expose runtime-audited token usage to the OpenAI adapter only when trusted."""
    if handle is None:
        return None
    try:
        usage = await _load_runtime_usage(handle.id)
    except Exception:
        return None
    if not usage.get("usage_known"):
        return None
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "trusted": True,
    }


async def begin_invocation(
    principal: AgentApiPrincipal, version_id: str, source: str, *, request_user: str = "", external_session_id: str = ""
) -> InvocationHandle:
    """Persist running before any model work, so failures retain owner attribution."""
    handle = InvocationHandle(
        id=f"inv_{uuid.uuid4().hex}", principal=principal,
        version_id=str(version_id or "")[:64], source=str(source or "")[:16],
        started_monotonic=time.monotonic(), external_session_id=str(external_session_id or "")[:64],
    )
    await _insert_invocation(AgentApiInvocation(
        id=handle.id, app_id=principal.app_id, version_id=handle.version_id,
        api_key_id=principal.key_id, owner_user_id=principal.owner_user_id,
        external_session_id=handle.external_session_id or None,
        source=handle.source, status="running", request_user=str(request_user or "")[:255] or None,
    ))
    return handle


async def finish_invocation(handle: InvocationHandle, result: InvocationResult) -> None:
    """Finalize once; an unknown provider charge remains NULL rather than zero."""
    try:
        runtime_usage = await _load_runtime_usage(handle.id)
    except Exception:
        runtime_usage = {}
    if runtime_usage:
        result = InvocationResult(
            status=result.status,
            http_status=result.http_status,
            error_code=result.error_code,
            error_message=result.error_message,
            input_tokens=result.input_tokens if result.input_tokens is not None else runtime_usage.get("input_tokens"),
            output_tokens=result.output_tokens if result.output_tokens is not None else runtime_usage.get("output_tokens"),
            reasoning_tokens=result.reasoning_tokens if result.reasoning_tokens is not None else runtime_usage.get("reasoning_tokens"),
            usage_known=result.usage_known or bool(runtime_usage.get("usage_known")),
            provider_amount_raw=result.provider_amount_raw or runtime_usage.get("provider_amount_raw"),
            provider_amount_unit=result.provider_amount_unit or runtime_usage.get("provider_amount_unit"),
            first_byte_at=result.first_byte_at,
        )
    latency_ms = max(0, int((time.monotonic() - handle.started_monotonic) * 1000))
    await _update_invocation(handle, {
        "status": str(result.status or "failed")[:16],
        "http_status": result.http_status,
        "error_code": str(result.error_code or "")[:128] or None,
        "error_message": str(result.error_message or "")[:512] or None,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "usage_known": bool(result.usage_known),
        "provider_amount_raw": result.provider_amount_raw,
        "provider_amount_unit": str(result.provider_amount_unit or "")[:32] or None,
        "first_byte_at": result.first_byte_at,
        "first_byte_latency_ms": latency_ms if result.first_byte_at else None,
        "latency_ms": latency_ms,
        "completed_at": func.now(),
    })
