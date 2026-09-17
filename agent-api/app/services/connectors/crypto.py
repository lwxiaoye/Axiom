"""连接器凭据加解密（Fernet）。

外部应用的访问令牌是**长期有效的用户凭据**——GitHub token 泄漏等于代码库泄漏，所以它
只以密文进 `agent_connector_binding.secret_cipher`，明文不落库、不进日志、不出接口层。

密钥来源优先级：
1. `settings.CONNECTOR_SECRET_KEY`（显式配置，**多实例部署必须走这条**）；
2. `settings.CONNECTOR_KEY_FILE` 指向的本地密钥文件（不存在则生成，权限 0600）。

第 2 条是单实例/开发环境的便利：它仍是一把真随机密钥，不是硬编码常量。但多实例各自
生成互不相同 → A 实例存的令牌 B 实例解不开，故显式配置时才算生产就绪，缺省会告警一次。
"""
import base64
import hashlib
import logging
import os
import threading
import tempfile
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_fernet: Optional[Fernet] = None
_key_id: str = ""
_warned = False


class ConnectorCryptoError(Exception):
    """密钥不可用或密文无法解开（换过密钥/数据被篡改）。"""


def _derive(raw: str) -> bytes:
    """任意长度的配置字符串 -> 32 字节 urlsafe base64 Fernet 密钥。

    直接把配置值当 Fernet key 会因为长度/编码不符而炸；统一过一次 SHA-256，
    运维随便填什么都能用，且同一个配置值恒定派生出同一把密钥。
    """
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def _key_from_file() -> bytes:
    path = Path(settings.CONNECTOR_KEY_FILE)
    if path.exists():
        return _derive(path.read_text(encoding="utf-8").strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = base64.urlsafe_b64encode(os.urandom(32)).decode()
    # 写完再以硬链接原子发布；并发启动者只能读取胜者，不能覆盖或读到半写密钥。
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secret.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    return _derive(path.read_text(encoding="utf-8").strip())


def initialize_credentials() -> None:
    """启动前验证密钥可用；API/Worker 不在首个任务内才争抢初始化。"""
    _get_fernet()


def _get_fernet() -> Fernet:
    global _fernet, _warned, _key_id
    if _fernet is not None:
        return _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        configured = (settings.CONNECTOR_SECRET_KEY or "").strip()
        if configured:
            key = _derive(configured)
        else:
            if not _warned:
                logger.warning(
                    "CONNECTOR_SECRET_KEY 未配置，连接器凭据改用本地密钥文件 %s 加密；"
                    "多实例部署下各实例密钥不同，务必显式配置该项。",
                    settings.CONNECTOR_KEY_FILE,
                )
                _warned = True
            try:
                key = _key_from_file()
            except OSError as exc:
                raise ConnectorCryptoError(f"连接器密钥不可用：{exc}") from exc
        _key_id = hashlib.sha256(b"harness-worker-key-scope\0" + key).hexdigest()
        _fernet = Fernet(key)
        return _fernet


def credential_key_id() -> str:
    """不可逆的队列兼容性标识，不包含密钥或用户凭据。"""
    _get_fernet()
    return _key_id


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        raise ConnectorCryptoError("凭据为空")
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(cipher: str) -> str:
    if not cipher:
        raise ConnectorCryptoError("凭据为空")
    try:
        return _get_fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        # 换过密钥或密文损坏——不能当成「令牌错误」让用户去 GitHub 查，要说清是本地的事
        raise ConnectorCryptoError("凭据无法解密（加密密钥可能已更换），请重新连接该应用") from exc


def reset_cache_for_test() -> None:
    """测试用：清掉进程内缓存的密钥，让下次调用重新按配置派生。"""
    global _fernet, _warned, _key_id
    with _lock:
        _fernet = None
        _key_id = ""
        _warned = False
