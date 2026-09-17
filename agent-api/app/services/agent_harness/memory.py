"""Long-term memory candidate governance independent of its storage backend."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class MemoryGrounding(str, Enum):
    USER_STATED = "user_stated"
    TOOL_VERIFIED = "tool_verified"
    INFERRED = "inferred"


class MemoryStability(str, Enum):
    STABLE = "stable"
    TEMPORARY = "temporary"


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    memory_type: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=2_000)
    grounding: MemoryGrounding
    stability: MemoryStability
    source_thread_id: str | None = None
    source_event_ids: tuple[str, ...] = ()
    confidence: int = Field(default=80, ge=0, le=100)
    sensitive: bool = False
    expires_at: datetime | None = None
    explicit_user_request: bool = False


class MemoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason_code: str
    memory_id: str | None = None


MemoryWriter = Callable[[MemoryCandidate], Awaitable[str | None]]


class MemoryController:
    """Turn model suggestions into writes only after deterministic governance."""

    def review(self, candidate: MemoryCandidate) -> MemoryDecision:
        if candidate.sensitive:
            return MemoryDecision(accepted=False, reason_code="sensitive_memory")
        if candidate.stability is MemoryStability.TEMPORARY:
            return MemoryDecision(accepted=False, reason_code="temporary_task_state")
        if candidate.grounding is MemoryGrounding.INFERRED:
            return MemoryDecision(accepted=False, reason_code="unverified_inference")
        if not candidate.explicit_user_request and not candidate.source_thread_id:
            return MemoryDecision(accepted=False, reason_code="source_missing")
        if candidate.confidence < 70:
            return MemoryDecision(accepted=False, reason_code="confidence_too_low")
        return MemoryDecision(accepted=True, reason_code="accepted")

    async def commit(self, candidate: MemoryCandidate, writer: MemoryWriter) -> MemoryDecision:
        decision = self.review(candidate)
        if not decision.accepted:
            return decision
        memory_id = await writer(candidate)
        if not memory_id:
            return MemoryDecision(accepted=False, reason_code="memory_store_failed")
        return MemoryDecision(accepted=True, reason_code="stored", memory_id=memory_id)
