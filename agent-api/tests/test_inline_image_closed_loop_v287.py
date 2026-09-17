"""v2.87: 对话附图硬闭环——有图源必须出 [图N]。"""

from app.services.chat.turn_finalizer import normalize_inline_image_refs
from app.services.agent_harness.model_driver import _trace_image_urls, _chat_inline_image_only_goal


def test_normalize_appends_when_missing_refs():
    urls = [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
        "https://cdn.example.com/c.jpg",
    ]
    out = normalize_inline_image_refs("漳州今天多云，气温 28℃。", image_urls=urls)
    assert "[图1]" in out
    assert "[图2]" in out
    assert "[图3]" in out
    assert "28℃" in out


def test_normalize_keeps_existing_refs():
    urls = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    out = normalize_inline_image_refs("天气不错\n\n[图1]", image_urls=urls)
    assert out.count("[图1]") == 1
    # 已有引用时不强制堆更多
    assert "[图2]" not in out or "[图1]" in out


def test_normalize_turns_labeled_image_link_into_renderable_ref():
    url = "https://cdn.example.com/dongshan.jpg"
    out = normalize_inline_image_refs(
        f"东山岛海岸风光。\n\n[图1]({url})",
        image_urls=[url],
    )
    assert out == "东山岛海岸风光。\n\n[图1]"
    assert url not in out


def test_trace_image_urls_from_observation_ui():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "observation": {
            "structured_data": {
                "ui": {
                    "images": [
                        {"url": "https://img.example.com/1.jpg", "title": "a"},
                        {"url": "https://img.example.com/2.jpg", "title": "b"},
                    ]
                }
            }
        },
    }]
    urls = _trace_image_urls(trace)
    assert urls == [
        "https://img.example.com/1.jpg",
        "https://img.example.com/2.jpg",
    ]


def test_trace_image_urls_from_meta():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "meta": {"images": [{"url": "https://img.example.com/x.png"}]},
    }]
    assert _trace_image_urls(trace) == ["https://img.example.com/x.png"]


def test_trace_image_urls_ignores_non_http():
    trace = [{
        "name": "search_web",
        "meta": {"images": [{"url": "data:image/png;base64,xxx"}, {"url": "https://ok.example/a.jpg"}]},
    }]
    assert _trace_image_urls(trace) == ["https://ok.example/a.jpg"]


def test_chat_inline_image_goal_negated_save():
    msg = "帮我看看漳州天气，附上几张照片，只要对话里看，不要保存到我的文件"
    assert _chat_inline_image_only_goal(msg) is True


def test_main_tool_turn_has_image_sink_closed_loop():
    """源码契约：落库前必须用 image_sink 强制 normalize。"""
    from pathlib import Path
    candidates = [
        Path("/app/app/services/chat/main_tool_turn.py"),
        Path(__file__).resolve().parents[1] / "app/services/chat/main_tool_turn.py",
    ]
    src = next(c.read_text(encoding="utf-8") for c in candidates if c.exists())
    assert "normalize_inline_image_refs" in src
    assert "image_sink" in src
    assert "forced inline image refs" in src


def test_artifact_image_sources_do_not_enter_chat_citations():
    from app.services.chat.turn_context_builder import _image_sources

    sink = [
        {"url": "https://cdn.example.com/chat.jpg", "display_scope": "chat_inline"},
        {"url": "https://cdn.example.com/ppt.jpg", "display_scope": "artifact_only"},
    ]
    sources = _image_sources(sink)
    assert [item["url"] for item in sources] == ["https://cdn.example.com/chat.jpg"]

def test_normalize_skips_force_when_model_declines_images():
    urls = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    text = (
        "漳州今天约 37℃。"
        "本次检索返回的配图目录中均为无关主题，没有可用的漳州当地实景照片，为避免误导未附图。"
    )
    out = normalize_inline_image_refs(text, image_urls=urls)
    assert "[图1]" not in out
    assert "未附图" in out


def test_normalize_force_when_model_forgets():
    urls = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    out = normalize_inline_image_refs("漳州今天多云 28℃。", image_urls=urls)
    assert "[图1]" in out and "[图2]" in out


def test_no_step0_provisional_plan_seed_v287():
    from pathlib import Path
    candidates = [
        Path("/app/app/services/agent_harness/model_driver.py"),
        Path(__file__).resolve().parents[1] / "app/services/agent_harness/model_driver.py",
    ]
    src = next(c.read_text(encoding="utf-8") for c in candidates if c.exists())
    # step-0 块里不应再有 provisional_plan_steps([], _goal0)
    assert "_provisional_plan_steps([], _goal0)" not in src

def test_file_create_not_chat_lookup_v287():
    from app.services.agent_harness.model_driver import (
        _goal_is_chat_lookup,
        _goal_wants_workspace_product,
        _user_requires_file_deliverable,
    )
    seed = "新建 continue-v285.md，内容只写 seed-v285，写完就停。不要搜索。"
    assert _goal_wants_workspace_product(seed) is True
    assert _user_requires_file_deliverable(seed) is True
    assert _goal_is_chat_lookup(seed) is False

    cont = "继续，把内容改成 seed-v285-continued"
    assert _goal_is_chat_lookup(cont) is False

    bare = "继续"
    assert _goal_is_chat_lookup(bare) is False

    weather = "你帮我看看今天漳州市的天气状况，并附上几张当地照片，只要对话里看就行，不要保存到我的文件。"
    assert _goal_is_chat_lookup(weather) is True

    research = "我需要做一个市场调研，内容是关于现在的网页端的agent哪些比较好用"
    assert _goal_is_chat_lookup(research) is True
