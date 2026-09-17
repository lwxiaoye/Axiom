"""外部应用连接器 REST。

全部端点按 `UserContext.user_id` 归属：连接器是**个人凭据**，不随租户共享。
业务错误由 connector_service.ConnectorError 携带 status_code 统一抛出。
"""
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.auth import UserContext, current_user
from app.core.config import settings
from app.services.connectors import connector_service
from app.services.connectors.connector_service import ConnectorError

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _wrap(exc: ConnectorError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("")
async def list_connectors(user: UserContext = Depends(current_user)):
    """连接器清单 + 当前用户的连接状态（不含任何凭据）。"""
    return await connector_service.list_connectors(user.user_id)


@router.get("/{provider_id}")
async def get_connector(provider_id: str, user: UserContext = Depends(current_user)):
    try:
        return await connector_service.get_connector(user.user_id, provider_id)
    except ConnectorError as exc:
        raise _wrap(exc)


def _redirect_uri(request: Request, provider_id: str) -> str:
    """回调地址：显式配置优先，否则按反向代理头推导。

    必须与「换令牌」那一步传的完全一致（GitHub 逐字比对），所以两处都从这里取。
    """
    configured = (settings.CONNECTOR_OAUTH_REDIRECT_URI or "").strip()
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}/agent-api/connectors/{provider_id}/oauth/callback"


@router.post("/{provider_id}/oauth/start")
async def start_oauth(
    provider_id: str,
    request: Request,
    return_to: str = Body("", embed=True),
    user: UserContext = Depends(current_user),
):
    """发起登录授权：返回 GitHub 授权页地址，前端**在当前页直接跳过去**。

    return_to = 授权完成后回到的站内地址（前端传当前路由），存进 state。
    """
    try:
        return await connector_service.start_oauth(
            user.user_id, provider_id, _redirect_uri(request, provider_id), return_to)
    except ConnectorError as exc:
        raise _wrap(exc)


# 回调**不能加鉴权依赖**：它是 GitHub 把用户浏览器重定向过来的请求，不带 X-Access-Token。
# 身份与防重放全靠一次性的 state（见 connector_service._oauth_states）。
@router.post("/{provider_id}/install/start")
async def start_install(
    provider_id: str,
    return_to: str = Body("", embed=True),
    user: UserContext = Depends(current_user),
):
    """第二段授权：返回 GitHub App 安装页地址，用户在 GitHub 上勾选授权哪些仓库。

    这一段才真正决定「能读什么」——第一段只拿到身份。
    """
    try:
        return await connector_service.start_install(user.user_id, provider_id, return_to)
    except ConnectorError as exc:
        raise _wrap(exc)


# 安装回调同样**无鉴权**（GitHub 重定向浏览器过来），身份由一次性 state 反查。
@router.get("/{provider_id}/install/callback", response_class=HTMLResponse)
async def install_callback(
    provider_id: str,
    state: str = "",
    installation_id: str = "",
    setup_action: str = "",
):
    """GitHub App 安装完成回调（Setup URL）。"""
    if setup_action == "request":
        # 用户没有管理员权限，只是**申请**安装，需要组织管理员批准
        return HTMLResponse(_callback_page(
            "已提交安装申请", "需要组织管理员批准后才能读取仓库。", ok=False))
    if not state or not installation_id:
        return HTMLResponse(_callback_page(
            "安装未完成", "回调参数不完整，请回到对话里重新点击添加仓库。", ok=False))
    try:
        result = await connector_service.complete_install(provider_id, state, installation_id)
    except ConnectorError as exc:
        return HTMLResponse(_callback_page("安装失败", str(exc), ok=False))
    count = len((result.get("connector") or {}).get("resources") or [])
    return _back_to_app(result.get("returnTo"), provider_id,
                        status="installed", detail=str(count))


@router.get("/{provider_id}/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    provider_id: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    # App 注册时勾了「安装过程中请求用户授权」：从安装页过来的那一趟会把它一起带回来，
    # 于是两段授权在一次跳转里就都完成了。
    installation_id: str = "",
):
    """GitHub 授权回调：换令牌落库，然后 303 跳回用户原来那一页。

    失败时才停在落地页上（那时 state 多半已失效，不知道该跳回哪）——文案要说人话，
    不能甩 JSON，用户是真的会看到这一页的。
    """
    if error:
        # 用户点了 Cancel 走这里，不是故障，别用报错口吻
        friendly = "你取消了本次授权。" if error == "access_denied" else (error_description or error)
        return HTMLResponse(_callback_page("授权未完成", friendly, ok=False))
    if not code or not state:
        return HTMLResponse(_callback_page("授权未完成", "回调参数不完整，请重新点击连接。", ok=False))
    try:
        result = await connector_service.complete_oauth(
            provider_id, code, state, installation_id=installation_id)
    except ConnectorError as exc:
        return HTMLResponse(_callback_page("连接失败", str(exc), ok=False))
    connector = result.get("connector") or {}
    if connector.get("reposAuthorized"):
        count = len(connector.get("resources") or [])
        return _back_to_app(result.get("returnTo"), provider_id,
                            status="installed", detail=str(count))
    # 只完成第一段：回到应用后由前端提示「还要再选仓库」，别停在一个中间页上
    return _back_to_app(result.get("returnTo"), provider_id, status="connected")


def _back_to_app(return_to: Optional[str], provider_id: str, *,
                 status: str, detail: str = "") -> RedirectResponse:
    """授权/安装完成后跳回用户原来那一页，把结果放查询串里交给前端提示。

    为什么不停在一个「已完成，请关闭窗口」的页面：授权是**原页跳转**发起的，
    停在那儿等于把用户扔在一个死胡同里，还得他自己按后退。
    return_to 已在 service 层过白名单（只接受站内绝对路径），这里不再拼接外部地址。
    """
    sep = "&" if "?" in (return_to or "") else "?"
    query = f"connector={quote(provider_id)}&connector_status={quote(status)}"
    if detail:
        query += f"&connector_detail={quote(detail)}"
    return RedirectResponse(f"{return_to or '/center/chat'}{sep}{query}", status_code=303)


def _callback_page(title: str, detail: str, *, ok: bool) -> str:
    """**只在无法回跳时**使用的落地页（state 失效、参数残缺、用户取消）。

    授权改成原页跳转后，成功路径一律 303 回到用户原来那一页（见 _back_to_app），
    不再有中间页。但 state 已经失效时我们连「他从哪来」都不知道，只能停在这里
    把原因说清楚——所以这个页面留着，且没有自动跳转/关窗。
    """
    import html as _html

    accent = "#111827" if ok else "#a1121f"
    auto_close = ""
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)}</title>"
        "<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
        "background:#fafafd;color:#111827}"
        ".card{max-width:420px;padding:32px;text-align:center}"
        f".t{{font-size:18px;font-weight:600;margin-bottom:10px;color:{accent}}}"
        ".d{font-size:13px;line-height:1.7;color:#6b7280}</style></head>"
        f"<body><div class=\"card\"><div class=\"t\">{_html.escape(title)}</div>"
        f"<div class=\"d\">{_html.escape(detail)}</div></div>{auto_close}</body></html>"
    )


@router.post("/{provider_id}/token")
async def connect_with_token(
    provider_id: str,
    token: str = Body(..., embed=True),
    # 账号型授权（QQ 邮箱 IMAP：邮箱地址 + 授权码）用得上。**必须显式声明**：
    # FastAPI 对请求体里没声明的键是**静默丢弃**的，前端一直在发 account，
    # 这里不声明就永远收不到，而故障要到三层之外才以 AttributeError 的形态爆出来
    # （adapter 走进 fetch_account 分支）——排查时完全看不出是参数掉了。
    account: str = Body("", embed=True),
    user: UserContext = Depends(current_user),
):
    """用访问令牌直连（没配 OAuth App 时的通路，也是最小权限方案：
    fine-grained PAT 可以只给指定仓库的只读权限）。"""
    try:
        return await connector_service.connect_with_token(
            user.user_id, provider_id, token, account=account,
        )
    except ConnectorError as exc:
        raise _wrap(exc)


@router.patch("/{provider_id}/accounts/selected")
async def set_account_selected(
    provider_id: str,
    account: str = Body(..., embed=True),
    selected: bool = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """勾选/取消某一个账户（多账户，2026-07-29）。

    与 `/enabled` 分开：那个是连接器总开关（这一轮用不用这个应用），
    这个是"哪些账户算在读取范围内"。合成一个会逼用户为了排除一个账户而关掉整个连接器。
    """
    try:
        return await connector_service.set_account_selected(
            user.user_id, provider_id, account, selected)
    except ConnectorError as exc:
        raise _wrap(exc)


@router.delete("/{provider_id}")
async def disconnect(provider_id: str, account: str = "",
                     user: UserContext = Depends(current_user)):
    try:
        return await connector_service.disconnect(
            user.user_id, provider_id, account=account)
    except ConnectorError as exc:
        raise _wrap(exc)


@router.patch("/{provider_id}/enabled")
async def set_enabled(
    provider_id: str,
    enabled: bool = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    try:
        return await connector_service.set_enabled(user.user_id, provider_id, enabled)
    except ConnectorError as exc:
        raise _wrap(exc)


@router.get("/{provider_id}/resources")
async def list_resources(provider_id: str, user: UserContext = Depends(current_user)):
    """列出账号下可选的资源（GitHub 即代码库）。前端本地搜索过滤，不做服务端分页。"""
    try:
        return await connector_service.list_resources(user.user_id, provider_id)
    except ConnectorError as exc:
        raise _wrap(exc)


@router.put("/{provider_id}/resources")
async def set_resources(
    provider_id: str,
    resources: list = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """保存用户勾选的资源白名单——它同时是运行期的硬校验依据。"""
    try:
        return await connector_service.set_resources(user.user_id, provider_id, resources)
    except ConnectorError as exc:
        raise _wrap(exc)
