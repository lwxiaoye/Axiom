"""GitHub 连接器适配：OAuth 授权码登录、令牌校验、仓库列举、资源白名单闸。

授权走标准的**授权码流程**（Authorization Code）：点「连接」在新窗口打开 GitHub 的登录
授权页，用户点完「Authorize」后浏览器被重定向回我们的回调地址，服务端拿 code 换令牌。
主界面在这期间转圈等待，连上后自动进入仓库勾选。

关于内网部署：回调是 **GitHub 让用户浏览器跳转**，不是 GitHub 服务器来访问我们，所以
回调地址只要用户的浏览器能打开即可，内网地址同样成立。

⚠️ 权限粒度的实话：GitHub 的 **OAuth App 没有只读的仓库 scope**——要读私有库就得申请
`repo`，而 `repo` 是读写全给。我们在两处收窄实际能力：
1. MCP 端点固定走 `/x/repos/readonly`，挂给模型的工具本身就没有写操作；
2. 仓库白名单在调用参数上硬校验（见 assert_repo_allowed）。
但**令牌本身**的权限依然大于只读。要真正的最小权限，走「访问令牌」那条路，让用户在
GitHub 生成一个 fine-grained PAT、只勾选指定仓库的 Contents: Read-only —— 那才是权限
真正被切到只读的方案。两条路我们都支持，UI 上把这点讲清楚。
"""
import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.connectors.oauth_base import OAuthError

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
API_BASE = "https://api.github.com"

# 读私有仓库所需的最小可用 scope 组合（见模块头关于粒度的说明）
OAUTH_SCOPE = "repo read:user"

_TIMEOUT = httpx.Timeout(15.0)
_REPO_PAGE_SIZE = 100
_REPO_MAX_PAGES = 5  # 最多拉 500 个仓库；再多就靠搜索框而不是全量列举


class GitHubError(OAuthError):
    """message 可直接展示给用户。

    继承 OAuthError 是为了让 connector_service 只 catch 一个基类——接第二家 OAuth
    应用之后，逐家 import 各自的异常类型会让上层重新长回 provider 硬编码。
    """


def _headers(token: str = "") -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-platform-connector",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# 这些地址是**硬编码常量**，没有任何用户输入拼接，因此不存在 SSRF 面，
# 用裸 httpx 即可（PinnedPublicTransport 是给用户可控 URL 用的）。
async def _post_form(url: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, data=data, headers={"Accept": "application/json"})
    if resp.status_code >= 500:
        raise GitHubError(f"GitHub 服务暂时不可用（HTTP {resp.status_code}）")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise GitHubError("GitHub 返回了无法解析的响应") from exc
    if not isinstance(payload, dict):
        raise GitHubError("GitHub 返回了无法解析的响应")
    return payload


async def _get_json(path: str, token: str, params: Optional[dict] = None,
                    *, unauthorized: str = "") -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}{path}", headers=_headers(token), params=params)
    if resp.status_code == 401:
        # 401 的话术要分场景：**首次连接**填错令牌时说「已失效，请重新连接」是错的
        # ——用户根本没连过，这句话会把他引向「是不是过期了」而不是「是不是抄错了」。
        # 调用方按自己的场景传 unauthorized（2026-07-28 真机走查实测到）。
        raise GitHubError(unauthorized or "GitHub 凭据已失效，请重新连接")
    if resp.status_code == 403:
        # 403 在 GitHub 上既可能是限流也可能是权限不足，回执里带上原因帮用户判断
        detail = ""
        try:
            detail = str((resp.json() or {}).get("message") or "")
        except ValueError:
            pass
        raise GitHubError(f"GitHub 拒绝了该请求（权限不足或触发限流）{('：' + detail) if detail else ''}")
    if resp.status_code >= 400:
        raise GitHubError(f"GitHub 请求失败（HTTP {resp.status_code}）")
    try:
        return resp.json()
    except ValueError as exc:
        raise GitHubError("GitHub 返回了无法解析的响应") from exc


# ---------------------------------------------------------------- 授权码流程

def oauth_configured() -> bool:
    return bool((settings.CONNECTOR_GITHUB_CLIENT_ID or "").strip()
                and (settings.CONNECTOR_GITHUB_CLIENT_SECRET or "").strip()
                and (settings.CONNECTOR_GITHUB_APP_SLUG or "").strip())


def build_install_url(state: str) -> str:
    """App 安装页地址：用户在 GitHub 上勾选**哪些仓库**授权给本应用。

    这是授权的第二段，也是真正决定「能读什么」的一段——第一段（登录授权）只拿到身份。
    """
    slug = (settings.CONNECTOR_GITHUB_APP_SLUG or "").strip()
    if not slug:
        raise GitHubError("未配置 GitHub App slug")
    return f"https://github.com/apps/{slug}/installations/new?" + urlencode({"state": state})


def build_authorize_url(state: str, redirect_uri: str) -> str:
    """拼 GitHub 登录授权页地址；用户在新窗口里完成登录与授权。"""
    client_id = (settings.CONNECTOR_GITHUB_CLIENT_ID or "").strip()
    if not client_id:
        raise GitHubError("未配置 GitHub OAuth Client ID，请改用访问令牌连接")
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPE,
        "state": state,
        # 已经授权过的账号再点连接时也让 GitHub 显示一次账号选择/确认，
        # 否则换账号连接会**静默沿用上一个账号**，用户完全看不出来
        "allow_signup": "false",
    })


async def exchange_code(code: str, redirect_uri: str) -> dict:
    """用回调拿到的 code 换访问令牌。返回 {access_token, scope}。"""
    client_id = (settings.CONNECTOR_GITHUB_CLIENT_ID or "").strip()
    client_secret = (settings.CONNECTOR_GITHUB_CLIENT_SECRET or "").strip()
    if not client_id or not client_secret:
        raise GitHubError("未配置 GitHub OAuth 应用凭据")
    payload = await _post_form(ACCESS_TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    })
    error = str(payload.get("error") or "")
    if error:
        # redirect_uri_mismatch 是这条链最常见的配置错，原样透出来免得靠猜
        raise GitHubError(f"GitHub 授权失败：{payload.get('error_description') or error}")
    token = str(payload.get("access_token") or "")
    if not token:
        raise GitHubError("GitHub 授权失败：未返回访问令牌")
    return {"access_token": token, "scope": str(payload.get("scope") or "")}


# ---------------------------------------------------------------- 账号与资源

async def fetch_account(token: str) -> dict:
    """校验令牌并取账号信息（连接时用来确认凭据真的能用）。"""
    data = await _get_json(
        "/user", token,
        unauthorized="GitHub 不认这个访问令牌，请确认复制完整、没有多余空格，且尚未过期。",
    )
    if not isinstance(data, dict) or not data.get("login"):
        raise GitHubError("GitHub 凭据无效")
    return {
        "login": str(data.get("login") or ""),
        "name": str(data.get("name") or data.get("login") or ""),
        "avatar": str(data.get("avatar_url") or ""),
    }


def _repo_view(item: dict) -> Optional[dict]:
    if not isinstance(item, dict) or not item.get("full_name"):
        return None
    return {
        "fullName": str(item["full_name"]),
        "name": str(item.get("name") or ""),
        "private": bool(item.get("private")),
        "description": str(item.get("description") or "")[:200],
        "language": str(item.get("language") or ""),
        "updatedAt": str(item.get("updated_at") or ""),
    }


async def list_installations(token: str) -> list[dict]:
    """当前用户可见的本 App 安装。没有安装 = 第二段授权还没做，读不到任何仓库。"""
    data = await _get_json("/user/installations", token, params={"per_page": 100})
    items = (data or {}).get("installations") if isinstance(data, dict) else None
    return [
        {"id": str(x.get("id")), "account": str((x.get("account") or {}).get("login") or "")}
        for x in (items or []) if isinstance(x, dict) and x.get("id")
    ]


async def list_installation_repositories(token: str, installation_id: str) -> list[dict]:
    """列出某次安装被授权的仓库——**这就是用户在 GitHub 上勾选的那批**。

    与旧实现（`/user/repos` 列出账号下所有仓库）的差别是本质的：那个列的是「你有哪些仓库」，
    这个列的是「你授权给了本应用哪些仓库」。后者才是我们能读的真实边界。
    """
    repos: list[dict] = []
    for page in range(1, _REPO_MAX_PAGES + 1):
        data = await _get_json(
            f"/user/installations/{installation_id}/repositories", token,
            params={"per_page": _REPO_PAGE_SIZE, "page": page},
        )
        batch = (data or {}).get("repositories") if isinstance(data, dict) else None
        if not batch:
            break
        for item in batch:
            view = _repo_view(item)
            if view:
                repos.append(view)
        if len(batch) < _REPO_PAGE_SIZE:
            break
    repos.sort(key=lambda r: r["updatedAt"], reverse=True)
    return repos


async def list_authorized_repositories(token: str, installation_id: str = "") -> list[dict]:
    """已授权仓库全集。指定了 installation_id 就只看那一次安装，否则合并全部安装。"""
    if installation_id:
        return await list_installation_repositories(token, installation_id)
    seen: set = set()
    merged: list[dict] = []
    for inst in await list_installations(token):
        for repo in await list_installation_repositories(token, inst["id"]):
            if repo["fullName"] not in seen:
                seen.add(repo["fullName"])
                merged.append(repo)
    return merged


# ---------------------------------------------------------------- 白名单硬闸

# 会话里模型给出的仓库标识可能带 .git 后缀或大小写不一致，比较前统一归一
def normalize_repo(full_name: str) -> str:
    name = str(full_name or "").strip().strip("/")
    if name.lower().endswith(".git"):
        name = name[: -len(".git")]
    return name.lower()


def assert_repo_allowed(args: dict, allowed: list[str]) -> None:
    """校验一次工具调用触碰的仓库都在用户勾选的白名单内，否则抛错拒绝执行。

    这是**硬闸**，不是提示词约束：GitHub MCP 的工具几乎都带 owner/repo 参数，模型完全
    可能（被文档内容诱导、或单纯猜错）去读一个用户没勾选的仓库。与 build_tools 里
    「工具集合本身即授权边界」同一条原则，只是这里的边界落在参数上。
    """
    allow = {normalize_repo(x) for x in (allowed or []) if x}
    if not allow:
        raise GitHubError("尚未选择可读取的代码库，请先在连接器里勾选")

    owner = str(args.get("owner") or "").strip()
    repo = str(args.get("repo") or "").strip()
    if owner and repo:
        target = normalize_repo(f"{owner}/{repo}")
        if target not in allow:
            raise GitHubError(
                f"代码库 {owner}/{repo} 不在你勾选的范围内，已拒绝访问。"
                f"当前可读取：{'、'.join(sorted(allow))}"
            )
    elif repo and "/" in repo:
        # 少数工具把 owner/repo 合在一个参数里传
        if normalize_repo(repo) not in allow:
            raise GitHubError(f"代码库 {repo} 不在你勾选的范围内，已拒绝访问。")


def scope_search_query(args: dict, allowed: list[str]) -> dict:
    """给搜索类工具的 query 注入 `repo:` 限定符，把搜索面收进白名单。

    搜索工具没有 owner/repo 参数，白名单校验无从下手——只能改写查询本身。模型自己写的
    `repo:` 限定同样要过滤：它写了个白名单外的仓库，等于绕过闸门。
    """
    allow = [normalize_repo(x) for x in (allowed or []) if x]
    if not allow:
        raise GitHubError("尚未选择可读取的代码库，请先在连接器里勾选")

    patched = dict(args or {})
    for key in ("query", "q"):
        raw = patched.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        kept: list[str] = []
        for token in raw.split():
            low = token.lower()
            if low.startswith("repo:"):
                # 模型自带的 repo: 限定：在白名单内才保留，否则整段丢掉（不报错，
                # 因为下面无条件补齐了全部白名单仓库，搜索仍然有效且不越界）
                if normalize_repo(token[len("repo:"):]) in allow:
                    kept.append(token)
                continue
            if low.startswith("org:") or low.startswith("user:"):
                # org:/user: 会把搜索面放大到整个组织，与白名单语义冲突，一律剔除
                continue
            kept.append(token)
        if not any(t.lower().startswith("repo:") for t in kept):
            kept.extend(f"repo:{name}" for name in allow)
        patched[key] = " ".join(kept)
    return patched
