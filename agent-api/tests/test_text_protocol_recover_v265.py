"""v2.65: text tool protocol recovery."""
import json
import pytest
from app.services.platform.text_protocol_recover import (
    _KNOWN_TOOLS,
    looks_like_text_tool_payload,
    recover_text_tool_calls,
)
from app.services.platform.text_protocol_guard import (
    StreamingProtocolScrubber,
    scrub_text,
)
from app.services.chat.tools.plan import _normalize_plan_steps


def test_recover_update_plan_xml_leak():
    sample = (
        '<tool_call> <update_plan> {"plan": ['
        '{"step": "读取设计包与字体映射表", "status": "in_progress", "intent": "读取TEMPLATES.md"},'
        '{"step": "编写全部slide页面HTML", "status": "pending", "intent": "编写6-8页"},'
        '{"step": "编译并质检交付PPTX", "status": "pending", "intent": "运行build_deck.py"}'
        ' ] } </update_plan> </tool_call>'
    )
    assert looks_like_text_tool_payload(sample) is True
    calls = recover_text_tool_calls(sample)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "update_plan"
    args = json.loads(calls[0]["function"]["arguments"])
    assert "steps" in args
    steps = _normalize_plan_steps(args["steps"])
    assert len(steps) == 3
    assert steps[0]["title"] == "读取设计包与字体映射表"
    assert steps[0]["status"] == "running"
    assert "TEMPLATES" in steps[0]["detail"]


def test_scrubber_keeps_full_drop_for_recovery():
    sample = "前文" + ("x" * 50) + '<tool_call><update_plan>{"plan":[{"step":"A","status":"pending"}]}</update_plan></tool_call>'
    sc = StreamingProtocolScrubber()
    safe = sc.feed(sample) + sc.flush()
    assert sc.leaked is True
    assert "前文" in safe
    assert "tool_call" not in safe
    assert len(sc.dropped_full) > 50
    calls = recover_text_tool_calls(sc.dropped_full)
    assert calls and calls[0]["function"]["name"] == "update_plan"


def test_scrub_text_truncates_marker():
    safe, leaked = scrub_text('ok<tool_call>{"a":1}')
    assert leaked is True
    assert safe == "ok"


@pytest.mark.parametrize("name", sorted(_KNOWN_TOOLS))
@pytest.mark.parametrize("prefix", ["", "准备执行下一步\n"])
def test_json_detection_and_recovery_share_all_known_tools(name, prefix):
    sample = prefix + json.dumps({"arguments": {"value": "example"}, "name": name})
    assert looks_like_text_tool_payload(sample)
    calls = recover_text_tool_calls(sample)
    assert len(calls) == 1 and calls[0]["function"]["name"] == name


@pytest.mark.parametrize("name", ["browser_open", "edit_file", "ask_user_choice"])
@pytest.mark.parametrize("shape", ["fenced", "wrapped", "xml"])
def test_complete_tool_envelopes_are_detected_without_a_second_name_list(name, shape):
    envelope = {"name": name, "arguments": {"value": "example"}}
    sample = (
        "```json\n" + json.dumps(envelope) + "\n```" if shape == "fenced" else
        json.dumps({"tool_calls": [{"type": "function", "function": envelope}]}) if shape == "wrapped" else
        f'<{name}>{{"value":"example"}}</{name}>'
    )
    assert looks_like_text_tool_payload(sample)
    assert recover_text_tool_calls(sample)[0]["function"]["name"] == name


@pytest.mark.parametrize("sample", [
    '{"name":"browser_open","description":"a browser capability"}',
    '{"name":"unknown_function","arguments":{}}',
    '# 协议研究报告\n下列是代码示例，不是调用请求。[1]\n```json\n{"name":"browser_open","arguments":{}}\n```',
])
def test_tool_mentions_and_report_code_examples_are_not_executed(sample):
    assert not looks_like_text_tool_payload(sample)
    assert not recover_text_tool_calls(sample)


def test_malformed_envelope_is_detected_but_never_recovered_with_empty_arguments():
    sample = '{"name":"edit_file","arguments":[1,2,3]}'
    assert looks_like_text_tool_payload(sample)
    assert not recover_text_tool_calls(sample)
