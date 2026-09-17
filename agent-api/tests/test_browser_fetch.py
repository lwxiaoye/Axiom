"""browser_fetch 定向测试（2026-07-27）。

覆盖的都是真机踩出来的坑，不是想象出来的分支：
- browser_evaluate 的返回值是 **JSON 序列化后**的（字符串带引号、换行是字面 \\n），
  不反序列化就把引号和转义符一起塞给摘要模型；空页面返回 `""`，不反序列化会被
  误判成「有正文」从而不触发 snapshot 回退（知乎登录墙就是这样漏过去的）。
- browser_navigate 只回 ~324 字节元信息 + 一个**指向文件的**快照链接，不含页面内容。
- 站点把我们拦掉时页面停在 about:blank、snapshot 只有脚手架没有内容，
  必须判成「取不到正文」而不是返回一坨废内容让模型据此作答。
- agent-browser 会间歇性对 200 响应返回空 body，服务端重启后所有会话回 404；
  两者都要重握手重试一次，而不是告诉用户「这个网页是空的」。
"""
import json

import pytest

from app.core.config import settings
from app.services.chat.tools import browser as browser_tools
from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.browser import (
    _McpSession,
    _RetryableMcpError,
    _evaluate_value,
    _has_real_content,
    _normalize_target,
    build_browser_tools,
)


# ---------- 注册开关 ----------

def test_settings_declares_browser_defaults():
    """工具构建会直接读取浏览器配置；Settings 必须声明默认值，缺失会让后台 Run 崩掉。"""
    assert isinstance(settings.BROWSER_SERVICE_URL, str)
    assert settings.BROWSER_FETCH_TIMEOUT_S > 0
    assert settings.BROWSER_FETCH_MAX_CONCURRENCY >= 1
    assert settings.BROWSER_FETCH_MAX_PER_USER >= 1
    assert settings.BROWSER_FETCH_MAX_PAGE_CHARS >= 2000
    assert settings.BROWSER_FETCH_CACHE_TTL_S >= 0
    assert settings.BROWSER_LIVE_MAX_SESSIONS >= 0
    assert settings.BROWSER_LIVE_IDLE_TTL_S > 0
    assert settings.BROWSER_LIVE_MAX_LIFE_S > 0
    assert settings.BROWSER_LIVE_SWEEP_INTERVAL_S >= 0
    assert isinstance(settings.BROWSER_FETCH_MODEL, str)


def test_tool_absent_when_service_unconfigured(monkeypatch):
    """BROWSER_SERVICE_URL 未配置 = 功能整体关闭，工具不出现在清单里。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "", raising=False)
    assert build_browser_tools() == []


def test_tool_present_and_not_parallel_safe(monkeypatch):
    """每次调用都在浏览器服务上占一个 context（重页面约 400MiB），不能进只读并发集合。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 0, raising=False)
    tools = build_browser_tools()
    assert [t.name for t in tools] == ["browser_fetch"]
    tool = tools[0]
    assert tool.readonly is True          # 无平台副作用，网关异常可降级直连
    assert tool.parallel_safe is False    # 但不并发
    assert set(tool.parameters["required"]) == {"url", "prompt"}


def test_live_tools_appear_only_when_enabled(monkeypatch):
    """有状态浏览是独立开关：关掉时连工具都不该出现（模型看不见就不会想用）。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)

    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 0, raising=False)
    assert [t.name for t in build_browser_tools()] == ["browser_fetch"]

    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    names = [t.name for t in build_browser_tools(run_id="r1")]
    assert names == ["browser_fetch", "browser_open", "browser_act", "browser_close"]


def test_live_tools_are_never_readonly(monkeypatch):
    """点击可能对外产生副作用（提交/发送/下单）：网关异常时绝不允许降级直连重试。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    live = [t for t in build_browser_tools(run_id="r1") if t.name != "browser_fetch"]
    assert live and all(t.readonly is False for t in live)
    assert all(t.parallel_safe is False for t in live)


def test_upload_and_dialog_are_not_offered(monkeypatch):
    """文件上传是外泄通道、对话框接受可能等同确认删除：连选项都不给模型看见。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    act = next(t for t in build_browser_tools(run_id="r1") if t.name == "browser_act")
    actions = set(act.parameters["properties"]["action"]["enum"])
    assert "upload" not in actions and "dialog" not in actions
    assert {"click", "type", "submit", "select", "press", "hover", "back"} == actions


# ---------- 目标 URL 归一化与拦截 ----------

@pytest.mark.asyncio
async def test_bare_domain_gets_https(monkeypatch):
    monkeypatch.setattr(browser_tools, "_url_allowed", None, raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)
    assert await _normalize_target("example.com") == "https://example.com"


@pytest.mark.asyncio
async def test_http_upgraded_to_https(monkeypatch):
    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)
    assert (await _normalize_target("http://example.com/a")).startswith("https://")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "ftp://x/y", "file:///etc/passwd"])
async def test_non_http_scheme_and_empty_rejected(bad):
    with pytest.raises(ToolSoftError):
        await _normalize_target(bad)


@pytest.mark.asyncio
async def test_internal_address_rejected(monkeypatch):
    """内网/环回/云元数据一律拒。规则复用 image_fetch 的那份，这里只验拒绝会被翻译成软失败。"""
    async def _deny(_url):
        return "主机不可达或位于内网（已拦截）"

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _deny)
    with pytest.raises(ToolSoftError) as excinfo:
        await _normalize_target("http://127.0.0.1:9080/")
    assert "内网" in str(excinfo.value)


# ---------- browser_evaluate 返回值反序列化 ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ('### Result\n"a\\nb"\n### Ran Playwright code\n```js\nx\n```', "a\nb"),
        ('### Result\n""\n### Ran Playwright code', ""),          # 空页面：必须变成真空串
        ("### Result\n123\n### Ran Playwright code", "123"),      # 非字符串原样
        ('### Result\n"含中文\\n第二行"', "含中文\n第二行"),
    ],
)
def test_evaluate_value_unquotes(raw, expected):
    assert _evaluate_value(raw) == expected


def test_evaluate_value_keeps_plain_text_without_header():
    assert _evaluate_value("just text") == "just text"


# ---------- 空内容判定 ----------

@pytest.mark.parametrize(
    "body,expected",
    [
        ("### Page\n- Page URL: about:blank\n### Snapshot\n```yaml\n\n```", False),
        ("- Page URL: https://x\n- Page Title: t", False),
        ("Page URL: https://x\n\nExample Domain\n\nThis domain is for documentation.", True),
        ("", False),
    ],
)
def test_has_real_content(body, expected):
    assert _has_real_content(body) is expected


# ---------- MCP 响应解析：空/坏响应必须是「可重试」而不是「结果为空」 ----------

def test_parse_sse_frame():
    raw = 'event: message\ndata: {"result":{"content":[{"type":"text","text":"hi"}]},"jsonrpc":"2.0","id":1}\n\n'
    parsed = _McpSession._parse(raw, "text/event-stream")
    assert _McpSession.text_of(parsed["result"]) == "hi"


def test_parse_plain_json():
    parsed = _McpSession._parse(json.dumps({"result": {"content": []}}), "application/json")
    assert parsed["result"] == {"content": []}


@pytest.mark.parametrize(
    "raw,content_type",
    [
        ("", "application/json"),                      # 实测的间歇性空 body
        ("   ", "application/json"),
        ("not json at all", "application/json"),
        ("event: message\ndata: {broken\n\n", "text/event-stream"),
        ("event: ping\n\n", "text/event-stream"),       # 只有通知帧、没有结果帧
    ],
)
def test_parse_bad_response_is_retryable(raw, content_type):
    """关键：这些都不能被当成「网页内容为空」，否则把上游抖动谎报成网页的问题。"""
    with pytest.raises(_RetryableMcpError):
        _McpSession._parse(raw, content_type)


# ---------- 重握手自愈 ----------

class _FakeResponse:
    def __init__(self, status_code: int, text: str, content_type: str = "application/json",
                 session_id: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type}
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id


class _FakeClient:
    """按脚本依次返回响应，并记录每次请求的 method 与携带的 session id。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, _url, json=None, headers=None):  # noqa: A002
        self.calls.append((json.get("method"), (headers or {}).get("Mcp-Session-Id")))
        return self.script.pop(0)


def _init_ok(session_id="s1"):
    return _FakeResponse(200, json.dumps({
        "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "Playwright"}},
    }), session_id=session_id)


def _notif_ok():
    return _FakeResponse(202, "", content_type="text/plain")


@pytest.mark.asyncio
async def test_call_recovering_rehandshakes_on_empty_body():
    """间歇性空 body → 重新 initialize 后重试一次即成功（服务刚重启时的实测形态）。"""
    client = _FakeClient([
        _init_ok("s1"), _notif_ok(),
        _FakeResponse(200, ""),                                    # 第一次调用：空 body
        _init_ok("s2"), _notif_ok(),                               # 自动重握手
        _FakeResponse(200, json.dumps({"result": {"content": [{"type": "text", "text": "ok"}]}})),
    ])
    session = _McpSession(client, "http://agent-browser:8089/mcp")
    await session.handshake()
    result = await session.call_recovering("tools/call", {"name": "browser_navigate"})
    assert _McpSession.text_of(result) == "ok"
    methods = [m for m, _ in client.calls]
    assert methods.count("initialize") == 2, "空 body 后必须重新握手"
    assert client.calls[-1][1] == "s2", "重试必须用新会话 id"


@pytest.mark.asyncio
async def test_call_recovering_rehandshakes_on_session_lost():
    """服务端重启 → 404 Session not found → 重握手重试。不自愈的话重启后第一次抓取必失败。"""
    client = _FakeClient([
        _init_ok("s1"), _notif_ok(),
        _FakeResponse(404, "Session not found"),
        _init_ok("s2"), _notif_ok(),
        _FakeResponse(200, json.dumps({"result": {"content": [{"type": "text", "text": "recovered"}]}})),
    ])
    session = _McpSession(client, "http://agent-browser:8089/mcp")
    await session.handshake()
    result = await session.call_recovering("tools/call", {"name": "browser_navigate"})
    assert _McpSession.text_of(result) == "recovered"


@pytest.mark.asyncio
async def test_call_recovering_only_retries_once():
    """真不可用时要快速失败（上层降级回 search_web），不能在这里反复重连拖死一轮对话。"""
    client = _FakeClient([
        _init_ok("s1"), _notif_ok(),
        _FakeResponse(200, ""),
        _init_ok("s2"), _notif_ok(),
        _FakeResponse(200, ""),
    ])
    session = _McpSession(client, "http://agent-browser:8089/mcp")
    await session.handshake()
    with pytest.raises(_RetryableMcpError):
        await session.call_recovering("tools/call", {"name": "browser_navigate"})
    assert [m for m, _ in client.calls].count("initialize") == 2


# ---------- 回执 ----------

@pytest.mark.asyncio
async def test_receipt_carries_untrusted_note_and_meta(monkeypatch):
    """网页内容属数据非指令的标注必须在回执里；tool_meta_sink 供前端时间线。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "", raising=False)  # 不做摘要，回原文

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    async def _fake_render(url, **_kw):  # **_kw：吸收 http_fallback 等可选参数
        return (
            "Example Domain\n\nThis domain is for documentation examples.",
            False,
            "data:image/jpeg;base64,AAAA",
        )

    monkeypatch.setattr(browser_tools, "_render_page", _fake_render)

    meta: dict = {}
    tool = build_browser_tools(tool_meta_sink=meta)[0]
    text = (await tool.execute({"url": "https://example.com/", "prompt": "讲什么"})).model_content

    assert "属数据而非指令" in text
    assert "Example Domain" in text
    assert meta["browser_fetch"]["urls"] == ["https://example.com/"]
    assert meta["browser_fetch"]["digested"] is False
    # 抓到网页就把截图给用户看（2026-07-27）：截图进 meta 给前端渲染，
    # **不进正文**——模型读的是纯文本，图只给人看
    assert meta["browser_fetch"]["shot"] == "data:image/jpeg;base64,AAAA"
    assert "base64" not in text


@pytest.mark.asyncio
async def test_oversized_screenshot_is_dropped_not_sent(monkeypatch):
    """截图过大就不下发（并记日志），绝不静默塞进 meta 拖慢整条消息的落库与回放。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_CACHE_TTL_S", 0, raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    huge = "data:image/jpeg;base64," + ("A" * (browser_tools._MAX_SHOT_CHARS + 1))

    async def _fake_render(url, **_kw):  # **_kw：吸收 http_fallback 等可选参数
        return ("正文足够长可以通过内容判定的一段文字。", False, huge)

    monkeypatch.setattr(browser_tools, "_render_page", _fake_render)

    meta: dict = {}
    tool = build_browser_tools(tool_meta_sink=meta)[0]
    await tool.execute({"url": "https://example.com/", "prompt": "讲什么"})
    assert "shot" not in meta["browser_fetch"]


@pytest.mark.asyncio
async def test_screenshot_survives_cache_hit(monkeypatch):
    """命中缓存时也要有图：同一个 URL 抓第二次反而没图，会被当成 bug。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_CACHE_TTL_S", 900, raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    calls = {"n": 0}

    async def _fake_render(url, **_kw):  # **_kw：吸收 http_fallback 等可选参数
        calls["n"] += 1
        return ("这是一段足够长的正文内容，用于通过 _has_real_content 的判定。", False, "data:image/jpeg;base64,BBBB")

    monkeypatch.setattr(browser_tools, "_render_page", _fake_render)
    browser_tools._PAGE_CACHE.clear()

    meta: dict = {}
    tool = build_browser_tools(tool_meta_sink=meta)[0]
    await tool.execute({"url": "https://example.com/cached", "prompt": "讲什么"})
    await tool.execute({"url": "https://example.com/cached", "prompt": "讲什么"})

    assert calls["n"] == 1, "第二次应命中缓存"
    assert meta["browser_fetch"]["cached"] is True
    assert meta["browser_fetch"]["shot"] == "data:image/jpeg;base64,BBBB"


@pytest.mark.asyncio
async def test_missing_prompt_is_soft_error(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)
    tool = build_browser_tools()[0]
    with pytest.raises(ToolSoftError):
        await tool.execute({"url": "https://example.com/"})


@pytest.mark.asyncio
async def test_service_failure_degrades_to_soft_error(monkeypatch):
    """浏览器服务不可用不能炸成硬错误——要软失败并引导模型改用 search_web。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_CACHE_TTL_S", 0, raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    async def _boom(_url):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(browser_tools, "_render_page", _boom)
    tool = build_browser_tools()[0]
    with pytest.raises(ToolSoftError) as excinfo:
        await tool.execute({"url": "https://example.com/", "prompt": "x"})
    assert "search_web" in str(excinfo.value)


def _stub_session_class(impl):
    """把一个替身实例包成 _McpSession 的形状。

    直接 `monkeypatch.setattr(browser_tools, "_McpSession", lambda ...)` 会把
    text_of / image_of 两个静态方法弄丢——_render_page 是按 `_McpSession.text_of(...)`
    调的（类名而不是实例），替身必须把它们原样带上。
    """

    class _Stub(impl):
        def __init__(self, *_a, **_kw):
            super().__init__()

        text_of = staticmethod(_McpSession.text_of)
        image_of = staticmethod(_McpSession.image_of)

    return _Stub

# ---------- 上游故障 vs 目标站点拒绝（2026-07-28）----------
# 2026-07-27 抓 example.com 拿到的原文是
#   `Error: browserBackend.callTool: net::ERR_ABORTED; maybe frame was detached?`
# 而工具回的是「可能是站点要求登录、有反爬拦截」——example.com 既没有登录墙也没有反爬。
# 模型据此对用户说「这个网站有反爬」，把一个**可修的服务故障**永久归因给了目标站点。

@pytest.mark.parametrize("head", [
    "### Error\nError: browserBackend.callTool: net::ERR_ABORTED; maybe frame was detached?",
    "Error: Target page, context or browser has been closed",
    "Protocol error (Page.navigate): Session closed.",
    "Error: Execution context was destroyed",
])
def test_upstream_fault_is_not_blamed_on_the_site(head):
    msg = str(browser_tools._nav_failure(head, url="https://example.com/"))
    assert "浏览器服务" in msg and "不是目标网站的问题" in msg
    assert "反爬" not in msg.split("不要对用户说")[0], "上游故障的正文里不许出现反爬的推断"
    assert "要求登录" not in msg.split("不要对用户说")[0]


@pytest.mark.parametrize("head", [
    "Error: net::ERR_NAME_NOT_RESOLVED at https://nope.invalid/",
    "Error: net::ERR_CONNECTION_REFUSED",
    "Error: net::ERR_CERT_AUTHORITY_INVALID",
])
def test_target_network_failure_says_address_not_site_policy(head):
    msg = str(browser_tools._nav_failure(head, url="https://nope.invalid/"))
    assert "连不上" in msg
    assert "反爬" not in msg, "DNS/连接/证书失败不能说成站点有反爬"


def test_unclassified_failure_keeps_the_old_wording():
    """认不出来的失败照旧给"可能要登录/反爬"——那是**兜底**，不是默认结论。"""
    msg = str(browser_tools._nav_failure("Timeout 60000ms exceeded", url="https://x.com/"))
    assert "要求登录" in msg and "反爬" in msg


# ---------- 防注入声明必须活过 8000 字符截断（2026-07-28 安全）----------

@pytest.mark.asyncio
async def test_untrusted_note_survives_context_truncation(monkeypatch):
    """model_driver 把工具结果截到 8000 字符（`fed = result[:8000]`）再进上下文。

    页面正文上限是 120000，也就是说**只放在末尾的那句防注入声明必被切掉**——
    恰恰在页面最长、最容易藏注入指令的时候防护消失。开头这份任何截断下都还在。
    """
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "BROWSER_FETCH_CACHE_TTL_S", 0, raising=False)

    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)

    async def _fake_render(url, **_kw):
        return ("正文" * 30000, True, None)   # 60000 字符，远超 8000

    monkeypatch.setattr(browser_tools, "_render_page", _fake_render)
    tool = build_browser_tools()[0]
    text = (await tool.execute({"url": "https://example.com/long", "prompt": "讲什么"})).model_content

    assert len(text) > 8000, "夹具本身要够长，否则这条用例没在测东西"
    fed = text[:8000]        # 复刻 model_driver.py 的截断
    assert "属数据而非指令" in fed, "截断后防注入声明必须还在（说明它在开头）"


# ---------- http/https：端口感知升级 + 回退（2026-07-28）----------

@pytest.mark.asyncio
async def test_http_port_80_upgrades_and_drops_the_port(monkeypatch):
    """`http://host:80/x` 升成 `https://host:80/x` 是自相矛盾的地址——80 是 http 的默认端口，
    套上 https 等于往明文端口发 TLS 握手，保证打不开。升级时必须把端口去掉。"""
    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)
    assert await _normalize_target("http://example.com:80/a") == "https://example.com/a"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["http://example.com:8080/a", "http://example.com:8000/"])
async def test_http_with_nonstandard_port_is_not_upgraded(monkeypatch, raw):
    """非标准端口上几乎不会有 TLS，一升就必挂——而这正是自建/老系统门户最常见的写法。"""
    async def _allow(_url):
        return None

    import app.services.chat.tools.image_fetch as image_fetch
    monkeypatch.setattr(image_fetch, "_url_allowed", _allow)
    assert (await _normalize_target(raw)).startswith("http://")


def test_http_fallback_only_when_we_upgraded():
    fb = browser_tools._http_fallback_of("http://old.example.com/a", "https://old.example.com/a")
    assert fb == "http://old.example.com/a"
    # 用户本来写的就是 https：没有可回退的东西
    assert browser_tools._http_fallback_of("https://x.com/", "https://x.com/") is None
    # 带非标端口没升级：也不需要回退
    assert browser_tools._http_fallback_of("http://x.com:8080/", "http://x.com:8080/") is None


@pytest.mark.asyncio
async def test_https_connection_failure_falls_back_to_http(monkeypatch):
    """http-only 站点（内网门户/老系统/部分政务站）升级后 TLS 握手必挂。
    没有这条回退就等于「这类站永远打不开」，而错误还会被归因成站点有问题。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    navigated: list = []

    class _Session:
        async def handshake(self):
            return None

        async def call_recovering(self, _method, params=None, *, stateful=False):
            name = (params or {}).get("name")
            if name == "browser_navigate":
                url = (params or {}).get("arguments", {}).get("url")
                navigated.append(url)
                if url.startswith("https://"):
                    return {"isError": True, "content": [
                        {"type": "text", "text": "Error: net::ERR_CONNECTION_REFUSED"}]}
                return {"content": [{"type": "text", "text": f"- Page URL: {url}\n- Page Title: 老门户"}]}
            if name == "browser_evaluate":
                return {"content": [{"type": "text", "text": '### Result\n"这是一段足够长的正文内容，用于通过 _has_real_content 的判定。"'}]}
            return {"content": []}

        async def close_all_contexts(self, *, why=""):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(browser_tools.httpx, "AsyncClient", lambda **_kw: _Client())
    monkeypatch.setattr(browser_tools, "_McpSession", _stub_session_class(_Session))

    body, _truncated, _shot = await browser_tools._render_page(
        "https://old.example.com/", http_fallback="http://old.example.com/")

    assert navigated == ["https://old.example.com/", "http://old.example.com/"]
    assert "正文" in body


@pytest.mark.asyncio
async def test_session_lost_midway_is_reported_as_upstream_not_antibot(monkeypatch):
    """导航成功、取正文时会话没了：重握手拿到的是空 context → about:blank →
    被 _has_real_content 判成「站点要登录/有反爬」。又一次把上游故障赖给目标站点。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)

    class _Session:
        async def handshake(self):
            return None

        async def call_recovering(self, _method, params=None, *, stateful=False):
            name = (params or {}).get("name")
            if name == "browser_navigate":
                return {"content": [{"type": "text", "text": "- Page URL: https://x.com/"}]}
            if stateful:
                raise browser_tools._LiveSessionLost("会话失效：HTTP 404 Session not found")
            raise AssertionError("取正文的调用必须是 stateful（否则会重握手拿空 context）")

        async def close_all_contexts(self, *, why=""):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(browser_tools.httpx, "AsyncClient", lambda **_kw: _Client())
    monkeypatch.setattr(browser_tools, "_McpSession", _stub_session_class(_Session))

    with pytest.raises(ToolSoftError) as excinfo:
        await browser_tools._render_page("https://x.com/")
    msg = str(excinfo.value)
    assert "不是目标网站的问题" in msg
    assert "反爬" not in msg.split("不要对用户说")[0]


# ===== 打不开的网页不出图（2026-07-28 用户拍板）=====
# 一度做成"失败也把图交出去"，用户否掉了：一张登录墙/报错页摊在执行流里，读者得先看懂
# "这张是失败的"，比一行文字说明更费解。所以截图只在正文确实拿到之后才截。

@pytest.mark.asyncio
async def test_no_screenshot_when_content_is_unusable(monkeypatch):
    """正文取不到（登录墙）时：抛 ToolSoftError，且**一次截图都不发起**（省一次往返）。"""
    calls: list = []

    # 继承真 _McpSession：text_of / image_of 是它的静态方法，_render_page 直接按类名调用
    class _FakeSession(browser_tools._McpSession):
        def __init__(self, *a, **k):  # noqa: D107 —— 不建真连接
            pass

        async def handshake(self):
            return None

        async def call_recovering(self, _method, params, stateful=False):
            name = params.get("name")
            calls.append(name)
            if name == "browser_navigate":
                return {"content": [{"type": "text", "text": "### Page URL: https://x/"}]}
            if name == "browser_evaluate":
                return {"content": [{"type": "text", "text": ""}]}      # 取不到正文
            if name == "browser_snapshot":
                return {"content": [{"type": "text", "text": "### Snapshot\n```yaml\n```"}]}
            if name == "browser_take_screenshot":
                return {"content": [{"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}]}
            return {"content": []}

        async def close_all_contexts(self, why=""):
            return None

    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(browser_tools, "_McpSession", _FakeSession)

    with pytest.raises(browser_tools.ToolSoftError):
        await browser_tools._render_page("https://x/")

    assert "browser_take_screenshot" not in calls, "打不开的网页不该截图"


@pytest.mark.asyncio
async def test_screenshot_taken_when_content_is_real(monkeypatch):
    """正文正常时：截图照常发起并随返回值带出。"""

    class _FakeSession(browser_tools._McpSession):
        def __init__(self, *a, **k):  # noqa: D107
            pass

        async def handshake(self):
            return None

        async def call_recovering(self, _method, params, stateful=False):
            name = params.get("name")
            if name == "browser_navigate":
                return {"content": [{"type": "text", "text": "### Page URL: https://x/"}]}
            if name == "browser_evaluate":
                return {"content": [{"type": "text", "text": json.dumps("这是一段足够长的正文" * 12)}]}
            if name == "browser_take_screenshot":
                return {"content": [{"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}]}
            return {"content": []}

        async def close_all_contexts(self, why=""):
            return None

    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(browser_tools, "_McpSession", _FakeSession)

    _text, _truncated, shot = await browser_tools._render_page("https://x/")
    assert str(shot or "").startswith("data:image/")


def test_attach_shot_drops_oversized_image():
    """超限的大图不下发：它会随 tool.completed 落库并在历史回放里重放。"""
    meta: dict = {}
    huge = "data:image/jpeg;base64," + "A" * (browser_tools._MAX_SHOT_CHARS + 1)
    browser_tools._attach_shot(meta, huge, "https://x/")
    assert "shot" not in meta

    browser_tools._attach_shot(meta, "data:image/jpeg;base64,QUJD", "https://x/")
    assert meta["shot"].startswith("data:image/")
