from __future__ import annotations

import asyncio

from app.services.agent_harness import (
    MemoryCandidate,
    MemoryController,
    MemoryGrounding,
    MemoryStability,
)


def _candidate(**changes):
    values = {
        "candidate_id": "c1",
        "user_id": "u1",
        "memory_type": "preference",
        "content": "用户偏好简洁回答",
        "grounding": MemoryGrounding.USER_STATED,
        "stability": MemoryStability.STABLE,
        "source_thread_id": "t1",
        "confidence": 80,
    }
    values.update(changes)
    return MemoryCandidate(**values)


def test_inferred_sensitive_and_temporary_candidates_are_rejected():
    controller = MemoryController()
    assert controller.review(_candidate(grounding=MemoryGrounding.INFERRED)).reason_code == "unverified_inference"
    assert controller.review(_candidate(sensitive=True)).reason_code == "sensitive_memory"
    assert controller.review(_candidate(stability=MemoryStability.TEMPORARY)).reason_code == "temporary_task_state"


def test_only_approved_candidate_reaches_store():
    writes = []

    async def writer(candidate):
        writes.append(candidate.content)
        return "m1"

    result = asyncio.run(MemoryController().commit(_candidate(), writer))
    assert result.accepted is True and result.memory_id == "m1"
    assert writes == ["用户偏好简洁回答"]


def test_store_failure_never_reports_memory_as_saved():
    async def writer(_candidate):
        return None

    result = asyncio.run(MemoryController().commit(_candidate(), writer))
    assert result.accepted is False
    assert result.reason_code == "memory_store_failed"
