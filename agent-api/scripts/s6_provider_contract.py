"""S6 多 provider 沙箱适配器契约（蓝本三 provider）：工厂选择 + 未配置优雅报错。容器内运行。"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.sandbox import (  # noqa: E402
    SandboxNotConfigured,
    create_configured_sandbox,
    create_sandbox,
)
from app.services.sandbox.base import SandboxNotSupported  # noqa: E402
from app.services.sandbox.factory import _PROVIDERS  # noqa: E402


async def main():
    # 1) 三个 provider 对齐蓝本 SandboxProviderType（无自建 docker）
    assert set(_PROVIDERS) == {"e2b", "sealosdevbox", "opensandbox"}, _PROVIDERS
    for name in _PROVIDERS:
        sb = create_sandbox(name, {}, {})
        assert sb.provider == name, (name, sb.provider)
    print("工厂构造三 provider OK:", list(_PROVIDERS))

    # 2) 未知 provider（含已移除的 docker）报错
    for bad in ("docker", "nope"):
        try:
            create_sandbox(bad, {})
            raise AssertionError(f"{bad} 应报错")
        except SandboxNotConfigured:
            pass
    print("未知/已移除 provider 拒绝 OK（docker 已移除）")

    # 3) 三 provider 未配置时 create() 抛 SandboxNotConfigured（管理员需补配置）
    for name in _PROVIDERS:
        sb = create_sandbox(name, {}, {})
        assert not await sb.ping(), f"{name} 未配置 ping 应为 False"
        try:
            await sb.create()
            raise AssertionError(f"{name} 未配置应抛 SandboxNotConfigured")
        except SandboxNotConfigured:
            pass
    print("三 provider 未配置优雅报错 OK")

    # 4) 可选能力默认 not-supported（对齐蓝本 BaseSandboxAdapter）
    sb = create_sandbox("e2b", {}, {})
    for coro in (sb.execute_stream("x", None), sb.execute_background("x"), sb.interrupt("s")):
        try:
            await coro
            raise AssertionError("应抛 not-supported")
        except SandboxNotSupported:
            pass
    print("可选能力默认 not-supported OK")

    # 5) create_configured_sandbox 按 settings 选（默认 opensandbox）
    cfg_sb = create_configured_sandbox("cfg-test")
    from app.core.config import settings

    assert cfg_sb.provider == settings.SKILL_SANDBOX_PROVIDER, cfg_sb.provider
    print("配置选择 provider OK:", cfg_sb.provider)

    print("S6 ALL PASS")


asyncio.run(main())
