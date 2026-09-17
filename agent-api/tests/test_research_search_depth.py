"""Research search_web must actually scrape; normal Q&A keeps the skip_scrape shortcut."""
import pytest

from app.services.knowledge import web_search_service as svc
from app.services.platform import platform_config_service as cfg


CONCRETE = "售价 12999 元，重量 239g，今日可核验。"


def _hits(n: int = 12):
    return [
        {
            "url": f"https://example.com/p{i}",
            "title": f"结果 {i}",
            "content": CONCRETE,
        }
        for i in range(n)
    ]


@pytest.fixture
def search_pipeline(monkeypatch):
    captured = {}

    async def _cfg():
        return {"enabled": True, "topK": 5, "rerankerProvider": "none"}

    async def _search(query, c):  # noqa: ARG001
        return (_hits(), "")

    async def _scrape(items, c, progress_cb=None, max_scrape=None):  # noqa: ARG001
        captured["max_scrape"] = max_scrape
        captured["called"] = True
        out = []
        for item in items[: max_scrape or len(items)]:
            row = dict(item)
            row["scraped"] = True
            row["content"] = ("正文" * 800) + CONCRETE
            out.append(row)
        return out

    async def _passthrough(items):
        return items

    monkeypatch.setattr(cfg, "get_web_search_config", _cfg)
    monkeypatch.setattr(svc, "_stage_search", _search)
    monkeypatch.setattr(svc, "_stage_scrape", _scrape)
    monkeypatch.setattr(svc, "_filter_public_or_no_url", _passthrough)
    monkeypatch.setattr(svc, "_filter_by_public_url", _passthrough)
    captured["called"] = False
    return captured


@pytest.mark.asyncio
async def test_research_depth_disables_skip_scrape_and_raises_caps(search_pipeline):
    result = await svc.search_web("折叠屏评测", research_depth=True)
    assert search_pipeline["called"] is True
    assert search_pipeline["max_scrape"] == 8
    assert len(result["results"]) == 8
    assert "正文" in result["text"]
    assert len(result["text"]) > 1500


@pytest.mark.asyncio
async def test_normal_search_keeps_skip_scrape_when_snippets_are_concrete(search_pipeline):
    result = await svc.search_web("折叠屏评测", research_depth=False)
    assert search_pipeline["called"] is False
    assert result["results"]
    assert "正文" not in result["text"]


@pytest.mark.asyncio
async def test_team_search_limits_pages_without_reducing_other_research(search_pipeline):
    result = await svc.search_web("循环机制", research_depth=True, research_page_limit=3)
    assert search_pipeline["max_scrape"] == 3
    assert len(result["results"]) == 3 and all(row["scraped"] for row in result["results"])
    await svc.search_web("其他研究", research_depth=True)
    assert search_pipeline["max_scrape"] == 8


@pytest.mark.asyncio
async def test_research_read_budget_spans_sites_and_keeps_read_bodies(search_pipeline, monkeypatch):
    from urllib.parse import urlsplit

    async def search(query, config):
        return ([{"url": f"https://blocked.example/p{i}", "title": f"摘要 {i}", "content": CONCRETE}
                 for i in range(10)] + [
            {"url": "https://source.example/report", "title": "研究报告", "content": ""},
            {"url": "https://docs.example/facts", "title": "一手说明", "content": ""},
        ], "")

    visited = []

    async def scrape(items, config, progress_cb=None, max_scrape=None):
        for item in items[:max_scrape]:
            host = urlsplit(item["url"]).hostname
            visited.append(host)
            if host != "blocked.example":
                item.update(scraped=True, content="可核验的正文材料。" * 80)
        return items

    async def rank(query, items, config):
        # The reranker can put concrete-looking snippets before read evidence.
        return sorted(items, key=lambda item: "blocked.example" not in item["url"])

    monkeypatch.setattr(svc, "_stage_search", search)
    monkeypatch.setattr(svc, "_stage_scrape", scrape)
    monkeypatch.setattr(svc, "_stage_rerank", rank)

    result = await svc.search_web("模型能力", research_depth=True, research_page_limit=3)

    assert visited == ["blocked.example", "source.example", "docs.example"]
    assert len(result["results"]) == 8
    assert len(result["scraped_pages"]) == 2
    assert all(item["scraped"] for item in result["results"][:2])
