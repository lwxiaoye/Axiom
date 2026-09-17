# -*- coding: utf-8 -*-
"""搜索后端故障 ≠ 没有结果（2026-07-28）。

原来的形态：`_stage_search` 里一个 `except Exception: return []`，于是
SearXNG 挂掉 / 超时 / 429 限流 / 引擎被 CAPTCHA 封 **全部**长成同一个样子——
`text` 是「（联网搜索未返回结果）」，与「确实一条都没搜到」一字不差。
模型分辨不出来，就直接告诉用户「没有查到相关信息」：一个可修的后端故障被说成了
事实结论，用户既不会重试、也不会报障（本机 SearXNG 中文引擎被封那次就是这么过去的）。

这组用例守两头：故障要说是故障，真的没结果也不能被误报成故障。
"""
import pytest

from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.web import build_web_tools
from app.services.knowledge import web_search_service


@pytest.fixture(autouse=True)
def _reset_provider_health():
    # Circuit-breaker state is intentionally process-local in production; unit cases must not
    # make later cases inherit an earlier case's three synthetic provider failures.
    web_search_service._provider_health.clear()
from app.services.platform import platform_config_service as cfg


def _cfg(**over):
    base = {"enabled": True, "searchProvider": "searxng", "searxngUrl": "http://searxng:8080",
            "topK": 5, "scraperProvider": "none", "rerankerProvider": "none"}
    base.update(over)
    return base


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"results": []}

    def json(self):
        return self._payload


class _Client:
    """httpx.AsyncClient 的最小替身：按脚本回一个响应或抛异常。"""

    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, *_a, **_kw):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def post(self, *_a, **_kw):
        return await self.get()


def _patch(monkeypatch, outcome, conf=None):
    async def _get_cfg():
        return conf if conf is not None else _cfg()

    monkeypatch.setattr(cfg, "get_web_search_config", _get_cfg)
    monkeypatch.setattr(web_search_service.httpx, "AsyncClient", lambda **_kw: _Client(outcome))

    async def _passthrough(items):
        return items

    # SSRF 预检要真去解析 DNS，与本组用例无关，直通
    monkeypatch.setattr(web_search_service, "_filter_public_or_no_url", _passthrough)


# ---------- 服务层：error 字段 ----------

@pytest.mark.asyncio
async def test_backend_exception_reports_error_not_empty(monkeypatch):
    _patch(monkeypatch, ConnectionError("connection refused"))
    res = await web_search_service.search_web("北京天气")
    assert res["error"], "搜索后端挂了必须给出 error，不能伪装成「没结果」"
    assert "ConnectionError" in res["error"]
    assert "不是" in res["text"] and "没有搜到" in res["text"]


@pytest.mark.asyncio
async def test_rate_limited_429_is_a_failure(monkeypatch):
    """429 与 5xx 的响应体是 HTML，直接 resp.json() 会抛 JSONDecodeError，
    日志里只剩一句含糊的 JSONDecodeError，看的人根本猜不到是被限流了。"""
    _patch(monkeypatch, _Resp(status_code=429))
    res = await web_search_service.search_web("北京天气")
    assert "429" in res["error"]


@pytest.mark.asyncio
async def test_unconfigured_backend_is_a_failure_not_empty(monkeypatch):
    """地址没配同样不是「没搜到」——那是部署问题，要能一眼看出来。"""
    _patch(monkeypatch, _Resp(), conf=_cfg(searxngUrl=""))
    res = await web_search_service.search_web("北京天气")
    assert "未配置" in res["error"]


@pytest.mark.asyncio
async def test_disabled_feature_is_labelled_too(monkeypatch):
    _patch(monkeypatch, _Resp(), conf=_cfg(enabled=False))
    res = await web_search_service.search_web("北京天气")
    assert res["enabled"] is False and "未启用" in res["error"]


@pytest.mark.asyncio
async def test_genuinely_empty_results_are_not_reported_as_failure(monkeypatch):
    """另一头同样重要：搜索跑通了、就是没有结果时 error 必须为空，
    否则模型会以为后端坏了，反复重试或拒绝作答。"""
    _patch(monkeypatch, _Resp(payload={"results": []}))
    res = await web_search_service.search_web("一个不存在的词条")
    assert res["error"] == ""
    assert res["results"] == []


@pytest.mark.asyncio
async def test_successful_search_has_empty_error(monkeypatch):
    _patch(monkeypatch, _Resp(payload={"results": [
        {"title": "标题", "url": "https://example.com/a", "content": "摘要"}]}))
    res = await web_search_service.search_web("北京天气")
    assert res["error"] == ""
    assert len(res["results"]) == 1


# ---------- 工具层：模型看到的是什么 ----------

@pytest.mark.asyncio
async def test_tool_soft_fails_and_tells_model_not_to_conclude(monkeypatch):
    """工具层必须软失败（以 failed 回灌模型），并明确禁止它下「查不到」的结论。"""
    async def _boom(_q, start_index=1, with_images=False, **_kw):
        return {"enabled": True, "results": [], "text": "（联网搜索失败：搜索后端不可用 —— 挂了）",
                "scraped_pages": [], "images": [], "error": "ConnectTimeout: 超时"}

    monkeypatch.setattr(web_search_service, "search_web", _boom)
    tool = build_web_tools()[0]
    with pytest.raises(ToolSoftError) as exc:
        await tool.execute({"query": "北京天气"})
    msg = str(exc.value)
    assert "不等于" in msg and "没有查到相关信息" in msg
    assert "ConnectTimeout" in msg, "要把真实原因带上，否则排障时无从下手"


@pytest.mark.asyncio
async def test_tool_still_returns_text_when_search_really_found_nothing(monkeypatch):
    """真的没搜到就照常返回文本，不能变成软失败——那会让模型以为服务坏了。"""
    async def _empty(_q, start_index=1, with_images=False, **_kw):
        return {"enabled": True, "results": [], "text": "（联网搜索未返回结果）",
                "scraped_pages": [], "images": [], "error": ""}

    monkeypatch.setattr(web_search_service, "search_web", _empty)
    tool = build_web_tools()[0]
    text = (await tool.execute({"query": "一个不存在的词条"})).model_content
    assert "未返回结果" in text


@pytest.mark.asyncio
async def test_old_callers_without_error_key_still_work(monkeypatch):
    """向后兼容：没有 error 字段的旧返回（工作流桩、录制基线）不能被当成故障。"""
    async def _legacy(_q, start_index=1, with_images=False, **_kw):
        return {"enabled": True, "results": [], "text": "", "scraped_pages": [], "images": []}

    monkeypatch.setattr(web_search_service, "search_web", _legacy)
    tool = build_web_tools()[0]
    assert (await tool.execute({"query": "x"})).model_content == "（联网搜索无结果）"
