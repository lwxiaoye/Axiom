"""陈旧广场智能体集合清理（version bump 后不再堆积整份旧副本）。

覆盖 vector_service.cleanup_stale_agent_collections 的安全边界：
  - 删更低版本的同 embedding 配置集合 + 裸 `agents`；
  - 保留当前集合、更高/相同版本、不同后缀（别的配置）、路由索引 agent_route_v1_*；
  - 当前集合为空时**一个都不删**（防止把唯一有数据的集合删空后广场彻底没数据）；
  - 当前集合名不符合口径时跳过（绝不据错误名推断“更低版本”）；
  - 幂等：重复运行第二次没有可删的。
以及 backfill_service._cleanup_stale_collections 把 embedding 配置解析出的集合名透传下去。
"""

import re
from types import SimpleNamespace

import pytest

from app.services.knowledge import vector_service
from app.services.platform import backfill_service


class _FakeQdrant:
    """最小可用的 AsyncQdrantClient 替身：collections 是 {集合名: 点数}。"""

    def __init__(self, collections: dict):
        self._collections = dict(collections)
        self.deleted: list = []

    async def count(self, collection_name):
        if collection_name not in self._collections:
            raise KeyError(collection_name)
        return SimpleNamespace(count=self._collections[collection_name])

    async def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self._collections]
        )

    async def delete_collection(self, collection_name):
        self._collections.pop(collection_name, None)
        self.deleted.append(collection_name)


def _current_version_and_suffix():
    """从真实 collection_name 解析当前版本号与后缀，令测试对 v3/v4… 任意版本都成立。"""
    name = vector_service.collection_name("model-x", 1024)
    m = re.match(r"^agents_v(\d+)_([0-9a-f]+)$", name)
    assert m, f"collection_name 输出格式变了: {name}"
    return name, int(m.group(1)), m.group(2)


@pytest.mark.asyncio
async def test_drops_lower_versions_and_legacy_only(monkeypatch):
    current, n, sha = _current_version_and_suffix()
    assert n >= 2  # 需要至少存在一个更低版本用于验证
    route = vector_service.route_collection_name("model-x", 1024)
    other_sha = "ffffffffffff"  # 另一套 embedding 配置的后缀

    lowers = [f"agents_v{v}_{sha}" for v in range(1, n)]  # 全部严格低于当前版本
    higher = f"agents_v{n + 1}_{sha}"
    other_config_lower = f"agents_v1_{other_sha}"

    collections = {
        current: 881,           # 当前集合 → 保留
        higher: 5,              # 更高版本 → 保留
        route: 42,              # 路由索引（前缀 agent_route_）→ 保留
        other_config_lower: 7,  # 不同后缀（别的配置）→ 保留
        "agents": 500,          # 版本化之前的裸集合 → 删
        **{name: 881 for name in lowers},  # 更低版本同配置 → 删
    }
    fake = _FakeQdrant(collections)
    monkeypatch.setattr(vector_service, "_client", fake)

    deleted = await vector_service.cleanup_stale_agent_collections(current)

    expected = set(lowers) | {"agents"}
    assert set(deleted) == expected
    assert set(fake.deleted) == expected
    # 保留项仍在
    for keep in (current, higher, route, other_config_lower):
        assert keep in fake._collections, f"{keep} 被误删"

    # 幂等：再跑一次没有可删的
    deleted2 = await vector_service.cleanup_stale_agent_collections(current)
    assert deleted2 == []


@pytest.mark.asyncio
async def test_empty_current_deletes_nothing(monkeypatch):
    """安全阀：当前集合为空 → 一个旧副本都不删。"""
    current, n, sha = _current_version_and_suffix()
    lower = f"agents_v1_{sha}"
    fake = _FakeQdrant({current: 0, lower: 881, "agents": 500})
    monkeypatch.setattr(vector_service, "_client", fake)

    deleted = await vector_service.cleanup_stale_agent_collections(current)

    assert deleted == []
    assert fake.deleted == []
    assert lower in fake._collections and "agents" in fake._collections


@pytest.mark.asyncio
async def test_malformed_current_name_skips(monkeypatch):
    """当前集合名不符合 agents_v{N}_{后缀} 口径 → 跳过，绝不据错误名删任何东西。"""
    _current, n, sha = _current_version_and_suffix()
    fake = _FakeQdrant({"agents": 500, f"agents_v1_{sha}": 881})
    monkeypatch.setattr(vector_service, "_client", fake)

    deleted = await vector_service.cleanup_stale_agent_collections("not_a_valid_collection")

    assert deleted == []
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_route_index_never_matched(monkeypatch):
    """路由索引 agent_route_v1_* 前缀不同，任何情况下都不该被清理。"""
    current, n, sha = _current_version_and_suffix()
    route = vector_service.route_collection_name("model-x", 1024)
    fake = _FakeQdrant({current: 10, route: 3})
    monkeypatch.setattr(vector_service, "_client", fake)

    deleted = await vector_service.cleanup_stale_agent_collections(current)

    assert deleted == []
    assert route in fake._collections


@pytest.mark.asyncio
async def test_backfill_cleanup_passes_resolved_collection(monkeypatch):
    """backfill_service._cleanup_stale_collections 把 embedding 配置解析出的集合名透传给 vector_service。"""
    current = vector_service.collection_name("model-x", 1024)

    async def _fake_config():
        return ("model-x", 1024, current)

    seen = {}

    async def _fake_cleanup(collection):
        seen["collection"] = collection
        return [f"{collection}_stale"]

    monkeypatch.setattr(backfill_service, "_get_embedding_config", _fake_config)
    monkeypatch.setattr(
        backfill_service.vector_service, "cleanup_stale_agent_collections", _fake_cleanup
    )

    result = await backfill_service._cleanup_stale_collections()

    assert seen["collection"] == current
    assert result == [f"{current}_stale"]


@pytest.mark.asyncio
async def test_backfill_cleanup_noop_without_embedding_config(monkeypatch):
    async def _no_config():
        return None

    monkeypatch.setattr(backfill_service, "_get_embedding_config", _no_config)
    assert await backfill_service._cleanup_stale_collections() == []
