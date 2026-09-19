# -*- coding: utf-8 -*-
"""browser_fetch 使用策略锁定（2026-07-28 用户拍板）。

网关表全量实测：search_web 成功率 ≈99%（1024/1031），browser_fetch ≈47%（55/117，
失败约一半是服务级故障、四成是目标站反爬）。且 search_web 管线自带网页正文抓取，
逐条打开搜索命中的链接几乎没有信息增量、只有失败风险。

拍板策略：**默认一律 search_web（深度研究也是）；browser_fetch 仅用于用户点名
给出的网址。** 口径落在两处：工具描述（模型选工具时读的那段）和 Deep Research 规约。
基础提示词的浏览器块按 Harness 规范只做能力说明、**不规定工具顺序**（「平台不规定检索、
打开或下载的固定顺序」），所以那里只锁「browser_fetch = 读指定 URL」以及
「或你已经知道该看哪个页面」这句许可证不得回流。历史教训是同类口径改一处漏两处
（见 test_prompt_tool_consistency）。
"""
import pytest

from app.core.config import settings
from app.services.chat.tools import build_tools
from app.services.chat.turn_context_builder import _build_system_prompt
from app.services.chat.turn_decision import TurnDecision


@pytest.mark.asyncio
async def test_tool_description_scopes_fetch_to_user_named_urls(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://browser.test", raising=False)
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u-policy",
        thread_id="th-policy", newapi_key="k", run_id="r-policy",
        user_message="随便查点东西", turn_intent="execution", action_authority="mutate",
    )
    fetch = next((t for t in tools if t.name == "browser_fetch"), None)
    assert fetch is not None, "BROWSER_SERVICE_URL 非空时 browser_fetch 必须注册"
    desc = fetch.description
    assert "用户" in desc and "search_web" in desc, "描述必须把使用范围限定到用户点名的网址，并把调研引到 search_web"
    assert "不要再逐条打开" in desc or "不要拿它逐条打开" in desc, "必须明说别逐条抓搜索命中的链接——这正是 47% 成功率的主要浪费源"


def test_base_prompt_browser_block_is_capability_only(monkeypatch):
    # 不能用「prompt 里有没有 browser_fetch」判断浏览器块是否存在：intent 工具清单里
    # 无条件写着 browser_fetch。直接把服务地址 patch 上，让浏览器块确定出现。
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://browser.test", raising=False)
    prompt = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="")
    assert "网页读取与浏览" in prompt, "BROWSER_SERVICE_URL 非空时浏览器块必须出现"
    assert "browser_fetch 用于读取指定 URL" in prompt, "基础提示词只说明能力：browser_fetch = 读指定 URL"
    assert "平台不规定检索、打开或下载的固定顺序" in prompt, (
        "Harness 规范：基础提示词不规定工具顺序；search_web 优先的口径由工具描述和 Deep Research 规约承担"
    )
    assert "或你已经知道该看哪个页面" not in prompt, (
        "这半句是模型主动抓搜索命中链接的许可证（2026-07-28 已收掉），不要再加回来"
    )

    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "", raising=False)
    prompt_without = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="")
    assert "网页读取与浏览" not in prompt_without, "服务未配置时不注册浏览器工具，也不得教模型用它"


def test_deep_research_block_keeps_the_same_policy():
    block = TurnDecision("execute", "mutate", "research", research_profile=True).prompt_block()
    assert "search_web" in block and "browser_fetch" in block
    assert "用户点名" in block, "深度研究规约必须与全局口径一致：browser_fetch 只用于用户点名的网址"
