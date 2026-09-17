import inspect
from unittest.mock import AsyncMock

import pytest

from app.services.chat.builtin_assistants.campus_services.policy import campus_knowledge_prompt, campus_turn_guard
from app.services.chat.tools.knowledge import (
    attach_chat_images,
    iter_embedded_images,
    materialize_chat_images,
    normalize_chat_image_url,
)


def test_campus_keeps_main_agent_loop_and_two_tools():
    from app.services.chat.builtin_assistants.campus_services.policy import CAMPUS_ALLOWED_TOOL_NAMES
    from app.services.chat import main_tool_turn
    from app.services.agent_harness import orchestrator
    from app.services.chat.builtin_assistants.runtime import (
        builtin_tool_build_options,
        get_builtin_runtime_policy,
    )

    assert CAMPUS_ALLOWED_TOOL_NAMES == {"search_knowledge", "search_web"}
    turn_src = inspect.getsource(main_tool_turn)
    orch_src = inspect.getsource(orchestrator)
    assert 'image_delivery_mode="off" if campus_mode' not in turn_src
    assert "**builtin_tool_build_options(" in turn_src
    assert "**builtin_tool_build_options(" in orch_src
    assert builtin_tool_build_options(
        get_builtin_runtime_policy("campus_services"),
    )["image_delivery_mode"] == "chat_inline"
    assert "async for payload in main_tool_turn.run_agent_turn(env):" in orch_src
    guard = campus_turn_guard()
    assert "search_web" in guard
    assert "[图N]" in guard
    assert "不要把检索计划" in guard


def test_normalize_keeps_same_origin_knowledge_images():
    assert normalize_chat_image_url("</api/sys/common/static/knowledge/a.png>") == (
        "/api/sys/common/static/knowledge/a.png"
    )
    assert normalize_chat_image_url("/api/sys/common/static/knowledge/a.png") == (
        "/api/sys/common/static/knowledge/a.png"
    )
    assert normalize_chat_image_url("https://www.example.edu.cn/map.png") == (
        "https://www.example.edu.cn/map.png"
    )
    assert normalize_chat_image_url("data:image/png;base64,abc") == ""


def test_materialize_rewrites_knowledge_markdown_to_figure_refs():
    sink: list = []
    text = "办理流程如下。\n\n![入学流程图](</api/sys/common/static/knowledge/flow.png>)\n\n再到教务处。"
    rewritten = materialize_chat_images(
        text, sink, extra_urls=["https://www.example.edu.cn/gate.jpg"], source="入学须知",
    )
    assert "[图1]" in rewritten
    assert "![入学流程图]" not in rewritten
    assert sink[0]["url"] == "/api/sys/common/static/knowledge/flow.png"
    assert sink[0]["display_scope"] == "chat_inline"
    assert any(item["url"] == "https://www.example.edu.cn/gate.jpg" for item in sink)
    assert campus_knowledge_prompt("hit", rewritten).count("[图N]") >= 1


def test_iter_embedded_images_reads_html_and_markdown():
    text = '见下图 ![校园](https://www.school.edu.cn/a.jpg) 和 <img src="https://www.school.edu.cn/b.png">'
    urls = [url for url, _title in iter_embedded_images(text)]
    assert urls == [
        "https://www.school.edu.cn/a.jpg",
        "https://www.school.edu.cn/b.png",
    ]


@pytest.mark.asyncio
async def test_official_domain_search_keeps_page_images(monkeypatch):
    from app.services.chat.tools.web import build_web_tools
    from app.services.knowledge import web_search_service

    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        return {
            "enabled": True,
            "results": [{
                "title": "校园风光",
                "url": "https://www.school.edu.cn/scenery",
                "content": "校园一角\n\n![东门](https://cdn.school.edu.cn/east.jpg)",
            }],
            "text": "[1] 校园风光\n校园一角\n\n![东门](https://cdn.school.edu.cn/east.jpg)\n来源: https://www.school.edu.cn/scenery",
            "scraped_pages": [{"title": "校园风光", "url": "https://www.school.edu.cn/scenery"}],
            "images": [{
                "url": "https://random-cdn.example/x.jpg",
                "title": "无关图",
                "source": "https://example.com/p",
            }],
            "error": "",
        }

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="怎么办理入学，学校的风景怎么样",
        image_delivery_mode="chat_inline",
        allowed_domains=[{"host": "school.edu.cn", "include_subdomains": True}],
    )[0]
    obs = await tool.execute({"query": "学校 入学 校园风景"})
    content = getattr(obs, "model_content", None) or str(obs)
    assert seen["with_images"] is False
    assert any(item["url"] == "https://cdn.school.edu.cn/east.jpg" for item in sink)
    assert all("random-cdn.example" not in str(item.get("url") or "") for item in sink)
    assert "[图1]" in content
    assert "![东门]" not in content


@pytest.mark.asyncio
async def test_empty_official_allowlist_fails_closed_for_web_images(monkeypatch):
    from app.services.chat.tools.web import build_web_tools
    from app.services.knowledge import web_search_service

    seen = {}

    async def _search(q, start_index=1, with_images=False, **kwargs):
        seen["allowed_domains"] = kwargs.get("allowed_domains")
        return {
            "enabled": True,
            "results": [{
                "title": "第三方校园图",
                "url": "https://gaokao.example/campus",
                "content": "第三方内容",
            }],
            "text": "第三方内容",
            "scraped_pages": [],
            "images": [{
                "url": "https://img.gaokao.example/campus.jpg",
                "title": "校园风光",
                "source": "https://gaokao.example/campus",
            }],
            "error": "",
        }

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="给我看看校园图片",
        image_delivery_mode="chat_inline",
        allowed_domains=[],
    )[0]
    await tool.execute({"query": "校园风光 图片"})

    assert seen["allowed_domains"] == []
    assert sink == []


@pytest.mark.asyncio
async def test_empty_official_allowlist_never_calls_general_search_provider(monkeypatch):
    from app.services.knowledge import web_search_service

    stage_search = AsyncMock(return_value=([], ""))
    monkeypatch.setattr(
        web_search_service.cfg,
        "get_web_search_config",
        AsyncMock(return_value={"enabled": True}),
    )
    monkeypatch.setattr(web_search_service, "_stage_search", stage_search)

    result = await web_search_service.search_web(
        "校园风光 图片",
        with_images=True,
        allowed_domains=[],
    )

    stage_search.assert_not_awaited()
    assert result["results"] == []
    assert result["images"] == []
    assert result["official_domain_no_hit"] is True
    assert "未执行网页或图片搜索" in result["text"]


def test_duplicate_urls_reuse_figure_index():
    sink: list = []
    attach_chat_images(sink, [{"url": "https://www.school.edu.cn/a.png", "title": "A"}])
    mapping = attach_chat_images(sink, [{"url": "https://www.school.edu.cn/a.png", "title": "A2"}])
    assert len(sink) == 1
    assert mapping["https://www.school.edu.cn/a.png"] == 1
