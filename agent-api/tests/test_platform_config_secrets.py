"""agent_platform_config 里 web_search / ocr 密钥字段的密文化：判据、读写、合并语义、启动迁移。

对话/重排/embedding 的密钥都已是 Fernet 密文入库，联网搜索与 OCR 的密钥字段曾是明文躺在
config_json 里。这里钉住：
1. 判据 crypto.is_cipher_text：gAAAA 前缀 **且** 当前密钥能解开——只看前缀会把换过密钥的旧密文误判；
2. 迁移 migrate_plaintext_secrets：明文 → 密文、读回明文；幂等（二次 0）；伪密文跳过不覆盖；行不存在不造行；
3. 读取：明文原样放行并只告警一次；解不开的伪密文置空（当作未配置）并只告警一次；
4. 保存：页面传空串 = 保持原密钥，这一合并在明文层完成后再加密，库里永远不出现明文。
"""
import json
import logging
from typing import Any, Dict, Optional

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.connectors import crypto
from app.services.platform import platform_config_service as service

SERPER = "serper-" + "a" * 40
TAVILY = "tvly-" + "b" * 32
VISION = "sk-" + "c" * 48


def _bogus_cipher() -> str:
    """另一把密钥加密出来的 token：前缀同为 gAAAA，但当前密钥解不开。"""
    return Fernet(Fernet.generate_key()).encrypt(b"whatever").decode("ascii")


class MemoryStore:
    """用内存 dict 顶替 agent_platform_config 表：_load_stored / _save_raw 的替身。

    _get_raw 经 _load_stored 读，迁移经 _load_stored 读、_save_raw 写，所以只替这两处就能
    让整条读写链路在内存里跑完，不碰线上库。
    """

    def __init__(self, rows: Optional[Dict[str, Dict[str, Any]]] = None):
        self.rows: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in (rows or {}).items()}
        self.saves = 0

    async def load(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.rows.get(key)
        return dict(row) if row is not None else None

    async def save(self, key: str, data: Dict[str, Any]) -> None:
        # 与真实 _save_raw 一样走一遍 JSON，顺便保证存进去的都可序列化
        self.rows[key] = json.loads(json.dumps(data, ensure_ascii=False))
        self.saves += 1


@pytest.fixture
def store(monkeypatch):
    saved = settings.CONNECTOR_SECRET_KEY
    settings.CONNECTOR_SECRET_KEY = "platform-config-secrets-test"
    crypto.reset_cache_for_test()
    service._secret_warned.clear()
    mem = MemoryStore()
    monkeypatch.setattr(service, "_load_stored", mem.load)
    monkeypatch.setattr(service, "_save_raw", mem.save)
    yield mem
    settings.CONNECTOR_SECRET_KEY = saved
    crypto.reset_cache_for_test()
    service._secret_warned.clear()


def _warnings(caplog) -> list:
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == service.logger.name]


# ---- 判据 ----

def test_is_cipher_text_distinguishes_plain_cipher_and_undecryptable(store):
    assert crypto.is_cipher_text(SERPER) is False                      # 明文
    assert crypto.is_cipher_text(crypto.encrypt_secret(SERPER)) is True  # 当前密钥的密文
    assert crypto.is_cipher_text(_bogus_cipher()) is False              # 前缀对、解不开 → 不算密文
    assert crypto.is_cipher_text("") is False
    assert crypto.is_cipher_text(None) is False


def test_classify_secret_maps_to_four_kinds(store):
    assert service._classify_secret("") == service.SECRET_EMPTY
    assert service._classify_secret(None) == service.SECRET_EMPTY
    assert service._classify_secret(SERPER) == service.SECRET_PLAIN
    assert service._classify_secret(crypto.encrypt_secret(SERPER)) == service.SECRET_CIPHER
    assert service._classify_secret(_bogus_cipher()) == service.SECRET_UNDECRYPTABLE


# ---- 迁移 ----

@pytest.mark.asyncio
async def test_migration_encrypts_plaintext_then_reads_back_plain(store, caplog):
    caplog.set_level(logging.WARNING, logger=service.logger.name)
    store.rows[service.WEB_SEARCH_KEY] = {
        "serperApiKey": SERPER, "tavilyApiKey": TAVILY, "jinaApiKey": "",
        "searxngUrl": "http://searxng.internal:8080", "topK": 8,
        "legacyUnknownField": {"keep": True},   # 未知字段也要原样保留，迁移只改密钥字段
    }
    store.rows[service.OCR_KEY] = {"visionApiKey": VISION, "strategy": "multimodal_model", "model": "qwen-vl-max"}

    stats = await service.migrate_plaintext_secrets()

    # empty 计的是「密钥字段为空或缺席」：web_search 6 个密钥字段里 4 个空 + ocr 的 apiKey 缺席
    assert stats == {"migrated": 3, "already": 0, "skipped": 0, "empty": 5, "rows": 2}
    assert store.saves == 2
    web = store.rows[service.WEB_SEARCH_KEY]
    ocr = store.rows[service.OCR_KEY]
    for value in (web["serperApiKey"], web["tavilyApiKey"], ocr["visionApiKey"]):
        assert value.startswith("gAAAA") and crypto.is_cipher_text(value)
    assert web["jinaApiKey"] == ""                        # 空的仍是空，不把 "" 加密成一个「已配置」
    assert web["searxngUrl"] == "http://searxng.internal:8080" and web["topK"] == 8
    assert web["legacyUnknownField"] == {"keep": True}
    assert ocr["strategy"] == "multimodal_model" and ocr["model"] == "qwen-vl-max"
    # 库里再也找不到明文
    assert SERPER not in json.dumps(web) and TAVILY not in json.dumps(web) and VISION not in json.dumps(ocr)

    # 运行时读回明文；管理页读回脱敏 + secrets_set；迁移后不再有「仍是明文」告警
    used = await service.get_web_search_config()
    assert used["serperApiKey"] == SERPER and used["tavilyApiKey"] == TAVILY and used["jinaApiKey"] == ""
    assert (await service.get_ocr_config())["visionApiKey"] == VISION
    masked = await service.get_web_search_masked()
    assert masked["serperApiKey"] == "" and masked["tavilyApiKey"] == ""
    assert masked["secrets_set"]["serperApiKey"] is True and masked["secrets_set"]["jinaApiKey"] is False
    assert (await service.get_ocr_masked())["secrets_set"]["visionApiKey"] is True
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_migration_is_idempotent(store):
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": SERPER}
    store.rows[service.OCR_KEY] = {"visionApiKey": VISION}
    first = await service.migrate_plaintext_secrets()
    assert first["migrated"] == 2 and store.saves == 2
    snapshot = {k: dict(v) for k, v in store.rows.items()}

    second = await service.migrate_plaintext_secrets()

    assert second == {"migrated": 0, "already": 2, "skipped": 0, "empty": 6, "rows": 2}
    assert store.saves == 2                   # 没有可迁的就不回写
    assert store.rows == snapshot             # 已是密文的值原样不动（不会再加密一层）


@pytest.mark.asyncio
async def test_migration_skips_missing_rows_and_undecryptable(store, caplog):
    caplog.set_level(logging.WARNING, logger=service.logger.name)
    # 两行都不存在：不凭空造行
    assert await service.migrate_plaintext_secrets() == {"migrated": 0, "already": 0, "skipped": 0, "empty": 0, "rows": 0}
    assert store.rows == {} and store.saves == 0

    bogus = _bogus_cipher()
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": bogus, "tavilyApiKey": TAVILY}
    stats = await service.migrate_plaintext_secrets()

    assert stats["migrated"] == 1 and stats["skipped"] == 1 and stats["rows"] == 1
    assert store.rows[service.WEB_SEARCH_KEY]["serperApiKey"] == bogus   # 解不开的原样保留，等管理员重填覆盖
    assert crypto.is_cipher_text(store.rows[service.WEB_SEARCH_KEY]["tavilyApiKey"])
    assert any("无法解密" in r.getMessage() for r in _warnings(caplog))


# ---- 读取 ----

@pytest.mark.asyncio
async def test_undecryptable_reads_as_empty_and_warns_once(store, caplog):
    caplog.set_level(logging.WARNING, logger=service.logger.name)
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": _bogus_cipher()}
    store.rows[service.OCR_KEY] = {"visionApiKey": _bogus_cipher()}

    assert (await service.get_web_search_config())["serperApiKey"] == ""
    assert (await service.get_web_search_config())["serperApiKey"] == ""
    assert (await service.get_ocr_config())["visionApiKey"] == ""
    assert (await service.get_web_search_masked())["secrets_set"]["serperApiKey"] is False

    # 每个 (键, 字段) 只告警一次，不随每次检索刷屏
    messages = [r.getMessage() for r in _warnings(caplog)]
    assert len(messages) == 2
    assert all("无法解密" in m for m in messages)


@pytest.mark.asyncio
async def test_plaintext_before_migration_passes_through_and_warns_once(store, caplog):
    caplog.set_level(logging.WARNING, logger=service.logger.name)
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": SERPER}

    assert (await service.get_web_search_config())["serperApiKey"] == SERPER
    assert (await service.get_web_search_config())["serperApiKey"] == SERPER
    assert (await service.resolve_secret(service.WEB_SEARCH_KEY, "serperApiKey", "")) == SERPER

    messages = [r.getMessage() for r in _warnings(caplog)]
    assert len(messages) == 1 and "仍是明文" in messages[0]


# ---- 保存 ----

@pytest.mark.asyncio
async def test_save_with_empty_secret_keeps_existing_key_and_stores_cipher(store):
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": crypto.encrypt_secret(SERPER), "topK": 5}

    result = await service.save_web_search({"serperApiKey": "", "tavilyApiKey": "", "topK": 7})

    assert result["topK"] == 7
    assert result["serperApiKey"] == "" and result["secrets_set"]["serperApiKey"] is True
    assert result["secrets_set"]["tavilyApiKey"] is False
    stored = store.rows[service.WEB_SEARCH_KEY]
    assert stored["topK"] == 7
    assert stored["serperApiKey"] != ""                                  # 「保持原密钥」没被误存成空
    assert crypto.is_cipher_text(stored["serperApiKey"])
    assert crypto.decrypt_secret(stored["serperApiKey"]) == SERPER
    assert stored["tavilyApiKey"] == ""
    assert (await service.get_web_search_config())["serperApiKey"] == SERPER


@pytest.mark.asyncio
async def test_save_new_secret_is_encrypted_and_replaces_old(store):
    store.rows[service.WEB_SEARCH_KEY] = {"serperApiKey": crypto.encrypt_secret(SERPER)}
    new_key = "serper-new-" + "z" * 30

    await service.save_web_search({"serperApiKey": new_key, "tavilyApiKey": TAVILY})

    stored = store.rows[service.WEB_SEARCH_KEY]
    dumped = json.dumps(stored)
    assert new_key not in dumped and TAVILY not in dumped and SERPER not in dumped
    assert crypto.decrypt_secret(stored["serperApiKey"]) == new_key
    assert crypto.decrypt_secret(stored["tavilyApiKey"]) == TAVILY
    assert (await service.resolve_secret(service.WEB_SEARCH_KEY, "tavilyApiKey", "")) == TAVILY
    assert (await service.resolve_secret(service.WEB_SEARCH_KEY, "tavilyApiKey", "submitted")) == "submitted"


@pytest.mark.asyncio
async def test_save_over_legacy_plaintext_row_encrypts_in_place(store):
    # 迁移尚未跑、库里还是明文时管理员先保存了一次：合并要认出明文原密钥，并顺手把它加密
    store.rows[service.OCR_KEY] = {"visionApiKey": VISION, "strategy": "multimodal_model"}

    result = await service.save_ocr({"visionApiKey": "", "apiKey": "", "model": "glm-4v"})

    assert result["secrets_set"]["visionApiKey"] is True and result["secrets_set"]["apiKey"] is False
    stored = store.rows[service.OCR_KEY]
    assert stored["model"] == "glm-4v" and stored["strategy"] == "multimodal_model"
    assert VISION not in json.dumps(stored)
    assert crypto.decrypt_secret(stored["visionApiKey"]) == VISION
    assert stored["apiKey"] == ""
    assert (await service.get_ocr_config())["visionApiKey"] == VISION


@pytest.mark.asyncio
async def test_save_ocr_empty_secret_keeps_existing_key(store):
    store.rows[service.OCR_KEY] = {"apiKey": crypto.encrypt_secret("custom-endpoint-key"), "strategy": "custom_endpoint"}

    await service.save_ocr({"apiKey": "", "endpointUrl": "http://ocr.internal/v1"})

    stored = store.rows[service.OCR_KEY]
    assert stored["endpointUrl"] == "http://ocr.internal/v1"
    assert crypto.decrypt_secret(stored["apiKey"]) == "custom-endpoint-key"
    assert (await service.get_ocr_config())["apiKey"] == "custom-endpoint-key"
