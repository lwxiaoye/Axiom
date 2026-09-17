from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_api import invocation_service
from app.services.agent_harness import model_usage_audit as audit


@pytest.mark.asyncio
async def test_begin_and_finish_invocation_preserve_unknown_charge(monkeypatch):
    """Replacing unknown provider charge with a zero amount must fail this test."""
    persisted = []
    finished = []
    principal = AgentApiPrincipal("key-a", "app-a", "publisher-a", "qza_test")

    async def _start(row):
        persisted.append(row)

    async def _finish(handle, values):
        finished.append((handle, values))

    monkeypatch.setattr(invocation_service, "_insert_invocation", _start)
    monkeypatch.setattr(invocation_service, "_update_invocation", _finish)

    handle = await invocation_service.begin_invocation(
        principal, "version-a", "openai_api", external_session_id="exts_a",
    )
    await invocation_service.finish_invocation(
        handle,
        invocation_service.InvocationResult(
            status="completed",
            http_status=200,
            input_tokens=12,
            output_tokens=3,
            usage_known=False,
            provider_amount_raw=None,
        ),
    )

    assert persisted[0].status == "running"
    assert persisted[0].owner_user_id == "publisher-a"
    assert persisted[0].external_session_id == "exts_a"
    assert handle.attribution == audit.ExternalAttribution(
        invocation_id=handle.id,
        key_id="key-a",
        app_id="app-a",
        owner_user_id="publisher-a",
        external_session_id="exts_a",
    )
    assert finished[0][1]["usage_known"] is False
    assert finished[0][1]["provider_amount_raw"] is None


@pytest.mark.asyncio
async def test_finish_invocation_aggregates_trusted_runtime_usage(monkeypatch):
    """Publisher usage must be projected from the Runtime audit rather than invented as zero."""
    values = []
    principal = AgentApiPrincipal("key-a", "app-a", "publisher-a", "qza_test")

    async def _start(_row):
        return None

    async def _update(_handle, row):
        values.append(row)

    async def _runtime_usage(_invocation_id):
        return {
            "usage_known": True,
            "input_tokens": 12,
            "output_tokens": 3,
            "reasoning_tokens": 1,
            "provider_amount_raw": "0.005",
            "provider_amount_unit": "USD",
        }

    monkeypatch.setattr(invocation_service, "_insert_invocation", _start)
    monkeypatch.setattr(invocation_service, "_update_invocation", _update)
    monkeypatch.setattr(invocation_service, "_load_runtime_usage", _runtime_usage)
    handle = await invocation_service.begin_invocation(principal, "version-a", "embed")
    await invocation_service.finish_invocation(handle, invocation_service.InvocationResult(status="success", http_status=200))

    assert values[0]["usage_known"] is True
    assert values[0]["input_tokens"] == 12
    assert values[0]["output_tokens"] == 3
    assert values[0]["provider_amount_raw"] == "0.005"


@pytest.mark.asyncio
async def test_logical_and_physical_audits_copy_external_attribution(monkeypatch):
    """Dropping attribution from either runtime audit row must fail this test."""
    added = []

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class PreviousResult:
        class Scalars:
            @staticmethod
            def first():
                return None

        def scalars(self):
            return self.Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement, _params=None):
            rendered = str(statement)
            if "INSERT INTO agent_model_run_counters" in rendered:
                return ScalarResult(1)
            if "UPDATE agent_model_logical_calls" in rendered:
                return ScalarResult(1)
            return PreviousResult()

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    attribution = audit.ExternalAttribution("inv-a", "key-a", "app-a", "publisher-a", "exts_a")
    logical = await audit.begin_logical_call(
        run_id="run-a",
        model="model-a",
        transport="openai",
        purpose="workflow_node",
        external_attribution=attribution,
    )
    attempt = await audit.begin_attempt(logical, wire_payload={"model": "model-a"})

    assert logical is not None and attempt is not None
    logical_row = next(row for row in added if type(row).__name__ == "AgentModelLogicalCall")
    attempt_row = next(row for row in added if type(row).__name__ == "AgentModelAttemptAudit")
    for row in (logical_row, attempt_row):
        assert row.external_invocation_id == "inv-a"
        assert row.external_key_id == "key-a"
        assert row.external_app_id == "app-a"
        assert row.external_owner_user_id == "publisher-a"
        assert row.external_session_id == "exts_a"
