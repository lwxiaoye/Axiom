"""ai_embedding_model.api_key 密文化：判据、读写、启动迁移的纯函数核心。

对话/重排模型的密钥早已 Fernet 密文入库，embedding 曾是唯一明文的例外。这里钉住三件事：
1. 「是密文」= gAAAA 前缀 **且** 当前密钥能解开——只看前缀会把换过密钥的旧密文误判成有效；
2. _read_key 对明文原样放行（迁移前检索不中断）、对解不开的伪密文返回空（当作未配置）；
3. 迁移核心 encrypt_plaintext_keys 幂等，解不开的行只告警不覆盖。
"""
import logging
import unittest
from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.core.config import settings
from app.routers import embedding_config as ec
from app.services.connectors import crypto
from app.services.knowledge import embedding_service as es

PLAIN = "sk-" + "x" * 114  # 与线上 DashScope key 同长（117 字符）


def _bogus_cipher() -> str:
    """另一把密钥加密出来的 token：前缀同为 gAAAA，但当前密钥解不开。"""
    return Fernet(Fernet.generate_key()).encrypt(b"whatever").decode("ascii")


class EmbeddingKeyCipherTest(unittest.TestCase):
    def setUp(self):
        self._saved_secret = settings.CONNECTOR_SECRET_KEY
        settings.CONNECTOR_SECRET_KEY = "embedding-key-cipher-test"
        crypto.reset_cache_for_test()
        es._key_warned.clear()

    def tearDown(self):
        settings.CONNECTOR_SECRET_KEY = self._saved_secret
        crypto.reset_cache_for_test()
        es._key_warned.clear()

    def test_is_cipher_requires_prefix_and_decryptable(self):
        self.assertFalse(es._is_cipher(PLAIN))
        self.assertFalse(es._is_cipher(""))
        self.assertFalse(es._is_cipher(None))
        self.assertFalse(es._is_cipher(_bogus_cipher()))   # 前缀对、解不开 → 不算密文
        self.assertTrue(es._is_cipher(es._store_key(PLAIN)))

    def test_store_then_read_roundtrip(self):
        cipher = es._store_key(PLAIN)
        self.assertTrue(cipher.startswith("gAAAA"))
        self.assertNotEqual(cipher, PLAIN)
        self.assertEqual(es._read_key(SimpleNamespace(id=1, api_key=cipher)), PLAIN)

    def test_read_key_passes_plaintext_through_and_warns_once(self):
        row = SimpleNamespace(id=7, api_key=PLAIN)
        with self.assertLogs(es.logger, level=logging.WARNING) as captured:
            self.assertEqual(es._read_key(row), PLAIN)
            self.assertEqual(es._read_key(row), PLAIN)
        self.assertEqual(len(captured.records), 1)  # 同一行只告警一次，不刷屏

    def test_read_key_treats_undecryptable_as_missing(self):
        row = SimpleNamespace(id=8, api_key=_bogus_cipher())
        with self.assertLogs(es.logger, level=logging.WARNING):
            self.assertEqual(es._read_key(row), "")

    def test_store_key_rejects_empty_and_overlong(self):
        with self.assertRaises(ValueError):
            es._store_key("")
        with self.assertRaises(ValueError):
            es._store_key("sk-" + "y" * 400)  # 加密后超过 VARCHAR(512)，不能让 MySQL 静默截断

    def test_migration_is_idempotent_and_skips_undecryptable(self):
        bogus = _bogus_cipher()
        rows = [
            SimpleNamespace(id=1, api_key=PLAIN),
            SimpleNamespace(id=2, api_key=None),
            SimpleNamespace(id=3, api_key=""),
            SimpleNamespace(id=4, api_key=bogus),
        ]
        with self.assertLogs(es.logger, level=logging.WARNING):
            first = es.encrypt_plaintext_keys(rows)
        self.assertEqual(first, {"migrated": 1, "already": 0, "skipped": 1, "empty": 2})
        self.assertTrue(es._is_cipher(rows[0].api_key))
        self.assertEqual(es._read_key(rows[0]), PLAIN)
        self.assertEqual(rows[3].api_key, bogus)   # 解不开的行原样保留，等管理员重填覆盖

        with self.assertLogs(es.logger, level=logging.WARNING):
            second = es.encrypt_plaintext_keys(rows)
        self.assertEqual(second, {"migrated": 0, "already": 1, "skipped": 1, "empty": 2})

    def test_masked_key_is_built_from_plaintext(self):
        # 页面遮罩要长成 sk-x****xxxx 而不是 gAAA****xxxx，否则管理员会以为 key 被改了
        row = SimpleNamespace(id=1, api_key=es._store_key(PLAIN))
        masked = ec._mask_key(es._read_key(row))
        self.assertEqual(masked, f"{PLAIN[:4]}****{PLAIN[-4:]}")


if __name__ == "__main__":
    unittest.main()
