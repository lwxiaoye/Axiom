"""沙箱 provider 工厂（对齐蓝本 sdk/sandbox-adapter createSandbox）。

管理员用 settings.SKILL_SANDBOX_PROVIDER 选一个 provider：
  local（本地测试/MVP，docker 起兄弟容器，不进生产）| e2b（商业云）
  | sealosdevbox（云）| opensandbox（自建 K8s，默认）
各 provider 的连接配置从 settings 的 SKILL_SANDBOX_<PROVIDER>_* 读取。
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings

from .base import SandboxAdapter, SandboxNotConfigured

# provider 名 -> adapter 类（懒加载各自依赖）。对齐蓝本 SandboxProviderType
# local = MVP/本地测试用 docker CLI 起兄弟容器（ADR-047 §6.5）；其余对齐蓝本三 provider
_PROVIDERS = ("local", "e2b", "sealosdevbox", "opensandbox")


def _connection_config_from_settings(provider: str) -> dict:
    """按 provider 从 settings 组装连接配置。"""
    if provider == "local":
        return {
            "image": settings.SKILL_SANDBOX_LOCAL_IMAGE,
            "network": settings.SKILL_SANDBOX_LOCAL_NETWORK,
            "memory": settings.SKILL_SANDBOX_LOCAL_MEMORY,
            "cpus": settings.SKILL_SANDBOX_LOCAL_CPUS,
        }
    if provider == "e2b":
        return {
            "apiKey": settings.SKILL_SANDBOX_E2B_API_KEY,
            "template": settings.SKILL_SANDBOX_E2B_TEMPLATE or None,
        }
    if provider == "sealosdevbox":
        return {
            "baseUrl": settings.SKILL_SANDBOX_SEALOS_BASE_URL,
            "token": settings.SKILL_SANDBOX_SEALOS_TOKEN,
            "name": settings.SKILL_SANDBOX_SEALOS_DEVBOX,
        }
    if provider == "opensandbox":
        return {
            "domain": settings.SKILL_SANDBOX_OPENSANDBOX_DOMAIN,
            "api_key": settings.SKILL_SANDBOX_OPENSANDBOX_API_KEY or None,
            "pool_ref": settings.SKILL_SANDBOX_OPENSANDBOX_POOL or None,
            "image": settings.SKILL_SANDBOX_OPENSANDBOX_IMAGE or None,
            "use_server_proxy": settings.SKILL_SANDBOX_OPENSANDBOX_USE_SERVER_PROXY,
            "cpu": settings.SKILL_SANDBOX_OPENSANDBOX_CPU,
            "memory": settings.SKILL_SANDBOX_OPENSANDBOX_MEMORY,
        }
    return {}


def create_sandbox(
    provider: str,
    connection_config: Optional[dict] = None,
    create_config: Optional[dict] = None,
) -> SandboxAdapter:
    """工厂：按 provider 名构造 adapter（= 蓝本 createSandbox）。"""
    provider = (provider or "").lower()
    if provider == "local":
        from .local_adapter import LocalDockerAdapter

        return LocalDockerAdapter(connection_config, create_config)
    if provider == "e2b":
        from .e2b_adapter import E2BAdapter

        return E2BAdapter(connection_config, create_config)
    if provider == "sealosdevbox":
        from .sealos_adapter import SealosDevboxAdapter

        return SealosDevboxAdapter(connection_config, create_config)
    if provider == "opensandbox":
        from .opensandbox_adapter import OpenSandboxAdapter

        return OpenSandboxAdapter(connection_config, create_config)
    raise SandboxNotConfigured(f"未知沙箱 provider: {provider}（可选 {', '.join(_PROVIDERS)}）")


def create_configured_sandbox(session_label: str = "") -> SandboxAdapter:
    """按 settings 选定的 provider + 组装的连接配置构造 adapter。"""
    provider = settings.SKILL_SANDBOX_PROVIDER
    return create_sandbox(
        provider,
        _connection_config_from_settings(provider),
        {"session": session_label},
    )


async def sandbox_available() -> bool:
    """探测当前配置的 provider 是否就绪（ping）。供健康检查/降级判断。"""
    try:
        sandbox = create_configured_sandbox()
        return await sandbox.ping()
    except Exception:  # noqa: BLE001
        return False
