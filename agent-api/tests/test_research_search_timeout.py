import asyncio
import json

import pytest

from app.services.knowledge import web_search_service as search
from app.services.knowledge.web_request_scope import WebRequestScope


@pytest.mark.asyncio
@pytest.mark.parametrize("delay,deadline,expires", [(13, 75, False), (0.2, 0.03, True)])
async def test_native_search_can_return_after_serp_timeout_but_is_bounded(monkeypatch, delay, deadline, expires):
    monkeypatch.setattr(search, "_NATIVE_SEARCH_TOTAL_SECONDS", deadline)
    tasks = set()
    async def upstream(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            await reader.readexactly(length)
            await asyncio.sleep(delay)  # the production failure was the shared 12s SERP timeout
            body = json.dumps({"status": "completed", "output": [{"type": "web_search_call",
                "status": "completed", "action": {"type": "open_page", "url": "https://example.org/fact"}}]}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())
    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(search.settings, "NEWAPI_BASE_URL", f"http://127.0.0.1:{port}")
    try:
        rows, error = await search._stage_search_single("q", {"deepseekApiKey": "test-key"}, "deepseek-official")
        if expires:
            assert not rows and search.failure_code(error) == "timeout"
        else:
            assert error == ""
            assert rows[0]["url"] == "https://example.org/fact"
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_empty_primary_and_failed_partner_remain_a_degraded_search(monkeypatch):
    monkeypatch.setattr(search, "_ordered_search_providers", _ordered)
    async def request(query, config, provider):
        return ([], "ReadTimeout") if provider == "deepseek-official" else ([], "")
    monkeypatch.setattr(search, "_stage_search_single", request)
    scope = WebRequestScope()
    c = {"_requestScope": scope}
    try:
        rows, error = await search._stage_research_search("q", c)
    finally:
        await scope.close()
    assert not rows and error
    assert c["_searchProvidersUsed"] == ["searxng"]
    assert c["_searchProviderFailures"] == {"deepseek-official": "timeout"}


async def _ordered(config):
    return ["searxng", "deepseek-official"]


@pytest.mark.asyncio
async def test_research_empty_primary_cannot_bypass_paid_search_limit(monkeypatch):
    monkeypatch.setattr(search, "_ordered_search_providers", _ordered)
    calls = []
    async def request(query, config, provider):
        calls.append(provider)
        return ([{"url": "https://example.org/fact"}], "") if provider == "deepseek-official" else ([], "")
    monkeypatch.setattr(search, "_stage_search_single", request)
    scope = WebRequestScope()
    await scope.claim_budget(search._RESEARCH_HYBRID_BUDGET_KEY, limit=1)
    try:
        rows, error = await search._stage_research_search("q", {
            "_requestScope": scope, "deepseekResearchMaxQueries": 1, "_researchSourceRecovery": True})
    finally:
        await scope.close()
    assert not rows and not error
    assert calls == ["searxng"]
