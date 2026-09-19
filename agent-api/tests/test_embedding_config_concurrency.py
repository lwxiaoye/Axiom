"""Embedding 配置域两条 P1（深扫 2026-07-26）。

P1-3 /embedding-config/test：探测结果曾无条件写进 is_active==1 的生效行——管理员试一个
     未保存的候选模型，探测到的 dimension 会盖掉线上模型的维度，集合名（按 model+dimension
     哈希）随之指向空集合，全站向量检索静默失效。修复=身份校验后才落库。
（P1-4 /reindex 与 /ensure-indexed 的广场智能体向量回填已随工作流编排删除。）
"""
import unittest
from unittest.mock import patch

from app.core.auth import UserContext
from app.routers import embedding_config as ec

ADMIN = UserContext(user_id="1", username="admin")


class _Row:
    def __init__(self, model_id="bge-large-zh", base_url="http://old", dimension=1024):
        self.id = 1
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = "sk-old"
        self.dimension = dimension
        self.is_active = 1
        self.test_status = None
        self.test_message = None
        self.last_test_time = None


class _FakeScalars:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return self

    def first(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, _stmt):
        return _FakeScalars([self._row] if self._row else [])

    async def commit(self):
        self.commits += 1


class EmbeddingTestPersistTest(unittest.IsolatedAsyncioTestCase):
    async def _run_test_endpoint(self, row, model, base_url):
        async def _probe(*_a, **_k):
            return {"status": "success", "dimension": 3072, "message": "测试通过"}

        with patch.object(ec, "async_session", lambda: _FakeSession(row)), patch.object(
            ec.embedding_service, "test_embedding_config", _probe
        ):
            return await ec.test_config(
                ec.EmbeddingConfigUpdate(model=model, base_url=base_url, api_key="sk-new"),
                user=ADMIN,
            )

    async def test_candidate_probe_does_not_touch_active_row(self):
        row = _Row(model_id="bge-large-zh", base_url="http://old", dimension=1024)
        result = await self._run_test_endpoint(row, model="text-embedding-3-large", base_url="http://new")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.dimension, 3072)   # 探测结果照常返回前端
        self.assertEqual(row.dimension, 1024)      # 生效行的维度不被污染
        self.assertIsNone(row.test_status)
        self.assertIsNone(row.last_test_time)
        self.assertFalse(result.persisted)     # 前端据此提示「先保存再测试」
        self.assertIn("未保存", result.message)

    async def test_probe_of_active_config_still_persists(self):
        row = _Row(model_id="bge-large-zh", base_url="http://old/", dimension=1024)
        result = await self._run_test_endpoint(row, model="bge-large-zh", base_url="http://old")

        self.assertEqual(result.status, "success")
        self.assertEqual(row.dimension, 3072)
        self.assertEqual(row.test_status, "success")
        self.assertIsNotNone(row.last_test_time)
        self.assertTrue(result.persisted)
