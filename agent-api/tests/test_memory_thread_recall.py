"""v3.0 记忆线程召回通道：recall 第三通道（source_thread_id）、去重、向后兼容与三段渲染。

无 DB：runtime_session 用假会话钉住（沿用 tests/test_memory_write_concurrency.py 风格）。
"""
import asyncio
from datetime import datetime

import pytest

from app.services.memory import memory_service as ms


class _Row:
    def __init__(self, content, mem_type="fact", thread_id=None, updated_at=None):
        self.content = content
        self.type = mem_type
        self.source_thread_id = thread_id
        self.updated_at = updated_at or datetime.now()
        self.expires_at = None
        self.confidence = 80


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _Scalars(self._rows)


class _Factory:
    def __init__(self, rows):
        self._rows = rows

    def __call__(self):
        return _Factory(self._rows)

    async def __aenter__(self):
        return _Session(self._rows)

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_runtime(monkeypatch):
    rows = [
        _Row("用户偏好简洁回答", "preference", None),
        _Row("本会话记住：在做教师节 PPT", "fact", "th-1"),
        _Row("本会话记住：用深色商务风", "fact", "th-1"),
        _Row("全局事实：用户是老师", "fact", None),
    ]
    monkeypatch.setattr(ms, "runtime_session", _Factory(rows))

    async def _enabled(user_id):
        return True
    monkeypatch.setattr(ms, "is_enabled", _enabled)
    return rows


async def _recall(fake_runtime, *, thread_id=None, query=None):
    return await ms.recall("u-1", query=query, thread_id=thread_id)


@pytest.mark.asyncio
async def test_recall_thread_channel(monkeypatch, fake_runtime):
    async def _no_rank(query, candidates, **_kwargs):
        return []
    monkeypatch.setattr(ms, "_rank_by_relevance", _no_rank)
    out = await _recall(fake_runtime, thread_id="th-1", query="继续做PPT")
    thread_items = [m for m in out if m.get("source") == "thread"]
    # 线程通道只取本会话记忆，且带 source 标记
    assert len(thread_items) == 2
    assert all(m["content"].startswith("本会话记住") for m in thread_items)
    # 偏好常驻通道仍在前
    assert out[0]["type"] == "preference"
    # 排序：偏好在前、线程次之
    kinds = [m.get("source", "semantic") for m in out]
    assert kinds[0] == "semantic" or out[0]["type"] == "preference"


@pytest.mark.asyncio
async def test_recall_thread_channel_dedup(monkeypatch, fake_runtime):
    """线程通道与**已返回**的语义条目按内容去重（注意 query 需 ≥6 字才开启语义通道）。"""
    fake_runtime.append(_Row("重复内容", "fact", None))
    fake_runtime.append(_Row("重复内容", "fact", "th-1"))
    async def _rank(query, candidates, **_kwargs):
        return [c for c in candidates if "重复内容" in c.content]
    monkeypatch.setattr(ms, "_rank_by_relevance", _rank)
    out = await ms.recall("u-1", query="请回忆重复内容相关事实", thread_id="th-1")
    thread_items = [m for m in out if m.get("source") == "thread"]
    # 「重复内容」已被语义通道召回 → 线程通道不再重复加一条
    assert all("重复内容" not in m["content"] for m in thread_items)
    # 但内容仍在结果里（走语义通道）
    assert any("重复内容" in m["content"] for m in out)


@pytest.mark.asyncio
async def test_recall_backward_compat(monkeypatch, fake_runtime):
    """不带 thread_id 时行为与旧版一致：无 source=thread 条目。"""
    async def _no_rank(query, candidates, **_kwargs):
        return []
    monkeypatch.setattr(ms, "_rank_by_relevance", _no_rank)
    out = await ms.recall("u-1", query="x" * 8)
    assert all(m.get("source") != "thread" for m in out)


def test_format_for_prompt_three_segments():
    rendered = ms.format_for_prompt([
        {"type": "preference", "content": "喜欢简洁回答"},
        {"type": "fact", "content": "本会话记住：在准备教师节 PPT", "source": "thread"},
        {"type": "fact", "content": "用户是老师"},
    ])
    assert "稳定倾向" in rendered
    assert "本会话相关记忆" in rendered
    assert "用户长期记忆" in rendered
    # 线程段在偏好段之后、语义段之前
    assert rendered.index("本会话相关记忆") > rendered.index("稳定倾向")
    assert rendered.index("用户长期记忆") > rendered.index("本会话相关记忆")


def test_recall_signature_and_call_site():
    src = open("app/services/memory/memory_service.py", encoding="utf-8").read()
    assert "thread_id: Optional[str] = None" in src
    assert "source_thread_id" in src
    turn = open("app/services/chat/turn_prepare.py", encoding="utf-8").read()
    assert "thread_id=thread_id" in turn
