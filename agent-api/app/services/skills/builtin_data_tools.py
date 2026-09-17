"""数据类系统工具的定义与运行时实现。"""
from __future__ import annotations

import asyncio
import base64
import calendar
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import hmac
import io
import ipaddress
import json
import logging
import math
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse
import uuid

import httpx


logger = logging.getLogger(__name__)


DATA_BUILTIN_TOOLS: list[dict] = [
    {
        "id": "builtin.base64",
        "name": "Base64 编解码",
        "category": "数据",
        "description": "对文本或 Base64 文件对象进行编码、解码；文件结果以可在工作流中传递的结构化对象返回。",
        "nodeType": "systemTool",
        "inputKeys": ["content", "operation", "is_file", "filename", "mime_type"],
        "outputKeys": ["text", "base64", "file"],
        "outputs": {
            "text": {"label": "文本结果", "valueType": "string"},
            "base64": {"label": "Base64 字符串", "valueType": "string"},
            "file": {"label": "文件对象", "valueType": "object"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "title": "输入内容",
                    "multiline": True,
                    "description": "文本，或文件的 Base64/data URL 内容。",
                },
                "operation": {
                    "type": "string",
                    "title": "处理方式",
                    "enum": ["编码", "解码"],
                    "description": "选择 Base64 编码或解码。",
                },
                "is_file": {
                    "type": "boolean",
                    "title": "文件模式",
                    "default": False,
                    "description": "开启后按二进制文件处理，返回文件对象而非强制转为文本。",
                },
                "filename": {
                    "type": "string",
                    "title": "文件名",
                    "description": "文件模式下的输出文件名，可选。",
                },
                "mime_type": {
                    "type": "string",
                    "title": "MIME 类型",
                    "default": "application/octet-stream",
                    "description": "文件模式下的 MIME 类型。",
                },
            },
            "required": ["content", "operation"],
        },
    },
    {
        "id": "builtin.text_to_sql",
        "name": "Text to SQL",
        "category": "数据",
        "description": "根据自然语言和库表定义，使用大模型直接生成只读 SQL。",
        "nodeType": "systemTool",
        "inputKeys": ["model", "query_text", "database_schema", "limit_rows"],
        "outputKeys": ["sql", "table_fields", "confidence", "syntax_error"],
        "outputs": {
            "sql": {"label": "SELECT SQL", "valueType": "string"},
            "table_fields": {"label": "表字段说明", "valueType": "arrayObject"},
            "confidence": {"label": "生成置信度", "valueType": "number"},
            "syntax_error": {"label": "语法错误提示", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "title": "生成模型",
                    "default": "default",
                    "modelSelector": True,
                    "description": "用于生成 SQL 的对话模型；选择默认模型时使用当前工作流的默认模型。",
                },
                "query_text": {
                    "type": "string",
                    "title": "自然语言查询",
                    "multiline": True,
                    "description": "描述需要查询的数据和筛选条件。",
                },
                "database_schema": {
                    "type": "string",
                    "title": "库表定义",
                    "multiline": True,
                    "description": (
                        "允许使用的表、字段、类型和关联关系；多表查询可在这里写明外键，"
                        "例如 sys_user.depart_id = sys_depart.id。"
                    ),
                },
                "limit_rows": {
                    "type": "integer",
                    "title": "返回条数",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            "required": ["query_text", "database_schema"],
        },
    },
    {
        "id": "builtin.sql_query",
        "name": "SQL 执行",
        "category": "数据",
        "description": "通过 DB URI 执行单条 SQL，支持查询、DML 和 DDL；实际权限由数据库账号控制。",
        "nodeType": "systemTool",
        "inputKeys": ["sql", "db_uri"],
        "outputKeys": ["columns", "rows", "row_count", "affected_rows", "truncated", "database_type"],
        "outputs": {
            "columns": {"label": "字段列表", "valueType": "arrayString"},
            "rows": {"label": "表格数据", "valueType": "arrayObject"},
            "row_count": {"label": "返回行数", "valueType": "number"},
            "affected_rows": {"label": "受影响行数", "valueType": "number"},
            "truncated": {"label": "是否截断", "valueType": "boolean"},
            "database_type": {"label": "数据库类型", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "title": "SQL 语句",
                    "multiline": True,
                    "description": "仅允许单条 SQL；DML 和 DDL 会按 DB URI 对应账号的权限执行并提交。",
                },
                "db_uri": {
                    "type": "string",
                    "title": "DB URI",
                    "secret": True,
                    "description": (
                        "SQLAlchemy 数据库连接串。配置示例："
                        "MySQL：mysql+pymysql://sql_user:password@127.0.0.1:3306/ai_boot?charset=utf8mb4；"
                        "PostgreSQL：postgresql+psycopg://sql_user:password@127.0.0.1:5432/ai_runtime；"
                        "SQLite（Windows）：sqlite:///G:/data/example.sqlite；"
                        "SQLite（Linux）：sqlite:////opt/data/example.sqlite。"
                        "请使用权限范围符合工作流用途的账号；写操作不可自动回滚。"
                        "密码中的特殊字符需进行 URL 编码，建议引用密码类型全局变量。"
                    ),
                },
            },
            "required": ["sql", "db_uri"],
        },
    },
    {
        "id": "builtin.table_transform",
        "name": "表格转换清洗",
        "category": "数据",
        "description": "CSV/TSV/JSON 表格互转，并支持列选择、过滤、排序、分组聚合和去重。",
        "nodeType": "systemTool",
        "inputKeys": [
            "source",
            "source_format",
            "output_format",
            "select_columns",
            "filters",
            "sort_by",
            "group_by",
            "aggregations",
            "dedupe",
            "dedupe_columns",
        ],
        "outputKeys": ["columns", "rows", "text", "row_count"],
        "outputs": {
            "columns": {"label": "字段列表", "valueType": "arrayString"},
            "rows": {"label": "表格数据", "valueType": "arrayObject"},
            "text": {"label": "转换文本", "valueType": "string"},
            "row_count": {"label": "行数", "valueType": "number"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "title": "输入表格",
                    "multiline": True,
                    "description": "CSV/TSV 文本、JSON 数组，或 {columns, rows} 对象。",
                },
                "source_format": {
                    "type": "string",
                    "title": "输入格式",
                    "enum": ["auto", "csv", "tsv", "json"],
                    "default": "auto",
                },
                "output_format": {
                    "type": "string",
                    "title": "输出格式",
                    "enum": ["json", "csv", "tsv", "markdown"],
                    "default": "json",
                },
                "select_columns": {
                    "type": "array",
                    "title": "保留列",
                    "items": {"type": "string"},
                    "description": "为空时保留全部列。",
                },
                "filters": {
                    "type": "array",
                    "title": "过滤条件",
                    "description": "形如 {column, op, value}；op 支持 =、!=、>、>=、<、<=、contains、in、empty。",
                },
                "sort_by": {
                    "type": "array",
                    "title": "排序",
                    "description": "形如 {column, direction}，direction 为 asc 或 desc。",
                },
                "group_by": {"type": "array", "title": "分组列", "items": {"type": "string"}},
                "aggregations": {
                    "type": "array",
                    "title": "聚合",
                    "description": "形如 {column, op, as}；op 支持 sum、avg、min、max、count。",
                },
                "dedupe": {
                    "type": "boolean",
                    "title": "去重",
                    "default": False,
                    "description": "开启后按 dedupe_columns 去重；未指定列时按整行去重。",
                },
                "dedupe_columns": {
                    "type": "array",
                    "title": "去重列",
                    "items": {"type": "string"},
                    "description": "为空且开启去重时按整行去重。",
                },
            },
            "required": ["source"],
        },
    },
    {
        "id": "builtin.text_extract",
        "name": "文本提取",
        "category": "数据",
        "description": "从文本中按正则、分隔符或行范围稳定提取字段。",
        "nodeType": "systemTool",
        "inputKeys": ["text", "mode", "pattern", "delimiter", "start_line", "end_line", "flags"],
        "outputKeys": ["matches", "text", "count"],
        "outputs": {
            "matches": {"label": "匹配结果", "valueType": "arrayObject"},
            "text": {"label": "提取文本", "valueType": "string"},
            "count": {"label": "数量", "valueType": "number"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "title": "输入文本", "multiline": True},
                "mode": {
                    "type": "string",
                    "title": "提取方式",
                    "enum": ["regex", "delimiter", "line_range"],
                    "default": "regex",
                },
                "pattern": {"type": "string", "title": "正则表达式"},
                "delimiter": {"type": "string", "title": "分隔符"},
                "start_line": {"type": "integer", "title": "起始行", "minimum": 1},
                "end_line": {"type": "integer", "title": "结束行", "minimum": 1},
                "flags": {
                    "type": "array",
                    "title": "正则选项",
                    "items": {"type": "string", "enum": ["ignore_case", "multiline", "dotall"]},
                },
            },
            "required": ["text"],
        },
    },
    {
        "id": "builtin.url_fetch",
        "name": "URL 读取",
        "category": "搜索",
        "description": "读取指定 HTTP/HTTPS URL，返回状态码、标题、正文、链接和来源标记；内置 SSRF 防护、超时和大小限制。",
        "nodeType": "systemTool",
        "inputKeys": ["url", "max_chars"],
        "outputKeys": ["url", "final_url", "status_code", "title", "text", "links", "source", "fetched_at"],
        "outputs": {
            "url": {"label": "原始 URL", "valueType": "string"},
            "final_url": {"label": "最终 URL", "valueType": "string"},
            "status_code": {"label": "状态码", "valueType": "number"},
            "title": {"label": "标题", "valueType": "string"},
            "text": {"label": "正文", "valueType": "string"},
            "links": {"label": "链接", "valueType": "arrayObject"},
            "source": {"label": "来源标记", "valueType": "string"},
            "fetched_at": {"label": "读取时间", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "URL", "description": "仅支持 http:// 或 https://。"},
                "max_chars": {"type": "integer", "title": "最大正文字数", "default": 12000, "minimum": 100, "maximum": 50000},
            },
            "required": ["url"],
        },
    },
    {
        "id": "builtin.crypto_digest",
        "name": "Hash / UUID / HMAC",
        "category": "数据",
        "description": "生成 MD5、SHA256、HMAC 摘要或 UUID，适合签名校验、去重 key 和回调参数处理。",
        "nodeType": "systemTool",
        "inputKeys": ["operation", "content", "secret"],
        "outputKeys": ["result", "algorithm"],
        "outputs": {
            "result": {"label": "结果", "valueType": "string"},
            "algorithm": {"label": "算法", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "title": "算法",
                    "enum": ["md5", "sha256", "sha512", "hmac_sha256", "hmac_sha512", "uuid4"],
                    "default": "sha256",
                },
                "content": {"type": "string", "title": "输入内容", "multiline": True},
                "secret": {"type": "string", "title": "HMAC 密钥", "secret": True},
            },
            "required": ["operation"],
        },
    },
    {
        "id": "builtin.date_range",
        "name": "日期区间计算",
        "category": "时间",
        "description": "计算两个日期相差天数、工作日数量、加减天/月，并返回周初周末、月初月末。",
        "nodeType": "systemTool",
        "inputKeys": ["start_date", "end_date", "operation", "amount", "unit", "holidays"],
        "outputKeys": [
            "days_between",
            "workdays",
            "result_date",
            "week_start",
            "week_end",
            "month_start",
            "month_end",
        ],
        "outputs": {
            "days_between": {"label": "相差天数", "valueType": "number"},
            "workdays": {"label": "工作日数", "valueType": "number"},
            "result_date": {"label": "结果日期", "valueType": "string"},
            "week_start": {"label": "周初", "valueType": "string"},
            "week_end": {"label": "周末", "valueType": "string"},
            "month_start": {"label": "月初", "valueType": "string"},
            "month_end": {"label": "月末", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "title": "开始日期", "description": "YYYY-MM-DD。"},
                "end_date": {"type": "string", "title": "结束日期", "description": "YYYY-MM-DD。"},
                "operation": {
                    "type": "string",
                    "title": "计算方式",
                    "enum": ["diff", "add", "subtract"],
                    "default": "diff",
                },
                "amount": {"type": "integer", "title": "加减数量", "default": 0},
                "unit": {
                    "type": "string",
                    "title": "单位",
                    "enum": ["days", "weeks", "months", "years"],
                    "default": "days",
                },
                "holidays": {"type": "array", "title": "节假日", "items": {"type": "string"}},
            },
            "required": ["start_date"],
        },
    },
    {
        "id": "builtin.chart",
        "name": "图表生成",
        "category": "数据",
        "description": "根据结构化数据自动匹配折线、柱状、饼图或散点图，返回数据 Markdown、SVG Base64、ECharts 配置和 HTML。",
        "nodeType": "systemTool",
        "inputKeys": ["dataset", "chart_type", "title", "width", "height"],
        "outputKeys": [
            "data_markdown",
            "image_base64",
            "image_mime_type",
            "echarts_option",
            "html",
            "chart_type",
            "render_warning",
        ],
        "outputs": {
            "data_markdown": {"label": "数据列表 Markdown", "valueType": "string"},
            "image_base64": {"label": "图表图片 Base64", "valueType": "string"},
            "image_mime_type": {"label": "图片 MIME", "valueType": "string"},
            "echarts_option": {"label": "ECharts 配置", "valueType": "object"},
            "html": {"label": "HTML 图表", "valueType": "string"},
            "chart_type": {"label": "实际图表类型", "valueType": "string"},
            "render_warning": {"label": "渲染提示", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "array",
                    "title": "结构化数据",
                    "description": (
                        "JSON 数组，或 {columns: [], rows: []} 表格对象。示例："
                        '[{"month":"1月","sales":12},{"month":"2月","sales":18}]'
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "title": "图表类型",
                    "enum": ["auto", "line", "bar", "pie", "scatter"],
                    "enumLabels": {
                        "auto": "自动",
                        "line": "折线图",
                        "bar": "柱状图",
                        "pie": "饼图",
                        "scatter": "散点图",
                    },
                    "description": "自动会根据字段类型和数据量选择图表。",
                },
                "title": {"type": "string", "title": "标题"},
                "width": {"type": "integer", "title": "宽度", "default": 800, "minimum": 320, "maximum": 2400},
                "height": {"type": "integer", "title": "高度", "default": 480, "minimum": 240, "maximum": 1600},
            },
            "required": ["dataset"],
        },
    },
]


_DATA_URL_RE = re.compile(r"^data:([^;,]+)?(?:;charset=[^;,]+)?;base64,(.*)$", re.I | re.S)
_BLOCKED_SQL_RE = re.compile(
    r"\b(insert|update|delete|replace|merge|upsert|create|alter|drop|truncate|grant|revoke|"
    r"comment|call|execute|exec|copy|load|attach|detach|pragma|vacuum|analyze|lock|unlock|"
    r"set|reset|use|into\s+outfile|into\s+dumpfile|for\s+update)\b",
    re.I,
)
CHART_RENDER_ROW_LIMIT = 1000
CHART_TYPE_ALIASES = {
    "自动": "auto",
    "折线图": "line",
    "折线": "line",
    "柱状图": "bar",
    "柱图": "bar",
    "条形图": "bar",
    "饼图": "pie",
    "散点图": "scatter",
    "散点": "scatter",
}
URL_FETCH_MAX_BYTES = 1_000_000
URL_FETCH_MAX_REDIRECTS = 3


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_safe_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _base64_payload(content: str) -> tuple[str, str | None]:
    match = _DATA_URL_RE.match(content.strip())
    return (match.group(2), match.group(1) or None) if match else (content.strip(), None)


def _decode_base64(content: str) -> tuple[bytes, str | None]:
    payload, detected_mime = _base64_payload(content)
    try:
        return base64.b64decode(payload, validate=True), detected_mime
    except Exception as exc:
        raise ValueError("输入不是合法的 Base64 内容") from exc


def _run_base64(args: dict) -> dict:
    content = str(args.get("content") or "")
    operation = str(args.get("operation") or "编码").strip().lower()
    is_file = bool(args.get("is_file"))
    mime_type = str(args.get("mime_type") or "application/octet-stream")
    filename = str(args.get("filename") or "decoded.bin")

    if operation in ("编码", "encode"):
        if is_file:
            raw, detected_mime = _decode_base64(content)
            mime_type = detected_mime or mime_type
        else:
            raw = content.encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        return {
            "text": "",
            "base64": encoded,
            "file": (
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": len(raw),
                    "content_base64": encoded,
                    "data_url": f"data:{mime_type};base64,{encoded}",
                }
                if is_file
                else None
            ),
        }
    if operation not in ("解码", "decode"):
        raise ValueError("operation 仅支持 编码 或 解码")

    raw, detected_mime = _decode_base64(content)
    mime_type = detected_mime or mime_type
    if is_file:
        encoded = base64.b64encode(raw).decode("ascii")
        return {
            "text": "",
            "base64": encoded,
            "file": {
                "filename": filename,
                "mime_type": mime_type,
                "size": len(raw),
                "content_base64": encoded,
                "data_url": f"data:{mime_type};base64,{encoded}",
            },
        }
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("解码结果不是 UTF-8 文本；请开启文件模式") from exc
    return {"text": decoded, "base64": "", "file": None}


def _sql_code(sql: str) -> str:
    """移除字符串与注释后留下 SQL 结构，用于关键词和分号检查。"""
    result: list[str] = []
    i = 0
    quote = ""
    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            if char == quote:
                if nxt == quote:
                    i += 2
                    continue
                quote = ""
            elif char == "\\":
                i += 2
                continue
            i += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            result.append(" ")
            i += 1
            continue
        if char == "-" and nxt == "-":
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end + 1
            result.append(" ")
            continue
        if char == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("SQL 注释未闭合")
            i = end + 2
            result.append(" ")
            continue
        result.append(char)
        i += 1
    if quote:
        raise ValueError("SQL 字符串未闭合")
    return "".join(result)


def validate_readonly_sql(sql: Any) -> str:
    statement = str(sql or "").strip()
    if not statement:
        raise ValueError("SQL 不能为空")
    code = _sql_code(statement).strip()
    if code.endswith(";"):
        code = code[:-1].rstrip()
        statement = statement[:-1].rstrip()
    if ";" in code:
        raise ValueError("仅允许执行单条 SQL")
    if not re.match(r"^(select|with)\b", code, re.I):
        raise ValueError("仅允许 SELECT 或 WITH 只读查询")
    if _BLOCKED_SQL_RE.search(code):
        raise ValueError("检测到 DDL/DML 或高风险 SQL 关键字")
    if re.search(r"\bselect\b[\s\S]*\binto\b", code, re.I):
        raise ValueError("禁止 SELECT INTO")
    return statement


def validate_sql_statement(sql: Any) -> str:
    """Validate the SQL executor's single-statement boundary.

    Transaction control is owned by the executor so a successful DML/DDL statement
    can be committed atomically. Database privileges remain the authority for what
    the configured connection may modify.
    """
    statement = str(sql or "").strip()
    if not statement:
        raise ValueError("SQL 不能为空")
    code = _sql_code(statement).strip()
    if code.endswith(";"):
        code = code[:-1].rstrip()
        statement = statement[:-1].rstrip()
    if ";" in code:
        raise ValueError("仅允许执行单条 SQL")
    if not code:
        raise ValueError("SQL 不能为空")
    if re.match(r"^(?:begin|start\s+transaction|commit|rollback|savepoint|release)\b", code, re.I):
        raise ValueError("不支持事务控制语句；每个 SQL 节点会自动提交或回滚")
    return statement


def _ensure_limit(sql: str, limit_rows: int) -> str:
    code = _sql_code(sql)
    if re.search(r"\bcount\s*\(", code, re.I) and not re.search(r"\bgroup\s+by\b", code, re.I):
        return sql
    if re.search(r"\blimit\s+\d+\s*$", code, re.I):
        return sql
    return f"{sql.rstrip()} LIMIT {limit_rows}"


def _normalize_table_name(value: str) -> str:
    return re.sub(r"[`\"\s]", "", value).strip().lower()


def _schema_table_names(schema: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(
        r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?"
        r"(?P<name>[`\"]?[A-Za-z_][\w$]*[`\"]?(?:\s*\.\s*[`\"]?[A-Za-z_][\w$]*[`\"]?)?)",
        schema,
        re.I,
    ):
        names.add(_normalize_table_name(match.group("name")))

    # 兼容简写 schema：users(id bigint, name varchar)
    for match in re.finditer(
        r"(?m)^\s*(?P<name>[`\"]?[A-Za-z_][\w$]*[`\"]?)\s*\([^;\n]*\)\s*[,;]?\s*$",
        schema,
    ):
        names.add(_normalize_table_name(match.group("name")))

    try:
        parsed = json.loads(schema)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        tables = parsed.get("tables")
        if isinstance(tables, list):
            for item in tables:
                if isinstance(item, dict) and (item.get("name") or item.get("table")):
                    names.add(_normalize_table_name(str(item.get("name") or item.get("table"))))
        elif isinstance(tables, dict):
            names.update(_normalize_table_name(str(name)) for name in tables)
    return {name for name in names if name}


def _referenced_sql_tables(sql: str) -> list[str]:
    cte_names = {
        _normalize_table_name(match.group(1))
        for match in re.finditer(r"(?:\bwith\b|,)\s*([A-Za-z_][\w$]*)\s+as\s*\(", sql, re.I)
    }
    result: list[str] = []
    for match in re.finditer(
        r"\b(?:from|join)\s+"
        r"(?P<name>[`\"]?[A-Za-z_][\w$]*[`\"]?(?:\s*\.\s*[`\"]?[A-Za-z_][\w$]*[`\"]?)?)",
        sql,
        re.I,
    ):
        name = _normalize_table_name(match.group("name"))
        if name in cte_names or name in result:
            continue
        result.append(name)
    return result


def _schema_table_error(schema: str, sql: str) -> str:
    allowed = _schema_table_names(schema)
    if not allowed:
        return ""
    unknown = [
        table
        for table in _referenced_sql_tables(sql)
        if table not in allowed and table.rsplit(".", 1)[-1] not in {item.rsplit(".", 1)[-1] for item in allowed}
    ]
    if unknown:
        return f"SQL 引用了 schema 中未声明的表: {', '.join(unknown)}"
    return ""


def _parse_text_to_sql_result(response: httpx.Response) -> tuple[dict, str]:
    if response.status_code >= 400:
        raise ValueError(f"模型调用失败: HTTP {response.status_code}")
    raw = str(((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.I | re.S)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("模型未返回合法 JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("模型返回格式不正确")
    return result, raw


def _response_format_is_explicitly_unsupported(response: httpx.Response) -> bool:
    """Return true only for a Provider's explicit ``response_format`` rejection.

    A generic HTTP 400 can describe authentication, quota, malformed messages, or a
    model-side validation error.  Replaying all such requests without
    ``response_format`` both wastes quota and hides the real failure.  Compatibility
    fallback is therefore deliberately narrow: the structured error must identify
    ``response_format`` and say that the parameter/capability is unknown or unsupported.
    """

    if int(getattr(response, "status_code", 0) or 0) != 400:
        return False
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - an opaque 400 is not compatibility evidence
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False

    param = str(error.get("param") or error.get("parameter") or "").strip().lower()
    code = str(error.get("code") or error.get("type") or "").strip().lower()
    message = str(error.get("message") or "").strip().lower()
    identifies_parameter = (
        param in {"response_format", "response.format"}
        or "response_format" in message
        or "response format" in message
    )
    if not identifies_parameter:
        return False

    explicit_codes = {
        "unsupported_parameter",
        "unsupported_value",
        "unknown_parameter",
        "unrecognized_parameter",
        "not_supported",
    }
    explicit_phrases = (
        "not support",
        "unsupported",
        "unknown parameter",
        "unrecognized parameter",
        "not implemented",
        "is not available",
        "不支持",
        "未知参数",
        "无法识别",
    )
    return code in explicit_codes or any(phrase in message for phrase in explicit_phrases)


async def _run_text_to_sql(args: dict, runtime: dict) -> dict:
    query_text = str(args.get("query_text") or runtime.get("user_input") or "").strip()
    schema = str(args.get("database_schema") or "").strip()
    if not query_text or not schema:
        raise ValueError("query_text 和 database_schema 不能为空")
    api_key = str(runtime.get("api_key") or "")
    model = str(args.get("model") or runtime.get("default_model") or "")
    if model == "default":
        model = str(runtime.get("default_model") or "")
    if not api_key or not model:
        raise ValueError("Text to SQL 需要可用的模型和模型调用凭证")
    limit_rows = _clamp_int(args.get("limit_rows"), 20, 1, 1000)
    system = (
        "根据给定的数据库表结构和用户问题生成一条只读 SQL，只能使用表结构中存在的表和字段。"
        "严格输出 JSON 对象："
        '{"sql":"...","table_fields":[{"table":"表","field":"字段","description":"用途"}],'
        '"confidence":0.0,"syntax_error":""}。'
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"数据库表结构：\n{schema}\n\n用户问题：\n{query_text}",
        },
    ]
    base_url = str(runtime.get("base_url") or "").rstrip("/")

    async def request_result(request_messages: list[dict]) -> tuple[dict, str]:
        from app.services.agent_harness import model_usage_audit

        run_id = str(runtime.get("run_id") or "")
        thread_id = str(runtime.get("thread_id") or "")
        root_run_id = str(runtime.get("audit_root_run_id") or runtime.get("root_run_id") or "")
        parent_tool_call_id = str(
            runtime.get("audit_parent_tool_call_id")
            or runtime.get("parent_tool_call_id")
            or ""
        )
        try:
            from app.services.chat.tools.base import current_tool_context

            tool_context = current_tool_context()
        except Exception:  # pragma: no cover - import/context lookup is fail-open
            tool_context = None
        if tool_context is not None:
            run_id = run_id or str(tool_context.run_id or "")
            thread_id = thread_id or str(tool_context.thread_id or "")
            parent_tool_call_id = parent_tool_call_id or str(tool_context.call_id or "")

        async def begin_logical(*, parent_id: str = "", fallback_reason: str = ""):
            if not run_id:
                return None
            return await model_usage_audit.begin_logical_call(
                run_id=run_id,
                thread_id=thread_id,
                root_run_id=root_run_id,
                parent_logical_call_id=parent_id,
                parent_tool_call_id=parent_tool_call_id,
                model=model,
                transport="chat_completions",
                purpose="tool_internal",
                purpose_detail="text_to_sql",
                scope_key="tool_internal:text_to_sql",
                fallback_reason=fallback_reason,
                provider_api_key=api_key,
            )

        logical = await begin_logical()
        if not run_id:
            logger.warning(
                "model_usage_orphan purpose=tool_internal purpose_detail=text_to_sql "
                "reason=missing_run_id"
            )

        base_payload = {
            "model": model,
            "messages": request_messages,
            "temperature": 0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            for attempt_index in range(2):
                payload = dict(base_payload)
                if attempt_index:
                    # This is a new semantic call, not a same-payload retry: the Provider
                    # explicitly rejected response_format and the wire controls now differ.
                    payload.pop("response_format", None)
                attempt = (
                    await model_usage_audit.begin_attempt(
                        logical,
                        wire_payload=payload,
                        attempt_kind="initial" if attempt_index == 0 else "protocol_fallback",
                        legacy_compatible=False,
                    )
                    if logical is not None else None
                )
                try:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                except asyncio.CancelledError:
                    await model_usage_audit.finish_attempt(
                        attempt,
                        terminal_status="cancelled",
                        provider_event_seen=False,
                        unknown_provider_charge=True,
                        committed=False,
                    )
                    await model_usage_audit.finish_logical_call(
                        logical, terminal_status="cancelled", committed=False,
                    )
                    raise
                except Exception as exc:
                    await model_usage_audit.finish_attempt(
                        attempt,
                        terminal_status="failed",
                        provider_event_seen=False,
                        error_code=type(exc).__name__,
                        committed=False,
                    )
                    await model_usage_audit.finish_logical_call(
                        logical, terminal_status="failed", committed=False,
                    )
                    raise

                try:
                    response_payload = response.json()
                except Exception:
                    response_payload = {}
                usage = model_usage_audit.provider_usage_from_response(response_payload)
                response_id = model_usage_audit.provider_response_id(response_payload)
                if attempt_index == 0 and _response_format_is_explicitly_unsupported(response):
                    await model_usage_audit.finish_attempt(
                        attempt,
                        terminal_status="protocol_rejected",
                        usage=usage,
                        response_id=response_id,
                        provider_event_seen=True,
                        terminal_seen=True,
                        http_status=response.status_code,
                        committed=False,
                    )
                    parent_id = str(getattr(logical, "logical_call_id", "") or "")
                    await model_usage_audit.finish_logical_call(
                        logical,
                        terminal_status="failed",
                        selected_attempt_id=str(getattr(attempt, "attempt_id", "") or ""),
                        committed=False,
                    )
                    logical = await begin_logical(
                        parent_id=parent_id,
                        fallback_reason="response_format_unsupported",
                    )
                    continue
                try:
                    parsed = _parse_text_to_sql_result(response)
                except Exception as exc:
                    await model_usage_audit.finish_attempt(
                        attempt,
                        terminal_status=(
                            "http_error" if response.status_code >= 400 else "invalid_response"
                        ),
                        usage=usage,
                        response_id=response_id,
                        provider_event_seen=True,
                        terminal_seen=True,
                        http_status=response.status_code,
                        error_code=type(exc).__name__,
                        committed=False,
                    )
                    await model_usage_audit.finish_logical_call(
                        logical, terminal_status="failed", committed=False,
                    )
                    raise
                await model_usage_audit.finish_attempt(
                    attempt,
                    terminal_status="completed",
                    usage=usage,
                    response_id=response_id,
                    provider_event_seen=True,
                    terminal_seen=True,
                    http_status=response.status_code,
                    committed=True,
                )
                await model_usage_audit.finish_logical_call(
                    logical,
                    terminal_status="completed",
                    selected_attempt_id=attempt.attempt_id if attempt is not None else "",
                    committed=True,
                )
                return parsed

            # The loop either returns a parsed result or raises on the terminal attempt.
            raise RuntimeError("Text to SQL model request did not produce a terminal result")
    result, _ = await request_result(messages)
    sql = str(result.get("sql") or "").strip()
    syntax_error = str(result.get("syntax_error") or "")
    if sql:
        try:
            sql = validate_readonly_sql(sql)
            schema_error = _schema_table_error(schema, sql)
            if schema_error:
                raise ValueError(schema_error)
            sql = _ensure_limit(sql, limit_rows)
        except ValueError as exc:
            syntax_error = str(exc)
            sql = ""
    fields = result.get("table_fields")
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "sql": sql,
        "table_fields": fields if isinstance(fields, list) else [],
        "confidence": max(0.0, min(1.0, confidence)),
        "syntax_error": syntax_error,
    }


def _sync_sql_execute(sql: str, db_uri: str) -> dict:
    if db_uri.startswith("sqlite:///"):
        import sqlite3

        path = db_uri[len("sqlite:///"):]
        if not path or path == ":memory:":
            raise ValueError("SQLite DB URI 必须指向数据库文件")
        try:
            connection = sqlite3.connect(path, timeout=30)
            cursor = connection.execute(sql)
            columns = [item[0] for item in cursor.description or []]
            fetched = cursor.fetchall() if cursor.description else []
            affected_rows = max(int(cursor.rowcount or 0), 0)
            connection.commit()
        except sqlite3.Error as exc:
            raise ValueError(f"SQL 执行失败: {type(exc).__name__}") from exc
        finally:
            if "connection" in locals():
                connection.close()
        rows = [
            {columns[index]: _json_safe_cell(value) for index, value in enumerate(row)}
            for row in fetched
        ]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "affected_rows": affected_rows,
            "truncated": False,
            "database_type": "sqlite",
        }

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    try:
        url = make_url(db_uri)
    except Exception as exc:
        raise ValueError("DB URI 格式不正确") from exc
    backend = url.get_backend_name()
    if backend not in {"postgresql", "mysql", "sqlite"}:
        raise ValueError("仅支持 PostgreSQL、MySQL 和 SQLite")
    normalized_uri = db_uri
    if db_uri.startswith("mysql+aiomysql://"):
        normalized_uri = "mysql+pymysql://" + db_uri[len("mysql+aiomysql://"):]
    elif db_uri.startswith("postgresql+asyncpg://"):
        normalized_uri = "postgresql+psycopg://" + db_uri[len("postgresql+asyncpg://"):]
    engine = create_engine(normalized_uri, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            if backend == "postgresql":
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            result = connection.execute(text(sql))
            columns = list(result.keys()) if result.returns_rows else []
            fetched = result.fetchall() if result.returns_rows else []
            affected_rows = max(int(result.rowcount or 0), 0)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"SQL 执行失败: {type(exc).__name__}") from exc
    finally:
        engine.dispose()
    rows = [
        {columns[index]: _json_safe_cell(value) for index, value in enumerate(row)}
        for row in fetched
    ]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "affected_rows": affected_rows,
        "truncated": False,
        "database_type": backend,
    }


async def _run_sql_query(args: dict) -> dict:
    sql = validate_sql_statement(args.get("sql"))
    db_uri = str(args.get("db_uri") or "").strip()
    if not db_uri:
        raise ValueError("DB URI 不能为空")
    return await asyncio.to_thread(_sync_sql_execute, sql, db_uri)


def _parse_json_text(raw: str) -> Any:
    text = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.I)
    if fence_match:
        text = fence_match.group(1).strip()
    text = (
        text.replace("\ufeff", "")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    text = re.sub(r",\s*([}\]])", r"\1", text)
    parsed = json.loads(text)
    if isinstance(parsed, str):
        nested = parsed.strip()
        if nested and nested != text and nested[0] in "[{":
            return _parse_json_text(nested)
    return parsed


def _normalize_dataset(raw: Any) -> tuple[list[str], list[dict]]:
    if isinstance(raw, str):
        try:
            raw = _parse_json_text(raw)
        except json.JSONDecodeError as exc:
            preview = raw.strip().replace("\n", "\\n")[:120]
            raise ValueError(f"dataset 不是合法 JSON，收到内容片段: {preview}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("columns"), list) and isinstance(raw.get("rows"), list):
        columns = [str(column) for column in raw["columns"]]
        rows = [
            dict(row) if isinstance(row, dict) else dict(zip(columns, row))
            for row in raw["rows"]
            if isinstance(row, (dict, list, tuple))
        ]
    elif isinstance(raw, list):
        rows = [dict(row) for row in raw if isinstance(row, dict)]
        columns = list(dict.fromkeys(key for row in rows for key in row))
    else:
        raise ValueError("dataset 需为对象数组或 {columns, rows}")
    if not columns or not rows:
        raise ValueError("dataset 不能为空")
    return columns, rows


def _list_arg(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    return [value]


def _string_list_arg(value: Any) -> list[str]:
    return [str(item) for item in _list_arg(value) if str(item)]


def _bool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是", "开启"}


def _parse_delimited_table(raw: str, delimiter: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    columns = [str(column) for column in (reader.fieldnames or [])]
    rows = [dict(row) for row in reader]
    if not columns:
        raise ValueError("表格缺少表头")
    return columns, rows


def _normalize_table_source(raw: Any, source_format: str) -> tuple[list[str], list[dict]]:
    fmt = (source_format or "auto").strip().lower()
    if not isinstance(raw, str):
        return _normalize_dataset(raw)
    text = raw.strip()
    if not text:
        raise ValueError("source 不能为空")
    if fmt == "auto":
        fmt = "json" if text[0] in "[{" else "tsv" if "\t" in text.splitlines()[0] else "csv"
    if fmt == "json":
        return _normalize_dataset(text)
    if fmt == "csv":
        return _parse_delimited_table(raw, ",")
    if fmt == "tsv":
        return _parse_delimited_table(raw, "\t")
    raise ValueError("source_format 仅支持 auto、csv、tsv、json")


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _compare_filter_cell(cell: Any, op: str, expected: Any) -> bool:
    normalized_op = op.strip().lower()
    if normalized_op in {"=", "==", "eq"}:
        return str(cell) == str(expected)
    if normalized_op in {"!=", "<>", "ne"}:
        return str(cell) != str(expected)
    if normalized_op in {">", ">=", "<", "<="}:
        left = _to_float(cell)
        right = _to_float(expected)
        if left is None or right is None:
            left_text, right_text = str(cell), str(expected)
            if normalized_op == ">":
                return left_text > right_text
            if normalized_op == ">=":
                return left_text >= right_text
            if normalized_op == "<":
                return left_text < right_text
            return left_text <= right_text
        if normalized_op == ">":
            return left > right
        if normalized_op == ">=":
            return left >= right
        if normalized_op == "<":
            return left < right
        return left <= right
    if normalized_op == "contains":
        return str(expected) in str(cell)
    if normalized_op == "not_contains":
        return str(expected) not in str(cell)
    if normalized_op in {"in", "not_in"}:
        candidates = {str(item) for item in _list_arg(expected)}
        matched = str(cell) in candidates
        return not matched if normalized_op == "not_in" else matched
    if normalized_op == "empty":
        return cell is None or str(cell) == ""
    if normalized_op == "not_empty":
        return cell is not None and str(cell) != ""
    raise ValueError(f"不支持的过滤操作: {op}")


def _filter_rows(rows: list[dict], filters: list) -> list[dict]:
    result = rows
    for item in filters:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "")
        op = str(item.get("op") or "=")
        expected = item.get("value")
        result = [row for row in result if _compare_filter_cell(row.get(column), op, expected)]
    return result


def _dedupe_rows(rows: list[dict], columns: list[str], dedupe_columns: list[str]) -> list[dict]:
    if not dedupe_columns:
        dedupe_columns = columns
    seen: set[tuple] = set()
    result: list[dict] = []
    for row in rows:
        key = tuple(_json_text(row.get(column)) for column in dedupe_columns)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _aggregate_rows(rows: list[dict], group_by: list[str], aggregations: list) -> tuple[list[str], list[dict]]:
    if not group_by:
        return [], rows
    normalized_aggs = [item for item in aggregations if isinstance(item, dict)]
    if not normalized_aggs:
        normalized_aggs = [{"op": "count", "as": "count"}]
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row.get(column) for column in group_by)
        grouped.setdefault(key, []).append(row)
    output_rows: list[dict] = []
    output_columns = list(group_by)
    agg_columns: list[str] = []
    for agg in normalized_aggs:
        op = str(agg.get("op") or "count").strip().lower()
        column = str(agg.get("column") or "")
        alias = str(agg.get("as") or f"{op}_{column or 'rows'}")
        if alias not in agg_columns:
            agg_columns.append(alias)
        if op not in {"sum", "avg", "min", "max", "count"}:
            raise ValueError(f"不支持的聚合操作: {op}")
    output_columns.extend(agg_columns)
    for key, group_rows in grouped.items():
        output = {column: key[index] for index, column in enumerate(group_by)}
        for agg in normalized_aggs:
            op = str(agg.get("op") or "count").strip().lower()
            column = str(agg.get("column") or "")
            alias = str(agg.get("as") or f"{op}_{column or 'rows'}")
            values = [row.get(column) for row in group_rows] if column else list(group_rows)
            if op == "count":
                output[alias] = len([value for value in values if value not in (None, "")])
                continue
            numeric_values = [_to_float(value) for value in values]
            numeric_values = [value for value in numeric_values if value is not None]
            if op == "sum":
                output[alias] = float(sum(numeric_values))
            elif op == "avg":
                output[alias] = float(sum(numeric_values) / len(numeric_values)) if numeric_values else None
            elif op == "min":
                output[alias] = min(numeric_values) if numeric_values else None
            elif op == "max":
                output[alias] = max(numeric_values) if numeric_values else None
        output_rows.append(output)
    return output_columns, output_rows


def _sort_rows(rows: list[dict], sort_by: list) -> list[dict]:
    result = list(rows)
    for item in reversed(sort_by):
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "")
        direction = str(item.get("direction") or "asc").lower()

        def key(row: dict) -> tuple:
            value = row.get(column)
            numeric = _to_float(value)
            return (value is None, 0 if numeric is not None else 1, numeric if numeric is not None else str(value))

        result.sort(key=key, reverse=direction in {"desc", "descending", "降序"})
    return result


def _serialize_rows(columns: list[str], rows: list[dict], output_format: str) -> str:
    fmt = (output_format or "json").strip().lower()
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, default=str)
    if fmt in {"csv", "tsv"}:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t" if fmt == "tsv" else ",")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column, "") for column in columns])
        return buffer.getvalue()
    if fmt == "markdown":
        return _dataset_markdown(columns, rows)
    raise ValueError("output_format 仅支持 json、csv、tsv、markdown")


def _run_table_transform(args: dict) -> dict:
    columns, rows = _normalize_table_source(args.get("source"), str(args.get("source_format") or "auto"))
    filters = _list_arg(args.get("filters"))
    rows = _filter_rows(rows, filters)
    dedupe_columns = _string_list_arg(args.get("dedupe_columns"))
    if dedupe_columns or _bool_arg(args.get("dedupe")):
        rows = _dedupe_rows(rows, columns, dedupe_columns)
    group_by = _string_list_arg(args.get("group_by"))
    if group_by:
        columns, rows = _aggregate_rows(rows, group_by, _list_arg(args.get("aggregations")))
    rows = _sort_rows(rows, _list_arg(args.get("sort_by")))
    selected = _string_list_arg(args.get("select_columns"))
    if selected:
        columns = [column for column in selected if column in columns or any(column in row for row in rows)]
        rows = [{column: row.get(column) for column in columns} for row in rows]
    text = _serialize_rows(columns, rows, str(args.get("output_format") or "json"))
    return {"columns": columns, "rows": rows, "text": text, "row_count": len(rows)}


def _regex_flags(value: Any) -> int:
    flags = 0
    for item in _list_arg(value):
        name = str(item).strip().lower()
        if name in {"ignore_case", "ignorecase", "i"}:
            flags |= re.I
        elif name in {"multiline", "m"}:
            flags |= re.M
        elif name in {"dotall", "s"}:
            flags |= re.S
    return flags


def _line_range_text(text: str, start_line: Any, end_line: Any) -> tuple[str, int]:
    lines = text.splitlines()
    start = _clamp_int(start_line, 1, 1, max(1, len(lines))) - 1
    end = _clamp_int(end_line, len(lines), 1, max(1, len(lines)))
    return "\n".join(lines[start:end]), start + 1


def _run_text_extract(args: dict) -> dict:
    text = str(args.get("text") or "")
    mode = str(args.get("mode") or "regex").strip().lower()
    if args.get("start_line") or args.get("end_line"):
        working_text, base_line = _line_range_text(text, args.get("start_line"), args.get("end_line"))
    else:
        working_text, base_line = text, 1
    if mode == "line_range":
        lines = working_text.splitlines()
        matches = [{"line_number": base_line + index, "value": line, "text": line} for index, line in enumerate(lines)]
        return {"matches": matches, "text": working_text, "count": len(matches)}
    if mode == "delimiter":
        delimiter = str(args.get("delimiter") or "")
        if not delimiter:
            raise ValueError("delimiter 不能为空")
        parts = working_text.split(delimiter)
        matches = [{"index": index, "value": value, "text": value} for index, value in enumerate(parts)]
        return {"matches": matches, "text": "\n".join(parts), "count": len(matches)}
    if mode != "regex":
        raise ValueError("mode 仅支持 regex、delimiter、line_range")
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("pattern 不能为空")
    regex = re.compile(pattern, _regex_flags(args.get("flags")))
    matches = [
        {
            "match": match.group(0),
            "groups": list(match.groups()),
            "named": match.groupdict(),
            "start": match.start(),
            "end": match.end(),
        }
        for match in regex.finditer(working_text)
    ]
    return {"matches": matches, "text": "\n".join(item["match"] for item in matches), "count": len(matches)}


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url_fetch 仅支持 HTTP/HTTPS URL")
    if not parsed.hostname:
        raise ValueError("URL 缺少主机名")
    host = parsed.hostname
    try:
        blocked_literal = _is_blocked_ip(host)
    except ValueError:
        blocked_literal = False
    if blocked_literal:
        raise ValueError("url_fetch 禁止访问内网、回环或保留地址")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("URL 主机名解析失败") from exc
    for info in infos:
        address = info[4][0]
        if _is_blocked_ip(address):
            raise ValueError("url_fetch 禁止访问解析到内网、回环或保留地址的主机")
    return url


def _html_title(content: str) -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", content, re.I)
    return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""


def _html_text(content: str, max_chars: int) -> str:
    content = re.sub(r"<(script|style|noscript)[^>]*>[\s\S]*?</\1>", " ", content, flags=re.I)
    content = re.sub(r"<!--[\s\S]*?-->", " ", content)
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.I)
    content = re.sub(r"</(p|div|section|article|header|footer|li|tr|h[1-6])>", "\n", content, flags=re.I)
    content = re.sub(r"<[^>]+>", " ", content)
    text = html.unescape(content)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:max_chars]


def _html_links(content: str, base_url: str) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", content, re.I):
        href = html.unescape(match.group(1).strip())
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or absolute in seen:
            continue
        seen.add(absolute)
        label = _html_text(match.group(2), 200)
        links.append({"url": absolute, "text": label})
        if len(links) >= 50:
            break
    return links


async def _run_url_fetch(args: dict) -> dict:
    current_url = _validate_fetch_url(str(args.get("url") or "").strip())
    original_url = current_url
    max_chars = _clamp_int(args.get("max_chars"), 12000, 100, 50000)
    headers = {"User-Agent": "AXIOMWorkflowBuiltin/1.0"}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for _ in range(URL_FETCH_MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    current_url = _validate_fetch_url(urljoin(str(response.url), response.headers["location"]))
                    continue
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > URL_FETCH_MAX_BYTES:
                        raise ValueError("URL 响应体超过大小限制")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                content = raw.decode(encoding, errors="replace")
                final_url = str(response.url)
                _validate_fetch_url(final_url)
                return {
                    "url": original_url,
                    "final_url": final_url,
                    "status_code": response.status_code,
                    "title": _html_title(content),
                    "text": _html_text(content, max_chars),
                    "links": _html_links(content, final_url),
                    "source": "url_fetch",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
    raise ValueError("URL 跳转次数超过限制")


def _run_crypto_digest(args: dict) -> dict:
    operation = str(args.get("operation") or "sha256").strip().lower().replace("-", "_")
    if operation in {"uuid", "uuid4"}:
        return {"result": str(uuid.uuid4()), "algorithm": "uuid4"}
    content = str(args.get("content") or "").encode("utf-8")
    if operation in {"md5", "sha1", "sha256", "sha512"}:
        digest = hashlib.new(operation)
        digest.update(content)
        return {"result": digest.hexdigest(), "algorithm": operation}
    if operation.startswith("hmac_"):
        algorithm = operation.removeprefix("hmac_")
        if algorithm not in {"md5", "sha1", "sha256", "sha512"}:
            raise ValueError("HMAC 仅支持 md5、sha1、sha256、sha512")
        secret = str(args.get("secret") or "")
        if not secret:
            raise ValueError("HMAC 需要 secret")
        digest = hmac.new(secret.encode("utf-8"), content, algorithm)
        return {"result": digest.hexdigest(), "algorithm": f"hmac_{algorithm}"}
    raise ValueError("operation 仅支持 md5、sha256、sha512、hmac_sha256、hmac_sha512、uuid4")


def _parse_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 不能为空")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD 日期") from exc


def _add_months(source: date, amount: int) -> date:
    month_index = source.month - 1 + amount
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _shift_date(source: date, amount: int, unit: str) -> date:
    normalized = unit.strip().lower()
    if normalized in {"day", "days", "日", "天"}:
        return source + timedelta(days=amount)
    if normalized in {"week", "weeks", "周"}:
        return source + timedelta(weeks=amount)
    if normalized in {"month", "months", "月"}:
        return _add_months(source, amount)
    if normalized in {"year", "years", "年"}:
        return _add_months(source, amount * 12)
    raise ValueError("unit 仅支持 days、weeks、months、years")


def _workday_count(start: date, end: date, holidays: set[date]) -> int:
    if end < start:
        start, end = end, start
    current = start
    count = 0
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            count += 1
        current += timedelta(days=1)
    return count


def _date_boundaries(source: date) -> dict:
    week_start = source - timedelta(days=source.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = source.replace(day=1)
    month_end = source.replace(day=calendar.monthrange(source.year, source.month)[1])
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
    }


def _run_date_range(args: dict) -> dict:
    start = _parse_date(args.get("start_date"), "start_date")
    end = _parse_date(args.get("end_date"), "end_date") if args.get("end_date") else start
    holidays = {_parse_date(item, "holidays") for item in _list_arg(args.get("holidays"))}
    operation = str(args.get("operation") or "diff").strip().lower()
    result_date = start
    if operation in {"add", "plus", "加"}:
        result_date = _shift_date(start, int(args.get("amount") or 0), str(args.get("unit") or "days"))
    elif operation in {"subtract", "sub", "minus", "减"}:
        result_date = _shift_date(start, -int(args.get("amount") or 0), str(args.get("unit") or "days"))
    elif operation not in {"diff", "range", "区间"}:
        raise ValueError("operation 仅支持 diff、add、subtract")
    return {
        "days_between": (end - start).days,
        "workdays": _workday_count(start, end, holidays),
        "result_date": result_date.isoformat(),
        **_date_boundaries(result_date),
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_temporal_column(column: str, rows: list[dict]) -> bool:
    name = column.lower()
    if any(keyword in name for keyword in ("date", "time", "month", "year", "day", "日期", "时间", "月份", "年份")):
        return True
    values = [str(row.get(column) or "").strip() for row in rows[:20]]
    values = [value for value in values if value]
    if not values:
        return False
    matched = sum(
        1
        for value in values
        if re.match(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?(?:\s+\d{1,2}:\d{2}(:\d{2})?)?$", value)
        or re.match(r"^\d{1,2}月(\d{1,2}日)?$", value)
    )
    return matched / len(values) >= 0.8


def _chart_kind(requested: str, columns: list[str], rows: list[dict]) -> str:
    if requested in {"line", "bar", "pie", "scatter"}:
        return requested
    numeric = [column for column in columns if all(_is_number(row.get(column)) for row in rows)]
    temporal = [column for column in columns if column not in numeric and _is_temporal_column(column, rows)]
    if temporal and numeric:
        return "line"
    if len(numeric) >= 2:
        return "scatter"
    if len(numeric) == 1 and len(rows) <= 8:
        return "pie"
    return "line" if len(rows) >= 8 and numeric else "bar"


def _chart_option(kind: str, title: str, columns: list[str], rows: list[dict]) -> dict:
    numeric = [column for column in columns if any(_is_number(row.get(column)) for row in rows)]
    label_col = next((column for column in columns if column not in numeric), columns[0])
    value_cols = numeric or columns[1:2]
    base: dict = {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "item" if kind in {"pie", "scatter"} else "axis"},
        "animationDuration": 500,
    }
    if kind == "pie":
        value_col = value_cols[0]
        base.update({
            "legend": {"bottom": 0},
            "series": [{
                "type": "pie",
                "radius": ["38%", "68%"],
                "data": [{"name": str(row.get(label_col, "")), "value": row.get(value_col)} for row in rows],
            }],
        })
    elif kind == "scatter":
        x_col, y_col = (numeric + columns)[:2]
        base.update({
            "xAxis": {"name": x_col, "type": "value"},
            "yAxis": {"name": y_col, "type": "value"},
            "series": [{"type": "scatter", "data": [[row.get(x_col), row.get(y_col)] for row in rows]}],
        })
    else:
        base.update({
            "legend": {"bottom": 0},
            "xAxis": {"type": "category", "data": [str(row.get(label_col, "")) for row in rows]},
            "yAxis": {"type": "value"},
            "series": [
                {"name": column, "type": kind, "smooth": kind == "line", "data": [row.get(column) for row in rows]}
                for column in value_cols
            ],
        })
    return base


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _dataset_markdown(columns: list[str], rows: list[dict]) -> str:
    header = "| " + " | ".join(_markdown_cell(column) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_markdown_cell(row.get(column)) for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _chart_svg(kind: str, title: str, columns: list[str], rows: list[dict], width: int, height: int) -> str:
    numeric = [column for column in columns if any(_is_number(row.get(column)) for row in rows)]
    value_col = numeric[-1] if numeric else columns[-1]
    label_col = next((column for column in columns if column != value_col), columns[0])
    values = [float(row.get(value_col)) if _is_number(row.get(value_col)) else 0.0 for row in rows]
    maximum = max([abs(value) for value in values] or [1]) or 1
    left, top, bottom = 64, 72, 58
    plot_width, plot_height = width - left - 32, height - top - bottom
    accents = ["#4f46e5", "#111827", "#64748b", "#818cf8", "#334155", "#a5b4fc"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="34" text-anchor="middle" font-family="sans-serif" font-size="20" '
        f'font-weight="600" fill="#111827">{html.escape(title or "数据图表")}</text>',
    ]
    if kind == "pie":
        total = sum(max(value, 0) for value in values) or 1
        offset = 0.0
        radius = min(plot_width, plot_height) * 0.34
        circumference = 2 * math.pi * radius
        cx, cy = width / 2, top + plot_height / 2
        for index, (row, value) in enumerate(zip(rows, values)):
            ratio = max(value, 0) / total
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{accents[index % len(accents)]}" '
                f'stroke-width="{radius * .62}" stroke-dasharray="{ratio * circumference} {circumference}" '
                f'stroke-dashoffset="{-offset * circumference}" transform="rotate(-90 {cx} {cy})"/>'
            )
            offset += ratio
            parts.append(
                f'<text x="{left + (index % 2) * plot_width / 2}" y="{height - 30 + (index // 2) * 0}" '
                f'font-family="sans-serif" font-size="11" fill="#475569">'
                f'{html.escape(str(row.get(label_col, "")))} {value:g}</text>'
            )
    else:
        parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#cbd5e1"/>')
        parts.append(
            f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#cbd5e1"/>'
        )
        count = max(len(rows), 1)
        points: list[str] = []
        for index, (row, value) in enumerate(zip(rows, values)):
            x = left + (index + 0.5) * plot_width / count
            y = top + plot_height * (1 - max(value, 0) / maximum)
            if kind == "bar":
                bar_width = max(4, plot_width / count * 0.58)
                parts.append(
                    f'<rect x="{x - bar_width / 2}" y="{y}" width="{bar_width}" '
                    f'height="{top + plot_height - y}" rx="3" fill="#4f46e5"/>'
                )
            else:
                points.append(f"{x},{y}")
                parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#4f46e5"/>')
            parts.append(
                f'<text x="{x}" y="{top + plot_height + 20}" text-anchor="middle" font-family="sans-serif" '
                f'font-size="10" fill="#64748b">{html.escape(str(row.get(label_col, "")))[:14]}</text>'
            )
        if kind == "line" and points:
            parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#4f46e5" stroke-width="3"/>')
    parts.append("</svg>")
    return "".join(parts)


def _run_chart(args: dict) -> dict:
    columns, rows = _normalize_dataset(args.get("dataset"))
    render_rows = rows[:CHART_RENDER_ROW_LIMIT]
    render_warning = (
        f"数据共 {len(rows)} 条，图表已按前 {CHART_RENDER_ROW_LIMIT} 条渲染。"
        if len(rows) > CHART_RENDER_ROW_LIMIT
        else ""
    )
    requested_raw = str(args.get("chart_type") or "auto").strip()
    requested = CHART_TYPE_ALIASES.get(requested_raw, requested_raw.lower())
    if requested not in {"auto", "line", "bar", "pie", "scatter"}:
        raise ValueError("chart_type 仅支持 自动、折线图、柱状图、饼图、散点图")
    kind = _chart_kind(requested, columns, render_rows)
    title = str(args.get("title") or "数据图表")
    width = _clamp_int(args.get("width"), 800, 320, 2400)
    height = _clamp_int(args.get("height"), 480, 240, 1600)
    option = _chart_option(kind, title, columns, render_rows)
    svg = _chart_svg(kind, title, columns, render_rows, width, height)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    option_json = _json_text(option).replace("<", "\\u003c")
    chart_html = (
        f'<div id="chart" style="width:{width}px;height:{height}px"></div>'
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>'
        f"<script>echarts.init(document.getElementById('chart')).setOption({option_json});</script>"
    )
    return {
        "data_markdown": _dataset_markdown(columns, render_rows),
        "image_base64": encoded,
        "image_mime_type": "image/svg+xml",
        "echarts_option": option,
        "html": chart_html,
        "chart_type": kind,
        "render_warning": render_warning,
    }


async def execute_data_builtin_tool(tool_id: str, args: dict, runtime: dict | None = None) -> dict:
    if tool_id == "builtin.base64":
        return _run_base64(args)
    if tool_id == "builtin.text_to_sql":
        return await _run_text_to_sql(args, runtime or {})
    if tool_id == "builtin.sql_query":
        return await _run_sql_query(args)
    if tool_id == "builtin.table_transform":
        return _run_table_transform(args)
    if tool_id == "builtin.text_extract":
        return _run_text_extract(args)
    if tool_id == "builtin.url_fetch":
        return await _run_url_fetch(args)
    if tool_id == "builtin.crypto_digest":
        return _run_crypto_digest(args)
    if tool_id == "builtin.date_range":
        return _run_date_range(args)
    if tool_id == "builtin.chart":
        return _run_chart(args)
    raise ValueError(f"未知数据系统工具: {tool_id}")
