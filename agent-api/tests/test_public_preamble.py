"""Codex-style public preamble compatibility for reasoning-first Responses models."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_harness import orchestrator, public_commentary


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "这份文档的价值取决于论点是否站得住、结构是否支持落地。核心观点与章节关系会成组核对，再用关键细节验证可执行性。",
                }],
            }],
        }


class _EnvironmentResponse(_Response):
    def json(self) -> dict:
        return {
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "开发基础的关键是确认 Python 环境、项目结构和版本状态是否完整。目录与 Git 证据会一起核对，再据此判断能否直接开工。",
                }],
            }],
        }


@pytest.mark.asyncio
async def test_public_preamble_uses_selected_model_without_thinking(monkeypatch) -> None:
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, *, json, headers):
            captured.update({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await orchestrator._generate_public_preamble(
        "分析这个文件",
        model="deepseek-v4-flash",
        api_key="test-key",
        attachments=[{"filename": "Agent = Model + Harness.docx"}],
    )

    assert text == (
        "这份文档的价值取决于论点是否站得住、结构是否支持落地。"
        "核心观点与章节关系会成组核对，再用关键细节验证可执行性。"
    )
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["reasoning"] == {"effort": "none"}
    assert "thinking" not in captured["json"]
    assert captured["url"].endswith("/responses")
    assert "Agent = Model + Harness.docx" in captured["json"]["input"][0]["content"]
    assert "不要用‘我先’‘我会先’" in captured["json"]["input"][0]["content"]
    assert captured["headers"] == {"Authorization": "Bearer test-key"}


def test_public_preamble_is_published_before_main_turn_preflight() -> None:
    source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    published = source.index("yield channel.message_commentary(public_preamble)")
    preflight = source.index("await self._await_pending_partial_persist(thread_id)", published)
    prepare = source.index("await turn_prepare.prepare_turn(", published)

    assert published < preflight < prepare
    assert "【本轮已公开的过程首句】" in source
    assert "runtime_policy.public_preamble_guidance" in source
    assert "and not (runtime_policy and runtime_policy.project_answer)" not in source.split("public_preamble = \"\"")[1][:800]


@pytest.mark.asyncio
async def test_public_preamble_includes_domain_guidance(monkeypatch) -> None:
    captured: dict = {}

    async def generate(**kwargs):
        captured.update(kwargs)
        return "简历和岗位要求会对照着看，第一问会围绕已写明的经历来准备。"

    monkeypatch.setattr(orchestrator, "generate_public_commentary", generate)
    text = await orchestrator._generate_public_preamble(
        "开始模拟面试",
        model="deepseek-v4-flash",
        api_key="test-key",
        extra_guidance="你正在主持一场文字模拟面试。过程首句应说明马上对照简历和岗位要求，准备第一问。",
    )
    assert "第一问" in text
    prompt = captured["developer_prompt"]
    assert "文字模拟面试" in prompt
    assert "准备第一问" in prompt
    assert "不要用‘我先’‘我会先’" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", " \n\t ", "这是什么"])
@pytest.mark.parametrize("filename", ["uploaded.png", ""])
async def test_attachment_preamble_accepts_empty_text_without_claiming_to_have_seen_image(
    monkeypatch, message, filename,
) -> None:
    calls: list[dict] = []

    async def generate(**kwargs):
        calls.append(kwargs)
        return "图片已收到，查看画面和可辨认的文字后再核对需要解释的内容。"

    monkeypatch.setattr(orchestrator, "generate_public_commentary", generate)
    text = await orchestrator._generate_public_preamble(
        message,
        model="deepseek-v4-flash",
        api_key="test-key",
        attachments=[{
            "filename": filename, "kind": "image", "file_id": "owned-image",
            "image_url": "data:image/png;base64,not-for-preamble",
            "text": "This attachment content must not enter the preamble.",
        }],
        run_id="image-run",
        thread_id="image-thread",
    )

    assert text
    assert len(calls) == 1
    call = calls[0]
    prompt = call["developer_prompt"]
    assert "尚未读取文件正文或图片内容" in prompt
    assert "不得根据文件名猜测画面" in prompt
    assert "data:image" not in prompt
    assert "This attachment content" not in prompt
    if not message.strip():
        assert "未附文字说明；具体意图尚未明确" in prompt
    else:
        assert "用户请求：这是什么" in prompt
    assert call["run_id"] == "image-run"
    assert call["thread_id"] == "image-thread"
    assert call["purpose"] == "public_preamble"
    assert call["max_output_tokens"] == 180


@pytest.mark.asyncio
@pytest.mark.parametrize("model,api_key,attachments", [
    ("deepseek-v4-flash", "test-key", []),
    ("", "test-key", [{"kind": "image", "file_id": "owned-image"}]),
    ("deepseek-v4-flash", "", [{"kind": "image", "file_id": "owned-image"}]),
])
async def test_preamble_still_skips_empty_turn_or_missing_credentials(
    monkeypatch, model, api_key, attachments,
) -> None:
    async def unexpected_call(**_kwargs):
        pytest.fail("An empty or unconfigured turn must not call a model")

    monkeypatch.setattr(orchestrator, "generate_public_commentary", unexpected_call)
    assert await orchestrator._generate_public_preamble(
        " ", model=model, api_key=api_key, attachments=attachments,
    ) == ""


@pytest.mark.parametrize(
    "model",
    [
        "deepseek-v4-flash",
        "DeepSeek-R1",
        "provider/deepseek-chat",
        "deepseek_v4_flash_vision_exp",
    ],
)
def test_all_deepseek_aliases_use_reasoning_first_responses_preamble(model: str) -> None:
    assert orchestrator._is_deepseek_model(model) is True
    assert orchestrator._requires_reasoning_first_preamble(model) is True


@pytest.mark.parametrize("model", ["gpt-5.5", "qwen3-max", "claude-sonnet-4"])
def test_native_commentary_model_does_not_pay_for_compatibility_preamble(model: str) -> None:
    assert orchestrator._requires_reasoning_first_preamble(model) is False


@pytest.mark.asyncio
async def test_public_preamble_does_not_use_tool_round_internal_filter(monkeypatch) -> None:
    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return _EnvironmentResponse()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await orchestrator._generate_public_preamble(
        "检查当前工作区是否适合开发",
        model="deepseek-v4-flash",
        api_key="test-key",
    )

    assert text.startswith("开发基础的关键是确认 Python 环境")
    assert "再据此判断能否直接开工" in text


@pytest.mark.asyncio
async def test_public_commentary_does_not_retry_incomplete_response(monkeypatch) -> None:
    calls: list[dict] = []

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, *, json, headers):
            calls.append({"json": json, "headers": headers})
            payload = {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output_text": "从Qwen官方发布记录里把型号拎出来，再",
            }

            class _DynamicResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return payload

            return _DynamicResponse()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await public_commentary.generate_public_commentary(
        model="deepseek-v4-flash-vision-exp",
        api_key="test-key",
        developer_prompt="写一条公开进度说明。",
        user_prompt="开始。",
        max_output_tokens=180,
    )

    assert text == ""
    assert len(calls) == 1
    assert calls[0]["json"]["max_output_tokens"] == 180
    assert public_commentary.DEFAULT_TIMEOUT_SECONDS == 5.0
    assert public_commentary.MAX_ATTEMPTS == 1


@pytest.mark.asyncio
async def test_public_commentary_never_publishes_completed_half_sentence(monkeypatch) -> None:
    calls = 0

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1

            class _HalfSentenceResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {
                        "status": "completed",
                        "output_text": "从Qwen官方发布记录里把各个型号按发布顺序拎出来，再",
                    }

            return _HalfSentenceResponse()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await public_commentary.generate_public_commentary(
        model="deepseek-v4-flash-vision-exp",
        api_key="test-key",
        developer_prompt="写一条公开进度说明。",
        user_prompt="开始。",
    )

    assert text == ""
    assert calls == 1


@pytest.mark.asyncio
async def test_public_commentary_does_not_issue_repair_attempt_for_half_sentence(monkeypatch) -> None:
    calls = 0

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1

            class _RetryResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    if calls == 1:
                        return {
                            "status": "completed",
                            "output_text": "先核对现有材料，再",
                        }
                    return {
                        "status": "completed",
                        "output_text": "现有材料已开始核对，下一步会按结构与事实分组检查。",
                    }

            return _RetryResponse()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await public_commentary.generate_public_commentary(
        model="deepseek-v4-flash-vision-exp",
        api_key="test-key",
        developer_prompt="写一条公开进度说明。",
        user_prompt="开始。",
    )

    assert calls == 1
    assert text == ""


def test_public_commentary_bounds_at_a_complete_sentence() -> None:
    text = "第一句完整。" + "第二句在长度边界内无法完整收束，" * 3 + "再"

    assert public_commentary._completed_public_text(text, max_chars=32) == "第一句完整。"


@pytest.mark.asyncio
async def test_public_commentary_audits_one_incomplete_provider_attempt(monkeypatch) -> None:
    calls: dict[str, list] = {
        "logical": [],
        "attempt": [],
        "attempt_finish": [],
        "logical_finish": [],
    }
    logical = SimpleNamespace(logical_call_id="logical-public")
    attempt = SimpleNamespace(attempt_id="attempt-public")

    async def begin_logical(**kwargs):
        calls["logical"].append(kwargs)
        return logical

    async def begin_attempt(handle, **kwargs):
        calls["attempt"].append((handle, kwargs))
        return attempt

    async def finish_attempt(handle, **kwargs):
        calls["attempt_finish"].append((handle, kwargs))
        return True

    async def finish_logical(handle, **kwargs):
        calls["logical_finish"].append((handle, kwargs))
        return True

    monkeypatch.setattr(public_commentary.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(public_commentary.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(public_commentary.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(public_commentary.model_usage_audit, "finish_logical_call", finish_logical)

    class _IncompleteResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "id": "response-public",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 16,
                    "prompt_cache_hit_tokens": 100,
                },
                "output_text": "还没说完，再",
            }

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _IncompleteResponse()

    monkeypatch.setattr(public_commentary.httpx, "AsyncClient", _Client)

    text = await public_commentary.generate_public_commentary(
        model="deepseek-v4-flash",
        api_key="key",
        developer_prompt="写一句。",
        user_prompt="开始。",
        run_id="run-public",
        thread_id="thread-public",
    )

    assert text == ""
    assert len(calls["logical"]) == 1
    assert calls["logical"][0]["purpose"] == "public_preamble"
    assert len(calls["attempt"]) == 1
    assert calls["attempt"][0][1]["legacy_compatible"] is False
    assert calls["attempt"][0][1]["wire_payload"]["reasoning"] == {"effort": "none"}
    assert calls["attempt_finish"] == [(
        attempt,
        {
            "terminal_status": "incomplete",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 16,
                "prompt_cache_hit_tokens": 100,
            },
            "response_id": "response-public",
            "provider_event_seen": True,
            "terminal_seen": True,
            "http_status": 200,
            "error_code": "max_output_tokens",
            "committed": False,
        },
    )]
    assert calls["logical_finish"][0][1]["terminal_status"] == "incomplete"
