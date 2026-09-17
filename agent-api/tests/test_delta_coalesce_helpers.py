"""message.delta 合并助手：跨进程泵只改 text、保留信封。"""
from app.services.chat.run_hub import (
    _delta_text_of,
    _payload_is_message_delta,
    _payload_is_stream_delta,
    _rewrite_delta_text,
    coalesce_delta_texts,
)


def _delta(text: str, seq: int = 1) -> str:
    import json
    body = {
        "version": "harness/1",
        "type": "message.delta",
        "data": {"text": text},
        "sequence": seq,
        "run_id": "r1",
        "event_id": f"e{seq}",
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def test_rewrite_keeps_sequence_and_merges_text():
    raw = _delta("甲", 7)
    assert _payload_is_message_delta(raw)
    assert _delta_text_of(raw) == "甲"
    merged = _rewrite_delta_text(raw, "甲乙丙")
    assert _delta_text_of(merged) == "甲乙丙"
    assert '"sequence": 7' in merged or '"sequence":7' in merged.replace(" ", "")
    assert not _payload_is_message_delta("data: {\"type\":\"tool.started\"}\n\n")


def test_coalesced_delta_concat_equals_uncoalesced():
    tokens = ["你", "好", "，", "世", "界", "。" + ("补" * 70)]
    groups = coalesce_delta_texts(tokens)
    assert "".join(groups) == "".join(tokens)
    assert groups[0] == "你"
    assert all(groups)


def _reasoning(text: str, seq: int = 1) -> str:
    import json
    body = {
        "version": "harness/1",
        "type": "message.reasoning.delta",
        "data": {"text": text},
        "sequence": seq,
        "run_id": "r1",
        "event_id": f"e{seq}",
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def test_reasoning_delta_is_stream_delta_and_keeps_type():
    raw = _reasoning("想", 4)
    assert _payload_is_stream_delta(raw)
    assert not _payload_is_message_delta(raw)
    assert _delta_text_of(raw) == "想"
    merged = _rewrite_delta_text(raw, "想清楚")
    assert _delta_text_of(merged) == "想清楚"
    assert "message.reasoning.delta" in merged
    assert '"sequence": 4' in merged or '"sequence":4' in merged.replace(" ", "")
