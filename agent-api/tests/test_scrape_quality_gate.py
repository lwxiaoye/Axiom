# -*- coding: utf-8 -*-
"""抓取质量闸：404/验证码页不得覆盖搜索摘要。"""
import pytest

from app.services.knowledge.web_search_service import (
    _usable_scrape_text,
    _query_date_tokens,
    _stage_scrape,
)


def test_usable_scrape_rejects_404_and_captcha():
    assert _usable_scrape_text("404 Not Found ============= nginx") is False
    assert _usable_scrape_text("请完成安全验证 VerifyCode：abc captcha") is False
    assert _usable_scrape_text("x" * 20) is False


def test_usable_scrape_accepts_real_article():
    body = (
        "漳州今日天气晴到多云，最高气温35℃，最低26℃，东南风2到3级。"
        "未来两天仍有午后雷阵雨，出行请注意防暑。" * 2
    )
    assert _usable_scrape_text(body) is True


def test_query_date_tokens_extracts_cn_date():
    toks = _query_date_tokens("漳州 今天 天气 2026年8月5日")
    assert any("2026" in t and "08" in t or "8月5日" in t for t in toks)
    assert "20260805" in toks or "2026-08-05" in toks


@pytest.mark.asyncio
async def test_stage_scrape_keeps_snippet_when_firecrawl_returns_404(monkeypatch):
    item = {
        "title": "漳州天气预报",
        "url": "https://example.com/zhangzhou",
        "content": "摘要：最高35℃ 最低26℃ 晴到多云",
    }

    async def _boom(url, base, key):
        return "404 Not Found =============  * * *  nginx"

    monkeypatch.setattr(
        "app.services.knowledge.web_search_service._scrape_one_firecrawl", _boom
    )

    async def _pub(items):
        return items

    monkeypatch.setattr(
        "app.services.knowledge.web_search_service._filter_by_public_url", _pub
    )

    out = await _stage_scrape(
        [item],
        {"scraperProvider": "firecrawl", "firecrawlUrl": "http://x", "firecrawlApiKey": "k"},
    )
    assert out[0]["content"].startswith("摘要：最高35℃")
    assert out[0].get("scraped") is not True


def test_freshness_requires_topic_hit():
    from app.services.knowledge.web_search_service import (
        _query_date_tokens, _topic_tokens, _freshness_boost_key,
    )
    q = "漳州市今天天气状况 2026年8月5日"
    dt = _query_date_tokens(q)
    tt = _topic_tokens(q, dt)
    assert any("漳州" in x or "天气" in x for x in tt)
    news = {"title": "漳州速报 2026年8月5日", "url": "http://x", "content": "牛蛙抗生素超标"}
    weather = {"title": "漳州天气预报一周", "url": "http://tianqi", "content": "今天漳州天气:晴 26℃~35℃"}
    assert _freshness_boost_key(weather, dt, tt) > _freshness_boost_key(news, dt, tt)


def test_pure_date_hit_without_topic_does_not_boost():
    from app.services.knowledge.web_search_service import _freshness_boost_key
    item = {"title": "2026年8月5日 国内要闻", "url": "http://n", "content": "综合消息"}
    # no topic tokens -> date alone yields zero topic/date boost
    key = _freshness_boost_key(item, ["2026年8月5日", "2026-08-05"], [])
    assert key[1] == 0  # topic_hits
    assert key[2] == 0  # date_boost requires topic hit


def test_concrete_content_outranks_title_shell():
    from app.services.knowledge.web_search_service import (
        _content_concrete_score, _freshness_boost_key, _topic_tokens, _query_date_tokens,
    )
    shell = {
        "title": "2026年08月06日漳州市天气_最高气温_最低气温 - 天气网",
        "url": "https://m.tianqi.com/tianqi/zhangzhou/20260806.html",
        "content": "7天前 - 安床 出行 上梁 旅游 理发 祭祀 解除 拆卸 出火 开光 伐木 合脊",
    }
    numeric = {
        "title": "漳州天气预报15天",
        "url": "https://tianqi.cncn.com/zhangzhou/15",
        "content": "08月06日漳州天气:雨转阴,气温24℃~28℃, 3级.点此复制08月02日漳州天气:多云转雨,气温24℃~34℃",
    }
    assert _content_concrete_score(numeric) > _content_concrete_score(shell)
    q = "漳州天气 8月6日"
    dt = _query_date_tokens(q) or ["8月6日"]
    tt = _topic_tokens("漳州天气", dt)
    assert _freshness_boost_key(numeric, dt, tt) > _freshness_boost_key(shell, dt, tt)


def test_snippets_already_concrete_gate():
    from app.services.knowledge.web_search_service import _snippets_already_concrete
    thin = [{"title": "a", "content": "导航 入口 更多"}]
    rich = [{"title": "a", "content": "今天晴 26℃~35℃ 东南风3级 湿度70%"}]
    assert _snippets_already_concrete(thin) is False
    assert _snippets_already_concrete(rich) is True
