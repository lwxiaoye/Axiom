"""工具调用共享原语：Agent 挂载（agent_executor）与画布动态节点（workflow_engine
_run_tool/_run_plugin_module/_run_app_module）共用同一事实源（gap-audit §7.3.7 第 6 条）。

四类原语：系统内置工具 / HTTP 工具集子工具 / MCP 工具集子工具 / 子工作流（含深度上限）。
密钥（headers）只存在服务端 config_json，本模块内消费，不经节点/接口回传。
"""
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_SUB_WORKFLOW_DEPTH = 3
TOOL_RESULT_LIMIT = 8000

# 路径/Header 占位符：{{param}} 或 ${param}，仅允许字母数字下划线
_PARAM_PLACEHOLDER = re.compile(
    r"\{\{\s*([A-Za-z_]\w*)\s*\}\}|\$\{\s*([A-Za-z_]\w*)\s*\}|(?<!\{)\{\s*([A-Za-z_]\w*)\s*\}(?!\})"
)


class ToolInvokeError(Exception):
    pass


@dataclass
class HttpUpload:
    filename: str
    content_type: str
    content: bytes


@dataclass
class HttpToolResponse:
    status_code: int
    headers: dict[str, str]
    body: str
    duration_ms: int
    truncated: bool


def parse_config(config_json: Optional[str]) -> dict:
    try:
        parsed = json.loads(config_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def truncate_result(text: str) -> str:
    """Compatibility shim that now preserves the lossless tool value.

    Result sizing belongs to the Harness ``ToolResultProjector`` after durable persistence.  The
    historical helper remains importable for workflow callers, but must not destroy the only copy
    before that boundary.
    """

    return str(text or "")


async def load_tool_app(app_id: str):
    """按 id 加载工具/应用行；不存在返回 None。"""
    from app.core.database import async_session
    from app.models import WorkflowApp

    async with async_session() as session:
        return await session.get(WorkflowApp, app_id)


async def load_published_definition(app_id: str) -> Optional[str]:
    from sqlalchemy import select

    from app.core.database import async_session
    from app.models import WorkflowDefinition

    async with async_session() as session:
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
        ).scalar_one_or_none()
    return definition.published_json if definition else None


async def load_tool_definition(app_id: str, *, allow_draft: bool = False) -> Optional[str]:
    from sqlalchemy import select

    from app.core.database import async_session
    from app.models import WorkflowDefinition

    async with async_session() as session:
        definition = (
            await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
        ).scalar_one_or_none()
    if not definition:
        return None
    return definition.published_json or (definition.draft_json if allow_draft else None)


async def invoke_builtin_tool(tool_id: str, args: dict, runtime: Optional[dict] = None):
    from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, execute_builtin_tool

    if tool_id not in BUILTIN_TOOL_MAP:
        raise ToolInvokeError(f"系统工具 {tool_id} 不存在")
    return await execute_builtin_tool(tool_id, args or {}, runtime)


def _find_tool_item(config: dict, tool_name: str) -> dict:
    for item in config.get("toolList") or []:
        if isinstance(item, dict) and str(item.get("name")) == tool_name:
            return item
    raise ToolInvokeError(f"工具「{tool_name}」不在工具集清单中")


def _apply_param_placeholders(template: str, values: dict) -> str:
    """把 {{param}} / ${param} 替换为实际值；缺失则保留占位符原样，避免误吞字面量。"""
    if not template or "{" not in template:
        return template

    def _replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        if name in values and values[name] not in (None, ""):
            return str(values[name])
        return match.group(0)

    return _PARAM_PLACEHOLDER.sub(_replace, template)


def _param_rows(item: dict, key: str) -> list[dict]:
    rows = item.get(key)
    return [row for row in rows if isinstance(row, dict) and row.get("key")] if isinstance(rows, list) else []


def _has_location_metadata(item: dict) -> bool:
    return any(isinstance(item.get(key), list) for key in ("pathParams", "queryParams", "headerParams", "bodyParams"))


def _pick_values(rows: list[dict], values: dict) -> dict:
    return {str(row["key"]): values.get(str(row["key"])) for row in rows if str(row["key"]) in values}


def _validate_required(rows: list[dict], values: dict, uploaded_files: dict[str, HttpUpload]) -> None:
    for row in rows:
        key = str(row["key"])
        present = key in values and values[key] not in (None, "")
        if row.get("type") == "file":
            present = key in uploaded_files or present
        if row.get("required") and not present:
            raise ToolInvokeError(f"缺少必填参数：{key}")


def _safe_filename(value: str) -> str:
    normalized = str(value or "file").replace("\\", "/")
    name = PurePath(normalized).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name or "file"


async def _resolve_file_reference(value) -> HttpUpload:
    if isinstance(value, dict):
        url = str(value.get("url") or value.get("fileUrl") or "")
        filename = str(value.get("name") or value.get("fileName") or "file")
    else:
        url = str(value or "")
        filename = PurePath(urlparse(url).path).name or "file"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolInvokeError("文件引用必须是平台文件 URL，不能使用本地文件路径")
    from app.services.gateway.mcp_client import PinnedPublicTransport, assert_public_http_url

    assert_public_http_url(url)
    async with httpx.AsyncClient(timeout=15, transport=PinnedPublicTransport()) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise ToolInvokeError(f"读取文件引用失败：HTTP {response.status_code}")
    if len(response.content) > 20 * 1024 * 1024:
        raise ToolInvokeError("文件超过 20MB 限制")
    return HttpUpload(
        filename=_safe_filename(filename),
        content_type=response.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
        content=response.content,
    )


def _form_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def execute_http_toolset_request(
    config: dict,
    tool_name: str,
    values: Optional[dict],
    uploaded_files: Optional[dict[str, HttpUpload]] = None,
) -> HttpToolResponse:
    """Assemble and execute one HTTP tool operation using explicit parameter locations."""
    from app.services.gateway.mcp_client import PinnedPublicTransport, assert_public_http_url, headers_from_config

    item = _find_tool_item(config, tool_name)
    payload = values if isinstance(values, dict) else {}
    uploads = dict(uploaded_files or {})
    input_schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else None
    declared = set((input_schema or {}).get("properties", {}).keys()) if input_schema else set()
    injectable = declared or set(payload.keys())
    method = str(item.get("method") or "GET").upper()
    path = str(item.get("path") or "")
    if path and not path.startswith("/"):
        path = "/" + path

    has_locations = _has_location_metadata(item)
    if has_locations:
        path_rows = _param_rows(item, "pathParams")
        query_rows = _param_rows(item, "queryParams")
        header_rows = _param_rows(item, "headerParams")
        body_rows = _param_rows(item, "bodyParams")
        _validate_required(path_rows + query_rows + header_rows + body_rows, payload, uploads)
        path_values = _pick_values(path_rows, payload)
        query_values = _pick_values(query_rows, payload)
        request_headers = _pick_values(header_rows, payload)
        body_values = _pick_values([row for row in body_rows if row.get("type") != "file"], payload)
        file_rows = [row for row in body_rows if row.get("type") == "file"]
    else:
        path_values = {key: payload.get(key) for key in injectable if key in payload}
        query_values = payload if method == "GET" else {}
        request_headers = {}
        body_values = payload if method != "GET" else {}
        file_rows = []

    encoded = {key: quote(str(value), safe="") for key, value in path_values.items() if value not in (None, "")}
    path = _apply_param_placeholders(path, encoded)
    url = f"{str(config.get('baseUrl') or '').rstrip('/')}{path}"
    assert_public_http_url(url)

    raw_headers = headers_from_config(config.get("headers"))
    headers = {
        key: _apply_param_placeholders(value, {name: payload.get(name) for name in injectable if name in payload})
        for key, value in raw_headers.items()
    }
    headers.update({key: str(value) for key, value in request_headers.items() if value is not None})

    files = None
    data = None
    json_body = body_values if body_values or (not has_locations and method != "GET") else None
    if file_rows:
        resolved: dict[str, HttpUpload] = {}
        for row in file_rows:
            key = str(row["key"])
            if key in uploads:
                resolved[key] = uploads[key]
            elif payload.get(key) not in (None, ""):
                resolved[key] = await _resolve_file_reference(payload[key])
        files = {
            key: (_safe_filename(upload.filename), upload.content, upload.content_type or "application/octet-stream")
            for key, upload in resolved.items()
        }
        data = {key: _form_value(value) for key, value in body_values.items() if value is not None}
        json_body = None

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=30, transport=PinnedPublicTransport()) as client:
        response = await client.request(
            method,
            url,
            headers=headers or None,
            params=query_values or None,
            json=json_body,
            data=data,
            files=files,
        )
    duration_ms = round((time.perf_counter() - started) * 1000)
    text = response.text
    return HttpToolResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=text,
        duration_ms=duration_ms,
        # Compatibility signal: the legacy preview boundary would have truncated this value.
        # ``body`` itself stays lossless for the durable/projector layer.
        truncated=len(text) > TOOL_RESULT_LIMIT,
    )


async def invoke_http_toolset_tool(config: dict, tool_name: str, payload: Optional[dict]) -> str:
    """Invoke a stored operation and preserve the historical string result contract."""
    result = await execute_http_toolset_request(config, tool_name, payload)
    if result.status_code >= 400:
        raise ToolInvokeError(f"HTTP {result.status_code}: {result.body[:300]}")
    return result.body


async def invoke_mcp_toolset_tool(config: dict, tool_name: str, args: Optional[dict]) -> str:
    from app.services.gateway.mcp_client import call_mcp_tool, headers_from_config

    _find_tool_item(config, tool_name)  # 校验工具在清单内，防任意工具名透传
    url = str(config.get("url") or "")
    headers = headers_from_config(config.get("headers"))
    result = await call_mcp_tool(url, headers, tool_name, args or {})
    return str(result or "")


async def run_sub_workflow(
    app_id: str,
    app_name: str,
    input_text: str,
    *,
    parent_ctx,
    variables: Optional[dict] = None,
    histories: Optional[list] = None,
    allow_draft: bool = False,
) -> str:
    """运行子应用的已发布工作流（pluginModule/appModule/Agent 工作流工具共用）。

    variables：被引用应用的全局变量传参（蓝本 chatConfig.variables 展开为输入）；
    histories：父级传入的聊天记录（蓝本 Input_Template_History）。
    """
    if parent_ctx.depth + 1 > MAX_SUB_WORKFLOW_DEPTH:
        raise ToolInvokeError(f"子工作流嵌套超过深度上限 {MAX_SUB_WORKFLOW_DEPTH}")
    # 环检测（开发计划 Phase 3）：引用链上出现重复 app 即拒绝——深度上限只兜「多层」，
    # 挡不住 A→B→A 这种间接自引用；发布期 BFS 只覆盖已发布态，运行期这里是最终兜底
    chain = list(getattr(parent_ctx, "app_chain", ()) or ())
    if not chain and parent_ctx.app_id:
        chain = [parent_ctx.app_id]
    if app_id in chain:
        raise ToolInvokeError("检测到子应用循环引用：" + " → ".join([*chain, app_id]))
    workflow_json = await load_tool_definition(app_id, allow_draft=allow_draft)
    if not workflow_json:
        raise ToolInvokeError(f"「{app_name}」没有可用的工作流定义")
    from app.services.workflows.workflow_engine import RunContext, WorkflowEngine, parse_graph

    sub_variables = dict(variables or {})
    if histories is not None:
        sub_variables["histories"] = histories
    sub_ctx = RunContext(
        input_text=input_text or "",
        variables=sub_variables,
        token=parent_ctx.token,
        user_id=parent_ctx.user_id,
        app_id=app_id,
        run_id=parent_ctx.run_id,
        thread_id=parent_ctx.thread_id,
        audit_run_id=getattr(parent_ctx, "audit_run_id", ""),
        audit_root_run_id=getattr(parent_ctx, "audit_root_run_id", ""),
        audit_parent_tool_call_id=getattr(parent_ctx, "audit_parent_tool_call_id", ""),
        audit_parent_logical_call_id=getattr(parent_ctx, "audit_parent_logical_call_id", ""),
        audit_execution_segment=getattr(parent_ctx, "audit_execution_segment", ""),
        audit_purpose=getattr(parent_ctx, "audit_purpose", "workflow_node"),
        external_execution=bool(getattr(parent_ctx, "external_execution", False)),
        external_session_id=getattr(parent_ctx, "external_session_id", None),
        external_workspace_ref=getattr(parent_ctx, "external_workspace_ref", None),
        external_file_ids=list(getattr(parent_ctx, "external_file_ids", []) or []),
        api_runtime=bool(getattr(parent_ctx, "api_runtime", False)),
        preview_only=bool(getattr(parent_ctx, "preview_only", False)),
        llm_api_key=parent_ctx.llm_api_key,
        default_model=parent_ctx.default_model,
        depth=parent_ctx.depth + 1,
        app_chain=tuple([*chain, app_id]),
    )
    sub_engine = WorkflowEngine(parse_graph(workflow_json), sub_ctx)
    await sub_engine.run()
    # M3：output_parts 为 (seq, text) 元组，按定序号稳定排序后拼接
    output = "".join(text for _seq, text in sorted(sub_ctx.output_parts, key=lambda p: p[0]) if text)
    if sub_ctx.uncaught_errors and not output:
        raise ToolInvokeError("；".join(sub_ctx.uncaught_errors[:2]))
    return output or "（工作流无文本输出）"


def parse_toolset_child_id(tool_id: str) -> Optional[tuple[str, str, str]]:
    """`http-{appId}/{toolName}` / `mcp-{appId}/{toolName}` -> (source, appId, toolName)。"""
    for prefix, source in (("http-", "http"), ("mcp-", "mcp")):
        if tool_id.startswith(prefix) and "/" in tool_id:
            rest = tool_id[len(prefix):]
            app_id, _, tool_name = rest.partition("/")
            if app_id and tool_name:
                return source, app_id, tool_name
    return None
