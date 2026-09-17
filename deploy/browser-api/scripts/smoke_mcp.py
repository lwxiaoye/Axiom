#!/usr/bin/env python3
"""agent-browser 冒烟（MCP / Streamable HTTP）—— 纯标准库，无第三方依赖。

验五件事，任一失败就非零退出：
  1. MCP 端点在，initialize 握手能过（拿到 protocolVersion + serverInfo）
  2. tools/list 里有 browser_navigate / browser_snapshot
  3. 真的能抓一个页面（tools/call browser_navigate）
  4. 快照里有 [ref=xxx] 标记 —— 这条最重要：整套方案的价值就在于返回「可访问性元素树带 ref」
     而不是整页 HTML，模型靠 ref 点击。没有 ref 就等于退化成纯文本抓取，必须验。
  5. 能截图（tools/call browser_take_screenshot，返回 image 内容块）

用法:
    ./scripts/smoke_mcp.py                        # 默认 http://127.0.0.1:8089
    ./scripts/smoke_mcp.py http://127.0.0.1:8089
    TARGET_URL=https://example.com ./scripts/smoke_mcp.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BROWSER_BASE_URL", "http://127.0.0.1:8089")).rstrip("/")
# 抓取目标。国内服务器出网情况不一，所以给一串候选依次试，任一成功即算通过：
# 先试轻量的 example.com，再退到国内一定通的站。指定 TARGET_URL 则只试那一个。
TARGETS = ([os.environ["TARGET_URL"]] if os.environ.get("TARGET_URL")
           else ["https://example.com/", "https://cn.bing.com/", "https://www.baidu.com/"])
# 不同版本 MCP 服务器挂载路径不一样：新版是 /mcp(Streamable HTTP)，老版只有 /sse。
# 依次试，第一个 initialize 成功的就用它。
CANDIDATE_PATHS = [p for p in (os.environ.get("MCP_PATH"), "/mcp", "/", "/sse") if p]
PROTOCOL_VERSION = os.environ.get("MCP_PROTOCOL_VERSION", "2025-06-18")

session_id: str | None = None
endpoint: str | None = None
_next_id = 0


def _rpc_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def _parse_body(raw: bytes, content_type: str) -> dict:
    """Streamable HTTP 的响应可能是 application/json，也可能是 SSE 流（data: {...}）。"""
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk:
                    continue
                obj = json.loads(chunk)
                # 只关心带 result/error 的响应帧，跳过 ping 之类的通知
                if "result" in obj or "error" in obj:
                    return obj
        raise RuntimeError(f"SSE 响应里没有 result/error 帧：{text[:300]}")
    return json.loads(text) if text else {}


def call(method: str, params: dict | None = None, *, notify: bool = False, path: str | None = None) -> dict:
    global session_id
    url = (BASE + (path if path is not None else endpoint or "/mcp"))
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if not notify:
        payload["id"] = _rpc_id()
    if params is not None:
        payload["params"] = params

    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    # 必须同时接受两种类型，否则部分实现直接 406
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("MCP-Protocol-Version", PROTOCOL_VERSION)
    if session_id:
        req.add_header("Mcp-Session-Id", session_id)

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            got_sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
            if got_sid:
                session_id = got_sid
            body = _parse_body(resp.read(), resp.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code} {e.reason} @ {url}\n      {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 {url}：{e.reason}") from None

    if notify:
        return {}
    if "error" in body:
        raise RuntimeError(f"{method} 返回错误：{json.dumps(body['error'], ensure_ascii=False)[:400]}")
    return body.get("result", {})


def text_of(result: dict) -> str:
    """把 tools/call 结果里的文本内容块拼起来。"""
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def main() -> int:
    global endpoint

    print(f"服务：{BASE}   抓取候选：{', '.join(TARGETS)}")

    # ── 1. 探路径 + initialize ────────────────────────────────────────────
    init_result = None
    errors = []
    for path in CANDIDATE_PATHS:
        try:
            init_result = call(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "agent-browser-smoke", "version": "1.0"},
                },
                path=path,
            )
            endpoint = path
            break
        except RuntimeError as e:
            errors.append(f"{path}: {e}")

    if init_result is None:
        print("[1/5] initialize  FAIL —— 所有候选路径都没握上手：")
        for line in errors:
            print(f"      {line}")
        return 1

    srv = init_result.get("serverInfo", {})
    print(f"[1/5] initialize  OK   端点={endpoint}  服务端={srv.get('name')} {srv.get('version')}  "
          f"协议={init_result.get('protocolVersion')}  session={'有' if session_id else '无'}")
    call("notifications/initialized", {}, notify=True)

    # ── 2. tools/list ────────────────────────────────────────────────────
    tools = [t["name"] for t in call("tools/list").get("tools", [])]
    need = {"browser_navigate", "browser_snapshot"}
    missing = need - set(tools)
    if missing:
        print(f"[2/5] tools/list  FAIL —— 缺少 {sorted(missing)}；实际有 {len(tools)} 个：{tools}")
        return 1
    print(f"[2/5] tools/list  OK   {len(tools)} 个工具，含 {sorted(need)}")

    # ── 3. 真抓一个页面（多个候选依次试，适配国内出网差异）──────────────────
    body = ""
    hit = None
    nav_errs = []
    for url in TARGETS:
        nav = call("tools/call", {"name": "browser_navigate", "arguments": {"url": url}})
        body = text_of(nav)
        if not nav.get("isError"):
            hit = url
            break
        nav_errs.append(f"{url}: {body.strip().splitlines()[-1][:120] if body.strip() else '(无错误正文)'}")
    if hit is None:
        print("[3/5] navigate    FAIL —— 所有候选目标都抓不到（服务器可能出不了网，或需要代理）：")
        for line in nav_errs:
            print(f"      {line}")
        return 1
    print(f"[3/5] navigate    OK   {hit}  返回 {len(body)} 字符")
    head = "\n".join(f"      | {ln}" for ln in body.splitlines()[:5])
    if head:
        print(head)

    # ── 4. 快照里必须有 ref ───────────────────────────────────────────────
    # 模型是靠 ref 点击的（browser_click(ref=...)）。没有 ref 就退化成纯文本抓取，
    # 整套方案相对于 search_web 的优势就没了，所以这条是硬断言。
    snap = call("tools/call", {"name": "browser_snapshot", "arguments": {}})
    snap_text = text_of(snap)
    if snap.get("isError") or "[ref=" not in snap_text:
        print(f"[4/5] snapshot    FAIL —— 快照里没有 [ref=xxx] 标记，模型无法按 ref 点击。")
        print(f"      前 400 字符：{snap_text[:400]}")
        return 1
    refs = snap_text.count("[ref=")
    print(f"[4/5] snapshot    OK   {len(snap_text)} 字符，{refs} 个可交互元素带 ref")
    for ln in snap_text.splitlines():
        if "[ref=" in ln:
            print(f"      | {ln.strip()[:110]}")
            break

    # ── 5. 截图 ──────────────────────────────────────────────────────────
    shot = call("tools/call", {"name": "browser_take_screenshot", "arguments": {}})
    images = [c for c in shot.get("content", []) if c.get("type") == "image"]
    if shot.get("isError") or not images:
        print(f"[5/5] screenshot  FAIL —— 没拿到 image 内容块：{text_of(shot)[:300]}")
        return 1
    b64 = images[0].get("data", "")
    print(f"[5/5] screenshot  OK   {images[0].get('mimeType')}  base64 {len(b64)} 字符 "
          f"(≈{len(b64) * 3 // 4 // 1024} KB)")

    # 干净收尾：关掉浏览器，别把 context 留给 TTL
    try:
        call("tools/call", {"name": "browser_close", "arguments": {}})
    except RuntimeError:
        pass

    print("\nPASS —— MCP 握手、工具清单、真实渲染、ref 快照、截图全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as exc:
        print(f"FAIL —— {exc}")
        sys.exit(1)
