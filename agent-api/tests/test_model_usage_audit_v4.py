from __future__ import annotations

import json

import pytest

from app.services.agent_harness import model_usage_audit as audit


def test_usage_normalization_preserves_reasoning_and_amount_text():
    usage = audit.normalize_model_usage({
        "input_tokens": 120,
        "output_tokens": 30,
        "input_tokens_details": {"cached_tokens": 90},
        "output_tokens_details": {"reasoning_tokens": 17},
        "provider_amount_raw": "0.0012300",
        "provider_amount_unit": "USD",
    })
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.reasoning_tokens == 17
    assert usage.cache_read_tokens == 90
    assert usage.cache_miss_tokens == 30
    assert usage.cache_miss_source == "derived"
    assert usage.amount_raw == "0.0012300"
    assert usage.provider_amount_raw == "0.0012300"
    assert usage.provider_amount_unit == "USD"


def test_usage_normalization_does_not_invent_missing_values():
    usage = audit.normalize_model_usage({"output_tokens": 0})
    assert usage.input_tokens is None
    assert usage.output_tokens == 0
    assert usage.reasoning_tokens is None
    assert usage.cache_read_tokens is None
    assert usage.cache_miss_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.amount_raw is None
    assert usage.has_usage is True


def test_deepseek_reported_cache_usage_wins_over_derivation():
    usage = audit.normalize_model_usage({
        "prompt_tokens": 100,
        "completion_tokens": 4,
        "prompt_cache_hit_tokens": 75,
        "prompt_cache_miss_tokens": 25,
    })
    assert usage.cache_read_tokens == 75
    assert usage.cache_miss_tokens == 25
    assert usage.cache_miss_source == "reported"
    assert usage.usage_schema == "deepseek"


def test_deepseek_explicit_hit_can_derive_missing_miss():
    usage = audit.normalize_model_usage({
        "prompt_tokens": 100,
        "prompt_cache_hit_tokens": 75,
    })
    assert usage.cache_read_tokens == 75
    assert usage.cache_miss_tokens == 25
    assert usage.cache_miss_source == "derived"


def test_nested_provider_amount_keeps_exact_text_and_unit():
    usage = audit.normalize_model_usage({
        "usage_metadata": {
            "amount": {"value": "0.0012300", "unit": "USD"},
        },
    })
    assert usage.amount_raw == "0.0012300"
    assert usage.provider_amount_unit == "USD"


def test_sibling_usage_metadata_amount_survives_response_extraction():
    response = {
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "usage_metadata": {
            "amount": {"value": "0.0012300", "unit": "USD"},
        },
    }
    extracted = audit.provider_usage_from_response(response)
    usage = audit.normalize_model_usage(extracted)

    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
    assert usage.amount_raw == "0.0012300"
    assert usage.provider_amount_unit == "USD"


def test_raw_response_normalization_keeps_sibling_usage_metadata_amount():
    usage = audit.normalize_model_usage({
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        "usage_metadata": {"amount": "1e-7", "unit": "credits"},
    })

    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
    assert usage.amount_raw == "1e-7"
    assert usage.provider_amount_unit == "credits"


def test_model_input_sanitizer_keeps_public_reasoning_controls_only():
    from app.services.chat import model_input_audit

    payload = model_input_audit._sanitize({
        "reasoning": {"effort": "none", "summary": "auto", "secret": "drop"},
        "thinking": {"type": "disabled", "internal": "drop"},
        "messages": [{
            "role": "assistant",
            "content": "visible",
            "reasoning": {"content": "hidden"},
            "reasoning_content": "hidden-too",
        }],
    })
    assert payload["reasoning"] == {"effort": "none", "summary": "auto"}
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning" not in payload["messages"][0]
    assert "reasoning_content" not in payload["messages"][0]


@pytest.mark.asyncio
async def test_begin_logical_call_is_fail_open_for_missing_db_or_bad_purpose(monkeypatch):
    monkeypatch.setattr(audit, "runtime_session", lambda: None)
    assert await audit.begin_logical_call(
        run_id="run-1", model="m", transport="responses"
    ) is None

    class Factory:
        pass

    monkeypatch.setattr(audit, "runtime_session", lambda: Factory)
    assert await audit.begin_logical_call(
        run_id="run-1", model="m", transport="responses", purpose="uncontrolled"
    ) is None


@pytest.mark.asyncio
async def test_attempt_allocates_db_sequences_and_legacy_input_in_one_commit(monkeypatch):
    captured = {"rows": []}

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
                return ScalarResult(7)
            if "UPDATE agent_model_logical_calls" in rendered:
                return ScalarResult(2)
            return PreviousResult()

        def add(self, row):
            captured["rows"].append(row)

        async def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    logical = audit.ModelLogicalCallHandle(
        logical_call_id="lg-1",
        run_id="run-1",
        root_run_id="root-1",
        thread_id="thread-1",
        parent_logical_call_id="",
        model="deepseek-v4",
        transport="responses",
        purpose="main_loop",
        scope_key="main-loop-scope",
    )
    handle = await audit.begin_attempt(
        logical,
        wire_payload={
            "input": [{"role": "user", "content": "private body"}],
            "reasoning": {"effort": "none"},
        },
        attempt_kind="responses_primary",
    )
    assert handle is not None
    assert handle.run_sequence == 7
    assert handle.run_request_sequence == 7
    assert handle.attempt_index == 2
    assert captured["committed"] is True
    assert [type(row).__name__ for row in captured["rows"]] == [
        "AgentModelAttemptAudit",
        "AgentModelInputAudit",
    ]
    attempt_row = captured["rows"][0]
    assert attempt_row.reasoning_tokens is None
    assert attempt_row.wire_payload["schema"] == "provider_payload_fingerprint_v1"
    assert attempt_row.wire_payload["controls"]["reasoning"] == {"effort": "none"}
    assert len(handle.semantic_payload_hash) == 64
    assert handle.semantic_payload_hash == handle.wire_payload_hash
    for value in (
        attempt_row.logical_payload,
        attempt_row.wire_payload,
        attempt_row.source_manifest,
        attempt_row.prefix_diagnostics,
        captured["rows"][1].visible_payload,
    ):
        assert "private body" not in json.dumps(value)


@pytest.mark.asyncio
async def test_wire_hash_covers_hidden_provider_items_without_persisting_them(monkeypatch):
    captured = []

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
        sequence = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement, _params=None):
            rendered = str(statement)
            if "INSERT INTO agent_model_run_counters" in rendered:
                Session.sequence += 1
                return ScalarResult(Session.sequence)
            if "UPDATE agent_model_logical_calls" in rendered:
                return ScalarResult(Session.sequence)
            return PreviousResult()

        def add(self, row):
            captured.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    logical = audit.ModelLogicalCallHandle(
        logical_call_id="lg-hidden",
        run_id="run-hidden",
        root_run_id="run-hidden",
        thread_id="thread-hidden",
        parent_logical_call_id="",
        model="m",
        transport="chat_completions",
        purpose="main_loop",
        scope_key="scope",
    )
    first = await audit.begin_attempt(logical, wire_payload={
        "messages": [{"role": "assistant", "content": "ok", "reasoning_content": "A"}],
    })
    second = await audit.begin_attempt(logical, wire_payload={
        "messages": [{"role": "assistant", "content": "ok", "reasoning_content": "B"}],
    })
    assert first is not None and second is not None
    assert first.wire_payload_hash != second.wire_payload_hash
    attempt_rows = [row for row in captured if type(row).__name__ == "AgentModelAttemptAudit"]
    assert all("reasoning_content" not in json.dumps(row.wire_payload) for row in attempt_rows)


@pytest.mark.asyncio
async def test_finish_attempt_updates_new_row_and_writes_legacy_cache(monkeypatch):
    captured = {"rows": []}

    class UpdateResult:
        rowcount = 1

    class MissingResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement, _params=None):
            return UpdateResult() if statement.__class__.__name__ == "Update" else MissingResult()

        def add(self, row):
            captured["rows"].append(row)

        async def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    handle = audit.ModelAttemptAuditHandle(
        attempt_id="at-1",
        logical_call_id="lg-1",
        request_id="req-1",
        run_id="run-1",
        root_run_id="run-1",
        thread_id="thread-1",
        run_sequence=9,
        attempt_index=1,
        model="deepseek-v4",
        transport="responses",
        purpose="main_loop",
        prefix_diagnostics={"lcp_item_count": 3},
        legacy_compatible=True,
    )
    ok = await audit.finish_attempt(
        handle,
        terminal_status="incomplete",
        usage={
            "input_tokens": 100,
            "output_tokens": 12,
            "output_tokens_details": {"reasoning_tokens": 7},
        },
        provider_event_seen=True,
        terminal_seen=True,
        committed=False,
    )
    assert ok is True and captured["committed"] is True
    assert [type(row).__name__ for row in captured["rows"]] == ["AgentModelCacheAudit"]
    cache_row = captured["rows"][0]
    assert cache_row.request_id == "req-1"
    assert cache_row.request_sequence == 9
    assert cache_row.input_tokens == 100


def test_attempt_table_name_and_purpose_contract_are_canonical():
    from app.runtime_models import AgentModelAttempt, AgentModelAttemptAudit

    assert AgentModelAttemptAudit.__tablename__ == "agent_model_attempt_audits"
    assert AgentModelAttempt is AgentModelAttemptAudit
    assert "main_loop" in audit.MODEL_CALL_PURPOSES
    assert "tool_internal" in audit.MODEL_CALL_PURPOSES
    assert "main_agent" not in audit.MODEL_CALL_PURPOSES
