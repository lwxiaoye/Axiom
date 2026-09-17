import asyncio

import httpx
import pytest

from app.services.knowledge import public_page_reader as reader
from app.services.knowledge import web_search_service as web
from app.services.knowledge.web_engine_health import EngineHealth
from app.services.knowledge.web_request_scope import WebRequestScope


@pytest.mark.parametrize("url,trust", [
    ("http://127.0.0.1:8085", False), ("http://127.0.0.1:8085", False),
    ("http://[::1]:8085", False), ("http://searxng:8080", False),
    ("https://api.firecrawl.dev", True), ("https://gateway.example/v1", True),
])
def test_private_providers_do_not_inherit_public_proxy(url, trust):
    assert web._provider_trust_env(url) is trust


def test_engine_cooldown_is_per_instance_and_probes_are_bounded(monkeypatch):
    health = EngineHealth()
    now = [100.0]
    monkeypatch.setattr("app.services.knowledge.web_engine_health.time.monotonic", lambda: now[0])
    health.record("a", ["baidu", "sogou", "bing"], [["baidu", "CAPTCHA"], ["sogou", "timeout"]])
    assert health.select("a", "baidu,sogou,bing") == ["bing"]
    assert health.select("b", "baidu,sogou,bing") == ["baidu", "sogou", "bing"]
    now[0] += 301
    assert health.select("a", "baidu,sogou,bing") == ["baidu", "bing"]
    assert health.select("a", "baidu,bing") == ["bing"]
    health.record("a", ["baidu"], [])
    assert health.select("a", "baidu,bing") == ["baidu", "bing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unresponsive", [[], [["baidu", "CAPTCHA"]]])
async def test_searx_empty_never_expands_to_unconfigured_engines(monkeypatch, unresponsive):
    requests = []
    async def reply(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [], "unresponsive_engines": unresponsive})
    client = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(web, "engine_health", EngineHealth())
    rows, error = await web._stage_search_single("test", {"searxngUrl": "https://search.example", "searxngEngines": "baidu"}, "searxng")
    assert rows == []
    assert bool(error) == bool(unresponsive)
    assert len(requests) == 1
    assert requests[0].url.params["engines"] == "baidu"


@pytest.mark.asyncio
async def test_partial_success_cools_only_failing_engine(monkeypatch):
    requested = []
    async def reply(request):
        requested.append(request.url.params["engines"])
        return httpx.Response(200, json={"results": [{"url": "https://example.org", "content": "result"}], "unresponsive_engines": [["baidu", "CAPTCHA"]]})
    client = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(web, "engine_health", EngineHealth())
    for _ in range(2):
        rows, error = await web._stage_search_single("q", {"searxngUrl": "https://search.example", "searxngEngines": "baidu,bing"}, "searxng")
        assert rows and not error
    assert requested == ["baidu,bing", "bing"]


@pytest.mark.asyncio
async def test_shared_request_survives_one_waiter_cancellation_and_copies_results():
    scope = WebRequestScope()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def fetch():
        calls.append(1)
        started.set()
        await release.wait()
        return {"results": [{"content": "source"}], "error": ""}
    first = asyncio.create_task(scope.fetch("q", fetch))
    await started.wait()
    second = asyncio.create_task(scope.fetch("q", fetch))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    release.set()
    result = await second
    result["results"][0]["content"] = "changed"
    assert (await scope.fetch("q", fetch))["results"][0]["content"] == "source"
    assert len(calls) == 1
    other = WebRequestScope()
    await other.fetch("q", fetch)
    assert len(calls) == 2
    await scope.close()
    await other.close()


@pytest.mark.asyncio
async def test_failure_cache_expires_and_last_waiter_cancels_transport():
    scope = WebRequestScope(failure_ttl=0)
    calls = []
    async def fail():
        calls.append(1)
        return {"error": "timeout"}
    await scope.fetch("q", fail)
    await scope.fetch("q", fail)
    assert len(calls) == 2
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def blocking():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    waiter = asyncio.create_task(scope.fetch("url", blocking))
    await started.wait()
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert cancelled.is_set()
    assert not scope._pending
    await scope.close()


@pytest.mark.asyncio
async def test_run_scoped_paid_search_budget_is_atomic_across_members():
    scope = WebRequestScope()
    claimed = await asyncio.gather(*(
        scope.claim_budget("deepseek_research_hybrid", limit=3)
        for _ in range(12)
    ))
    assert claimed.count(True) == 3
    assert claimed.count(False) == 9
    await scope.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_ok", [True, False])
async def test_static_first_and_firecrawl_fallback_with_shared_url(monkeypatch, direct_ok):
    calls = []
    body = "Useful official documentation with verifiable facts. " * 10
    async def direct(url):
        calls.append("direct")
        return body if direct_ok else "Enable JavaScript"
    async def fire(url, base, key, c):
        calls.append("firecrawl")
        return body
    async def public(rows):
        return rows
    monkeypatch.setattr(web, "read_public_page", direct)
    monkeypatch.setattr(web, "_scrape_one_firecrawl", fire)
    monkeypatch.setattr(web, "_filter_by_public_url", public)
    scope = WebRequestScope()
    config = {"scraperProvider": "firecrawl", "_requestScope": scope}
    for _ in range(2):
        result = await web._stage_scrape([{"url": "https://example.org", "content": "original snippet"}], config)
        assert result[0]["scraped"] and result[0]["content"] == body
        assert result[0]["snippet"] == "original snippet"
    assert calls == (["direct"] if direct_ok else ["direct", "firecrawl"])
    await scope.close()


@pytest.mark.asyncio
async def test_official_image_evidence_keeps_rich_scraper(monkeypatch):
    async def direct(url):
        pytest.fail("official image extraction must keep Markdown")
    async def rich(*args):
        return "Official information " * 8 + "![campus map](https://example.org/map.png)"
    async def public(rows):
        return rows
    monkeypatch.setattr(web, "read_public_page", direct)
    monkeypatch.setattr(web, "_scrape_one_firecrawl", rich)
    monkeypatch.setattr(web, "_filter_by_public_url", public)
    result = await web._stage_scrape([{"url": "https://example.org"}], {"scraperProvider": "firecrawl", "_requireRichContent": True})
    assert "![campus map]" in result[0]["content"]


@pytest.mark.asyncio
async def test_failed_read_keeps_snippet_and_stable_diagnostics(monkeypatch):
    async def timeout(*args):
        raise httpx.ReadTimeout("timeout with private backend details")
    async def public(rows):
        return rows
    monkeypatch.setattr(web, "read_public_page", timeout)
    monkeypatch.setattr(web, "_scrape_one_firecrawl", timeout)
    monkeypatch.setattr(web, "_filter_by_public_url", public)
    result = await web._stage_scrape([{"url": "https://example.org", "content": "usable snippet"}], {"scraperProvider": "firecrawl"})
    assert result[0]["content"] == "usable snippet"
    assert result[0]["read_error"] == "direct:timeout;firecrawl:timeout"
    assert result[0]["content_kind"] == "search_snippet"
    assert not result[0].get("scraped")


def test_html_reader_omits_scripts_navigation_and_keeps_article():
    assert reader.extract_text("<nav>menu</nav><script>malicious()</script><p>Article &amp; facts</p>", "text/html") == "Article & facts"


@pytest.mark.asyncio
async def test_reader_redirect_revalidates_and_rejects_private_ip(monkeypatch):
    from app.services.gateway import mcp_client
    resolved = []
    async def resolve(url):
        resolved.append(url)
        if "127.0.0.1" in url:
            raise ValueError("private address")
        return "93.184.216.34", "example.org", 443
    async def wire(self, request):
        assert request.url.host == "93.184.216.34"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    monkeypatch.setattr(mcp_client, "resolve_public_http_target_async", resolve)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", wire)
    with pytest.raises(ValueError, match="private"):
        await reader.read_public_page("https://example.org")
    assert len(resolved) == 2


@pytest.mark.asyncio
async def test_reader_caps_response_size(monkeypatch):
    monkeypatch.setattr(reader, "MAX_PAGE_BYTES", 32)
    monkeypatch.setattr(reader, "PinnedPublicTransport", lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"a" * 33)))
    with pytest.raises(ValueError, match="page_too_large"):
        await reader.read_public_page("https://example.org")
