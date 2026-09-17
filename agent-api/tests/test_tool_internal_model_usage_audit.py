import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness import model_usage_audit
from app.services.chat.tools.base import CURRENT_TOOL_CONTEXT, ToolExecutionContext
from app.services.files import document_parse_service
from app.services.sandbox import visual_review
from app.services.skills import builtin_data_tools, ppt_style_reference


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


def _install_audit_spies(monkeypatch, *, attempt_count: int = 1, logical_count: int = 1):
    logicals = [
        SimpleNamespace(logical_call_id=f"logical-{index + 1}")
        for index in range(logical_count)
    ]
    attempts = [SimpleNamespace(attempt_id=f"attempt-{index + 1}") for index in range(attempt_count)]
    begin_logical = AsyncMock(side_effect=logicals)
    begin_attempt = AsyncMock(side_effect=attempts)
    finish_attempt = AsyncMock(return_value=True)
    finish_logical = AsyncMock(return_value=True)
    monkeypatch.setattr(model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(model_usage_audit, "finish_logical_call", finish_logical)
    return begin_logical, begin_attempt, finish_attempt, finish_logical


@pytest.mark.asyncio
async def test_text_to_sql_protocol_fallback_creates_child_logical_call(monkeypatch) -> None:
    begin_logical, begin_attempt, finish_attempt, finish_logical = _install_audit_spies(
        monkeypatch, attempt_count=2, logical_count=2,
    )
    client = AsyncMock()
    client.post.side_effect = [
        _Response(400, {
            "id": "rejected",
            "usage": {"prompt_tokens": 11},
            "error": {
                "code": "unsupported_parameter",
                "param": "response_format",
                "message": "response_format is not supported by this model",
            },
        }),
        _Response(200, {
            "id": "accepted",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            "choices": [{"message": {"content": json.dumps({
                "sql": "SELECT id FROM users",
                "table_fields": [],
                "confidence": 0.8,
                "syntax_error": "",
            })}}],
        }),
    ]
    monkeypatch.setattr(
        builtin_data_tools.httpx, "AsyncClient", lambda **_kwargs: _ClientContext(client),
    )

    result = await builtin_data_tools._run_text_to_sql(
        {"query_text": "users", "database_schema": "users(id bigint)"},
        {
            "api_key": "key",
            "default_model": "model-a",
            "base_url": "https://provider.example/v1",
            "run_id": "run-1",
            "thread_id": "thread-1",
            "audit_root_run_id": "root-1",
            "audit_parent_tool_call_id": "tool-sql-1",
        },
    )

    assert result["sql"] == "SELECT id FROM users LIMIT 20"
    assert begin_logical.await_count == 2
    assert begin_logical.await_args_list[0].kwargs["purpose_detail"] == "text_to_sql"
    assert begin_logical.await_args_list[0].kwargs["root_run_id"] == "root-1"
    assert begin_logical.await_args_list[0].kwargs["parent_tool_call_id"] == "tool-sql-1"
    assert begin_logical.await_args_list[1].kwargs["parent_logical_call_id"] == "logical-1"
    assert begin_logical.await_args_list[1].kwargs["fallback_reason"] == "response_format_unsupported"
    assert begin_attempt.await_count == 2
    assert begin_attempt.await_args_list[0].kwargs["attempt_kind"] == "initial"
    assert begin_attempt.await_args_list[1].kwargs["attempt_kind"] == "protocol_fallback"
    assert begin_attempt.await_args_list[0].args[0].logical_call_id == "logical-1"
    assert begin_attempt.await_args_list[1].args[0].logical_call_id == "logical-2"
    assert "retry_of_attempt_id" not in begin_attempt.await_args_list[1].kwargs
    assert "response_format" in client.post.await_args_list[0].kwargs["json"]
    assert "response_format" not in client.post.await_args_list[1].kwargs["json"]
    assert [call.kwargs["terminal_status"] for call in finish_attempt.await_args_list] == [
        "protocol_rejected", "completed",
    ]
    assert finish_attempt.await_args_list[1].kwargs["response_id"] == "accepted"
    assert finish_logical.await_count == 2
    assert finish_logical.await_args_list[0].kwargs["selected_attempt_id"] == "attempt-1"
    assert finish_logical.await_args_list[1].kwargs["selected_attempt_id"] == "attempt-2"


@pytest.mark.asyncio
async def test_text_to_sql_generic_400_does_not_retry_or_fallback(monkeypatch) -> None:
    begin_logical, begin_attempt, finish_attempt, finish_logical = _install_audit_spies(monkeypatch)
    client = AsyncMock()
    client.post.return_value = _Response(400, {
        "id": "bad-request",
        "error": {
            "code": "invalid_request_error",
            "param": "response_format",
            "message": "response_format must use the json_object type",
        },
    })
    monkeypatch.setattr(
        builtin_data_tools.httpx, "AsyncClient", lambda **_kwargs: _ClientContext(client),
    )

    with pytest.raises(ValueError, match="HTTP 400"):
        await builtin_data_tools._run_text_to_sql(
            {"query_text": "users", "database_schema": "users(id bigint)"},
            {
                "api_key": "key",
                "default_model": "model-a",
                "base_url": "https://provider.example/v1",
                "run_id": "run-1",
                "thread_id": "thread-1",
            },
        )

    assert client.post.await_count == 1
    assert begin_logical.await_count == 1
    assert begin_attempt.await_count == 1
    assert finish_attempt.await_args.kwargs["terminal_status"] == "http_error"
    assert finish_logical.await_count == 1


@pytest.mark.asyncio
async def test_document_vision_uses_current_tool_owner_and_captures_usage(monkeypatch) -> None:
    begin_logical, _begin_attempt, finish_attempt, finish_logical = _install_audit_spies(monkeypatch)
    token = CURRENT_TOOL_CONTEXT.set(ToolExecutionContext(
        call_id="tool-1", run_id="run-2", thread_id="thread-2",
    ))
    try:
        client = AsyncMock()
        client.post.return_value = _Response(200, {
            "id": "vision-1",
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            "choices": [{"message": {"content": "recognized"}}],
        })
        payload = {"model": "vision", "messages": [{"role": "user", "content": "image"}]}
        _response, data = await document_parse_service._audited_document_model_post(
            client,
            "https://provider.example/v1/chat/completions",
            model="vision",
            purpose_detail="document_image_vision",
            headers={"Authorization": "Bearer hidden"},
            wire_payload=payload,
            json_payload=payload,
            provider_api_key="secret",
            response_has_output=document_parse_service._chat_completion_has_output,
        )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert data["id"] == "vision-1"
    assert begin_logical.await_args.kwargs["run_id"] == "run-2"
    assert begin_logical.await_args.kwargs["thread_id"] == "thread-2"
    assert begin_logical.await_args.kwargs["purpose"] == "tool_internal"
    assert finish_attempt.await_args.kwargs["usage"]["prompt_tokens"] == 20
    assert finish_attempt.await_args.kwargs["response_id"] == "vision-1"
    assert finish_attempt.await_args.kwargs["committed"] is True
    assert finish_logical.await_args.kwargs["selected_attempt_id"] == "attempt-1"


@pytest.mark.asyncio
async def test_visual_review_retryable_status_is_one_logical_call_with_two_attempts(monkeypatch) -> None:
    begin_logical, begin_attempt, finish_attempt, finish_logical = _install_audit_spies(
        monkeypatch, attempt_count=2,
    )
    token = CURRENT_TOOL_CONTEXT.set(ToolExecutionContext(
        call_id="tool-2", run_id="run-3", thread_id="thread-3",
    ))
    client = AsyncMock()
    client.post.side_effect = [
        _Response(503, {"id": "unavailable", "usage": {"prompt_tokens": 5},
                        "error": {"message": "temporarily unavailable"}}),
        _Response(200, {"id": "good", "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                        "choices": [{"message": {"content": json.dumps({
                            "passed": True, "requirement_checks": [], "issues": [],
                            "next_actions": [],
                        })}}]}),
    ]
    monkeypatch.setattr(visual_review.httpx, "AsyncClient", lambda **_kwargs: _ClientContext(client))

    async def config():
        return {"model": "vision", "visionBaseUrl": "https://provider.example/v1",
                "visionApiKey": "key"}

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", config)
    try:
        verdict = await visual_review._ask_vision(
            {"pages": [], "total_pages": 0},
            filename="result.pptx",
            task_brief="make slides",
            outline={"outline": ""},
            newapi_key="",
        )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert verdict is None  # no visual batches; the requirement call itself still completed
    assert begin_logical.await_args.kwargs["parent_tool_call_id"] == "tool-2"
    assert begin_attempt.await_count == 2
    assert begin_attempt.await_args_list[1].kwargs["attempt_kind"] == "retry"
    assert begin_attempt.await_args_list[1].kwargs["retry_of_attempt_id"] == "attempt-1"
    assert [call.kwargs["terminal_status"] for call in finish_attempt.await_args_list] == [
        "http_error", "completed",
    ]
    assert finish_logical.await_args.kwargs["selected_attempt_id"] == "attempt-2"


@pytest.mark.asyncio
async def test_visual_review_generic_400_is_not_retried(monkeypatch) -> None:
    _begin_logical, begin_attempt, finish_attempt, finish_logical = _install_audit_spies(monkeypatch)
    token = CURRENT_TOOL_CONTEXT.set(ToolExecutionContext(
        call_id="tool-3", run_id="run-4", thread_id="thread-4",
    ))
    client = AsyncMock()
    client.post.return_value = _Response(400, {
        "id": "bad-request", "error": {"message": "invalid image payload"},
    })
    monkeypatch.setattr(visual_review.httpx, "AsyncClient", lambda **_kwargs: _ClientContext(client))

    async def config():
        return {"model": "vision", "visionBaseUrl": "https://provider.example/v1",
                "visionApiKey": "key"}

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", config)
    try:
        verdict = await visual_review._ask_vision(
            {"pages": [], "total_pages": 0},
            filename="result.pptx",
            task_brief="make slides",
            outline={"outline": ""},
            newapi_key="",
        )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert verdict is None
    assert client.post.await_count == 1
    assert begin_attempt.await_count == 1
    assert finish_attempt.await_args.kwargs["provider_event_seen"] is True
    assert finish_logical.await_count == 1


@pytest.mark.asyncio
async def test_visual_review_transport_retry_marks_no_provider_event_before_response(monkeypatch) -> None:
    _begin_logical, begin_attempt, finish_attempt, _finish_logical = _install_audit_spies(
        monkeypatch, attempt_count=2,
    )
    token = CURRENT_TOOL_CONTEXT.set(ToolExecutionContext(
        call_id="tool-4", run_id="run-5", thread_id="thread-5",
    ))
    client = AsyncMock()
    client.post.side_effect = [
        builtin_data_tools.httpx.ConnectError("connection reset"),
        _Response(200, {"id": "good", "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                        "choices": [{"message": {"content": json.dumps({
                            "passed": True, "requirement_checks": [], "issues": [],
                            "next_actions": [],
                        })}}]}),
    ]
    monkeypatch.setattr(visual_review.httpx, "AsyncClient", lambda **_kwargs: _ClientContext(client))

    async def config():
        return {"model": "vision", "visionBaseUrl": "https://provider.example/v1",
                "visionApiKey": "key"}

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", config)
    try:
        await visual_review._ask_vision(
            {"pages": [], "total_pages": 0},
            filename="result.pptx",
            task_brief="make slides",
            outline={"outline": ""},
            newapi_key="",
        )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert begin_attempt.await_count == 2
    assert finish_attempt.await_args_list[0].kwargs["provider_event_seen"] is False
    assert finish_attempt.await_args_list[1].kwargs["provider_event_seen"] is True


@pytest.mark.asyncio
async def test_missing_owner_logs_orphan_without_starting_audit(monkeypatch, caplog) -> None:
    begin_logical = AsyncMock()
    begin_attempt = AsyncMock()
    monkeypatch.setattr(model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(model_usage_audit, "begin_attempt", begin_attempt)

    async def config():
        return {"model": "vision", "visionBaseUrl": "https://provider.example/v1",
                "visionApiKey": "key"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response(500, {"error": {"message": "failed"}})

    from app.services.platform import platform_config_service

    monkeypatch.setattr(platform_config_service, "get_ocr_config", config)
    monkeypatch.setattr(ppt_style_reference.httpx, "AsyncClient", lambda **_kwargs: Client())
    token = CURRENT_TOOL_CONTEXT.set(None)
    try:
        with caplog.at_level("WARNING"):
            result = await ppt_style_reference.analyze_ppt_style_reference(
                "按这张图的风格做 PPT",
                [{"kind": "image", "image_url": "data:image/png;base64,AAAA"}],
            )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert result == ""
    begin_logical.assert_not_awaited()
    begin_attempt.assert_not_awaited()
    assert (
        "model_usage_orphan purpose=tool_internal purpose_detail=ppt_style_reference "
        "reason=missing_run_id"
    ) in caplog.text
