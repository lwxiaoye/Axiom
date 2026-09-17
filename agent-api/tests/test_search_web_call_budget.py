# -*- coding: utf-8 -*-
"""search_web quality heuristics without a hard per-turn call cap."""
import pytest

from app.services.chat.tools.base import _model_text
from app.services.chat.tools.web import build_web_tools
from app.services.knowledge import web_search_service


def _ok_payload(*, images=None, results=None, text="ok"):
    rich = {
        "title": "City forecast",
        "url": "https://example.com/a",
        "content": "Today cloudy 26-36℃ humidity 70% wind 2 level, tomorrow light rain 27-33℃.",
    }
    return {
        "enabled": True,
        "results": results if results is not None else [rich],
        "text": text,
        "scraped_pages": [],
        "images": images or [],
        "error": "",
    }


def _empty_payload():
    return _ok_payload(results=[], text="empty")


def test_search_web_is_serial_for_budget_integrity():
    """v2.47: search_web must not parallel_safe, or same-batch dual calls bypass budget."""
    tool = build_web_tools()[0]
    assert tool.name == "search_web"
    assert tool.readonly is True
    assert tool.parallel_safe is False


@pytest.mark.asyncio
async def test_second_search_is_model_controlled_when_first_had_results(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools()[0]

    r1 = await tool.observe({"query": "weather a"})
    r2 = await tool.observe({"query": "weather b"})

    assert r1.status == "succeeded"
    assert r2.status == "succeeded"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_second_search_allowed_when_first_empty(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _empty_payload()
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools()[0]
    r1 = await tool.observe({"query": "weather a"})
    r2 = await tool.observe({"query": "weather b"})
    assert r1.status == "succeeded"
    assert r2.status == "succeeded"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_second_search_allowed_when_user_needs_images_still_empty(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok_payload(images=[])
        return _ok_payload(images=[{
            "url": "https://cdn.example.com/a.jpg",
            "title": "sea",
            "source": "https://example.com",
        }])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink = []
    tool = build_web_tools(image_sink=sink, user_message="weather and photos please")[0]
    r1 = await tool.observe({"query": "city weather"})
    r2 = await tool.observe({"query": "island photo"})
    assert r1.status == "succeeded"
    assert r2.status == "succeeded"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_third_search_web_remains_available(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        return _empty_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools()[0]
    r1 = await tool.observe({"query": "a"})
    r2 = await tool.observe({"query": "b"})
    r3 = await tool.observe({"query": "c"})
    assert r1.status == "succeeded"
    assert r2.status == "succeeded"
    assert r3.status == "succeeded"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_new_tool_build_has_independent_state(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    t1 = build_web_tools()[0]
    await t1.observe({"query": "a"})
    r_block = await t1.observe({"query": "b"})
    assert r_block.status == "succeeded"
    t2 = build_web_tools()[0]
    r2 = await t2.observe({"query": "d"})
    assert r2.status == "succeeded"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_description_mentions_combined_search_and_budget():
    tool = build_web_tools()[0]
    assert ("优先只搜" in tool.description) or ("1 次" in tool.description)
    assert "照片" in tool.description


@pytest.mark.asyncio
async def test_second_search_allowed_when_first_thin(monkeypatch):
    """Portal-shell snippets must not freeze the budget."""
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok_payload(
                results=[{
                    "title": "入口",
                    "url": "https://example.com/portal",
                    "content": "2026年5月26日 - 天气公报每日天气提示春运气象服务专报气象灾害预警重要天气提示",
                }],
                text="thin",
            )
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools()[0]
    r1 = await tool.observe({"query": "city weather"})
    r2 = await tool.observe({"query": "city forecast temp"})
    assert r1.status == "succeeded"
    assert "偏薄" in _model_text(r1) or "再搜" in _model_text(r1)
    assert r2.status == "succeeded"
    assert calls["n"] == 2


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_stale_fresh_query_allows_second_search(monkeypatch):
    """有温度但日期过旧 + 用户问今天：不得锁预算。"""
    calls = {"n": 0, "queries": []}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        calls["queries"].append(q)
        if calls["n"] == 1:
            return _ok_payload(
                results=[{
                    "title": "漳州天气公报",
                    "url": "https://example.com/old",
                    "content": "7月30日漳州最高气温 35.8℃，湿度 85%，南风 2.3–3.3 m/s。",
                    "publishedDate": "2026-07-30",
                }],
                text="stale",
            )
        return _ok_payload(
            results=[{
                "title": "漳州今日天气",
                "url": "https://example.com/new",
                "content": "8月5日漳州多云，气温 26-36℃，湿度 70%。",
                "publishedDate": "2026-08-05",
            }],
            text="fresh",
        )

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools(user_message="你帮我看看今天漳州市的天气状况")[0]
    r1 = await tool.observe({"query": "漳州天气"})
    r2 = await tool.observe({"query": "漳州 8月5日 天气 实况"})
    assert r1.status == "succeeded"
    body1 = _model_text(r1)
    assert ("偏旧" in body1) or ("过期" in body1) or ("再搜" in body1)
    assert r2.status == "succeeded"
    assert calls["n"] == 2
    assert all(str(q).strip() for q in calls["queries"])


@pytest.mark.asyncio
async def test_stale_without_temporal_remains_model_controlled(monkeypatch):
    """无时效紧迫词时，带数字的旧结果仍算可用并锁二次搜索。"""
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, **_kw):
        calls["n"] += 1
        return _ok_payload(
            results=[{
                "title": "历史公报",
                "url": "https://example.com/hist",
                "content": "7月30日漳州最高气温 35.8℃，湿度 85%，南风 2.3-3.3 m/s，午后体感闷热，夜间约 30.7℃。",
                "publishedDate": "2026-07-30",
            }],
        )

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools(user_message="总结一下这份旧天气公报的数据")[0]
    r1 = await tool.observe({"query": "漳州 7月30日 天气公报"})
    r2 = await tool.observe({"query": "再搜一次"})
    assert r1.status == "succeeded"
    assert r2.status == "succeeded"
    assert calls["n"] == 2


def test_search_results_usable_helper():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat.tools.web import _search_results_usable

    assert _search_results_usable([]) is False
    assert _search_results_usable([{"content": "短"}]) is False
    assert _search_results_usable([{
        "content": "Today 26-36℃ sunny humidity 80% light wind, UV high",
    }]) is True
    now = datetime(2026, 8, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    stale = [{
        "content": "7月30日漳州最高气温 35.8℃，湿度 85%，南风 2.3-3.3 m/s，夜间约 30.7℃，体感闷热。",
        "publishedDate": "2026-07-30",
    }]
    assert _search_results_usable(
        stale, query="漳州天气", user_message="今天天气如何", now=now,
    ) is False
    assert _search_results_usable(
        stale, query="历史公报", user_message="总结旧数据", now=now,
    ) is True
    fresh = [{
        "content": "8月5日漳州多云到晴，气温 26-36℃，湿度 70%，东南风 2-3 级，紫外线较强。",
        "publishedDate": "2026-08-05",
    }]
    assert _search_results_usable(
        fresh, query="漳州天气", user_message="今天天气", now=now,
    ) is True


def test_cross_year_archive_not_usable_without_past_year_anchor():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat.tools.web import _search_results_usable

    now = datetime(2026, 8, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = [{
        "content": "2025年8月6日漳州小雨转多云，气温 26-33℃，湿度 80%，南风 2 级。",
        "publishedDate": "2025-08-06",
    }]
    assert _search_results_usable(
        archive, query="漳州天气", user_message="查一下漳州天气并附图", now=now,
    ) is False
    assert _search_results_usable(
        archive, query="漳州 2025年8月6日 天气", user_message="查历史天气", now=now,
    ) is True


def test_with_today_date_token_is_passthrough():
    from datetime import datetime
    from app.services.chat.tools.web_freshness import with_today_date_token
    now = datetime(2026, 8, 6, 12, 0, 0)
    assert with_today_date_token("漳州天气", now=now) == "漳州天气"
    assert with_today_date_token("漳州 天气", now=now) == "漳州 天气"




def test_compact_search_query_strips_polite_and_dates():
    from app.services.chat.tools.web_freshness import compact_search_query
    assert compact_search_query("漳州 天气") == "漳州 天气"
    out = compact_search_query("请帮我查一下今天8月6日漳州市的天气实况")
    assert "漳州" in out and "天气" in out
    assert "今天" not in out and "8月" not in out and "实况" not in out
    out2 = compact_search_query("漳州 2026年8月6日 天气 实况 预报")
    assert "漳州" in out2 and "天气" in out2
    assert "2026" not in out2 and "实况" not in out2
    assert compact_search_query("东山岛 风景 照片") == "东山岛 风景 照片"

def test_mixed_undated_concrete_not_poisoned_by_old_news():
    """同组混有旧闻日期时，无日期但有 ℃ 的预报摘要仍应 usable（v2.43）。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat.tools.web_freshness import (
        has_fresh_enough_concrete,
        results_look_stale,
        search_results_usable,
    )

    now = datetime(2026, 8, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
    mixed = [
        {
            "title": "旧闻",
            "content": "7月16日漳州晨间晴、26℃、西北风 3–4 级。",
            "publishedDate": "2026-07-16",
            "url": "https://example.com/old",
        },
        {
            "title": "预报卡",
            "content": "漳州晴、26℃~35℃、3 级风，次日多云、27℃~36℃。",
            "url": "https://example.com/forecast",
        },
    ]
    assert has_fresh_enough_concrete(mixed, now=now) is True
    assert results_look_stale(mixed, now=now) is False
    assert search_results_usable(
        mixed, query="漳州 天气", user_message="帮我看看今天漳州市的天气状况", now=now,
    ) is True


def test_all_dated_old_concrete_still_stale_for_fresh_query():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat.tools.web_freshness import search_results_usable

    now = datetime(2026, 8, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
    only_old = [{
        "title": "旧闻",
        "content": "7月16日漳州晨间晴、26℃、西北风 3–4 级，白天最高 32℃。",
        "publishedDate": "2026-07-16",
        "url": "https://example.com/old",
    }]
    assert search_results_usable(
        only_old, query="漳州 天气", user_message="今天漳州天气", now=now,
    ) is False
