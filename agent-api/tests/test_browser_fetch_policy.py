# -*- coding: utf-8 -*-
"""browser_fetch 使用策略锁定（2026-07-28 用户拍板）。

网关表全量实测：search_web 成功率 ≈99%（1024/1031），browser_fetch ≈47%（55/117，
失败约一半是服务级故障、四成是目标站反爬）。且 search_web 管线自带网页正文抓取，
逐条打开搜索命中的链接几乎没有信息增量、只有失败风险。

拍板策略：**默认一律 search_web（深度研究也是）；browser_fetch 仅用于用户点名
给出的网址。** 该口径散布三处（工具描述/基础提示词浏览器块/Deep Research 规约），
本测试锁住三处不漂移——历史教训是同类口径改一处漏两处（见 test_prompt_tool_consistency）。
"""
import pytest

from app.services.chat.tools import build_tools
from app.services.chat.turn_context_builder import _build_system_prompt
from app.services.chat.turn_decision import TurnDecision


@pytest.mark.asyncio
async def test_tool_description_scopes_fetch_to_user_named_urls():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u-policy",
        thread_id="th-policy", newapi_key="k", run_id="r-policy",
        user_message="随便查点东西", turn_intent="execution", action_authority="mutate",
    )
    fetch = next((t for t in tools if t.name == "browser_fetch"), None)
    if fetch is None:
        pytest.skip("BROWSER_SERVICE_URL 未配置，browser_fetch 未注册")
    desc = fetch.description
    assert "用户" in desc and "search_web" in desc, "描述必须把使用范围限定到用户点名的网址，并把调研引到 search_web"
    assert "不要再逐条打开" in desc or "不要拿它逐条打开" in desc, "必须明说别逐条抓搜索命中的链接——这正是 47% 成功率的主要浪费源"


def test_base_prompt_browser_block_prefers_search():
    prompt = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="")
    if "browser_fetch" not in prompt:
        pytest.skip("BROWSER_SERVICE_URL 未配置，提示词不含浏览器块")
    assert "信息调研一律用 search_web" in prompt
    assert "用户给出了具体网址" in prompt, "browser_fetch 的授权必须锚定在「用户给出网址」上"
    assert "或你已经知道该看哪个页面" not in prompt, (
        "这半句是模型主动抓搜索命中链接的许可证（2026-07-28 已收掉），不要再加回来"
    )


def test_deep_research_block_keeps_the_same_policy():
    block = TurnDecision("execute", "mutate", "research", research_profile=True).prompt_block()
    assert "search_web" in block and "browser_fetch" in block
    assert "用户点名" in block, "深度研究规约必须与全局口径一致：browser_fetch 只用于用户点名的网址"
