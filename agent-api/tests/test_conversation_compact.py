# -*- coding: utf-8 -*-
"""Codex-style live history compaction."""

import pytest

from app.services.agent_harness.conversation_compact import (
    SUMMARY_PREFIX,
    build_compacted_history,
    collect_user_messages,
    is_context_overflow_error,
    is_summary_message,
    message_text,
    replacement_history_from_messages,
    select_recent_user_messages,
    should_compact_history,
    _COMPACTION_FAILURES,
    generate_compaction_summary,
)


def test_summary_prefix_matches_codex_template():
    assert SUMMARY_PREFIX.startswith("Another language model started to solve this problem")
    assert "information in this summary" in SUMMARY_PREFIX
    assert is_summary_message(f"{SUMMARY_PREFIX}\nprogress so far")
    assert not is_summary_message("please continue the ppt")


def test_build_compacted_history_keeps_system_recent_users_and_summary():
    history = build_compacted_history(
        system_messages=[{"role": "system", "content": "You are AXIOM Agent."}],
        user_messages=["做一份年报 PPT", "把封面改成深蓝"],
        summary="已完成目录，下一步导出。",
    )
    assert history[0] == {"role": "system", "content": "You are AXIOM Agent."}
    assert history[1]["role"] == "user" and "年报 PPT" in history[1]["content"]
    assert history[2]["content"] == "把封面改成深蓝"
    assert history[-1]["role"] == "user"
    assert history[-1]["content"].startswith(SUMMARY_PREFIX)
    assert "已完成目录" in history[-1]["content"]
    assert all(item["role"] in {"system", "user"} for item in history)


def test_collect_user_messages_skips_tool_receipts_and_prior_summary():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "目标：写周报"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "name": "bash", "content": "wrote /tmp/a.md"},
        {"role": "user", "content": f"{SUMMARY_PREFIX}\nold summary"},
        {"role": "user", "content": "继续"},
    ]
    assert collect_user_messages(messages) == ["目标：写周报", "继续"]


def test_select_recent_user_messages_prefers_newest_within_budget():
    selected = select_recent_user_messages(["aaaa", "bbbb", "cccc"], max_tokens=2)
    assert selected[-1].startswith("cccc") or selected[-1] == "cccc"


def test_oversized_segment_rejected_without_dropping_source(monkeypatch):
    from app.services.agent_harness import conversation_compact as cc
    monkeypatch.setattr(cc, "_compaction_input_limit", lambda _: 1)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old"},
        {"role": "user", "content": "new"},
    ]
    with pytest.raises(RuntimeError, match="original history retained"):
        cc._fit_compaction_input(messages, model="small")
    assert [message_text(item) for item in messages] == ["sys", "old", "new"]


def test_overflow_detector_only_matches_context_window_errors():
    assert is_context_overflow_error(413, "")
    assert is_context_overflow_error(400, '{"error":{"code":"context_length_exceeded"}}')
    assert is_context_overflow_error(400, "This model's maximum context length is 128000 tokens")
    assert not is_context_overflow_error(401, "invalid api key")
    assert not is_context_overflow_error(429, "rate limit")
    assert not is_context_overflow_error(500, "internal")


def test_should_compact_history_respects_trigger(monkeypatch):
    from app.services.platform import model_window

    monkeypatch.setattr(model_window, "resolve_window", lambda _model: 1000)
    monkeypatch.setattr(model_window, "compact_trigger", lambda _window: 50)
    tiny = [{"role": "user", "content": "hi"}]
    huge = [{"role": "user", "content": "字" * 4000}]
    assert should_compact_history(tiny, model="demo", window=1000) is False
    assert should_compact_history(huge, model="demo", window=1000) is True


def test_replacement_history_is_only_user_and_system():
    compacted = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "goal"},
        {"role": "user", "content": f"{SUMMARY_PREFIX}\nnote"},
        {"role": "assistant", "content": "should drop"},
    ]
    replaced = replacement_history_from_messages(compacted)
    assert [item["role"] for item in replaced] == ["system", "user", "user"]


@pytest.mark.asyncio
async def test_compaction_overflow_keeps_source_and_dedupes(monkeypatch):
    import httpx

    from app.services.agent_harness import conversation_compact
    from app.services.platform import model_window

    calls: list[dict] = []
    audit_calls: dict[str, list] = {
        "logical": [],
        "attempt": [],
        "attempt_finish": [],
        "logical_finish": [],
    }

    class _Logical:
        logical_call_id = "logical-compact"

    class _Attempt:
        def __init__(self, index):
            self.attempt_id = f"attempt-compact-{index}"

    async def begin_logical(**kwargs):
        audit_calls["logical"].append(kwargs)
        return _Logical()

    async def begin_attempt(handle, **kwargs):
        audit_calls["attempt"].append((handle, kwargs))
        return _Attempt(len(audit_calls["attempt"]))

    async def finish_attempt(handle, **kwargs):
        audit_calls["attempt_finish"].append((handle, kwargs))
        return True

    async def finish_logical(handle, **kwargs):
        audit_calls["logical_finish"].append((handle, kwargs))
        return True

    async def chat_transport(*_args, **_kwargs):
        return "chat_completions"

    class _OverflowResponse:
        status_code = 413
        text = '{"error":{"code":"context_length_exceeded"}}'

        def json(self):
            return {}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json, headers):
            calls.append({"json": json, "headers": headers})
            return _OverflowResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "finish_logical_call", finish_logical)
    monkeypatch.setattr(conversation_compact, "_compaction_transport", chat_transport)
    monkeypatch.setattr(model_window, "resolve_window", lambda _model: 1_000_000)
    monkeypatch.setattr(model_window, "compact_trigger", lambda _window: 900_000)
    _COMPACTION_FAILURES.clear()
    messages = [
        {"role": "system", "content": "stable"},
        *({"role": "user", "content": f"history-{index}"} for index in range(12)),
    ]

    with pytest.raises(RuntimeError, match="compaction overflow"):
        await generate_compaction_summary(
            messages,
            model="demo-model",
            api_key="test-key",
            request_scope_id="run-1",
            run_id="run-1",
            thread_id="thread-1",
            purpose="compaction_live",
        )
    # A rejected segment must fail intact, not "repair" overflow by deleting source items.
    assert len(calls) == 1
    assert all(call["json"]["max_tokens"] == 4096 for call in calls)
    assert len(audit_calls["logical"]) == 1
    assert audit_calls["logical"][0]["purpose"] == "compaction_live"
    assert [item[1]["attempt_kind"] for item in audit_calls["attempt"]] == [
        "initial",
    ]
    assert len(audit_calls["attempt_finish"]) == 1
    assert all(
        item[1]["terminal_status"] == "failed"
        and item[1]["error_code"] == "context_overflow"
        for item in audit_calls["attempt_finish"]
    )
    assert len(audit_calls["logical_finish"]) == 1
    assert audit_calls["logical_finish"][0][1]["terminal_status"] == "failed"

    with pytest.raises(RuntimeError, match="temporarily deduplicated"):
        await generate_compaction_summary(
            messages,
            model="demo-model",
            api_key="test-key",
            request_scope_id="run-1",
            run_id="run-1",
            thread_id="thread-1",
            purpose="compaction_live",
        )
    assert len(calls) == 1
    assert len(audit_calls["logical"]) == 1
    assert len(audit_calls["attempt"]) == 1
    _COMPACTION_FAILURES.clear()


@pytest.mark.asyncio
async def test_compaction_uses_responses_when_run_transport_is_responses(monkeypatch):
    import httpx

    from app.services.agent_harness import conversation_compact

    calls: list[tuple[str, dict]] = []

    async def responses_transport(*_args, **_kwargs):
        return "responses"

    class _Logical:
        logical_call_id = "logical-responses-compact"

    class _Attempt:
        attempt_id = "attempt-responses-compact"

    async def begin_logical(**kwargs):
        assert kwargs["transport"] == "responses"
        return _Logical()

    async def begin_attempt(_handle, **kwargs):
        assert "input" in kwargs["wire_payload"]
        assert "messages" not in kwargs["wire_payload"]
        return _Attempt()

    async def finish(*_args, **_kwargs):
        return True

    class _Response:
        status_code = 200
        text = ""
        terminal_status = "completed"

        def json(self):
            return {
                "id": "resp-compact",
                "status": self.terminal_status,
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "checkpoint summary"}],
                }],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            }

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json, headers):
            calls.append((url, json))
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(conversation_compact, "_compaction_transport", responses_transport)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "finish_attempt", finish)
    monkeypatch.setattr(conversation_compact.model_usage_audit, "finish_logical_call", finish)
    _COMPACTION_FAILURES.clear()

    summary = await generate_compaction_summary(
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}],
        model="demo-model",
        api_key="test-key",
        request_scope_id="run-responses",
        run_id="run-responses",
    )

    assert summary == "checkpoint summary"
    assert calls[0][0].endswith("/responses")
    assert calls[0][1]["max_output_tokens"] == 4096
    assert calls[0][1]["store"] is False

    _Response.terminal_status = "incomplete"
    with pytest.raises(RuntimeError, match="did not complete: incomplete"):
        await generate_compaction_summary(
            [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}],
            model="demo-model",
            api_key="test-key",
            request_scope_id="run-responses-incomplete",
            run_id="run-responses-incomplete",
        )
    _COMPACTION_FAILURES.clear()
