import asyncio
import logging
import time

import pytest

from app.services.chat.tools.web import build_web_tools
from app.services.knowledge import web_search_service as search
from app.services.platform import platform_config_service as config


def test_provider_pool_migrates_deepseek_to_primary():
    normalized = config._normalize_provider_pool({
        "providerMode": "deepseek_first",
        "providerPool": [
            {"id": "deepseek-official", "enabled": True, "weight": 900},
            {"id": "searxng", "enabled": True, "weight": 1},
        ]
    })
    assert normalized["providerMode"] == "deepseek_first"
    assert [row["id"] for row in normalized["providerPool"]] == ["deepseek-official", "searxng"]
    assert normalized["providerPool"][1]["fallbackOnly"] is True
    assert normalized["deepseekResearchHybrid"] is False
    assert normalized["deepseekResearchMaxQueries"] == 3


def test_research_hybrid_budget_is_normalized():
    normalized = config._normalize_provider_pool({
        "deepseekResearchHybrid": False,
        "deepseekResearchMaxQueries": 999,
    })
    assert normalized["deepseekResearchHybrid"] is False
    assert normalized["deepseekResearchMaxQueries"] == 12
    assert config._normalize_provider_pool({"deepseekResearchHybrid": "false"})["deepseekResearchHybrid"] is False


def test_deepseek_endpoint_is_not_admin_configurable():
    normalized = config._normalize_provider_pool({
        "deepseekBaseUrl": "https://attacker.example/collect-user-key",
        "deepseekCredentialMode": "user",
        "deepseekSearchProtocol": "anthropic_messages",
        "deepseekMaxUses": 20,
    })
    assert "deepseekBaseUrl" not in normalized
    assert "deepseekMaxUses" not in normalized
    assert normalized["deepseekCredentialMode"] == "assigned-newapi-key"
    assert normalized["deepseekSearchProtocol"] == "responses"


@pytest.mark.asyncio
async def test_deepseek_request_uses_newapi_responses_search_and_assigned_key(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "web_search_call", "status": "completed",
                        "action": {"type": "search", "queries": ["q"]},
                    },
                    {
                        "type": "web_search_call", "status": "completed",
                        "action": {
                            "type": "open_page", "url": "https://www.deepseek.com/",
                            "title": "DeepSeek",
                        },
                    },
                    {
                        "type": "message", "status": "completed", "content": [{
                            "type": "output_text",
                            "text": "See [DeepSeek](https://www.deepseek.com/) for details.",
                            "annotations": [],
                        }],
                    },
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(search.settings, "NEWAPI_BASE_URL", "https://gateway.example/new-api/v1")
    monkeypatch.setattr(search.httpx, "AsyncClient", FakeClient)
    runtime_config = {
        "deepseekApiKey": "assigned-user-key",
        "deepseekBaseUrl": "https://attacker.example/collect-user-key",
    }
    rows, error = await search._stage_search_single("q", runtime_config, "deepseek-official")

    assert error == ""
    assert rows == [{
        "title": "DeepSeek",
        "url": "https://www.deepseek.com/",
        "content": "",
        "publishedDate": "",
    }]
    assert captured["url"] == "https://gateway.example/new-api/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer assigned-user-key"
    assert captured["json"]["tools"] == [{"type": "web_search"}]
    assert captured["json"]["tool_choice"] == "auto"
    assert captured["json"]["reasoning"] == {"effort": "none"}
    assert captured["json"]["max_output_tokens"] == 4096
    assert "messages" not in captured["json"]
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert runtime_config["_deepseekUsage"] == {"input_tokens": 11, "output_tokens": 7}


def test_deepseek_source_parser_ignores_pending_actions_and_model_prose_links():
    rows, search_seen = search._deepseek_responses_search_rows({
        "output": [
            {
                "type": "web_search_call", "status": "in_progress",
                "action": {"type": "open_page", "url": "https://pending.invalid/"},
            },
            {
                "type": "web_search_call", "status": "completed",
                "action": {
                    "type": "search",
                    "sources": [{
                        "url": "https://source.example/report?utm_source=deepseek",
                        "title": "Primary report", "snippet": "source fact",
                    }],
                },
            },
            {
                "type": "message", "status": "completed", "content": [{
                    "type": "output_text",
                    "text": "Model-only [claim](https://hallucinated.invalid/) must be ignored.",
                    "annotations": [{
                        "type": "url_citation", "url": "https://source.example/report",
                        "title": "Verified report",
                    }],
                }],
            },
        ],
    })

    assert search_seen is True
    assert rows == [{
        "title": "Primary report",
        "url": "https://source.example/report?utm_source=deepseek",
        "content": "source fact",
        "publishedDate": "",
    }]


@pytest.mark.asyncio
async def test_deepseek_failure_logs_bounded_structure_without_response_content(
    monkeypatch, caplog,
):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "incomplete",
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "in_progress",
                        "action": {
                            "type": "search",
                            "queries": ["PRIVATE_QUERY_TEXT"],
                            "sources": [{
                                "url": "https://PRIVATE_SOURCE.example/",
                                "title": "PRIVATE_SOURCE_TITLE",
                            }],
                        },
                    },
                    {
                        "type": "message",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": "PRIVATE_MODEL_BODY",
                            "annotations": [{
                                "type": "url_citation",
                                "url": "https://PRIVATE_CITATION.example/",
                            }],
                        }],
                    },
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "api_key": "PRIVATE_PROVIDER_KEY",
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(search.httpx, "AsyncClient", FakeClient)
    caplog.set_level(logging.WARNING, logger=search.__name__)
    rows, error = await search._stage_search_single(
        "PRIVATE_QUERY_TEXT",
        {"deepseekApiKey": "PRIVATE_ASSIGNED_KEY", "_callerRunId": "run-9"},
        "deepseek-official",
    )

    assert rows == []
    assert "未执行服务端联网搜索" in error
    assert search.failure_code(error) == "missing_structured_result"
    diagnostic_records = [
        record.getMessage() for record in caplog.records
        if "deepseek_search_response_rejected" in record.getMessage()
    ]
    assert len(diagnostic_records) == 1
    logged = diagnostic_records[0]
    assert "reason=search_not_executed" in logged
    assert "run_id=run-9" in logged
    assert '\"response_status\":\"incomplete\"' in logged
    assert '\"output_item_types\":{\"message\":1,\"web_search_call\":1}' in logged
    assert '\"output_item_statuses\":{\"completed\":1,\"in_progress\":1}' in logged
    assert '\"action_types\":{\"search\":1}' in logged
    assert '\"annotation_count\":1' in logged
    assert '\"source_count\":1' in logged
    for private_value in (
        "PRIVATE_QUERY_TEXT",
        "PRIVATE_SOURCE",
        "PRIVATE_MODEL_BODY",
        "PRIVATE_CITATION",
        "PRIVATE_PROVIDER_KEY",
        "PRIVATE_ASSIGNED_KEY",
    ):
        assert private_value not in logged


@pytest.mark.asyncio
async def test_legal_empty_primary_result_does_not_spend_fallback(monkeypatch):
    calls = []

    async def ordered(_config):
        return ["searxng", "deepseek-official"]

    async def single(_query, _config, provider):
        calls.append(provider)
        return [], ""

    async def health(*_args, **_kwargs):
        return None

    monkeypatch.setattr(search, "_ordered_search_providers", ordered)
    monkeypatch.setattr(search, "_stage_search_single", single)
    monkeypatch.setattr(search, "_record_provider_health", health)
    rows, error = await search._stage_search("q", {})
    assert rows == [] and error == ""
    assert calls == ["searxng"]


@pytest.mark.asyncio
async def test_primary_failure_uses_deepseek_fallback(monkeypatch):
    calls = []

    async def ordered(_config):
        return ["searxng", "deepseek-official"]

    async def single(_query, _config, provider):
        calls.append(provider)
        if provider == "searxng":
            return [], "transport failed"
        return [{"title": "ok", "url": "https://example.com", "content": "ok"}], ""

    async def health(*_args, **_kwargs):
        return None

    monkeypatch.setattr(search, "_ordered_search_providers", ordered)
    monkeypatch.setattr(search, "_stage_search_single", single)
    monkeypatch.setattr(search, "_record_provider_health", health)
    rows, error = await search._stage_search("q", {"_callerUserId": "u1"})
    assert error == "" and rows[0]["title"] == "ok"
    assert calls == ["searxng", "deepseek-official"]


@pytest.mark.asyncio
async def test_search_web_always_uses_research_router_for_research(monkeypatch):
    from app.services.knowledge.web_request_scope import WebRequestScope

    async def get_config():
        return {
            "enabled": True,
            "topK": 5,
            "scraperProvider": "none",
            "rerankerProvider": "none",
            "deepseekResearchHybrid": True,
            "providerPool": [
                {"id": "searxng", "enabled": True},
                {"id": "deepseek-official", "enabled": True, "fallbackOnly": True},
            ],
        }

    calls = []

    async def hybrid(_query, runtime_config):
        calls.append("research")
        runtime_config["_searchMode"] = "deepseek_first"
        runtime_config["_searchProvidersAttempted"] = ["deepseek-official"]
        runtime_config["_searchProvidersUsed"] = ["deepseek-official"]
        runtime_config["_searchProviderFailures"] = {}
        return [{"title": "ok", "url": "https://example.com", "content": "snippet"}], ""

    async def primary(_query, runtime_config):
        calls.append("primary")
        runtime_config["_searchMode"] = "primary_fallback"
        runtime_config["_searchProvidersAttempted"] = ["searxng"]
        runtime_config["_searchProvidersUsed"] = ["searxng"]
        runtime_config["_searchProviderFailures"] = {}
        return [{"title": "ok", "url": "https://example.com", "content": "snippet"}], ""

    async def public(rows):
        return rows

    monkeypatch.setattr(search.cfg, "get_web_search_config", get_config)
    monkeypatch.setattr(search, "_stage_research_search", hybrid)
    monkeypatch.setattr(search, "_stage_search", primary)
    monkeypatch.setattr(search, "_filter_public_or_no_url", public)
    scope = WebRequestScope()
    try:
        result = await search.search_web(
            "q", research_depth=True, request_scope=scope,
            caller_newapi_key="assigned-user-key",
        )
        unscoped = await search.search_web(
            "q2", research_depth=True,
            caller_newapi_key="assigned-user-key",
        )
    finally:
        await scope.close()

    assert calls == ["research", "research"]
    assert result["search_meta"]["mode"] == "deepseek_first"
    assert result["search_meta"]["providers"] == ["deepseek-official"]
    assert unscoped["search_meta"]["mode"] == "deepseek_first"


@pytest.mark.asyncio
async def test_open_breaker_is_not_bypassed_when_no_provider_is_available(monkeypatch):
    calls = []

    async def single(_query, _config, provider):
        calls.append(provider)
        return [], "should not execute"

    monkeypatch.setattr(search, "_stage_search_single", single)
    search._provider_health["searxng"] = {
        "failures": search._PROVIDER_FAILURE_THRESHOLD,
        "opened_at": time.monotonic(),
        "half_open": False,
    }
    try:
        rows, error = await search._stage_search("q", {
            "searxngUrl": "http://searxng.local",
            "providerPool": [{"id": "searxng", "enabled": True}],
        })
    finally:
        search._provider_health.pop("searxng", None)

    assert rows == []
    assert "熔断冷却期" in error
    assert calls == []


@pytest.mark.asyncio
async def test_search_uses_calling_users_assigned_key_not_platform_config(monkeypatch):
    async def get_config():
        return {
            "enabled": True,
            "deepseekApiKey": "legacy-platform-key",
            "providerPool": [
                {"id": "searxng", "enabled": True},
                {"id": "deepseek-official", "enabled": True, "fallbackOnly": True},
            ],
        }

    async def stage(_query, runtime_config):
        assert runtime_config["deepseekApiKey"] == "assigned-user-key"
        assert runtime_config["_callerUserId"] == "user-7"
        assert runtime_config["_callerRunId"] == "run-9"
        return [], "forced-stop"

    monkeypatch.setattr(search.cfg, "get_web_search_config", get_config)
    monkeypatch.setattr(search, "_stage_search", stage)
    result = await search.search_web(
        "q",
        caller_user_id="user-7",
        caller_run_id="run-9",
        caller_newapi_key="assigned-user-key",
    )
    assert result["error"] == "forced-stop"
    assert "legacy-platform-key" not in str(result)
    assert "assigned-user-key" not in str(result)


@pytest.mark.asyncio
async def test_main_chat_web_tool_forwards_assigned_key_only_in_backend(monkeypatch):
    captured = {}

    async def fake_search_web(_query, **kwargs):
        captured.update(kwargs)
        return {
            "enabled": True,
            "results": [],
            "text": "（联网搜索未返回结果）",
            "scraped_pages": [],
            "images": [],
            "error": "",
        }

    monkeypatch.setattr(search, "search_web", fake_search_web)
    tool = build_web_tools(
        user_id="user-7", run_id="run-9", newapi_key="assigned-user-key",
    )[0]
    value = await tool.execute({"query": "q"})

    assert captured["caller_user_id"] == "user-7"
    assert captured["caller_run_id"] == "run-9"
    assert captured["caller_newapi_key"] == "assigned-user-key"
    assert "assigned-user-key" not in value.model_dump_json()


@pytest.mark.asyncio
async def test_paid_fallback_log_is_attributed_without_separate_key_ledger(monkeypatch, caplog):
    async def ordered(_config):
        return ["searxng", "deepseek-official"]

    async def single(_query, runtime_config, provider):
        if provider == "searxng":
            return [], "transport failed"
        runtime_config["_deepseekUsage"] = {"input_tokens": 11, "output_tokens": 7}
        return [{"title": "ok", "url": "https://example.com", "content": "ok"}], ""

    async def health(*_args, **_kwargs):
        return None

    monkeypatch.setattr(search, "_ordered_search_providers", ordered)
    monkeypatch.setattr(search, "_stage_search_single", single)
    monkeypatch.setattr(search, "_record_provider_health", health)
    caplog.set_level(logging.INFO, logger=search.__name__)

    rows, error = await search._stage_search(
        "private query text",
        {"_callerUserId": "user-7", "_callerRunId": "run-9"},
    )

    assert error == "" and rows
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "provider=deepseek-official" in log_text
    assert "user_id=user-7" in log_text
    assert "run_id=run-9" in log_text
    assert "input_tokens=11" in log_text
    assert "private query text" not in log_text
