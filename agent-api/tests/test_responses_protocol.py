import json

import httpx
import pytest

from app.services.agent_harness import model_driver
from app.services.agent_harness.responses_protocol import (
    ResponsesTerminalError,
    ResponsesRoundState,
    _UNPAIRED_FUNCTION_CALL_OUTPUT,
    messages_to_responses_input,
    model_uses_responses_transport,
    pair_responses_function_call_outputs,
    responses_api_is_unsupported,
    responses_capability_from_metadata,
    response_output_text,
    tools_to_responses,
)
from app.services.agents.agent_service import AgentService, agent_service
from app.services.chat.tools.base import MainTool


def test_chat_cursor_is_translated_to_responses_items() -> None:
    rows = messages_to_responses_input([
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "I will check.",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ])

    assert rows == [
        {"role": "developer", "content": "rules"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "I will check."},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": '{"q":"x"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": "result"},
    ]


def test_native_responses_cursor_replays_opaque_reasoning_without_duplicates() -> None:
    native = [
        {
            "id": "rs-1",
            "type": "reasoning",
            "encrypted_content": "opaque-ciphertext",
            "summary": [{"type": "summary_text", "text": "checked"}],
        },
        {
            "id": "fc-1",
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": "{}",
        },
    ]
    rows = messages_to_responses_input([
        {"role": "user", "content": "check"},
        {
            "role": "assistant",
            "content": "lossy chat projection",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
            "_responses_output_items": native,
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ])

    assert rows == [
        {"role": "user", "content": "check"},
        *native,
        {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
    ]
    assert sum(item.get("type") == "function_call" for item in rows) == 1


def test_unpaired_native_function_call_gets_synthetic_output_before_next_user() -> None:
    rows = messages_to_responses_input([
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": "",
            "_responses_output_items": [{
                "type": "function_call",
                "call_id": "call_01_G1BfeHAKbTC2tOv6ONe45522",
                "name": "get_interview_session",
                "arguments": "{}",
            }],
        },
        {"role": "user", "content": "continue"},
    ])

    assert rows[1]["type"] == "function_call"
    assert rows[1]["call_id"] == "call_01_G1BfeHAKbTC2tOv6ONe45522"
    assert rows[2] == {
        "type": "function_call_output",
        "call_id": "call_01_G1BfeHAKbTC2tOv6ONe45522",
        "output": _UNPAIRED_FUNCTION_CALL_OUTPUT,
    }
    assert rows[3] == {"role": "user", "content": "continue"}


def test_pair_outputs_keeps_real_receipts_and_only_fills_the_gap() -> None:
    rows = pair_responses_function_call_outputs([
        {"type": "function_call", "call_id": "call-a", "name": "lookup", "arguments": "{}"},
        {"type": "function_call", "call_id": "call-b", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-a", "output": "ok"},
        {"role": "user", "content": "next"},
    ])
    outputs = [item for item in rows if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in outputs] == ["call-a", "call-b"]
    assert outputs[0]["output"] == "ok"
    assert outputs[1]["output"] == _UNPAIRED_FUNCTION_CALL_OUTPUT
    assert rows[-1] == {"role": "user", "content": "next"}


def test_stateless_native_message_drops_server_id_and_summary_only_reasoning() -> None:
    rows = messages_to_responses_input([{
        "role": "assistant",
        "content": "answer",
        "_responses_output_items": [
            {
                "id": "rs-summary-only",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "brief"}],
            },
            {
                "id": "msg-server-reference",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
        ],
    }])

    assert rows == [{
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "answer"}],
    }]


def test_tools_are_flattened_for_responses() -> None:
    tools = tools_to_responses([{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "read data",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }])

    assert tools == [{
        "type": "function",
        "name": "lookup",
        "description": "read data",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("deepseek-v4-flash", True),
        ("provider/DeepSeek-R1", True),
        ("deepseek_v4_flash_vision_exp", True),
        ("gpt-5.5", True),
        ("qwen3-max", True),
    ],
)
def test_model_transport_routing(model: str, expected: bool) -> None:
    assert model_uses_responses_transport(model) is expected


def test_opaque_model_id_routes_by_deepseek_registry_alias() -> None:
    assert model_uses_responses_transport(
        "model-42",
        aliases=("DeepSeek V4 Flash", "vendor/deepseek"),
    ) is True
    assert model_uses_responses_transport(
        "model-42",
        aliases=("Qwen 3 Max", "alibaba"),
    ) is True
    assert model_uses_responses_transport(
        "model-42",
        aliases=("Qwen 3 Max", "alibaba"),
        supports_responses=False,
    ) is False


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"supports_responses": True}, True),
        ({"capabilities": {"responses": "supported"}}, True),
        ({"supported_endpoints": ["responses", "embeddings"]}, True),
        ({"supported_endpoints": ["chat/completions"]}, False),
        ({"capabilities": {"vision": True}}, None),
        ({}, None),
    ],
)
def test_responses_capability_from_model_catalog(metadata, expected) -> None:
    assert responses_capability_from_metadata(metadata) is expected


def test_deepseek_contract_overrides_negative_catalog_metadata() -> None:
    assert model_uses_responses_transport(
        "opaque-id",
        aliases=("DeepSeek V4",),
        supports_responses=False,
    ) is True


def test_only_exact_structured_conversion_500_is_protocol_unsupported() -> None:
    exact = {
        "error": {
            "code": "convert_request_failed",
            "message": "not implemented",
        }
    }
    assert responses_api_is_unsupported(500, exact) is True
    assert responses_api_is_unsupported(500, json.dumps(exact)) is True
    assert responses_api_is_unsupported(
        500,
        {"error": {"code": "convert_request_failed", "message": "upstream timeout"}},
    ) is False
    assert responses_api_is_unsupported(
        500,
        {"error": {"code": "server_error", "message": "not implemented"}},
    ) is False
    assert responses_api_is_unsupported(
        500,
        "convert_request_failed: not implemented",
    ) is False


def test_observed_model_capability_is_isolated_by_user_key() -> None:
    service = AgentService()
    service.remember_model_responses_capability("shared-model", "key-a", True)
    service.remember_model_responses_capability("shared-model", "key-b", False)

    assert service.model_responses_capability("shared-model", "key-a") is True
    assert service.model_responses_capability("shared-model", "key-b") is False
    assert service.model_responses_capability("shared-model", "key-c") is None


def test_native_phase_preserves_commentary_reasoning_tool_order() -> None:
    state = ResponsesRoundState()
    public = []
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": "msg-1", "type": "message", "role": "assistant", "phase": "commentary"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "item_id": "msg-1", "delta": "我先核对条件，"},
        {"type": "response.output_text.delta", "output_index": 0, "item_id": "msg-1", "delta": "再查询。"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "我先核对条件，再查询。"}],
            },
        },
        {"type": "response.reasoning_summary_text.delta", "item_id": "rs-1", "delta": "checking"},
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "id": "fc-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": '{"q":"x"}',
            },
        },
    ]
    for event in events:
        public.extend(state.ingest(event))

    commentary = [item for item in public if item["type"] == "commentary"]
    text_deltas = [item for item in public if item["type"] == "output_text_delta"]
    reasoning_deltas = [item for item in public if item["type"] == "reasoning_delta"]
    assert commentary == [{
        "type": "commentary",
        "text": "我先核对条件，再查询。",
        "item_id": "msg-1",
    }]
    assert [item["text"] for item in text_deltas] == ["我先核对条件，", "再查询。"]
    assert [item["phase"] for item in text_deltas] == ["commentary", "commentary"]
    assert [item["text"] for item in reasoning_deltas] == ["checking"]
    assert state.reasoning_parts == ["checking"]
    assert state.tool_calls[0]["function"] == {
        "name": "lookup",
        "arguments": '{"q":"x"}',
    }
    assert state.final_text == ""
    assert state.assistant_context_text == "我先核对条件，再查询。"


def test_completed_response_text_extraction() -> None:
    assert response_output_text({
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "hello"}],
        }],
    }) == "hello"


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            },
            "response_incomplete",
        ),
        (
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"code": "provider_error", "message": "upstream reset"},
                },
            },
            "response_failed",
        ),
    ],
)
def test_only_completed_is_a_success_terminal(event: dict, code: str) -> None:
    state = ResponsesRoundState()
    state.ingest(event)
    with pytest.raises(ResponsesTerminalError) as exc_info:
        state.require_completed()
    assert exc_info.value.code == code

    missing = ResponsesRoundState()
    with pytest.raises(ResponsesTerminalError) as missing_info:
        missing.require_completed()
    assert missing_info.value.code == "response_terminal_missing"


@pytest.mark.asyncio
async def test_drive_model_emits_commentary_then_reasoning_then_tool(monkeypatch) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    first_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "我先核对数据，再给结论。"}],
            },
        }),
        sse({
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs-1",
            "summary_index": 0,
            "delta": "checking",
        }),
        sse({
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {
                "id": "rs-1",
                "type": "reasoning",
                "encrypted_content": "opaque-round-1",
                "summary": [{"type": "summary_text", "text": "checking"}],
            },
        }),
        sse({
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "id": "fc-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": "{}",
            },
        }),
        sse({"type": "response.completed", "response": {"id": "resp-1", "status": "completed"}}),
        "data: [DONE]",
    ]
    final_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-2",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "结论已核对。"}],
            },
        }),
        sse({"type": "response.completed", "response": {"id": "resp-2", "status": "completed"}}),
        "data: [DONE]",
    ]

    class Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [first_round, final_round]
        urls = []
        payloads = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **kwargs):
            type(self).urls.append(url)
            type(self).payloads.append(kwargs["json"])
            return Response(type(self).responses.pop(0))

    async def lookup(_args):
        return "ok"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-flash",
        api_key="k",
        user_input="check",
        tools=[MainTool(name="lookup", description="lookup", parameters={}, execute=lookup)],
    )]

    kinds = [event["type"] for event in events]
    assert kinds.index("commentary") < kinds.index("reasoning") < kinds.index("tool_started")
    started = next(event for event in events if event["type"] == "tool_started")
    assert started["call_id"] == "call-1"
    assert Client.urls and all(url.endswith("/responses") for url in Client.urls)
    assert all("chat/completions" not in url for url in Client.urls)
    second_input = Client.payloads[1]["input"]
    assert {
        "id": "rs-1",
        "type": "reasoning",
        "encrypted_content": "opaque-round-1",
        "summary": [{"type": "summary_text", "text": "checking"}],
    } in second_input
    assert sum(item.get("type") == "function_call" for item in second_input) == 1
    assert any(
        item.get("type") == "function_call_output"
        and item.get("call_id") == "call-1"
        and item.get("output") == "ok"
        for item in second_input
    )
    assert next(event for event in events if event["type"] == "final")["answer"] == "结论已核对。"


@pytest.mark.asyncio
async def test_real_action_can_continue_after_plan_without_status_echo(
    monkeypatch,
) -> None:
    """A real action is never blocked on an extra status-only update_plan call."""

    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    def round_with(*items: dict) -> list[str]:
        return [
            *[
                sse({
                    "type": "response.output_item.done",
                    "output_index": index,
                    "item": item,
                })
                for index, item in enumerate(items)
            ],
            sse({
                "type": "response.completed",
                "response": {"id": "resp", "status": "completed"},
            }),
            "data: [DONE]",
        ]

    def commentary(item_id: str, text: str) -> dict:
        return {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": text}],
        }

    def function(call_id: str, name: str, arguments: dict) -> dict:
        return {
            "id": f"item-{call_id}",
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        }

    def final(item_id: str, text: str) -> dict:
        return {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": text}],
        }

    running = [
        {"title": "读取资料", "status": "in_progress"},
        {"title": "生成文档", "status": "pending"},
    ]
    class Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [
            round_with(
                function("plan-1", "update_plan", {"steps": running}),
                function("lookup-1", "lookup", {}),
            ),
            round_with(
                commentary("msg-progress", "资料已核对，开始生成文档。"),
                function("bash-1", "bash", {}),
            ),
            round_with(final("msg-final", "文档已生成。")),
        ]

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, **_kwargs):
            return Response(type(self).responses.pop(0))

    calls: list[str] = []

    async def execute(name: str):
        calls.append(name)
        return f"{name}-ok"

    async def lookup(_args):
        return await execute("lookup")

    async def bash(_args):
        return await execute("bash")

    async def update_plan(_args):
        return "ok"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-flash",
        api_key="k",
        user_input="分步生成文档",
        tools=[
            MainTool(
                name="update_plan",
                description="plan",
                parameters={},
                execute=update_plan,
                internal=True,
                control_command=True,
            ),
            MainTool(name="lookup", description="lookup", parameters={}, execute=lookup),
            MainTool(name="bash", description="bash", parameters={}, execute=bash),
        ],
    )]

    visible_commentary = [
        event["text"] for event in events if event["type"] == "commentary"
    ]
    assert visible_commentary == ["资料已核对，开始生成文档。"]
    assert calls == ["lookup", "bash"]
    assert next(event for event in events if event["type"] == "final")["answer"] == "文档已生成。"


@pytest.mark.asyncio
async def test_non_deepseek_main_agent_uses_chat_completions(monkeypatch) -> None:
    lines = [
        "data: " + json.dumps({
            "choices": [{"delta": {"content": "Chat 结论。"}, "finish_reason": None}],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }, ensure_ascii=False),
        "data: [DONE]",
    ]
    captured = {}

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, **kwargs):
            captured.update({"method": method, "url": url, "payload": kwargs["json"]})
            return Response()

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    events = [event async for event in model_driver.drive_model(
        model="gpt-5.5",
        api_key="k",
        user_input="answer",
        tools=[],
    )]

    assert captured["url"].endswith("/chat/completions")
    assert "messages" in captured["payload"]
    assert "input" not in captured["payload"]
    assert next(event for event in events if event["type"] == "final")["answer"] == "Chat 结论。"


@pytest.mark.asyncio
async def test_responses_text_waits_for_completed_classification_without_duplication(
    monkeypatch,
) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    first = "这是需要等到 Responses 终态后才能确认归类的第一段正文。"
    second = "这是第二段。"
    lines = [
        sse({
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "msg-live",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
            },
        }),
        sse({
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs-live",
            "delta": "正在核对",
        }),
        sse({
            "type": "response.output_text.delta",
            "output_index": 0,
            "item_id": "msg-live",
            "delta": first,
        }),
        sse({
            "type": "response.output_text.delta",
            "output_index": 0,
            "item_id": "msg-live",
            "delta": second,
        }),
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-live",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": first + second}],
            },
        }),
        sse({
            "type": "response.completed",
            "response": {"id": "resp-live", "status": "completed"},
        }),
        "data: [DONE]",
    ]
    captured = {}

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-flash",
        api_key="k",
        user_input="answer",
        tools=[],
    )]

    assert "reasoning" in [event["type"] for event in events]
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas == [first + second]
    assert next(event for event in events if event["type"] == "final")["answer"] == first + second
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert captured["store"] is False


@pytest.mark.asyncio
async def test_responses_final_answer_item_followed_by_tool_is_process_commentary(
    monkeypatch,
) -> None:
    """Provider 在同一 Response 后置 function_call 时，前面的文本不得先漏成黑色终答。"""

    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    process = "python-docx 可用。我按 OOXML 规范用脚本生成文档，先写好生成器。"
    first_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-process",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": process}],
            },
        }),
        sse({
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {
                "id": "fc-write",
                "type": "function_call",
                "call_id": "call-write",
                "name": "lookup",
                "arguments": "{}",
            },
        }),
        sse({"type": "response.completed", "response": {"id": "resp-1", "status": "completed"}}),
        "data: [DONE]",
    ]
    final_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-final",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "文档已生成。"}],
            },
        }),
        sse({"type": "response.completed", "response": {"id": "resp-2", "status": "completed"}}),
        "data: [DONE]",
    ]

    class Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [first_round, final_round]

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, **_kwargs):
            return Response(type(self).responses.pop(0))

    async def lookup(_args):
        return "ok"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-flash",
        api_key="k",
        user_input="生成文档",
        tools=[MainTool(name="lookup", description="lookup", parameters={}, execute=lookup)],
    )]

    commentary = [event for event in events if event["type"] == "commentary"]
    assert [event["text"] for event in commentary] == [process]
    assert [event.get("kind") for event in commentary] == ["tool_round"]
    assert process not in [event["text"] for event in events if event["type"] == "delta"]
    assert next(event for event in events if event["type"] == "final")["answer"] == "文档已生成。"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["incomplete", "failed", "missing"])
async def test_noncompleted_responses_never_execute_partial_tool_call(
    monkeypatch,
    terminal: str,
) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    lines = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "fc-partial",
                "type": "function_call",
                "call_id": "call-partial",
                "name": "mutate",
                "arguments": "{}",
            },
        }),
    ]
    if terminal == "incomplete":
        lines.append(sse({
            "type": "response.incomplete",
            "response": {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        }))
    elif terminal == "failed":
        lines.append(sse({
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"code": "provider_error", "message": "reset"},
            },
        }))
    lines.append("data: [DONE]")

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, **_kwargs):
            type(self).calls += 1
            return Response()

    calls = []

    async def mutate(_args):
        calls.append("mutate")
        return "changed"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    with pytest.raises(ResponsesTerminalError):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-v4-flash",
            api_key="k",
            user_input="change it",
            tools=[MainTool(name="mutate", description="mutate", parameters={}, execute=mutate)],
        )]

    assert calls == []
    assert Client.calls == 1


@pytest.mark.asyncio
async def test_unknown_model_probes_responses_then_streams_chat_when_unsupported(
    monkeypatch,
) -> None:
    chat_lines = [
        "data: " + json.dumps({
            "choices": [{"delta": {"content": "Chat fallback 正常。"}, "finish_reason": None}],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }, ensure_ascii=False),
        "data: [DONE]",
    ]

    class Response:
        def __init__(self, status_code: int, lines=None, body: bytes = b""):
            self.status_code = status_code
            self.lines = list(lines or [])
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [
            Response(404, body=b"unknown endpoint"),
            Response(200, chat_lines),
        ]
        urls = []
        payloads = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **kwargs):
            type(self).urls.append(url)
            type(self).payloads.append(kwargs["json"])
            return type(self).responses.pop(0)

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="unknown-probe-model-404",
        api_key="probe-key-404",
        user_input="answer",
        tools=[],
    )]

    assert [url.rsplit("/", 1)[-1] for url in Client.urls] == [
        "responses",
        "completions",
    ]
    assert Client.urls[1].endswith("/chat/completions")
    assert "input" in Client.payloads[0]
    assert Client.payloads[1]["stream"] is True
    assert "messages" in Client.payloads[1]
    connection = [event for event in events if event["type"] == "model_connection"]
    assert [event["status"] for event in connection] == ["recovering", "recovered"]
    assert next(event for event in events if event["type"] == "final")["answer"] == (
        "Chat fallback 正常。"
    )


@pytest.mark.asyncio
async def test_deepseek_exact_conversion_500_falls_back_before_service_retries(
    monkeypatch,
) -> None:
    chat_lines = [
        "data: " + json.dumps({
            "choices": [{
                "delta": {"reasoning_content": "先核对。"},
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{
                "delta": {"content": "DeepSeek Chat 兜底正常。"},
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        }),
        "data: [DONE]",
    ]
    conversion_error = json.dumps({
        "error": {
            "code": "convert_request_failed",
            "message": "not implemented",
        }
    }).encode()

    class Response:
        def __init__(self, status_code: int, lines=None, body: bytes = b""):
            self.status_code = status_code
            self.lines = list(lines or [])
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [
            Response(500, body=conversion_error),
            Response(200, lines=chat_lines),
        ]
        urls = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **_kwargs):
            type(self).urls.append(url)
            return type(self).responses.pop(0)

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-structured-500",
        api_key="deepseek-structured-500-key",
        user_input="answer",
        tools=[],
    )]

    assert [url.rsplit("/", 1)[-1] for url in Client.urls] == [
        "responses",
        "completions",
    ]
    connection = [event for event in events if event["type"] == "model_connection"]
    assert [event["transport"] for event in connection] == [
        "chat_completions_fallback",
        "chat_completions_fallback",
    ]
    assert not any(event.get("transport") == "stream_retry" for event in connection)
    assert any(
        event["type"] == "reasoning" and "先核对" in event["text"]
        for event in events
    )
    assert next(event for event in events if event["type"] == "final")["answer"] == (
        "DeepSeek Chat 兜底正常。"
    )


@pytest.mark.asyncio
async def test_deepseek_chat_fallback_preserves_reasoning_tools_and_final_answer(
    monkeypatch,
) -> None:
    conversion_error = json.dumps({
        "error": {
            "code": "convert_request_failed",
            "message": "not implemented",
        }
    }).encode()
    tool_round = [
        "data: " + json.dumps({
            "choices": [{
                "delta": {"reasoning_content": "需要先查询。"},
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-chat-fallback",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"q":"x"}',
                        },
                    }]
                },
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
        }),
        "data: [DONE]",
    ]
    final_round = [
        "data: " + json.dumps({
            "choices": [{
                "delta": {"reasoning_content": "已核对工具结果。"},
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{
                "delta": {"content": "兜底后的最终正文。"},
                "finish_reason": None,
            }],
        }, ensure_ascii=False),
        "data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        }),
        "data: [DONE]",
    ]

    class Response:
        def __init__(self, status_code: int, lines=None, body: bytes = b""):
            self.status_code = status_code
            self.lines = list(lines or [])
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [
            Response(500, body=conversion_error),
            Response(200, lines=tool_round),
            Response(200, lines=final_round),
        ]
        urls = []
        payloads = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **kwargs):
            type(self).urls.append(url)
            type(self).payloads.append(kwargs["json"])
            return type(self).responses.pop(0)

    calls = []

    async def lookup(args):
        calls.append(args)
        return "tool-result"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-fallback-tools",
        api_key="deepseek-fallback-tools-key",
        user_input="lookup then answer",
        tools=[MainTool(
            name="lookup",
            description="lookup",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            execute=lookup,
        )],
    )]

    assert [url.rsplit("/", 1)[-1] for url in Client.urls] == [
        "responses",
        "completions",
        "completions",
    ]
    assert calls == [{"q": "x"}]
    assert any(event["type"] == "tool_started" for event in events)
    assert any(
        event["type"] == "reasoning"
        and ("需要先查询" in event["text"] or "已核对" in event["text"])
        for event in events
    )
    assert next(event for event in events if event["type"] == "final")["answer"] == (
        "兜底后的最终正文。"
    )
    second_chat_messages = Client.payloads[2]["messages"]
    assert any(message.get("tool_calls") for message in second_chat_messages)
    assert any(
        message.get("role") == "tool"
        and message.get("tool_call_id") == "call-chat-fallback"
        and message.get("content") == "tool-result"
        for message in second_chat_messages
    )


@pytest.mark.asyncio
async def test_generic_responses_500_retries_same_protocol_instead_of_chat(
    monkeypatch,
) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    response_lines = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-after-retry",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Responses 重连成功。"}],
            },
        }),
        sse({
            "type": "response.completed",
            "response": {"id": "resp-after-retry", "status": "completed"},
        }),
        "data: [DONE]",
    ]

    class Response:
        def __init__(self, status_code: int, lines=None, body: bytes = b""):
            self.status_code = status_code
            self.lines = list(lines or [])
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [
            Response(500, body=b'{"error":{"code":"server_error","message":"busy"}}'),
            Response(200, lines=response_lines),
        ]
        urls = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **_kwargs):
            type(self).urls.append(url)
            return type(self).responses.pop(0)

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    events = [event async for event in model_driver.drive_model(
        model="deepseek-v4-generic-500",
        api_key="deepseek-generic-500-key",
        user_input="answer",
        tools=[],
    )]

    assert all(url.endswith("/responses") for url in Client.urls)
    assert len(Client.urls) == 2
    assert any(
        event.get("transport") == "stream_retry"
        and event.get("status") == "recovering"
        for event in events
    )
    assert next(event for event in events if event["type"] == "final")["answer"] == (
        "Responses 重连成功。"
    )


@pytest.mark.asyncio
async def test_responses_event_before_conversion_500_forbids_replay_and_fallback(
    monkeypatch,
) -> None:
    request = httpx.Request("POST", "https://example.test/responses")
    conversion_error = json.dumps({
        "error": {
            "code": "convert_request_failed",
            "message": "not implemented",
        }
    }).encode()

    class Response:
        def __init__(self, *, status=200, lines=None, body=b"", tail_error=None):
            self.status_code = status
            self.lines = list(lines or [])
            self.body = body
            self.tail_error = tail_error

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line
            if self.tail_error:
                raise self.tail_error

    class Client:
        responses = [
            Response(
                lines=["data: " + json.dumps({"type": "response.created"})],
                tail_error=httpx.ReadError("reset", request=request),
            ),
            Response(status=500, body=conversion_error),
        ]
        urls = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **_kwargs):
            type(self).urls.append(url)
            return type(self).responses.pop(0)

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(model_driver.settings, "MODEL_STREAM_MAX_RETRIES", 1)

    # response.created is already a Provider event.  Even though no public text or tool side
    # effect has been committed locally, the request can already be chargeable; reconnecting
    # the same payload (and then falling back to Chat) would hide a second physical attempt.
    with pytest.raises(model_driver.ModelStreamReplayUnsafe):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-v4-event-before-500",
            api_key="deepseek-event-before-500-key",
            user_input="answer",
            tools=[],
        )]

    assert len(Client.urls) == 1
    assert all(url.endswith("/responses") for url in Client.urls)


@pytest.mark.asyncio
async def test_native_responses_cursor_never_downgrades_to_chat(monkeypatch) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    lines = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-cursor-final",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "cursor-safe"}],
            },
        }),
        sse({
            "type": "response.completed",
            "response": {"id": "resp-cursor", "status": "completed"},
        }),
        "data: [DONE]",
    ]
    captured = {}

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **kwargs):
            captured.update({"url": url, "payload": kwargs["json"]})
            return Response()

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    native_reasoning = {
        "id": "reasoning-cursor",
        "type": "reasoning",
        "encrypted_content": "opaque",
    }
    events = [event async for event in model_driver.drive_model(
        model="catalog-now-says-chat",
        api_key="cursor-key",
        initial_messages=[
            {"role": "user", "content": "continue"},
            {
                "role": "assistant",
                "content": "",
                "_responses_output_items": [native_reasoning],
            },
        ],
        tools=[],
    )]

    assert captured["url"].endswith("/responses")
    assert native_reasoning in captured["payload"]["input"]
    assert next(event for event in events if event["type"] == "final")["answer"] == (
        "cursor-safe"
    )


@pytest.mark.asyncio
async def test_native_responses_cursor_rejects_structured_500_downgrade(
    monkeypatch,
) -> None:
    body = json.dumps({
        "error": {
            "code": "convert_request_failed",
            "message": "not implemented",
        }
    }).encode()

    class Response:
        status_code = 500

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return body

    class Client:
        urls = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **_kwargs):
            type(self).urls.append(url)
            return Response()

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_driver.settings, "MODEL_STREAM_MAX_RETRIES", 0)
    native_reasoning = {
        "id": "reasoning-opaque-no-downgrade",
        "type": "reasoning",
        "encrypted_content": "opaque",
    }

    with pytest.raises(model_driver.ModelStreamRetriesExhausted):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-v4-opaque-500",
            api_key="deepseek-opaque-500-key",
            initial_messages=[
                {"role": "user", "content": "continue"},
                {
                    "role": "assistant",
                    "content": "",
                    "_responses_output_items": [native_reasoning],
                },
            ],
            tools=[],
        )]

    assert len(Client.urls) == 1
    assert Client.urls[0].endswith("/responses")


@pytest.mark.asyncio
async def test_sensitive_words_policy_500_stops_without_retry_or_fallback(
    monkeypatch,
) -> None:
    body = json.dumps({
        "error": {
            "message": "sensitive_words_detected (request id: req-private)",
            "type": "new_api_error",
            "param": "",
            "code": "sensitive_words_detected",
        }
    }).encode()

    class Response:
        status_code = 500

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return body

    class Client:
        calls = 0
        urls = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, url, **_kwargs):
            type(self).calls += 1
            type(self).urls.append(url)
            return Response()

        async def post(self, *_args, **_kwargs):
            raise AssertionError("policy rejection must not use non-stream fallback")

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(model_driver.ModelProviderPolicyRejected) as failure:
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-v4-policy-stop",
            api_key="policy-test-key",
            initial_messages=[{"role": "user", "content": "blocked input"}],
            tools=[],
        )]

    assert Client.calls == 1
    assert len(Client.urls) == 1
    assert Client.urls[0].endswith("/responses")
    assert "本轮已停止" in failure.value.public_message
    assert "req-private" not in failure.value.public_message


@pytest.mark.asyncio
async def test_stream_reconnects_five_times_then_recovers(monkeypatch) -> None:
    terminal_lines = [
        "data: " + json.dumps({
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
        }),
        "data: [DONE]",
    ]

    class Response:
        def __init__(self, *, status=200, lines=None, enter_error=None, body=b""):
            self.status_code = status
            self.lines = list(lines or [])
            self.enter_error = enter_error
            self.body = body

        async def __aenter__(self):
            if self.enter_error:
                raise self.enter_error
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return self.body

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    request = httpx.Request("POST", "https://example.test/chat/completions")
    attempts = [
        Response(enter_error=httpx.ConnectError("offline", request=request)),
        Response(status=503, body=b"unavailable"),
        Response(enter_error=httpx.ReadTimeout("timeout", request=request)),
        Response(status=502, body=b"bad gateway"),
        Response(enter_error=httpx.RemoteProtocolError("reset")),
        Response(lines=terminal_lines),
    ]

    class Client:
        calls = 0

        def stream(self, *_args, **_kwargs):
            type(self).calls += 1
            return attempts.pop(0)

    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    envelopes = [item async for item in model_driver._iter_model_stream_with_reconnect(
        client=Client(),
        url="https://example.test/chat/completions",
        payload={"stream": True},
        headers={},
        use_responses_transport=False,
        responses_fallback_allowed=False,
    )]

    retrying = [item for item in envelopes if item["kind"] == "retrying"]
    assert [item["attempt"] for item in retrying] == [1, 2, 3, 4, 5]
    assert [item["max_retries"] for item in retrying] == [5] * 5
    assert any(item["kind"] == "recovered" for item in envelopes)
    assert Client.calls == 6


@pytest.mark.asyncio
async def test_stream_stops_after_five_failed_reconnects(monkeypatch) -> None:
    class Response:
        status_code = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return b"service unavailable"

    class Client:
        calls = 0

        def stream(self, *_args, **_kwargs):
            type(self).calls += 1
            return Response()

    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    envelopes = []
    with pytest.raises(model_driver.ModelStreamRetriesExhausted):
        async for item in model_driver._iter_model_stream_with_reconnect(
            client=Client(),
            url="https://example.test/responses",
            payload={"stream": True},
            headers={},
            use_responses_transport=True,
            responses_fallback_allowed=False,
        ):
            envelopes.append(item)

    assert Client.calls == 6
    assert [item["attempt"] for item in envelopes if item["kind"] == "retrying"] == [
        1, 2, 3, 4, 5,
    ]
    assert envelopes[-1] == {"kind": "failed", "attempt": 5, "max_retries": 5}


@pytest.mark.asyncio
async def test_authentication_error_is_not_reconnected() -> None:
    from app.services.agent_harness.public_errors import ModelRequestRejected

    class Response:
        status_code = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aread(self):
            return b"unauthorized"

    class Client:
        calls = 0

        def stream(self, *_args, **_kwargs):
            type(self).calls += 1
            return Response()

    envelopes = []
    with pytest.raises(ModelRequestRejected) as failure:
        async for item in model_driver._iter_model_stream_with_reconnect(
            client=Client(),
            url="https://example.test/responses",
            payload={"stream": True},
            headers={},
            use_responses_transport=True,
            responses_fallback_allowed=False,
        ):
            envelopes.append(item)

    assert envelopes == []
    assert Client.calls == 1
    assert failure.value.status_code == 401
    assert "鉴权失败" in str(failure.value)
    assert "unauthorized" not in str(failure.value)


@pytest.mark.asyncio
async def test_interrupted_responses_tool_call_is_not_replayed_or_executed(
    monkeypatch,
) -> None:
    def sse(event: dict) -> str:
        return "data: " + json.dumps(event, ensure_ascii=False)

    tool_item = {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "id": "fc-retry",
            "type": "function_call",
            "call_id": "call-retry",
            "name": "mutate",
            "arguments": "{}",
        },
    }
    completed = sse({
        "type": "response.completed",
        "response": {"id": "resp-tool", "status": "completed"},
    })
    final_item = sse({
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "id": "msg-final-retry",
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "done"}],
        },
    })

    class Response:
        status_code = 200

        def __init__(self, lines, *, fail_after=False):
            self.lines = lines
            self.fail_after = fail_after

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line
            if self.fail_after:
                raise httpx.ReadError("connection reset")

    class Client:
        responses = [
            Response([sse(tool_item)], fail_after=True),
            Response([sse(tool_item), completed, "data: [DONE]"]),
            Response([
                final_item,
                sse({
                    "type": "response.completed",
                    "response": {"id": "resp-final", "status": "completed"},
                }),
                "data: [DONE]",
            ]),
        ]

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return type(self).responses.pop(0)

    calls = []

    async def mutate(_args):
        calls.append("mutate")
        return "changed"

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    # A completed function-call item is a Provider event and may have been charged even though
    # the Harness intentionally waits for response.completed before executing it.  Do not replay
    # the request: fail before the local side effect, leaving two scripted responses untouched.
    with pytest.raises(model_driver.ModelStreamReplayUnsafe):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-retry-test",
            api_key="retry-key",
            user_input="change",
            tools=[MainTool(name="mutate", description="mutate", parameters={}, execute=mutate)],
        )]

    assert calls == []
    assert len(Client.responses) == 2
