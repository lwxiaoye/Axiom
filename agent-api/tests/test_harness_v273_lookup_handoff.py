# -*- coding: utf-8 -*-
"""v2.73: short lookup answers must not be treated as handoff lists."""
from pathlib import Path
import re


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_lookup_substantive_regex_and_handoff_guard_present():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "_LOOKUP_SUBSTANTIVE_ANSWER_RE" in src
    assert "if _LOOKUP_SUBSTANTIVE_ANSWER_RE.search(t):" in src
    # 短答不再触发「auto-continue 收口」网：那是按关键词推断收尾方式的旧机制，已退役
    assert "net_auto_continue_close_lookup" not in src


def test_lookup_substantive_matches_weather_short_answer():
    # execute only the regex definition
    src = _src("app/services/agent_harness/model_driver.py")
    start = src.find("_LOOKUP_SUBSTANTIVE_ANSWER_RE = re.compile(")
    end = src.find("_HANDOFF_PHRASES = (", start)
    ns = {"re": re}
    exec(src[start:end], ns)
    rx = ns["_LOOKUP_SUBSTANTIVE_ANSWER_RE"]
    assert rx.search("漳州今天（8月6日）：晴，气温 26℃～35℃，风力 3 级。")
    assert rx.search("最高温 35℃，注意防晒。")
    assert not rx.search("你可以选择：1. 自己查 2. 换个城市")


def test_model_selector_prefers_deepseek_flash():
    # FE lives outside agent-api container mount in some deploy modes; skip if absent.
    fe = Path(__file__).resolve().parents[2].joinpath(
        "src/views/peopleCenter/components/ModelSelector.vue"
    )
    if not fe.exists():
        return
    src = fe.read_text(encoding="utf-8")
    assert "deepseek-v4-flash" in src
    assert "preferredIds" in src
