"""连接器服务层：绑定的增删改查、授权流程编排、MCP 工具清单同步。

对外暴露的所有 dict 都经 `_public()` 序列化——**凭据永远不出这一层**。
"""
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models import ConnectorBinding
from app.services.connectors import github as github_adapter
from app.services.connectors import gmail as gmail_adapter
from app.services.connectors import imapmail as imap_adapter
from app.services.connectors import outlook as outlook_adapter
from app.services.connectors.crypto import ConnectorCryptoError, decrypt_secret, encrypt_secret
from app.services.connectors.oauth_base import OAuthError
from app.services.connectors.providers import ProviderSpec, get_provider, list_providers

logger = logging.getLogger(__name__)


class ConnectorError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# 进行中的 OAuth 授权：state -> {user_id, provider, redirect_uri, expires_at}。
#
# state 有两个职责，缺一不可：
# 1. **CSRF 防护**——回调是浏览器带着 code 打过来的，没有 state 的话任何人都能构造一个
#    回调把自己的授权码塞进别人的账号；
# 2. **身份载体**——回调请求来自 GitHub 的重定向，**不带我们的鉴权头**，所以「这次授权
#    属于哪个用户」只能靠服务端存的 state 反查。
# 因此 state 必须不可猜（secrets.token_urlsafe）、一次性、且带过期。
#
# 进程内存即可：agent-api 单 worker（Dockerfile 未指定 --workers），流程只活 10 分钟；
# 重启导致的丢失表现为「请重新点连接」，不会造成脏数据。
_oauth_states: dict[str, dict] = {}
_OAUTH_STATE_TTL = 10 * 60


# 各 provider 的 OAuth 适配器（实现 oauth_base.OAuthProvider 协议的模块）。
# **新增一家 OAuth 应用要在这里登记**，否则 start_oauth 会明确拒绝而不是静默失败。
# 这张表取代了原先散在六处的 `if provider.id != "github"` —— 那种写法在接第二家时
# 表现为「点连接没反应、不报错、无日志」，是最难查的一类。
_OAUTH_ADAPTERS: dict[str, object] = {
    "github": github_adapter,
    "gmail": gmail_adapter,
    "outlook": outlook_adapter,
}

# 没有 OAuth、只能粘凭据的 provider（QQ 邮箱走 IMAP + 授权码——腾讯不给个人邮箱 OAuth）。
# 它们不实现 OAuth 协议，但同样要提供 verify_account：「先验证再落库」对它们更重要，
# 因为授权码填错时 IMAP 只回一句笼统的认证失败，不当场验就只能等用户真去用才发现，
# 而那时用户已经在 QQ 邮箱那边开 IMAP、发短信、抄码折腾了一整轮。
_NATIVE_ADAPTERS: dict[str, object] = {
    "qqmail": imap_adapter,
}


def tool_adapter(provider: ProviderSpec):
    """取**提供本地工具定义**的适配器；kind=mcp 或未接入返回 None。

    ⚠️ 这里必须按 `provider.kind` 判断，**不能按它落在哪个鉴权注册表里判断**。
    两个注册表分的是「怎么鉴权」，`kind` 分的是「工具从哪来」，是**两条正交的轴**：

        provider   鉴权注册表          kind      工具来源
        github     _OAUTH_ADAPTERS     mcp       远程 MCP 端点
        gmail      _OAUTH_ADAPTERS     native    适配器里的 TOOLS   ← 两轴不一致
        outlook    _OAUTH_ADAPTERS     native    适配器里的 TOOLS   ← 两轴不一致
        qqmail     _NATIVE_ADAPTERS    native    适配器里的 TOOLS

    2026-07-29 真机事故：此前这里只查 `_NATIVE_ADAPTERS`，Gmail 因为走 OAuth 而登记在
    `_OAUTH_ADAPTERS`，于是查不到 → 掉进 MCP 分支 → `mcp_endpoint` 写死 `!= "github"`
    直接抛 → 绑定被标成 `status=error`、错误信息「该应用尚未接入」。
    用户看到的是：**授权明明成功了、标签列表也出来了，但开关就是打不开**——因为
    `_public.enabled` 要求 `status == "active"`。而那句错误和他刚做的事毫无关系。
    """
    if provider.kind == "mcp":
        return None
    pid = str(provider.id or "").strip().lower()
    return _OAUTH_ADAPTERS.get(pid) or _NATIVE_ADAPTERS.get(pid)


def native_adapter(provider_id: str):
    """兼容旧调用点（按 id 取 native 适配器）。新代码请用 tool_adapter(provider)。"""
    return _NATIVE_ADAPTERS.get(str(provider_id or "").strip().lower())


# access token 提前多久就当作要过期。60 秒足够覆盖「取到令牌 → 发出请求」之间的间隔，
# 不留一个「查的时候还没过期、用的时候刚好过期」的窗口。
_TOKEN_SKEW_SECONDS = 60


async def ensure_fresh_token(binding: ConnectorBinding, provider: ProviderSpec) -> str:
    """取一个**当下可用**的 access token，快过期就先刷新并落库。

    ⚠️ 这个函数是补一个从一开始就缺的环节，不是优化：
    适配器里早就写了 `refresh_access_token`，但**上层一直没有调用方**——
    于是 Google 的 access token（约 1 小时）一到期，下一次调用就是 401，
    绑定被标 `status=error`、`last_error='Gmail 授权已失效'`，而 refresh token
    好端端地躺在库里从没被用过。

    表现极具迷惑性：**修好之后能用一小时，然后又坏**。人会以为是"数据又被谁改了"
    （我就这么以为过一轮，去查并发写入者），而真相是它每小时自己坏一次。
    只改数据是治标，这里才是根。

    刷新失败不抛异常：返回旧令牌让调用方照常往下走，让真正的 401 去触发
    `_mark_error`——这样错误信息说的是"授权失效，请重新连接"，而不是一句
    发生在刷新阶段、用户看不懂也无从下手的话。
    """
    # 走**模块级 decrypt_token** 而不是 _decrypt：前者是这一层的公开取令牌入口，
    # 上游测试普遍 monkeypatch 它来喂假令牌。绕开它等于让每个用假绑定的用例都去
    # 真解密一串不存在的密文——测试会以"工具一个都没挂"的形式失败，而报错完全指不到这里。
    token = decrypt_token(binding)
    if not getattr(binding, "refresh_cipher", None) or not getattr(binding, "token_expires_at", None):
        # 没有 refresh token（GitHub App 那条路故意不启用过期），或者对方没给过期时间
        return token
    if binding.token_expires_at > datetime.utcnow() + timedelta(seconds=_TOKEN_SKEW_SECONDS):
        return token                                    # 还没到期

    adapter = _OAUTH_ADAPTERS.get(provider.id)
    refresh = getattr(adapter, "refresh_access_token", None)
    if not callable(refresh):
        return token
    try:
        refresh_token = decrypt_secret(binding.refresh_cipher)
        payload = await refresh(refresh_token)
    except (ConnectorCryptoError, OAuthError) as exc:
        logger.warning("连接器 %s 刷新令牌失败：%s", provider.id, exc)
        return token

    new_token = str((payload or {}).get("access_token") or "").strip()
    if not new_token:
        return token
    expires_in = (payload or {}).get("expires_in")
    # **对方给了新的 refresh token 就必须换掉旧的**——微软官方明确要求这一点，
    # 不换的话旧的会在某次刷新后失效，表现又是「过一阵子突然要重新授权」。
    new_refresh = str((payload or {}).get("refresh_token") or "")
    async with async_session() as session:
        row = await session.get(ConnectorBinding, binding.id)
        if row is not None:
            row.secret_cipher = encrypt_secret(new_token)
            if new_refresh:
                row.refresh_cipher = encrypt_secret(new_refresh)
            row.token_expires_at = (
                datetime.utcnow() + timedelta(seconds=int(expires_in))
                if expires_in else None
            )
            # 刷成功说明凭据是好的：把上一次过期造成的 error 态一并清掉，
            # 否则界面会一直显示"授权已失效"而实际已经自愈。
            if row.status == "error":
                row.status = "active"
                row.last_error = ""
            await session.commit()
    logger.info("连接器 %s 已刷新 access token", provider.id)
    return new_token


def _oauth_adapter(provider: ProviderSpec):
    adapter = _OAUTH_ADAPTERS.get(provider.id)
    if adapter is None:
        raise ConnectorError(f"{provider.name} 暂不支持登录连接", 400)
    return adapter


def _sweep_oauth_states() -> None:
    now = time.time()
    for key in [k for k, v in _oauth_states.items() if v.get("expires_at", 0) < now]:
        _oauth_states.pop(key, None)


def _require_provider(provider_id: str) -> ProviderSpec:
    provider = get_provider(provider_id)
    if provider is None:
        raise ConnectorError("未知的连接器", 404)
    ok, reason = _provider_available(provider)
    if not ok:
        raise ConnectorError(reason, 503)
    return provider


def _parse_resources(binding: Optional[ConnectorBinding]) -> list[str]:
    if binding is None or not binding.scope_json:
        return []
    try:
        parsed = json.loads(binding.scope_json)
    except (json.JSONDecodeError, TypeError):
        return []
    items = (parsed or {}).get("repos") if isinstance(parsed, dict) else None
    return [str(x) for x in (items or []) if str(x).strip()]


def parse_tools(binding: ConnectorBinding) -> list[dict]:
    if not binding.tools_json:
        return []
    try:
        parsed = json.loads(binding.tools_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [t for t in (parsed or []) if isinstance(t, dict) and t.get("name")]


def _provider_available(provider: ProviderSpec) -> tuple[bool, str]:
    """provider 是否真的能用 = 配置齐了 **且** 适配器已经接进来。

    `ProviderSpec.available()` 只看配置（凭据/MCP 地址），它不知道代码写没写。
    注册表在本模块，所以这道补充判据只能在这一层加。

    为什么必须有：ProviderSpec 是先于适配器落地的（先把连接器摆进界面，再逐个写实现）。
    这期间 QQ 邮箱那种"不需要任何平台注册"的 provider 会显示成**完全可用**——用户
    照着引导去 QQ 邮箱开 IMAP、发短信验证、抄下 16 位授权码，回来点连接，撞一句
    「尚未接入」。前面那一串操作全白做，而且失败发生在最后一步。
    宁可一开始就灰着说"开发中"。
    """
    ok, reason = provider.available()
    if not ok:
        return ok, reason
    if provider.id not in _OAUTH_ADAPTERS and provider.id not in _NATIVE_ADAPTERS:
        return False, f"{provider.name} 连接器正在开发中，敬请期待"
    return True, ""


def _data_authorized(provider: ProviderSpec, binding: Optional[ConnectorBinding]) -> bool:
    """第二段授权（「能读什么」）是否已经拿到。

    GitHub 的两段授权是它自己的形态：登录只证明「你是谁」，能读哪些仓库由**装 App**
    决定，所以判据是 installation_id。别的 provider 没有这一段——Gmail 的数据权限在
    OAuth scope 里就给全了，QQ 邮箱的授权码本身即权限。

    原先这里写死 `bool(binding.installation_id)`，接入第二家时会让所有新 provider 的
    `enabled` 恒为 False —— 表现是「连上了、开关也打开了，但工具就是不挂载」，而且
    界面上完全看不出缺了什么（installation_id 这个概念对邮箱用户毫无意义）。
    """
    if binding is None or binding.status == "revoked":
        return False
    if provider.id == "github":
        return bool(binding.installation_id)
    return True


def _account_view(binding: ConnectorBinding) -> dict:
    """账户列表里的一项（不含任何凭据）。"""
    return {
        "login": binding.account_login or "",
        "name": binding.account_name or "",
        "avatar": binding.account_avatar or "",
        # 账户级选中：与 provider 级 enabled 是两个维度，见 models.py 的说明
        "selected": bool(binding.account_selected),
        "status": binding.status or "",
        "lastError": binding.last_error or "",
    }


def _public(provider: ProviderSpec, binding: Optional[ConnectorBinding],
            bindings: Optional[list[ConnectorBinding]] = None) -> dict:
    """连接器 + 当前用户绑定状态的对外视图（不含任何凭据）。

    `binding` 是"主"账户（第一条），保留是为了不动既有字段的语义；
    `bindings` 是全部账户，多出来的信息走 `accounts` 数组。**没有删任何旧字段**——
    前端分批上线，删字段会让还没更新的那一版直接白屏。
    """
    rows = bindings if bindings is not None else ([binding] if binding else [])
    resources = _parse_resources(binding)
    available, reason = _provider_available(provider)
    return {
        "id": provider.id,
        "name": provider.name,
        "icon": provider.icon,
        "summary": provider.summary,
        "beta": provider.beta,
        "available": available,
        "unavailableReason": reason,
        "authKinds": list(provider.auth_available()),
        "authNotice": provider.auth_notice(),
        "resourceKind": provider.resource_kind,
        "resourceLabel": provider.resource_label,
        "resourceHint": provider.resource_hint,
        "tokenLabel": provider.token_label,
        "tokenHelp": provider.token_help,
        "tokenLink": provider.token_link,
        # IMAP 这类要「账号 + 令牌」两样，空串时前端只渲染一个令牌输入框
        "accountLabel": provider.account_label,
        "accountPlaceholder": provider.account_placeholder,
        # 前端据此决定要不要做本地格式校验。显式下发而不是让前端从 resourceKind 或
        # placeholder 去猜——猜错的表现是校验静默不执行，界面看着却像有校验。
        "accountFormat": provider.account_format,
        "configLink": provider.config_link,
        # ---- 连接器详情弹窗（2026-07-29）----
        "description": provider.detail_description,
        "examplePrompts": list(provider.example_prompts),
        "author": provider.author,
        "homepage": provider.homepage,
        "privacyLink": provider.privacy_link,
        "docLink": provider.doc_link,
        # 「连接器类型」那一格：MCP（对方官方开的，工具由厂商定义）/ 应用（我们自研适配）。
        # 原先前端写死成「应用」，Canva 接进来之后就错了。
        "connectorType": provider.connector_type_label,
        # 两段授权的状态，驱动界面上「✅ 授权账户 — ○ 授权仓库」那条引导：
        # 只完成第一段时连接器是**连上了但读不到任何东西**的，必须让用户看见还差一步。
        "accountAuthorized": binding is not None and binding.status != "revoked",
        "reposAuthorized": _data_authorized(provider, binding),
        "connected": binding is not None and binding.status != "revoked",
        # `enabled` 是**开关的位置**（用户的意图），不是「此刻能不能用」。
        #
        # ⚠️ 2026-07-29 真机事故：这里原本把 `status == "active"` 也揉进来，理由是
        # 「没拿到数据权限时即便 enabled 也不该当成在用」。后果是**一次临时故障会夺走
        # 用户对开关的控制**：Gmail 的 access token 过期把 status 标成 error 之后，
        # 用户点开关 → 后端确实写入 `enabled=1` → 但这里仍然回 False → 界面渲染成关。
        # 用户看到的是「开关怎么点都打不开」，而**它其实一直是开的**。
        # 更糟的是这形成了死锁的观感：error 只在成功调用一次之后才清，而用户以为
        # 自己根本没能把它打开。
        #
        # 现在只反映开关位置。「此刻能不能用」前端另有三个字段可判：
        # `status`（active/error/revoked）、`lastError`（人话原因）、`reposAuthorized`
        # （GitHub 的第二段授权做没做）。三者语义清晰，不必挤进一个布尔里。
        "enabled": bool(binding and binding.enabled and binding.status != "revoked"),
        "status": binding.status if binding else "",
        "lastError": (binding.last_error or "") if binding else "",
        "account": {
            "login": binding.account_login or "",
            "name": binding.account_name or "",
            "avatar": binding.account_avatar or "",
        } if binding else None,
        # 多账户（2026-07-29 用户拍板）：前端据此列出全部账户并逐个勾选。
        # 单账户时也是长度 1 的数组，前端不必分两套渲染。
        "accounts": [_account_view(x) for x in rows],
        "resources": resources,
        "resourceCount": len(resources),
        "toolCount": len(parse_tools(binding)) if binding else 0,
    }


async def _load_all(user_id: str, provider_id: str) -> list[ConnectorBinding]:
    """一个 provider 下该用户的**全部**账户绑定（多账户，2026-07-29）。

    排序固定按 account_login：界面上账户顺序必须稳定，否则每次刷新都在跳，
    用户会以为自己点错了行。
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider_id,
                ).order_by(ConnectorBinding.account_login)
            )
        ).scalars().all()
    return list(rows)


async def _load(user_id: str, provider_id: str,
                account: Optional[str] = None) -> Optional[ConnectorBinding]:
    """单条绑定。`account` 为空时取第一条——**不能再用 scalar_one_or_none()**：
    多账户之后同一个 provider 天然有多行，那个方法会直接抛 MultipleResultsFound，
    而它抛在一条所有人都会走的读路径上（列表、详情、开关全都经过这里）。
    """
    rows = await _load_all(user_id, provider_id)
    if account:
        key = str(account).strip().lower()
        for row in rows:
            if str(row.account_login or "").strip().lower() == key:
                return row
        return None
    return rows[0] if rows else None


async def _load_required(user_id: str, provider_id: str) -> ConnectorBinding:
    binding = await _load(user_id, provider_id)
    if binding is None:
        raise ConnectorError("尚未连接该应用", 404)
    return binding


async def list_connectors(user_id: str) -> list[dict]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(ConnectorBinding.user_id == user_id)
            )
        ).scalars().all()
    # 多账户：必须**分组成列表**。原先是 `{r.provider: r}`，同一个 provider 有多行时
    # 只会留下最后一条，而且是静默的——界面上表现为"我连了两个账号，只看到一个"。
    by_provider: dict[str, list[ConnectorBinding]] = {}
    for r in rows:
        by_provider.setdefault(r.provider, []).append(r)
    for group in by_provider.values():
        group.sort(key=lambda x: str(x.account_login or ""))
    out = []
    for p in list_providers():
        group = by_provider.get(p.id) or []
        out.append(_public(p, group[0] if group else None, group))
    return out


async def get_connector(user_id: str, provider_id: str) -> dict:
    provider = _require_provider(provider_id)
    rows = await _load_all(user_id, provider.id)
    return _public(provider, rows[0] if rows else None, rows)


# ---------------------------------------------------------------- 授权

def _safe_return_to(raw: str) -> str:
    """回跳地址白名单：只接受**站内绝对路径**。

    这个值最终会进 303 的 Location，接受站外地址等于给自己开一个开放重定向
    （钓鱼链接可以借我们的域名跳到任意站点）。

    ⚠️ 必须**先归一化再判断**，顺序反了等于没防（2026-07-28 实测出四个绕过：
    `/\evil.com`、`/\/evil.com`、`/\n/evil.com`、`/\t/evil.com` 全部放行）。原因是
    判断发生在**浏览器归一化之前**：
    - WHATWG URL 规范里反斜杠等价于斜杠，浏览器把 `/\evil.com` 当 `//evil.com`；
    - 浏览器会直接剥掉 URL 里的 Tab(0x09)/LF(0x0A)/CR(0x0D)，`/\n/evil.com`
      还原之后同样是 `//evil.com`。
    两条路最后都变成协议相对地址 → 跳去站外，正是本函数要防的那件事。
    """
    path = str(raw or "").strip()
    # 剥掉浏览器会忽略的控制字符，再把反斜杠折成斜杠——之后 startswith 的判断才作数
    path = path.translate({9: None, 10: None, 13: None})
    path = path.replace("\\", "/")
    if not path.startswith("/") or path.startswith("//"):
        return "/center/chat"
    return path[:512]


async def start_oauth(user_id: str, provider_id: str, redirect_uri: str,
                      return_to: str = "") -> dict:
    """签发 state 并返回 GitHub 登录授权页地址；前端**在当前页直接跳转**过去。

    return_to 是授权完成后回到的站内地址（默认主对话），存在 state 里——回调是
    GitHub 重定向过来的，除了 state 我们没有别的东西能认出「他刚才在哪一页」。
    """
    provider = _require_provider(provider_id)
    if "oauth" not in provider.auth_available():
        raise ConnectorError("该应用未开放登录连接，请改用访问令牌", 400)
    adapter = _oauth_adapter(provider)

    _sweep_oauth_states()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "provider": provider.id,
        # 换令牌时必须回传**同一个** redirect_uri，GitHub 会逐字比对
        "redirect_uri": redirect_uri,
        "return_to": _safe_return_to(return_to),
        "expires_at": time.time() + _OAUTH_STATE_TTL,
    }
    try:
        url = adapter.build_authorize_url(state, redirect_uri)
    except OAuthError as exc:
        _oauth_states.pop(state, None)
        raise ConnectorError(str(exc), 503)
    return {"authorizeUrl": url}


async def complete_oauth(provider_id: str, code: str, state: str,
                         installation_id: str = "") -> dict:
    """回调落地：校验 state → 换令牌 → 落库。返回 {status, connector?}。

    调用方是**无鉴权**的回调端点，用户身份完全由 state 反查（见 _oauth_states 注释）。
    """
    _sweep_oauth_states()
    entry = _oauth_states.pop(str(state or ""), None)  # 一次性，防重放
    if not entry or entry.get("provider") != str(provider_id or "").lower():
        raise ConnectorError("授权链接已失效，请回到对话里重新点击连接", 410)

    provider = _require_provider(entry["provider"])
    adapter = _oauth_adapter(provider)
    try:
        exchanged = await adapter.exchange_code(code, entry["redirect_uri"])
    except OAuthError as exc:
        raise ConnectorError(str(exc), 502)

    connector = await _save_credential(
        user_id=entry["user_id"], provider=provider,
        token=exchanged["access_token"], auth_kind="oauth",
        granted_scopes=str(exchanged.get("scope") or ""),
        refresh_token=str(exchanged.get("refresh_token") or ""),
        expires_in=exchanged.get("expires_in"),
    )
    # App 注册时勾了「安装过程中请求用户授权」，所以**从安装页过来的那一趟**会把
    # installation_id 一起带回本回调——此时两段授权在一次跳转里就都完成了，
    # 不必再让用户点一次「添加仓库」。
    if installation_id:
        try:
            result = await _adopt_installation(entry["user_id"], provider, installation_id)
            result["returnTo"] = entry.get("return_to") or "/center/chat"
            return result
        except ConnectorError as exc:
            logger.warning("授权回调携带的安装 id 处理失败: %s", exc)
    else:
        # 回调没带 installation_id ≠ 用户没装过 App。**重新登录**走的是纯 OAuth 授权页，
        # GitHub 不会捎上 installation_id；而 _upsert_binding 为了防「换账号后旧安装串号」
        # 会把 installation_id 清成 None。两件事撞在一起的后果是：用户明明还装着 App，
        # 重登一次就变回「还需要选择授权的代码库」，工具也被 load_active_bindings 摘掉
        # ——连接器静默失效，且界面上看不出发生过什么。
        # 这里主动回查一次该用户名下本 App 的安装，唯一那个直接认领回来。多于一个说明
        # 装到了多个账号/组织上，我们一行只存一个 id，交给用户自己点「添加代码库」去选。
        adopted = await _adopt_existing_installation(entry["user_id"], provider, connector)
        if adopted is not None:
            adopted["returnTo"] = entry.get("return_to") or "/center/chat"
            return adopted
    return {"status": "connected", "connector": connector,
            "returnTo": entry.get("return_to") or "/center/chat"}


async def _adopt_existing_installation(user_id: str, provider: ProviderSpec,
                                       connector: dict) -> Optional[dict]:
    """重登后把仍然有效的旧安装认领回来。任何一步不确定就返回 None 走原来的流程。"""
    if provider.id != "github":
        return None
    try:
        binding = await _load_required(user_id, provider.id)
        installs = await github_adapter.list_installations(_decrypt(binding))
    except (ConnectorError, ConnectorCryptoError, github_adapter.GitHubError) as exc:
        # 这是一条**锦上添花**的路径：认领不回来只是回到「用户自己点添加代码库」，
        # 绝不能让它把一次本来成功的登录变成 500。
        logger.warning("重登后回查 GitHub 安装失败，按未安装处理: %s", exc)
        return None
    if len(installs) != 1:
        return None
    try:
        return await _adopt_installation(user_id, provider, installs[0]["id"])
    except ConnectorError as exc:
        logger.warning("重登后认领旧安装失败: %s", exc)
        return None


async def _adopt_installation(user_id: str, provider: ProviderSpec, installation_id: str) -> dict:
    """把一次安装挂到绑定上，并按已授权仓库修剪/初始化勾选。"""
    binding = await _load_required(user_id, provider.id)
    token = _decrypt(binding)
    try:
        allowed = await github_adapter.list_authorized_repositories(token, installation_id)
    except github_adapter.GitHubError as exc:
        raise ConnectorError(str(exc), 502)
    allowed_names = {r["fullName"] for r in allowed}

    async with async_session() as session:
        row = await session.get(ConnectorBinding, binding.id)
        if row is not None:
            row.installation_id = str(installation_id or "") or None
            kept = [x for x in _parse_resources(row) if x in allowed_names]
            if not kept:
                limit = max(1, int(settings.CONNECTOR_MAX_RESOURCES or 20))
                kept = [r["fullName"] for r in allowed][:limit]
            row.scope_json = json.dumps({"repos": kept}, ensure_ascii=False)
            row.status = "active"
            row.last_error = ""
            await session.commit()
    return {"status": "installed", "connector": await get_connector(user_id, provider.id)}


async def connect_with_token(
    user_id: str, provider_id: str, token: str, account: str = "",
) -> dict:
    provider = _require_provider(provider_id)
    if "token" not in provider.auth_available():
        raise ConnectorError("该应用不支持访问令牌连接", 400)
    token = str(token or "").strip()
    if not token:
        raise ConnectorError(f"请填写{provider.token_label}")
    account = str(account or "").strip()
    # 声明了 account_label 的 provider（IMAP 类）**必须**拿到账号：光有授权码连不上，
    # 也无法反查身份。这里挡住比让它走进 fetch_account 分支强——那条路对 native
    # 适配器根本不存在，报出来是 AttributeError，看不出是"账号没填/没传到"。
    if provider.account_label and not account:
        raise ConnectorError(f"请填写{provider.account_label}")
    return await _save_credential(
        user_id=user_id, provider=provider, token=token,
        auth_kind="token", granted_scopes="", account_hint=account,
    )


async def _save_credential(
    *, user_id: str, provider: ProviderSpec, token: str,
    auth_kind: str, granted_scopes: str, tenant_id: str = "0",
    refresh_token: str = "", expires_in: Optional[int] = None,
    account_hint: str = "",
) -> dict:
    """校验凭据 → 加密落库 → 同步工具清单。任一步失败都不留下半截绑定。

    **凭据必须先验证再落库**：不验就存的话，界面显示「已连接」而每一次调用都失败，
    用户完全不知道该回去改哪一步（GitHub 那边的 installation 丢失就是这个形态）。
    """
    adapter = _OAUTH_ADAPTERS.get(provider.id) or _NATIVE_ADAPTERS.get(provider.id)
    if adapter is None:
        raise ConnectorError(f"{provider.name} 尚未接入", 501)
    try:
        # account_hint 是用户自己填的账号（IMAP 类要「邮箱地址 + 授权码」两样，
        # 光凭授权码连不上，更谈不上反查身份）。OAuth 类不需要，传空串。
        if account_hint:
            account = await adapter.verify_account(token, account_hint)
        elif hasattr(adapter, "fetch_account"):
            account = await adapter.fetch_account(token)
        else:
            # native 适配器（IMAP 类）只能验证「账号 + 授权码」，没有"凭令牌反查身份"
            # 这回事。走到这里说明账号没送到——给一句用户能照做的话，不要 AttributeError。
            raise ConnectorError(
                f"请填写{provider.account_label or '账号'}", 400,
            )
    except OAuthError as exc:
        raise ConnectorError(str(exc), 400)

    try:
        cipher = encrypt_secret(token)
        refresh_cipher = encrypt_secret(refresh_token) if refresh_token else None
    except ConnectorCryptoError as exc:
        raise ConnectorError(str(exc), 500)

    # 多账户 upsert：按 (user, provider, **account_login**) 定位。
    # 原先只按 (user, provider) 找，第二次连同一家会**覆盖掉第一个账户**——
    # 而且是静默覆盖：界面只显示新账户，旧账户连同它的凭据一起消失。
    # 同一个账户重连则应当更新那一行（换了新令牌），不是新增一行。
    login_key = str((account or {}).get("login") or "").strip()
    async with async_session() as session:
        rows_existing = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider.id,
                )
            )
        ).scalars().all()
        binding = next(
            (r for r in rows_existing if (r.account_login or "") == login_key), None)
        if binding is None:
            # `enabled` 是 **provider 级**的总开关（界面上一个连接器只有一个开关），
            # 所以同一个 provider 的所有账户行必须取同一个值。这里跟随**已有行**，
            # 不能硬编码 1：
            #   用户先关掉了 QQ 邮箱的开关，再连第二个账户 → 新行 enabled=1、老行 0，
            #   而 load_active_bindings 是按行过滤的，于是**新账户挂载、老账户不挂载**，
            #   界面上却只有一个显示为"开"的开关。用户看到的是"我开着，却只读到一个邮箱"。
            # 一条都没有时（首次连接）才默认开。
            enabled_now = 1 if not rows_existing else int(bool(rows_existing[0].enabled))
            binding = ConnectorBinding(
                id=uuid.uuid4().hex, user_id=user_id, tenant_id=tenant_id,
                provider=provider.id, enabled=enabled_now,
                account_login=login_key,
                # 新连上的账户默认选中——「连上了却读不到」这个死胡同已经踩过一次
                account_selected=1,
            )
            session.add(binding)
        binding.secret_cipher = cipher
        # 刷新令牌：Google 只在**首次**授权时返回，重连若没带就必须保留旧的，
        # 否则一次「重新连接」会把续期能力永久弄丢（表现为几小时后连接器静默失效）。
        if refresh_cipher:
            binding.refresh_cipher = refresh_cipher
        binding.token_expires_at = (
            datetime.utcnow() + timedelta(seconds=int(expires_in))
            if expires_in else None
        )
        binding.auth_kind = auth_kind
        binding.granted_scopes = granted_scopes[:512]
        binding.account_login = account["login"][:128]
        binding.account_name = account["name"][:128]
        binding.account_avatar = account["avatar"][:512]
        binding.status = "active"
        binding.last_error = ""
        # 重新连接（换账号/换令牌）时旧的仓库勾选可能指向新账号无权访问的仓库，
        # 保留反而会让白名单闸拦下每一次调用且原因难懂——直接清空，让用户重选。
        binding.scope_json = None
        binding.tools_json = None
        binding.tools_synced_at = None
        # 换账号重连时旧的安装 id 属于上一个账号，留着会让「已授权仓库」列成别人的
        binding.installation_id = None
        await session.commit()
        binding_id = binding.id

    await _sync_tools(binding_id, provider, token)
    return await get_connector(user_id, provider.id)


async def disconnect(user_id: str, provider_id: str, account: str = "") -> dict:
    """断开连接。`account` 非空＝只断那一个账户，空＝断掉该 provider 的**全部**账户。

    默认断全部而不是"断第一个"：用户点的是连接器上的「断开连接」，他的意图是
    "我不用这个应用了"。只断掉其中一个、剩下的还连着，是最难被察觉的一种半成品状态。
    """
    provider = _require_provider(provider_id)
    key = str(account or "").strip().lower()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider.id,
                )
            )
        ).scalars().all()
        targets = [r for r in rows
                   if not key or str(r.account_login or "").strip().lower() == key]
        for binding in targets:
            # 直接删行而不是标 revoked：留着一条带密文的行没有任何用途，
            # 「断开连接」在用户预期里就是凭据被清掉。
            await session.delete(binding)
        # commit 放在循环**外**：断多个账户时逐条提交，中途失败会留下"删了一半"的状态，
        # 而用户看到的是一次操作。一次提交要么全删要么全不删。
        await session.commit()

    # ⚠️ 这里必须**重新读一次**，不能 `return _public(provider, None)`。
    # 那句在单账户时刚好是对的（删完就真没了），多账户下却是错的：按账户断开时
    # 别的账户还连着，而它回的是「什么都没连」——界面会把整个连接器显示成已断开，
    # 用户以为自己误删了全部。
    #
    # 这是「重写后 return 还在、但返回的是重写前的结构」这一类：AST 查得到 return、
    # 类型注解也对（都是 dict）、前端不报错，**只是内容是过时的**。
    return await get_connector(user_id, provider.id)


async def set_enabled(user_id: str, provider_id: str, enabled: bool) -> dict:
    """连接器总开关（provider 级）。多账户时**一起开关**——这个开关的语义是
    "这一轮用不用这个应用"，不是"用哪个账户"；选哪些账户由 set_account_selected 管。"""
    provider = _require_provider(provider_id)
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider.id,
                )
            )
        ).scalars().all()
        if not rows:
            raise ConnectorError("尚未连接该应用", 404)
        for binding in rows:
            binding.enabled = 1 if enabled else 0
        await session.commit()
    # ⚠️ 这个 return 不是可有可无的：签名写着 `-> dict`，路由把返回值原样下发，
    # 前端 `applyConnector(next)` 第一件事就是读 `next.id`。漏了它接口回 null，
    # 用户点开关时看到的是 `Cannot read properties of null (reading 'id')` ——
    # 一句和"开关"毫无关系的报错，而**开关本身其实已经切成功了**（事务已提交），
    # 只是界面没法更新，看起来像"点不动"。
    # 2026-07-29 真机踩到：多账户改造时这个函数从"改一行"重写成"循环改多行"，
    # 返回值在重写中丢了，而 Python 不会因为漏 return 报任何错。
    return await get_connector(user_id, provider.id)


async def set_account_selected(user_id: str, provider_id: str,
                               account: str, selected: bool) -> dict:
    """勾选/取消某一个账户（多账户，2026-07-29 用户拍板）。

    与总开关分开：用户有两个邮箱、这轮只想看工作那个时，不该被迫关掉整个连接器。
    **不拦"全部取消"**——那等于用户明确表示这一轮谁都不读，是合法意图；
    此时工具不挂载，与总开关关掉的效果一致，不需要额外提示。
    """
    provider = _require_provider(provider_id)
    key = str(account or "").strip().lower()
    if not key:
        raise ConnectorError("缺少账户标识", 400)
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider.id,
                )
            )
        ).scalars().all()
        hit = [r for r in rows
               if str(r.account_login or "").strip().lower() == key]
        if not hit:
            raise ConnectorError("没有这个账户", 404)
        for binding in hit:
            binding.account_selected = 1 if selected else 0
        await session.commit()
    return await get_connector(user_id, provider.id)


async def set_resources(user_id: str, provider_id: str, resources: list) -> dict:
    provider = _require_provider(provider_id)
    if not provider.resource_kind:
        raise ConnectorError("该应用无需选择资源", 400)
    cleaned: list[str] = []
    for item in resources or []:
        name = str(item or "").strip()
        if name and name not in cleaned:
            cleaned.append(name)
    limit = max(1, int(settings.CONNECTOR_MAX_RESOURCES or 20))
    if len(cleaned) > limit:
        raise ConnectorError(f"最多只能选择 {limit} 个{provider.resource_label or '资源'}")

    async with async_session() as session:
        binding = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.provider == provider.id,
                )
            )
        ).scalar_one_or_none()
        if binding is None:
            raise ConnectorError("尚未连接该应用", 404)
        binding.scope_json = json.dumps({"repos": cleaned}, ensure_ascii=False)
        await session.commit()
    return await get_connector(user_id, provider.id)


async def list_resources(user_id: str, provider_id: str) -> list[dict]:
    """可选资源 = **用户在 GitHub 上授权给本应用的仓库**，不是账号下的全部仓库。

    这条区别是整个改造的重点：以前列 `/user/repos`（你有哪些仓库），用户在我们界面里勾选，
    但令牌本身仍能读全部——勾选只是软过滤。现在列的是安装被授权的那批，GitHub 那层已经
    把边界钉死了，我们的勾选退化成「在已授权范围内再挑几个给模型看」。
    """
    provider = _require_provider(provider_id)
    binding = await _load_required(user_id, provider.id)
    token = _decrypt(binding)
    try:
        if provider.id == "github":
            return await github_adapter.list_authorized_repositories(
                token, binding.installation_id or "")
        # 其余 provider 各自列自己的资源：Gmail 列标签、邮箱类列文件夹。
        # 形状统一成 [{fullName, name, description?}]，前端那份勾选面板不必按 provider 分叉。
        adapter = _OAUTH_ADAPTERS.get(provider.id) or _NATIVE_ADAPTERS.get(provider.id)
        if adapter is None or not hasattr(adapter, "list_resources"):
            return []
        return await adapter.list_resources(token, binding.account_login or "")
    except OAuthError as exc:
        await _mark_error(binding.id, str(exc))
        raise ConnectorError(str(exc), 502)


async def start_install(user_id: str, provider_id: str, return_to: str = "") -> dict:
    """第二段授权：跳 GitHub App 安装页，让用户勾选授权哪些仓库。"""
    provider = _require_provider(provider_id)
    if "oauth" not in provider.auth_available():
        raise ConnectorError("该应用未开放安装授权", 400)
    await _load_required(user_id, provider.id)  # 必须先完成第一段

    _sweep_oauth_states()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "provider": provider.id,
        "kind": "install",
        "redirect_uri": "",
        "return_to": _safe_return_to(return_to),
        "expires_at": time.time() + _OAUTH_STATE_TTL,
    }
    try:
        url = github_adapter.build_install_url(state)
    except github_adapter.GitHubError as exc:
        _oauth_states.pop(state, None)
        raise ConnectorError(str(exc), 503)
    return {"installUrl": url}


async def complete_install(provider_id: str, state: str, installation_id: str) -> dict:
    """安装回调落地：把 installation_id 记到绑定上。

    安装完成后用户已授权的仓库集合可能变了（新增/移除），所以顺手把已勾选资源里**已经
    不在授权范围内**的条目剔掉——留着它们只会让模型每次调用都撞白名单闸。
    """
    _sweep_oauth_states()
    entry = _oauth_states.pop(str(state or ""), None)
    if not entry or entry.get("provider") != str(provider_id or "").lower():
        raise ConnectorError("安装链接已失效，请回到对话里重新点击添加仓库", 410)

    provider = _require_provider(entry["provider"])
    binding = await _load_required(entry["user_id"], provider.id)
    token = _decrypt(binding)

    # 首次安装、用户还没勾过任何东西时，_adopt_installation 会把已授权仓库默认全给上——
    # 他刚在 GitHub 上亲手选过一遍，再让他在我们这儿选第二遍是多余的。
    result = await _adopt_installation(entry["user_id"], provider, installation_id)
    result["returnTo"] = entry.get("return_to") or "/center/chat"
    return result


# ---------------------------------------------------------------- 凭据与工具清单

def _decrypt(binding: ConnectorBinding) -> str:
    try:
        return decrypt_secret(binding.secret_cipher)
    except ConnectorCryptoError as exc:
        raise ConnectorError(str(exc), 500)


async def _mark_error(binding_id: str, message: str) -> None:
    async with async_session() as session:
        binding = await session.get(ConnectorBinding, binding_id)
        if binding is not None:
            binding.status = "error"
            binding.last_error = message[:512]
            await session.commit()


def mcp_endpoint(provider: ProviderSpec, token: str) -> tuple[str, dict]:
    if provider.id != "github":
        raise ConnectorError("该应用尚未接入", 501)
    return provider.mcp_url, {"Authorization": f"Bearer {token}"}


async def _store_tools(binding_id: str, tools: list[dict]) -> None:
    """工具清单落库并把绑定标成可用。native 与 MCP 两条路径共用，
    免得「同一件事的两处实现只改一处」——本文件已经因为这个栽过一次。"""
    async with async_session() as session:
        binding = await session.get(ConnectorBinding, binding_id)
        if binding is not None:
            binding.tools_json = json.dumps(tools, ensure_ascii=False)
            binding.tools_synced_at = datetime.now()
            binding.status = "active"
            binding.last_error = ""
            await session.commit()


async def _sync_tools(binding_id: str, provider: ProviderSpec, token: str) -> list[dict]:
    """连上远程 MCP 拉一次工具清单并落库。

    失败**不阻断连接**：凭据已经验证过了，工具清单只是缓存——标记错误让用户看到，
    下次挂载时会再试一次。把连接整个判失败反而更难排查（用户不知道令牌到底对没对）。
    """
    from app.services.gateway.mcp_client import McpClientError, list_mcp_tools

    # native provider（邮箱三家走 IMAP）的工具是**适配器本地定义**的，不存在远程
    # MCP 端点。此前这里无条件调 mcp_endpoint()，而它写死了 `provider.id != "github"
    # → 501`，于是 QQ 邮箱**凭据验证成功之后**才在同步工具这步炸掉，用户看到的是
    # 「该应用尚未接入」——一句和"授权码对不对"毫无关系、也完全无从下手的话。
    # 对话层（chat/tools/connectors.py）本来就有 native 分支不走 MCP，只有连接时
    # 这一步漏了：**同一件事的两处实现只改了一处**。
    adapter = tool_adapter(provider)
    if adapter is not None:
        tools = list(getattr(adapter, "TOOLS", []) or [])
        await _store_tools(binding_id, tools)
        return tools

    try:
        url, headers = mcp_endpoint(provider, token)
        tools = await list_mcp_tools(url, headers)
    except ConnectorError as exc:
        # mcp_endpoint 原本在 try **外面**——这个函数的注释写着「失败不阻断连接」，
        # 而它的第一行就能抛出去，承诺在自己的入口处就是空的。
        logger.warning("连接器 %s 没有可用的工具端点: %s", provider.id, exc)
        await _mark_error(binding_id, str(exc))
        return []
    except McpClientError as exc:
        # 把端点原样写进错误：这条链最可能出错的就是 MCP 地址（工具集裁剪路径写错、
        # 对方改版），而报错里不带地址的话，运维只能盲猜是凭据还是网络的问题。
        logger.warning("连接器 %s 工具清单同步失败 url=%s: %s", provider.id, url, exc)
        await _mark_error(binding_id, f"工具清单同步失败（{url}）：{exc}")
        return []

    await _store_tools(binding_id, tools)
    return tools


async def ensure_tools(binding: ConnectorBinding, provider: ProviderSpec, token: str) -> list[dict]:
    """返回可用工具清单；缓存缺失或过期时前台刷新一次。"""
    tools = parse_tools(binding)
    ttl = max(60, int(settings.CONNECTOR_TOOLS_TTL_SECONDS or 86400))
    fresh = bool(
        tools and binding.tools_synced_at
        and (datetime.now() - binding.tools_synced_at).total_seconds() < ttl
    )
    if fresh:
        return tools
    refreshed = await _sync_tools(binding.id, provider, token)
    # 刷新失败时退回旧缓存：远程抖一下不该让用户的连接器整个消失
    return refreshed or tools


def binding_mountable(binding: ConnectorBinding, provider: ProviderSpec) -> bool:
    """这条绑定这一轮该不该挂成工具。

    抽成**纯函数**是为了能被直接测到：它原先是 load_active_bindings 里的两个 continue，
    而那个函数要连真库，测起来要么跨事件循环炸、要么就干脆没人测。守卫写在没人测的地方
    等于没有守卫——这类"少挂了/多挂了一个"的缺陷，出问题时表现是功能静默缺失。
    """
    # 需要选资源却一个都没选 = 用户还没配完，挂上去只会让每次调用都被白名单闸拒绝
    if provider.resource_kind and not _parse_resources(binding):
        return False
    # 账户没被勾选 = 用户明确把它排除在这一轮之外（多账户，2026-07-29）
    if not binding.account_selected:
        return False
    return True


async def load_active_bindings(user_id: str) -> list[tuple[ConnectorBinding, ProviderSpec]]:
    """主对话挂载用：当前用户已启用、且已选好资源的绑定。"""
    if not user_id:
        return []
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ConnectorBinding).where(
                    ConnectorBinding.user_id == user_id,
                    ConnectorBinding.enabled == 1,
                )
            )
        ).scalars().all()

    result: list[tuple[ConnectorBinding, ProviderSpec]] = []
    for binding in rows:
        provider = get_provider(binding.provider)
        if provider is None:
            continue
        ok, _reason = provider.available()
        if not ok:
            continue
        # 第二段授权没做（没装 App）= 令牌只能证明身份、读不到任何仓库，挂上去必然全失败
        if provider.id == "github" and binding.auth_kind == "oauth" and not binding.installation_id:
            continue
        if not binding_mountable(binding, provider):
            continue
        result.append((binding, provider))
    return result


def resources_of(binding: ConnectorBinding) -> list[str]:
    return _parse_resources(binding)


def decrypt_token(binding: ConnectorBinding) -> str:
    return _decrypt(binding)


async def mark_error(binding_id: str, message: str) -> None:
    await _mark_error(binding_id, message)
