"""v3.0 记忆摘要自动刷新：stale 判定、冷却与单飞。"""

import pytest

from app.services.memory import memory_service as ms
from app.services.memory import personalization_service as ps


def _mem(updated_at: str):
    return {"id": "m1", "type": "fact", "content": "c", "updated_at": updated_at}


def _cfg(summary_at: str):
    return {"memorySummaryAt": summary_at, "memorySummary": "摘要"}


@pytest.mark.asyncio
async def test_summary_is_stale_no_summary_at(monkeypatch):
    async def _cfg_none(user_id):
        return {"memorySummary": "", "memorySummaryAt": ""}
    monkeypatch.setattr(ps, "get_personalization", _cfg_none)
    assert await ms._summary_is_stale("u-1", [_mem("2026-08-01T00:00:00+00:00")]) is True


@pytest.mark.asyncio
async def test_summary_is_stale_newer_memory(monkeypatch):
    async def _cfg_old(user_id):
        return _cfg("2026-08-01T00:00:00+00:00")
    monkeypatch.setattr(ps, "get_personalization", _cfg_old)
    assert await ms._summary_is_stale("u-1", [_mem("2026-08-02T00:00:00+00:00")]) is True


@pytest.mark.asyncio
async def test_summary_is_stale_not_stale(monkeypatch):
    async def _cfg_new(user_id):
        return _cfg("2026-08-03T00:00:00+00:00")
    monkeypatch.setattr(ps, "get_personalization", _cfg_new)
    assert await ms._summary_is_stale("u-1", [_mem("2026-08-02T00:00:00+00:00")]) is False


@pytest.mark.asyncio
async def test_maybe_auto_summarize_cooldown_and_single_flight(monkeypatch):
    """冷却期第二次调用直接返回；单飞集合内不重复生成。"""
    calls = {"generate": 0, "save": 0}

    async def _list_mem(user_id, limit=100):
        return [_mem("2026-08-02T00:00:00+00:00")]

    async def _stale(user_id, memories):
        return True

    async def _gen(user_id, *, model, api_key):
        calls["generate"] += 1
        return "概览：用户是老师"

    async def _save(user_id, incoming):
        calls["save"] += 1
        return {}

    monkeypatch.setattr(ms, "list_memories", _list_mem)
    monkeypatch.setattr(ms, "_summary_is_stale", _stale)
    monkeypatch.setattr(ms, "generate_summary", _gen)
    monkeypatch.setattr(ps, "save_personalization", _save)
    ms._AUTO_SUMMARY_AT.pop("u-1", None)
    ms._auto_summarizing.discard("u-1")

    assert await ms.maybe_auto_summarize("u-1", model="m", api_key="k") is True
    assert calls["generate"] == 1
    assert calls["save"] == 1

    # 冷却期内直接返回 False，不再生成
    assert await ms.maybe_auto_summarize("u-1", model="m", api_key="k") is False
    assert calls["generate"] == 1

    # force=True 绕过冷却
    assert await ms.maybe_auto_summarize("u-1", model="m", api_key="k", force=True) is True
    assert calls["generate"] == 2


@pytest.mark.asyncio
async def test_maybe_auto_summarize_not_stale_skips(monkeypatch):
    async def _list_mem(user_id, limit=100):
        return [_mem("2026-08-02T00:00:00+00:00")]

    async def _not_stale(user_id, memories):
        return False

    calls = {"generate": 0}

    async def _gen(user_id, *, model, api_key):
        calls["generate"] += 1
        return "x"

    monkeypatch.setattr(ms, "list_memories", _list_mem)
    monkeypatch.setattr(ms, "_summary_is_stale", _not_stale)
    monkeypatch.setattr(ms, "generate_summary", _gen)
    ms._AUTO_SUMMARY_AT.pop("u-1", None)
    ms._auto_summarizing.discard("u-1")
    assert await ms.maybe_auto_summarize("u-1", model="m", api_key="k", force=True) is False
    assert calls["generate"] == 0


def test_auto_summary_trigger_in_extract():
    """抽取落库新记忆后必须触发自动摘要；手动按钮端点不受冷却约束。"""
    src = open("app/services/memory/memory_service.py", encoding="utf-8").read()
    assert "_guarded_auto_summarize(user_id, model=model, api_key=api_key)" in src
    assert "_AUTO_SUMMARY_COOLDOWN_SEC" in src
    router = open("app/api/router.py", encoding="utf-8").read()
    assert "memorySummaryAt" in router
