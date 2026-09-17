import asyncio

import app.services.workflows.prompt_debug_service as prompt_debug_service
from app.services.workflows.prompt_debug_service import (
    complete_prompt_experiment,
    interpolate_prompt,
    prompt_generation_messages,
)


def test_interpolate_prompt_preserves_unknown_variables_and_serializes_structured_values():
    result = interpolate_prompt(
        "你好，{{ user.name }}；资料={{ profile }}；未知={{ missing }}",
        {"user.name": "小青", "profile": {"campus": "南山"}},
    )

    assert result == '你好，小青；资料={"campus": "南山"}；未知={{ missing }}'


def test_prompt_experiment_returns_only_the_model_raw_output(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(prompt_debug_service.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(
        complete_prompt_experiment(
            api_key="test-key",
            model="test-model",
            messages=[{"role": "user", "content": "你好"}],
        )
    )

    assert result["rawOutput"] == '{"answer":"ok"}'
    assert set(result) == {"model", "rawOutput", "durationMs", "usage"}


def test_prompt_generation_messages_include_product_context_without_treating_it_as_instructions():
    messages = prompt_generation_messages(
        app_name="迎新助手",
        app_description="回答新生报到问题",
        current_prompt="",
        goal="准确、简洁地回答新生问题",
        variable_keys=["studentName", "college"],
    )

    assert messages[0]["role"] == "system"
    assert "只返回可直接使用的系统提示词正文" in messages[0]["content"]
    assert "迎新助手" in messages[1]["content"]
    assert "{{studentName}}" in messages[1]["content"]
    assert "准确、简洁地回答新生问题" in messages[1]["content"]
