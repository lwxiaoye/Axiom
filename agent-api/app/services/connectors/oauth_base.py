"""OAuth 授权码流程的通用骨架 —— 「再接一家 OAuth 应用」的扩展点。

## 为什么要有这一层

第一批只有 GitHub，于是 `connector_service` 里直接调 `github_adapter.*`，并用六处
`if provider.id != "github"` 挡住其它 provider。接第二家（Gmail / Outlook / Canva
都是 OAuth）时这套写法会以两种方式坏掉：

1. 那六处守卫**静默拒绝**新 provider——界面上是"点连接没反应"，不报错、无日志；
2. 就算逐处放开，`exchange_code` / `fetch_account` 仍写死打 GitHub 的域名。

所以把「一家 OAuth 应用需要提供什么」抽成本模块的 `OAuthProvider` 协议，
`connector_service` 只跟协议打交道。

## 各家的差异都藏在哪

真实差异比想象中大，协议必须容得下：

- **令牌是否过期**：GitHub App 令牌我们没启用过期；微软 access token 约 1 小时；
  Google 也是小时级。所以 `exchange_code` 的返回里 `expires_in` / `refresh_token`
  都是可选的，`refresh` 也允许不实现（`supports_refresh=False`）。
- **refresh token 什么时候给**：Google 只在**首次**授权时返回 refresh_token，
  必须带 `access_type=offline&prompt=consent`；微软要在 scope 里显式写 `offline_access`。
  这些都由各家 `build_authorize_url` 自己处理，上层不关心。
- **刷新后 refresh token 会不会换**：微软官方明确要求「用新的替换旧的」；Google 通常不换。
  所以 `refresh` 的返回里 refresh_token 也是可选的，上层按「给了就换、没给就留着」处理。
- **有效期到底多久**：微软两份官方文档自相矛盾（一处说 web 应用无固定有效期，一处说通常
  90 天）。**因此上层绝不能按文档硬编码过期时间**，只能"到点就刷、刷不动就让用户重连"。

## 账号信息

`fetch_account` 返回的是**展示用**的身份（列表里那句「已连接 @someone」），不含任何凭据。
各家字段名完全不同（GitHub 是 login/name/avatar_url，Google 是 email/name/picture，
微软是 userPrincipalName/displayName），归一化在各自适配器里做，别泄到上层。
"""
from typing import Optional, Protocol, runtime_checkable


class OAuthError(Exception):
    """授权流程中来自对方的失败（换令牌被拒、刷新失败、账号接口报错等）。

    各家适配器原有的异常类型（如 GitHubError）继承它，这样 `connector_service`
    只需要 catch 一个基类，不必逐家 import。
    """


@runtime_checkable
class OAuthProvider(Protocol):
    """一家 OAuth 应用需要提供的全部能力。

    实现者是 `app/services/connectors/<name>.py` 模块本身（用模块当实例，
    不必建类）——`github.py` 已经是这个形状，新增的照抄即可。
    """

    def oauth_configured(self) -> bool:
        """Client ID / Secret 是否配齐。没配齐时连接器整体不可用。"""
        ...

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        """拼授权页地址。scope、access_type、prompt 这些各家自己决定。"""
        ...

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """授权码换令牌。

        返回 dict，约定字段：
        - `access_token`（必须）
        - `refresh_token`（可选；Google 只在首次授权给）
        - `scope`（可选，空格分隔的字符串）
        - `expires_in`（可选，秒）
        """
        ...

    async def fetch_account(self, token: str) -> dict:
        """取展示用账号信息，归一化成 {login, name, avatar}。"""
        ...


def normalize_token_payload(raw: dict) -> dict:
    """把各家换令牌接口的返回归一成上面约定的四个字段。

    单独抽出来是因为字段缺失的表现很隐蔽：`access_token` 拿成 None 时，
    后续每一次 API 调用都会以 401 失败，而错误信息指向的是「令牌无效」，
    没人会想到是换令牌那一步就没拿到东西。所以这里直接把缺失变成显式异常。
    """
    token = str((raw or {}).get("access_token") or "").strip()
    if not token:
        # 对方通常会在 error / error_description 里说明原因，带上比只说"失败"有用得多
        detail = str((raw or {}).get("error_description")
                     or (raw or {}).get("error") or "").strip()
        raise OAuthError(f"授权服务器没有返回访问令牌{('：' + detail) if detail else ''}")
    expires_in = (raw or {}).get("expires_in")
    try:
        expires_in = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in = None
    return {
        "access_token": token,
        "refresh_token": str((raw or {}).get("refresh_token") or "") or None,
        "scope": str((raw or {}).get("scope") or ""),
        "expires_in": expires_in,
    }


def missing_scopes(granted: str, required: tuple[str, ...]) -> tuple[str, ...]:
    """检查授权回来的 scope 里少了哪些必需项。

    **这个检查不是可有可无的。** Google 现在的同意页把每一项权限做成**默认不勾**的
    复选框，用户直接点「继续」而不勾，我们照样拿得到一个合法 access_token——
    只是它什么邮件都读不到。没有这道检查，界面会显示「已连接」，用户要等到真去问
    「帮我看看今天的邮件」才发现是坏的，而那时错误信息只会是一句 403。

    返回缺失的 scope 元组；空元组表示齐了。
    """
    have = {s.strip() for s in str(granted or "").replace(",", " ").split() if s.strip()}
    if not have:
        # 有的服务商不回 scope 字段。此时无从判断，只能放行——宁可漏检也不能把
        # 正常授权误判成失败（fail-open 在这里是唯一选择，但要让调用方知道）。
        return ()
    return tuple(s for s in required if s not in have)


def account_display(login: str = "", name: str = "", avatar: str = "") -> dict:
    """账号展示信息的统一形状，顺带做长度裁剪（列宽 128/512）。"""
    return {
        "login": str(login or "")[:128],
        "name": str(name or "")[:128],
        "avatar": str(avatar or "")[:512],
    }


__all__ = [
    "OAuthError", "OAuthProvider",
    "normalize_token_payload", "missing_scopes", "account_display",
]
