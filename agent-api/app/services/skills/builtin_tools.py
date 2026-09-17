"""
系统内置工具：随运行时发版，可在「系统工具」面板查看、被对话 Agent 挂载调用。

对齐蓝本「系统工具」定位（FastGPT system plugin tools 的首批本地实现）：
服务端执行、无需用户自行开发。
"""
import ast
import csv
from copy import copy
from datetime import date, datetime, timezone
from html import escape
from io import BytesIO, StringIO
import json
import os
import re
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.services.agent_time import datetime_from_timestamp_in_agent_timezone
from app.services.skills.builtin_data_tools import DATA_BUILTIN_TOOLS, execute_data_builtin_tool

TOOL_RESULT_LIMIT = 8000


def _build_timezone_options() -> list[str]:
    zones = sorted(available_timezones())
    if not zones:
        zones = [
            "UTC",
            "Asia/Shanghai",
            "Asia/Tokyo",
            "Asia/Singapore",
            "Europe/London",
            "America/New_York",
            "America/Los_Angeles",
        ]
    return ["UTC", *[zone for zone in zones if zone != "UTC"]]


_ALL_TIMEZONES = _build_timezone_options()

BUILTIN_TOOLS: list[dict] = [
    {
        "id": "builtin.datetime",
        "name": "当前时间",
        "category": "时间",
        "description": "获取当前日期时间，可传日期格式（默认 yyyy-MM-dd HH:mm:ss，兼容 strftime 格式）。",
        "nodeType": "systemTool",
        "inputKeys": ["format"],
        "outputKeys": ["result"],
        "parameters": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "title": "日期格式", "description": "日期格式，如 yyyy-MM-dd HH:mm:ss，可选"}
            },
        },
    },
    {
        "id": "builtin.timestamp",
        "name": "获取时间戳",
        "category": "时间",
        "description": "获取当前 UTC 时间戳，可输出秒级或毫秒级。",
        "nodeType": "systemTool",
        "inputKeys": ["unit"],
        "outputKeys": ["result"],
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {
                    "type": "string",
                    "title": "单位",
                    "enum": ["秒", "毫秒"],
                    "description": "时间戳单位，默认秒。",
                }
            },
        },
    },
    {
        "id": "builtin.time_convert",
        "name": "时间转换",
        "category": "时间",
        "description": "将时间字符串、时间戳或日期对象转换为指定目标格式。",
        "nodeType": "systemTool",
        "inputKeys": ["source_value", "source_format", "target_format"],
        "outputKeys": ["result"],
        "parameters": {
            "type": "object",
            "properties": {
                "source_value": {"type": "string", "title": "时间/时间戳", "description": "时间字符串、秒/毫秒时间戳或日期值。"},
                "source_format": {"type": "string", "title": "源格式", "description": "源时间自定义解析格式，可选。"},
                "target_format": {
                    "type": "string",
                    "title": "目标格式",
                    "enum": [
                        "yyyy-MM-dd HH:mm:ss",
                        "yyyy-MM-dd",
                        "yyyy/MM/dd HH:mm:ss",
                        "ISO8601",
                        "timestamp_seconds",
                        "timestamp_milliseconds",
                    ],
                    "description": "目标格式，支持常用下拉选项。",
                },
            },
            "required": ["source_value", "target_format"],
        },
    },
    {
        "id": "builtin.timezone_convert",
        "name": "时区转换",
        "category": "时间",
        "description": "支持 UTC、北京时间和海外时区互相换算，返回目标时区标准时间、目标时区和 UTC 时间戳。",
        "nodeType": "systemTool",
        "inputKeys": ["time_str", "from_timezone", "to_timezone"],
        "outputKeys": ["target_time", "target_timezone", "utc_timestamp"],
        "parameters": {
            "type": "object",
            "properties": {
                "time_str": {"type": "string", "title": "时间", "description": "待转换时间，如 2026-07-17 09:05:06。"},
                "from_timezone": {
                    "type": "string",
                    "title": "转换前时区",
                    "enum": _ALL_TIMEZONES,
                    "searchable": True,
                    "description": "转换前时区，如 UTC、Asia/Shanghai。",
                },
                "to_timezone": {
                    "type": "string",
                    "title": "转换后时区",
                    "enum": _ALL_TIMEZONES,
                    "searchable": True,
                    "description": "转换后时区，如 UTC、America/New_York。",
                },
            },
            "required": ["time_str", "from_timezone", "to_timezone"],
        },
    },
    {
        "id": "builtin.weekday",
        "name": "星期几计算器",
        "category": "时间",
        "description": "输入任意日期，计算中文星期、数字周几和年内第几周序号。",
        "nodeType": "systemTool",
        "inputKeys": ["date_str", "start_week"],
        "outputKeys": ["chinese_weekday", "weekday_number", "week_index"],
        "parameters": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "title": "日期", "description": "日期字符串，如 2026-07-17。"},
                "start_week": {
                    "type": "string",
                    "title": "一周起始日",
                    "enum": ["周一", "周日"],
                    "description": "一周起始日，默认周一。",
                },
            },
            "required": ["date_str"],
        },
    },
    {
        "id": "builtin.json_extract",
        "name": "JSON 提取",
        "category": "数据",
        "description": "从 JSON 文本中按路径提取字段，路径形如 data.items[0].name。",
        "nodeType": "systemTool",
        "inputKeys": ["json", "path"],
        "outputKeys": ["result"],
        "parameters": {
            "type": "object",
            "properties": {
                "json": {"type": "string", "title": "JSON 文本", "description": "JSON 文本"},
                "path": {"type": "string", "title": "提取路径", "description": "提取路径，如 data.items[0].name"},
            },
            "required": ["json", "path"],
        },
    },
    {
        "id": "builtin.json_parse",
        "name": "JSON 转换",
        "category": "数据",
        "description": "将 JSON 字符串解析为可被下游引用的对象或数组。",
        "nodeType": "systemTool",
        "inputKeys": ["json"],
        "outputKeys": ["result"],
        "parameters": {
            "type": "object",
            "properties": {
                "json": {"type": "string", "title": "JSON 字符串", "description": "需要转换的 JSON 字符串，内容必须是对象或数组。"},
            },
            "required": ["json"],
        },
    },
    {
        "id": "builtin.document_export",
        "name": "导出到我的文件",
        "category": "文件",
        "description": "将已经整理好的内容真实生成 DOCX、PDF、XLSX、JSON、CSV 或 Markdown 文件，保存到当前用户的“我的文件”，并返回文件回执。需要交付报告、清单、结构化结果或表格时必须调用。docx/pdf 可选套用内置排版模板「工作总结」「正式报告」。",
        "nodeType": "systemTool",
        "inputKeys": ["filename", "format", "title", "content", "template", "organization", "date"],
        "outputKeys": ["file", "message"],
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "交付文件名；可省略扩展名，系统会按 format 补齐。名称应说明内容，例如“合同版本差异报告”。",
                },
                "format": {
                    "type": "string",
                    "enum": ["docx", "pdf", "xlsx", "json", "csv", "md"],
                    "description": "导出格式。报告、通知、总结用 docx；需要不可编辑的定稿用 pdf（由 docx 转换）；结构化表格用 xlsx 或 csv；机器可读结果用 json。",
                },
                "title": {"type": "string", "description": "DOCX/PDF 文档标题；其他格式可选。"},
                "content": {
                    "type": "string",
                    "description": "要导出的完整正文。docx/pdf 用 Markdown：# 一级标题 / ## 二级 / ### 三级，管道表格会变成 Word 表格。xlsx/csv 可提供 JSON 数组（对象数组将成为表格行）；json 必须提供合法 JSON。",
                },
                "template": {
                    "type": "string",
                    "enum": ["默认", "工作总结", "正式报告"],
                    "description": "仅 docx/pdf 有效。「工作总结」为公文式（仿宋三号正文、黑体一级标题、固定 28 磅行距、页码「— 1 —」、文末落款）；「正式报告」为报告式（宋体小四正文、黑体 1/1.1/1.1.1 标题、1.5 倍行距、表格带边框）；不传或「默认」为普通 Word 样式。",
                },
                "organization": {"type": "string", "description": "可选，署名单位（如“XX学院”），用于落款或标题下方。"},
                "date": {"type": "string", "description": "可选，成文日期（如“2026年9月15日”），用于落款或标题下方。"},
            },
            "required": ["filename", "format", "content"],
        },
    },
    {
        "id": "builtin.document_typeset",
        "name": "套用排版模板",
        "category": "文件",
        "description": "把已有正文（Markdown/纯文本，或“我的文件”里的 DOCX/DOC/MD/TXT）套用平台内置排版模板「工作总结」或「正式报告」，真实生成排版后的 DOCX 和/或 PDF，保存到当前用户的“我的文件”。自动处理标题层级（Markdown # 或“一、/（一）/1.”惯例）、正文字体字号、段落间距与首行缩进、基础表格样式和页码；返回识别出的标题大纲供核对。不改写正文内容。",
        "nodeType": "systemTool",
        "inputKeys": ["template", "content", "source_file_id", "title", "organization", "date", "output_format", "filename"],
        "outputKeys": ["files", "file", "message", "outline", "heading_counts", "table_count", "template", "template_spec"],
        "outputs": {
            "files": {"label": "生成文件", "valueType": "arrayObject"},
            "file": {"label": "首个文件", "valueType": "object"},
            "message": {"label": "结果说明", "valueType": "string"},
            "outline": {"label": "标题大纲", "valueType": "arrayObject"},
            "heading_counts": {"label": "标题层级统计", "valueType": "object"},
            "table_count": {"label": "表格数量", "valueType": "number"},
            "template": {"label": "模板", "valueType": "string"},
            "template_spec": {"label": "模板规格", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "enum": ["工作总结", "正式报告"],
                    "description": "内置模板。工作总结/年度总结/述职/公文 → 「工作总结」；分析报告/调研报告/科研报告/评估报告 → 「正式报告」。",
                },
                "content": {
                    "type": "string",
                    "description": "要排版的完整正文（Markdown 或纯文本）。与 source_file_id 二选一；两者都给时以 source_file_id 的文件为准。",
                },
                "source_file_id": {
                    "type": "string",
                    "description": "可选，“我的文件”中的 DOCX/DOC/MD/TXT 文件 ID（用户消息中标出的文件 ID）。",
                },
                "title": {"type": "string", "description": "文档标题；不传时取正文开头唯一的一级标题或源文件名。"},
                "organization": {"type": "string", "description": "可选，署名单位，如“XX学院”。"},
                "date": {"type": "string", "description": "可选，成文日期，如“2026年9月15日”。"},
                "output_format": {
                    "type": "string",
                    "enum": ["docx", "pdf", "docx+pdf"],
                    "description": "输出格式，默认 docx；pdf 由排版后的 docx 转换而来。",
                },
                "filename": {"type": "string", "description": "可选，输出文件名（不含扩展名）；默认用标题。"},
            },
            "required": ["template"],
        },
    },
    {
        "id": "builtin.document_convert",
        "name": "文档格式转换",
        "category": "文件",
        "description": "把“我的文件”里的源文件转换成目标格式并另存：DOCX/DOC → PDF（保留原排版）、DOCX/DOC → DOCX、DOCX/DOC → Markdown；MD/TXT → DOCX/PDF。不改写内容、不套模板；需要套模板请用「套用排版模板」。",
        "nodeType": "systemTool",
        "inputKeys": ["source_file_id", "target_format", "filename"],
        "outputKeys": ["file", "message", "source_format", "target_format"],
        "outputs": {
            "file": {"label": "转换后的文件", "valueType": "object"},
            "message": {"label": "结果说明", "valueType": "string"},
            "source_format": {"label": "源格式", "valueType": "string"},
            "target_format": {"label": "目标格式", "valueType": "string"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "source_file_id": {
                    "type": "string",
                    "description": "源文件的 file_id，只能使用用户消息中标出的文件 ID。",
                },
                "target_format": {
                    "type": "string",
                    "enum": ["pdf", "docx", "md"],
                    "description": "目标格式。",
                },
                "filename": {"type": "string", "description": "可选，输出文件名（不含扩展名）；默认沿用源文件名。"},
            },
            "required": ["source_file_id", "target_format"],
        },
    },
    {
        "id": "builtin.mindmap_export",
        "name": "导出可交互思维导图",
        "category": "文件",
        "description": "将层级主题树真实生成独立的 HTML 思维导图，支持浏览器预览、展开/折叠和下载；文件保存到当前用户的“我的文件”。tree 必须为 {title, children} 结构。",
        "nodeType": "systemTool",
        "inputKeys": ["filename", "title", "tree"],
        "outputKeys": ["file", "message"],
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "导图文件名；可省略 .html 后缀，例如“项目推进思维导图”。",
                },
                "title": {
                    "type": "string",
                    "description": "导图标题；可省略，默认使用 tree.title。",
                },
                "tree": {
                    "type": "object",
                    "description": "思维导图的 JSON 树，格式为 {\"title\":\"中心主题\",\"children\":[{\"title\":\"一级主题\",\"children\":[]}] }。",
                },
            },
            "required": ["tree"],
        },
    },
    {
        "id": "builtin.template_fill",
        "name": "填充 Word/Excel 模板",
        "category": "文件",
        "description": "读取当前用户上传的 DOCX 或 XLSX 模板，按字段值填充 {{字段}}、${字段}、【字段】占位符；常见的“字段名 + 相邻空白单元格”表格会安全填入。XLSX 可按表头批量填写空白明细行，或安全追加到已有明细末尾并保留样式和公式。始终另存为新的可预览交付文件到“我的文件”，不会覆盖原件。",
        "nodeType": "systemTool",
        "inputKeys": ["source_file_id", "field_values", "output_filename"],
        "outputKeys": ["file", "message", "applied_fields", "unmatched_fields"],
        "parameters": {
            "type": "object",
            "properties": {
                "source_file_id": {
                    "type": "string",
                    "description": "本轮上传模板的 file_id。只能使用用户消息中标出的文件 ID。",
                },
                "field_values": {
                    "type": "string",
                    "description": "JSON 对象，键为模板字段名、值为要填入的内容。例如 {\"项目名称\":\"…\",\"负责人\":\"…\"}。若要填写 Excel 明细表，使用 {\"__tables\":{\"费用明细\":[{\"日期\":\"2026-08-18\",\"金额（元）\":553}]}}；系统按表头写入预留空白行，或安全追加到已有明细末尾。只填有原文依据或用户明确确认的数据。",
                },
                "output_filename": {
                    "type": "string",
                    "description": "可选，输出文件名；不传时以“已填写_原文件名”另存。扩展名必须与模板一致。",
                },
            },
            "required": ["source_file_id", "field_values"],
        },
    },
    {
        "id": "builtin.web_search",
        "name": "联网搜索",
        "category": "搜索",
        "description": "复用系统联网搜索配置，按关键词搜索互联网，返回可引用摘要、结构化结果和图片结果。",
        "nodeType": "systemTool",
        "inputKeys": ["query", "with_images"],
        "outputKeys": ["text", "results", "images", "scraped_pages", "enabled"],
        "outputs": {
            "text": {"label": "搜索摘要", "valueType": "string"},
            "results": {"label": "搜索结果", "valueType": "arrayObject"},
            "images": {"label": "图片结果", "valueType": "arrayObject"},
            "scraped_pages": {"label": "已阅读页面", "valueType": "arrayObject"},
            "enabled": {"label": "是否启用", "valueType": "boolean"},
        },
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "title": "搜索关键词",
                    "multiline": True,
                    "description": "要联网搜索的问题或关键词。",
                },
                "with_images": {
                    "type": "boolean",
                    "title": "包含图片",
                    "default": False,
                    "description": "开启后同时搜索图片结果。",
                },
            },
            "required": ["query"],
        },
    },
    *DATA_BUILTIN_TOOLS,
]

BUILTIN_TOOL_MAP = {tool["id"]: tool for tool in BUILTIN_TOOLS}

_STRPTIME_TOKENS = {
    "yyyy": "%Y",
    "YYYY": "%Y",
    "yy": "%y",
    "YY": "%y",
    "SSS": "%f",
    "MM": "%m",
    "dd": "%d",
    "DD": "%d",
    "HH": "%H",
    "hh": "%I",
    "mm": "%M",
    "ss": "%S",
    "M": "%m",
    "d": "%d",
    "D": "%d",
    "H": "%H",
    "h": "%I",
    "m": "%M",
    "s": "%S",
}

_COMMON_PARSE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S",
]

_TIMEZONE_ALIASES = {
    "utc": "UTC",
    "gmt": "UTC",
    "北京时间": "Asia/Shanghai",
    "北京": "Asia/Shanghai",
    "中国标准时间": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "纽约": "America/New_York",
    "美东": "America/New_York",
    "伦敦": "Europe/London",
    "东京": "Asia/Tokyo",
    "新加坡": "Asia/Singapore",
    "洛杉矶": "America/Los_Angeles",
    "美西": "America/Los_Angeles",
}

_CHINESE_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

_JSON_CODE_FENCE_RE = re.compile(r"^\s*```(?:json|javascript|js|python)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_BARE_JSON_KEY_RE = re.compile(r"(?P<prefix>[{,]\s*)(?P<key>[A-Za-z_]\w*)(?P<suffix>\s*:)")


def _extract_path(data: Any, path: str) -> Any:
    current = data
    for raw in path.replace("]", "").split("."):
        for key in raw.split("["):
            if key == "":
                continue
            if isinstance(current, list):
                current = current[int(key)]
            elif isinstance(current, dict):
                current = current.get(key)
            else:
                return None
    return current


def _strip_json_code_fence(text: str) -> str:
    match = _JSON_CODE_FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def _extract_balanced_json_segment(text: str) -> str | None:
    start = next((index for index, char in enumerate(text) if char in "[{"), -1)
    if start < 0:
        return None
    stack: list[str] = []
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in "[{":
            stack.append("]" if char == "[" else "}")
            continue
        if char in "]}":
            if not stack or stack[-1] != char:
                return None
            stack.pop()
            if not stack:
                return text[start:index + 1]
    return None


def _json_candidate_texts(text: str) -> list[str]:
    bases = [text]
    segment = _extract_balanced_json_segment(text)
    if segment and segment not in bases:
        bases.append(segment)
    candidates: list[str] = []
    for base in bases:
        unescaped = base.replace('\\"', '"').replace("\\'", "'")
        for candidate in (base, unescaped):
            without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", candidate)
            bare_key_candidate = _BARE_JSON_KEY_RE.sub(r'\g<prefix>"\g<key>"\g<suffix>', without_trailing_commas)
            single_quote_candidate = bare_key_candidate.replace("'", '"')
            for item in (candidate, without_trailing_commas, bare_key_candidate, single_quote_candidate):
                if item not in candidates:
                    candidates.append(item)
    return candidates


def _parse_json_object_or_array(raw: Any) -> dict | list:
    if isinstance(raw, (dict, list)):
        return raw

    text = _strip_json_code_fence(str(raw or ""))
    last_error = "格式不合法"
    for candidate in _json_candidate_texts(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc.msg
            try:
                parsed = ast.literal_eval(candidate)
            except (SyntaxError, ValueError) as literal_exc:
                last_error = str(literal_exc) or last_error
                continue
        if isinstance(parsed, str):
            if _extract_balanced_json_segment(_strip_json_code_fence(parsed)):
                try:
                    return _parse_json_object_or_array(parsed)
                except ValueError as exc:
                    last_error = str(exc)
                    continue
            raise ValueError("JSON 转换仅支持对象或数组")
        if not isinstance(parsed, (dict, list)):
            raise ValueError("JSON 转换仅支持对象或数组")
        return parsed

    raise ValueError(f"JSON 解析失败: {last_error}")


def _bool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "0", "false", "no", "off", "否", "关闭"}:
        return False
    if text in {"1", "true", "yes", "on", "是", "开启"}:
        return True
    return bool(value)


def _format_datetime(now: datetime, fmt: str) -> str:
    """支持前端常见日期格式，同时保留 strftime 兼容。"""
    if "%" in fmt:
        return now.strftime(fmt)

    tokens = {
        "yyyy": f"{now.year:04d}",
        "YYYY": f"{now.year:04d}",
        "yy": f"{now.year % 100:02d}",
        "YY": f"{now.year % 100:02d}",
        "MM": f"{now.month:02d}",
        "M": str(now.month),
        "dd": f"{now.day:02d}",
        "DD": f"{now.day:02d}",
        "d": str(now.day),
        "D": str(now.day),
        "HH": f"{now.hour:02d}",
        "H": str(now.hour),
        "hh": f"{(now.hour % 12) or 12:02d}",
        "h": str((now.hour % 12) or 12),
        "mm": f"{now.minute:02d}",
        "m": str(now.minute),
        "ss": f"{now.second:02d}",
        "s": str(now.second),
        "SSS": f"{now.microsecond // 1000:03d}",
    }
    ordered_tokens = sorted(tokens, key=len, reverse=True)
    result: list[str] = []
    i = 0
    while i < len(fmt):
        matched = False
        for token in ordered_tokens:
            if fmt.startswith(token, i):
                result.append(tokens[token])
                i += len(token)
                matched = True
                break
        if not matched:
            result.append(fmt[i])
            i += 1
    return "".join(result)


def _frontend_format_to_strptime(fmt: str) -> str:
    if "%" in fmt:
        return fmt
    ordered_tokens = sorted(_STRPTIME_TOKENS, key=len, reverse=True)
    result: list[str] = []
    i = 0
    while i < len(fmt):
        matched = False
        for token in ordered_tokens:
            if fmt.startswith(token, i):
                result.append(_STRPTIME_TOKENS[token])
                i += len(token)
                matched = True
                break
        if not matched:
            result.append(fmt[i])
            i += 1
    return "".join(result)


def _parse_timestamp_value(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            number = float(stripped)
        except ValueError:
            return None
    else:
        return None

    if abs(number) >= 100_000_000_000:
        number = number / 1000
    return datetime.fromtimestamp(number, timezone.utc)


def _parse_datetime_value(value: Any, source_format: str | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    timestamp_dt = _parse_timestamp_value(value)
    if timestamp_dt is not None:
        return timestamp_dt

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("时间不能为空")

    if source_format:
        return datetime.strptime(raw, _frontend_format_to_strptime(source_format))

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in _COMMON_PARSE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError("无法解析时间，请提供 source_format")


def _format_converted_time(value: datetime, target_format: str) -> str:
    normalized = str(target_format or "yyyy-MM-dd HH:mm:ss").strip()
    lowered = normalized.lower()
    value_for_timestamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if lowered in ("timestamp_seconds", "timestamp_second", "seconds", "秒"):
        return str(int(value_for_timestamp.timestamp()))
    if lowered in ("timestamp_milliseconds", "timestamp_millisecond", "milliseconds", "毫秒"):
        return str(int(value_for_timestamp.timestamp() * 1000))
    if lowered in ("iso8601", "iso"):
        return value.isoformat()
    return _format_datetime(value, normalized)


def _timezone_from_name(name: Any) -> ZoneInfo:
    raw = str(name or "UTC").strip()
    zone_name = _TIMEZONE_ALIASES.get(raw.lower()) or _TIMEZONE_ALIASES.get(raw) or raw
    try:
        return ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"不支持的时区: {raw}") from exc


def _timezone_label(zone: ZoneInfo) -> str:
    return "UTC" if zone.key == "UTC" else zone.key


def _normalize_timestamp_unit(unit: Any) -> str:
    raw = str(unit or "秒").strip().lower()
    if raw in ("毫秒", "ms", "millisecond", "milliseconds", "timestamp_milliseconds"):
        return "milliseconds"
    if raw in ("秒", "s", "sec", "second", "seconds", "timestamp_seconds"):
        return "seconds"
    raise ValueError("unit 仅支持 秒 或 毫秒")


def _week_start_index(start_week: Any) -> int:
    raw = str(start_week or "周一").strip().lower()
    if raw in ("周日", "星期日", "sunday", "sun", "0", "7"):
        return 6
    if raw in ("周一", "星期一", "monday", "mon", "1"):
        return 0
    raise ValueError("start_week 仅支持 周一 或 周日")


def _week_index_of_year(day: date, start_index: int) -> int:
    jan1 = date(day.year, 1, 1)
    offset = (jan1.weekday() - start_index) % 7
    week1_start = date.fromordinal(jan1.toordinal() - offset)
    return ((day - week1_start).days // 7) + 1


_DOCUMENT_EXPORT_EXTENSIONS = {"docx", "pdf", "xlsx", "json", "csv", "md"}
_DOCUMENT_EXPORT_MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "csv": "text/csv",
    "md": "text/markdown",
}


def pop_generated_file_receipts(result: Any) -> list[dict]:
    """从工具结果里取出产物回执并删掉内部键。

    单文件工具放 ``_generated_file_receipt``，多文件工具（如套模板同时出 DOCX+PDF）放
    ``_generated_file_receipts``；两个消费方（agent_executor / workflow_engine）统一走这里，
    避免只认单个键时第二个文件悄悄丢掉。
    """
    if not isinstance(result, dict):
        return []
    single = result.pop("_generated_file_receipt", None)
    many = result.pop("_generated_file_receipts", None)
    receipts: list[dict] = []
    seen: set[str] = set()
    for item in [single, *(many if isinstance(many, list) else [])]:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id") or "")
        if file_id and file_id not in seen:
            seen.add(file_id)
            receipts.append(item)
    return receipts


def _safe_export_filename(raw: Any, fmt: str) -> str:
    name = os.path.basename(str(raw or "").replace("\\", "/").strip())
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(". ")
    if not name:
        name = f"导出结果.{fmt}"
    stem, ext = os.path.splitext(name)
    if ext.lower().lstrip(".") != fmt:
        name = f"{stem or name}.{fmt}"
    return name[:200]


def _try_parse_export_data(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    text = str(content or "").strip()
    if not text:
        return []
    return json.loads(text)


def _table_rows_from_export_data(data: Any) -> tuple[list[str], list[list[Any]]]:
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data = data["rows"]
    if isinstance(data, list):
        if not data:
            return [], []
        if all(isinstance(item, dict) for item in data):
            headers: list[str] = []
            for item in data:
                for key in item:
                    key = str(key)
                    if key not in headers:
                        headers.append(key)
            return headers, [[item.get(key, "") for key in headers] for item in data]
        return ["内容"], [[item] for item in data]
    if isinstance(data, dict):
        return ["字段", "值"], [[key, value] for key, value in data.items()]
    return ["内容"], [[data]]


def _cell_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def _build_docx_bytes(title: str, content: str) -> bytes:
    from docx import Document

    document = Document()
    if title.strip():
        document.add_heading(title.strip(), level=0)
    for raw_line in str(content or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            document.add_paragraph("")
        elif line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:].strip(), level=1)
        elif re.match(r"^[-*+]\s+", line):
            document.add_paragraph(re.sub(r"^[-*+]\s+", "", line), style="List Bullet")
        elif re.match(r"^\d+[.)、]\s+", line):
            document.add_paragraph(re.sub(r"^\d+[.)、]\s+", "", line), style="List Number")
        else:
            document.add_paragraph(raw_line)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _build_xlsx_bytes(content: Any, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    data = _try_parse_export_data(content)
    headers, rows = _table_rows_from_export_data(data)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (title.strip() or "结果")[:31]
    if headers:
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    for row in rows:
        sheet.append([_cell_value(value) for value in row])
    for column in sheet.columns:
        width = min(max((len(str(cell.value or "")) for cell in column), default=8) + 2, 48)
        sheet.column_dimensions[column[0].column_letter].width = width
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _build_csv_bytes(content: Any) -> bytes:
    try:
        data = _try_parse_export_data(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(content or "").encode("utf-8-sig")
    headers, rows = _table_rows_from_export_data(data)
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    if headers:
        writer.writerow(headers)
    writer.writerows([[_cell_value(value) for value in row] for row in rows])
    return stream.getvalue().encode("utf-8-sig")


async def _export_document_to_user_files(args: dict, runtime: dict | None) -> dict:
    runtime = runtime or {}
    user_id = str(runtime.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("缺少当前用户，无法保存导出文件")
    fmt = str(args.get("format") or "").strip().lower()
    if fmt not in _DOCUMENT_EXPORT_EXTENSIONS:
        raise ValueError("format 仅支持 docx、pdf、xlsx、json、csv 或 md")
    content = args.get("content")
    if content is None or (isinstance(content, str) and not content.strip()):
        raise ValueError("content 不能为空，不能生成空交付文件")
    title = str(args.get("title") or "").strip()
    filename = _safe_export_filename(args.get("filename"), fmt)
    template_key = ""
    if fmt in {"docx", "pdf"}:
        from app.services.skills import document_typeset

        template = document_typeset.resolve_template(args.get("template"))
        raw_template = str(args.get("template") or "").strip()
        if raw_template and raw_template != document_typeset.TEMPLATE_DEFAULT and template is None:
            raise ValueError("template 仅支持「默认」「工作总结」「正式报告」")
        docx_title = title or os.path.splitext(filename)[0]
        if template is not None:
            # 套内置模板：与「套用排版模板」同一渲染器，正文开头唯一的一级标题当作文档标题
            blocks = document_typeset.parse_blocks(str(content))
            embedded_title = document_typeset.pop_title(blocks)
            document_typeset.normalize_heading_levels(blocks)
            if not title and embedded_title:
                docx_title = embedded_title
            if not any(block.kind != "heading" for block in blocks):
                raise ValueError("正文只有标题没有内容，不能生成空交付文件")
            template_key = template.key
            data = document_typeset.render_docx(
                blocks,
                template,
                title=docx_title,
                organization=str(args.get("organization") or ""),
                date=str(args.get("date") or ""),
            )
        else:
            data = _build_docx_bytes(docx_title, str(content))
        if fmt == "pdf":
            data = await document_typeset.docx_to_pdf(f"{os.path.splitext(filename)[0]}.docx", data)
    elif fmt == "xlsx":
        data = _build_xlsx_bytes(content, title)
    elif fmt == "json":
        parsed = _try_parse_export_data(content)
        data = json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
    elif fmt == "csv":
        data = _build_csv_bytes(content)
    else:
        data = str(content).encode("utf-8")

    from app.services.files import user_file_service

    preview_only = bool(runtime.get("preview_only"))
    saved = await (
        user_file_service.save_preview_bytes(
            user_id, filename, data, run_id=str(runtime.get("run_id") or "") or None,
        )
        if preview_only
        else user_file_service.save_generated_bytes(
            user_id,
            filename,
            data,
            thread_id=str(runtime.get("thread_id") or "") or None,
            run_id=str(runtime.get("run_id") or "") or None,
        )
    )
    receipt = {
        "id": str(saved.get("id") or ""),
        "filename": str(saved.get("filename") or filename),
        "mime": str(saved.get("mime") or _DOCUMENT_EXPORT_MIMES[fmt]),
        "size": int(saved.get("size") or len(data)),
        "source": "generated",
        "versionNo": int(saved.get("versionNo") or 1),
        "deliverable": True,
        "previewOnly": preview_only,
        "origin": {
            "runId": str(runtime.get("run_id") or ""),
            "tool": "builtin.document_export",
            **({"template": template_key} if template_key else {}),
        },
    }
    if not receipt["id"]:
        raise ValueError("文件保存未返回 file_id")
    return {
        "message": f"已生成《{receipt['filename']}》并保存到我的文件。",
        "file": receipt,
        "_generated_file_receipt": receipt,
    }


def _normalize_mindmap_tree(raw: Any) -> dict[str, Any]:
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("tree 必须是合法 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("tree 必须是包含 title 和 children 的对象")

    def normalize(node: Any, depth: int = 0) -> dict[str, Any]:
        if depth > 12:
            raise ValueError("思维导图层级最多支持 12 层")
        if not isinstance(node, dict):
            raise ValueError("tree.children 中的每一项都必须是对象")
        title = str(node.get("title") or node.get("name") or "").strip()
        if not title:
            raise ValueError("思维导图的每个节点都需要 title")
        children = node.get("children") or []
        if not isinstance(children, list):
            raise ValueError("tree.children 必须是数组")
        if len(children) > 80:
            raise ValueError("单个思维导图节点最多支持 80 个子节点")
        return {"title": title[:240], "children": [normalize(child, depth + 1) for child in children]}

    return normalize(value)


def _mindmap_tree_html(node: dict[str, Any]) -> str:
    title = escape(str(node["title"]))
    children = node.get("children") or []
    if not children:
        return f'<li><span class="topic leaf">{title}</span></li>'
    nested = "".join(_mindmap_tree_html(child) for child in children)
    return (
        '<li><details open><summary><span class="topic">'
        f"{title}</span></summary><ul>{nested}</ul></details></li>"
    )


def _build_mindmap_html(tree: dict[str, Any], title: str) -> bytes:
    document_title = escape(title or tree["title"])
    root = _mindmap_tree_html(tree)
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{document_title}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-width: 760px; background: #f7f8fc; color: #1f2937; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 22px 32px; background: rgba(255,255,255,.94); border-bottom: 1px solid #e7eaf0; backdrop-filter: blur(12px); }}
    h1 {{ margin: 0; font-size: 22px; }}
    header p {{ margin: 6px 0 0; color: #6b7280; font-size: 13px; }}
    .mindmap {{ overflow: auto; min-height: calc(100vh - 94px); padding: 44px 36px 72px; }}
    .tree, .tree ul {{ display: flex; justify-content: center; margin: 0; padding: 20px 0 0; list-style: none; position: relative; min-width: max-content; }}
    .tree > li {{ padding-top: 0; }}
    .tree li {{ position: relative; padding: 26px 10px 0; text-align: center; }}
    .tree li::before, .tree li::after {{ content: ""; position: absolute; top: 0; width: 50%; height: 26px; border-top: 1px solid #cbd5e1; }}
    .tree li::before {{ right: 50%; border-right: 1px solid #cbd5e1; }}
    .tree li::after {{ left: 50%; border-left: 1px solid #cbd5e1; }}
    .tree > li::before, .tree > li::after, .tree li:only-child::before, .tree li:only-child::after {{ display: none; }}
    .tree li:first-child::before, .tree li:last-child::after {{ border: 0; }}
    .tree li:last-child::before {{ border-radius: 0 8px 0 0; }}
    .tree li:first-child::after {{ border-radius: 8px 0 0 0; }}
    details {{ display: inline-block; }}
    summary {{ list-style: none; cursor: pointer; }}
    summary::-webkit-details-marker {{ display: none; }}
    .topic {{ display: inline-flex; align-items: center; max-width: 260px; min-height: 42px; padding: 9px 14px; border: 1px solid #c7d2fe; border-radius: 12px; background: #eef2ff; color: #1e3a8a; font-size: 14px; font-weight: 650; line-height: 1.45; text-align: left; box-shadow: 0 3px 12px rgba(55, 90, 190, .08); }}
    .tree > li > details > summary .topic {{ border-color: #1d4ed8; background: #1d4ed8; color: #fff; font-size: 16px; box-shadow: 0 8px 24px rgba(29, 78, 216, .25); }}
    .topic.leaf {{ border-color: #dbe3ef; background: #fff; color: #334155; font-weight: 500; }}
    details > summary .topic::after {{ content: "−"; margin-left: 8px; opacity: .72; }}
    details:not([open]) > summary .topic::after {{ content: "+"; }}
    @media print {{ header {{ position: static; }} .mindmap {{ padding: 20px; }} }}
  </style>
</head>
<body>
  <header><h1>{document_title}</h1><p>可点击带符号的主题展开或折叠分支。</p></header>
  <main class="mindmap"><ul class="tree">{root}</ul></main>
</body>
</html>"""
    return html_doc.encode("utf-8")


async def _export_mindmap_to_user_files(args: dict, runtime: dict | None) -> dict:
    runtime = runtime or {}
    user_id = str(runtime.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("缺少当前用户，无法保存导出文件")
    tree = _normalize_mindmap_tree(args.get("tree"))
    title = str(args.get("title") or tree["title"]).strip() or tree["title"]
    filename = _safe_export_filename(args.get("filename") or f"{title}思维导图", "html")
    data = _build_mindmap_html(tree, title)

    from app.services.files import user_file_service

    preview_only = bool(runtime.get("preview_only"))
    saved = await (
        user_file_service.save_preview_bytes(user_id, filename, data, run_id=str(runtime.get("run_id") or "") or None)
        if preview_only
        else user_file_service.save_generated_bytes(
            user_id,
            filename,
            data,
            thread_id=str(runtime.get("thread_id") or "") or None,
            run_id=str(runtime.get("run_id") or "") or None,
        )
    )
    receipt = {
        "id": str(saved.get("id") or ""),
        "filename": str(saved.get("filename") or filename),
        "mime": str(saved.get("mime") or "text/html"),
        "size": int(saved.get("size") or len(data)),
        "source": "generated",
        "versionNo": int(saved.get("versionNo") or 1),
        "deliverable": True,
        "previewOnly": preview_only,
        "origin": {"runId": str(runtime.get("run_id") or ""), "tool": "builtin.mindmap_export"},
    }
    if not receipt["id"]:
        raise ValueError("文件保存未返回 file_id")
    return {
        "message": f"已生成可交互思维导图《{receipt['filename']}》并保存到我的文件。",
        "file": receipt,
        "_generated_file_receipt": receipt,
    }


def _parse_template_mapping(raw: Any) -> dict[str, Any]:
    """Parse the JSON payload accepted by the template-filling tool."""
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("field_values 必须是合法 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("field_values 必须是 JSON 对象")
    return value


def _template_field_values(raw: Any) -> dict[str, str]:
    """Parse scalar field mappings for template filling.

    Field labels are user-visible business data.  Keeping this mapping explicit
    prevents a model from guessing which blank cell in a document should be
    overwritten, while still supporting common Word/Excel form templates.  The
    reserved ``__tables`` key is handled separately so repeated Excel rows can
    retain their source number/date types instead of being serialized as text.
    """
    value = _parse_template_mapping(raw)
    fields: dict[str, str] = {}
    for key, item in value.items():
        field = str(key or "").strip()
        if not field or field == "__tables":
            continue
        if isinstance(item, (dict, list)):
            fields[field] = json.dumps(item, ensure_ascii=False)
        elif item is not None:
            fields[field] = str(item)
    if not fields and not _template_table_rows(raw):
        raise ValueError("field_values 不能为空")
    return fields


def _template_table_rows(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Extract repeated-row payloads from ``field_values.__tables``.

    Example::

        {
          "申请人": "陈雨婷",
          "__tables": {
            "费用明细": [{"日期": "2026-08-18", "金额（元）": 553}]
          }
        }

    Table cells keep numeric values as numbers so existing SUM formulas remain
    meaningful in the generated workbook.
    """
    raw_tables = _parse_template_mapping(raw).get("__tables")
    if raw_tables is None:
        return {}
    if not isinstance(raw_tables, dict):
        raise ValueError("field_values.__tables 必须是对象，键为表格名称、值为行对象数组")

    tables: dict[str, list[dict[str, Any]]] = {}
    for raw_name, raw_rows in raw_tables.items():
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_rows, list):
            continue
        rows: list[dict[str, Any]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                continue
            row = {
                str(key or "").strip(): value
                for key, value in raw_row.items()
                if str(key or "").strip() and value is not None
            }
            if row:
                rows.append(row)
        if rows:
            tables[name] = rows
    if raw_tables and not tables:
        raise ValueError("field_values.__tables 未包含可填写的行对象")
    return tables


def _replace_template_tokens(value: Any, fields: dict[str, str]) -> tuple[str, list[str]]:
    text = str(value or "")
    applied: list[str] = []
    for key, replacement in fields.items():
        for token in (f"{{{{{key}}}}}", f"${{{key}}}", f"【{key}】"):
            if token in text:
                text = text.replace(token, replacement)
                applied.append(key)
    return text, applied


def _write_docx_paragraph(paragraph: Any, fields: dict[str, str]) -> list[str]:
    """Replace placeholders while preserving the template's run styles when possible."""
    applied: list[str] = []
    for run in paragraph.runs:
        replaced, keys = _replace_template_tokens(run.text, fields)
        if keys:
            run.text = replaced
            applied.extend(keys)
    if applied:
        return applied

    # Word often splits a visible placeholder into several styled runs.  The
    # fallback keeps the first run's styling and clears the remaining runs,
    # which is preferable to creating an unstyled replacement paragraph.
    original = paragraph.text
    replaced, keys = _replace_template_tokens(original, fields)
    if keys and paragraph.runs:
        paragraph.runs[0].text = replaced
        for run in paragraph.runs[1:]:
            run.text = ""
        return keys
    return []


def _write_docx_cell(cell: Any, value: str) -> None:
    paragraph = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    if paragraph.runs:
        paragraph.runs[0].text = value
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(value)


def _clean_template_label(value: Any) -> str:
    return str(value or "").strip().rstrip("：:").strip()


def _fill_docx_template(data: bytes, fields: dict[str, str]) -> tuple[bytes, list[str]]:
    from docx import Document

    document = Document(BytesIO(data))
    applied: list[str] = []
    for paragraph in document.paragraphs:
        applied.extend(_write_docx_paragraph(paragraph, fields))
    for section in document.sections:
        for paragraph in (*section.header.paragraphs, *section.footer.paragraphs):
            applied.extend(_write_docx_paragraph(paragraph, fields))
    for table in document.tables:
        for row in table.rows:
            cells = list(row.cells)
            for cell in cells:
                for paragraph in cell.paragraphs:
                    applied.extend(_write_docx_paragraph(paragraph, fields))
            # Common form convention: a label in one cell and a blank value
            # cell to its right.  Only fill a truly blank neighbour, never
            # overwrite a pre-existing user/template value.
            for index, cell in enumerate(cells[:-1]):
                label = _clean_template_label(cell.text)
                target = cells[index + 1]
                if label in fields and not target.text.strip():
                    _write_docx_cell(target, fields[label])
                    applied.append(label)
    if not applied:
        raise ValueError("模板中未找到可填写字段；请使用 {{字段名}} 占位符或“字段名 + 相邻空白单元格”表格")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue(), list(dict.fromkeys(applied))


def _xlsx_blank_row_capacity(sheet: Any, start_row: int, columns: dict[str, int]) -> int:
    """Return the contiguous writable-row capacity at ``start_row``.

    Only the columns that will be written are considered.  This keeps an
    unrelated note column from preventing a legitimate table fill, while a
    value or merged cell in a target column remains a hard stop.
    """
    from openpyxl.cell.cell import MergedCell

    capacity = 0
    for row_index in range(start_row, sheet.max_row + 1):
        targets = [sheet.cell(row_index, column) for column in columns.values()]
        if any(isinstance(cell, MergedCell) or cell.value not in (None, "") for cell in targets):
            break
        capacity += 1
    return capacity


def _xlsx_row_is_summary(sheet: Any, row_index: int, columns: dict[str, int]) -> bool:
    """Detect a common total/footer row so appended records stay above it."""
    values = [sheet.cell(row_index, column).value for column in columns.values()]
    text = " ".join(str(value) for value in values if value not in (None, ""))
    if any(token in text for token in ("合计", "总计", "小计", "汇总")):
        return True
    populated = [value for value in values if value not in (None, "")]
    return bool(populated) and all(isinstance(value, str) and value.startswith("=") for value in populated)


def _copy_xlsx_row_style(sheet: Any, source_row: int, target_row: int, columns: dict[str, int]) -> None:
    """Copy only presentation from an existing detail row; never copy its data."""
    if source_row <= 0:
        return
    for column in set(columns.values()):
        source = sheet.cell(source_row, column)
        target = sheet.cell(target_row, column)
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)
        if source.font:
            target.font = copy(source.font)
        if source.fill:
            target.fill = copy(source.fill)
        if source.border:
            target.border = copy(source.border)
        if source.protection:
            target.protection = copy(source.protection)
    height = sheet.row_dimensions[source_row].height
    if height is not None:
        sheet.row_dimensions[target_row].height = height


def _extend_xlsx_summary_formula_ranges(sheet: Any, summary_row: int, insert_at_row: int, amount: int) -> None:
    """Extend simple ranges ending immediately above newly inserted detail rows.

    ``openpyxl.insert_rows`` preserves formulas but does not update their
    referenced ranges.  This covers the common ``SUM(B2:B10)`` footer shape
    without attempting to rewrite arbitrary formulas.
    """
    range_pattern = re.compile(
        r"(?P<start_col>\$?[A-Z]{1,3})(?P<start_row>\$?\d+):"
        r"(?P<end_col>\$?[A-Z]{1,3})(?P<end_row>\$?\d+)"
    )

    def extend_range(match: re.Match[str]) -> str:
        raw_end = match.group("end_row")
        end_row = int(raw_end.replace("$", ""))
        if end_row != insert_at_row - 1:
            return match.group(0)
        row_prefix = "$" if raw_end.startswith("$") else ""
        return (
            f"{match.group('start_col')}{match.group('start_row')}:"
            f"{match.group('end_col')}{row_prefix}{end_row + amount}"
        )

    for cell in sheet[summary_row]:
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.value = range_pattern.sub(extend_range, cell.value)


def _xlsx_append_start_row(sheet: Any, header_row: int, columns: dict[str, int], row_count: int) -> tuple[int, int]:
    """Find or create a writable detail range without overwriting existing rows.

    Templates commonly contain a table that already has records and no spare
    blank detail rows.  The earlier implementation rejected those templates.
    We now prefer a preallocated blank range, otherwise append directly after
    the data region.  A detected total row is shifted downward before writing,
    so it is not overwritten.
    """
    from openpyxl.cell.cell import MergedCell

    first_blank_row: int | None = None
    blank_capacity = 0
    last_detail_row = header_row
    for row_index in range(header_row + 1, sheet.max_row + 1):
        targets = [sheet.cell(row_index, column) for column in columns.values()]
        writable_blank = all(
            not isinstance(cell, MergedCell) and cell.value in (None, "") for cell in targets
        )
        if writable_blank:
            if first_blank_row is None:
                first_blank_row = row_index
                blank_capacity = 0
            blank_capacity += 1
            if blank_capacity >= row_count:
                return first_blank_row, max(header_row + 1, first_blank_row - 1)
            continue

        if _xlsx_row_is_summary(sheet, row_index, columns):
            start_row = first_blank_row or row_index
            missing_rows = max(0, row_count - blank_capacity)
            if missing_rows:
                sheet.insert_rows(row_index, amount=missing_rows)
                _extend_xlsx_summary_formula_ranges(
                    sheet,
                    summary_row=row_index + missing_rows,
                    insert_at_row=row_index,
                    amount=missing_rows,
                )
            return start_row, max(header_row + 1, start_row - 1)

        # A gap followed by another populated row belongs to a separate table
        # section, so do not write into that separator by mistake.
        first_blank_row = None
        blank_capacity = 0
        last_detail_row = row_index

    if first_blank_row is not None:
        return first_blank_row, max(header_row + 1, first_blank_row - 1)
    return max(sheet.max_row + 1, header_row + 1), last_detail_row


def _fill_xlsx_table_rows(workbook: Any, tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Write repeated rows by header, including safe append to existing tables.

    A workbook may include both an empty template and populated source data with
    the same headers.  Prefer a preallocated empty range, then an exact sheet
    name, before falling back to the strongest header match.
    """
    from openpyxl.cell.cell import MergedCell

    applied: list[str] = []
    for table_name, rows in tables.items():
        required_columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
        if not required_columns:
            continue
        candidates: list[tuple[int, bool, bool, int, Any, int, dict[str, int]]] = []
        for sheet in workbook.worksheets:
            for header_row in range(1, sheet.max_row + 1):
                column_by_label: dict[str, int] = {}
                for column in range(1, sheet.max_column + 1):
                    cell = sheet.cell(header_row, column)
                    if isinstance(cell, MergedCell):
                        continue
                    label = _clean_template_label(cell.value)
                    if label and label not in column_by_label:
                        column_by_label[label] = column
                matched = {key: column_by_label[key] for key in required_columns if key in column_by_label}
                if len(matched) < 2:
                    continue
                capacity = _xlsx_blank_row_capacity(sheet, header_row + 1, matched)
                exact_sheet = _clean_template_label(sheet.title) == _clean_template_label(table_name)
                candidates.append((
                    len(matched),
                    capacity >= len(rows),
                    exact_sheet,
                    capacity,
                    sheet,
                    header_row,
                    matched,
                ))
        if not candidates:
            continue
        _matched_count, _has_capacity, _exact_sheet, _capacity, sheet, header_row, matched = max(
            candidates,
            key=lambda item: (item[0], item[1], item[2], item[3]),
        )
        start_row, style_row = _xlsx_append_start_row(sheet, header_row, matched, len(rows))
        wrote = 0
        for offset, row in enumerate(rows):
            target_row = start_row + offset
            _copy_xlsx_row_style(sheet, style_row, target_row, matched)
            for key, value in row.items():
                column = matched.get(key)
                if column is None:
                    continue
                target = sheet.cell(target_row, column)
                if target.value in (None, ""):
                    target.value = value
                    wrote += 1
        if wrote:
            applied.append(f"{table_name}（{len(rows)}行）")
    return applied


def _fill_xlsx_template(
    data: bytes, fields: dict[str, str], tables: dict[str, list[dict[str, Any]]]
) -> tuple[bytes, list[str]]:
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell

    workbook = load_workbook(BytesIO(data))
    applied: list[str] = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                # Never rewrite a formula unless it deliberately contains a
                # supported placeholder.  This protects existing calculations.
                original = cell.value
                if isinstance(original, str):
                    replaced, keys = _replace_template_tokens(original, fields)
                    if keys:
                        cell.value = replaced
                        applied.extend(keys)
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                label = _clean_template_label(cell.value)
                if label not in fields:
                    continue
                right = sheet.cell(cell.row, cell.column + 1)
                below = sheet.cell(cell.row + 1, cell.column)
                target = right if right.value in (None, "") else (below if below.value in (None, "") else None)
                if isinstance(target, MergedCell):
                    target = None
                if target is not None:
                    target.value = fields[label]
                    applied.append(label)
    applied.extend(_fill_xlsx_table_rows(workbook, tables))
    if not applied:
        raise ValueError("模板中未找到可填写字段；请使用占位符、字段名相邻空白单元格，或提供可匹配的表头与空白明细行")
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue(), list(dict.fromkeys(applied))


async def _fill_template_to_user_files(args: dict, runtime: dict | None) -> dict:
    runtime = runtime or {}
    user_id = str(runtime.get("user_id") or "").strip()
    source_file_id = str(args.get("source_file_id") or "").strip()
    if not user_id or not source_file_id:
        raise ValueError("缺少当前用户或 source_file_id，无法填写模板")
    fields = _template_field_values(args.get("field_values"))
    tables = _template_table_rows(args.get("field_values"))

    from app.services.files import user_file_service

    source, source_bytes = await user_file_service.read_bytes(user_id, source_file_id)
    source_name = str(source.filename or "模板")
    ext = os.path.splitext(source_name)[1].lower()
    if ext == ".docx":
        data, applied = _fill_docx_template(source_bytes, fields)
    elif ext == ".xlsx":
        data, applied = _fill_xlsx_template(source_bytes, fields, tables)
    else:
        raise ValueError("目前仅支持填充 DOCX 或 XLSX 模板；PDF 请先作为参考材料，再导出 DOCX/XLSX 结果")

    requested_name = str(args.get("output_filename") or "").strip()
    default_name = f"已填写_{os.path.splitext(source_name)[0]}{ext}"
    filename = _safe_export_filename(requested_name or default_name, ext.lstrip("."))
    preview_only = bool(runtime.get("preview_only"))
    saved = await (
        user_file_service.save_preview_bytes(
            user_id, filename, data, run_id=str(runtime.get("run_id") or "") or None,
        )
        if preview_only
        else user_file_service.save_generated_bytes(
            user_id,
            filename,
            data,
            thread_id=str(runtime.get("thread_id") or "") or None,
            run_id=str(runtime.get("run_id") or "") or None,
        )
    )
    receipt = {
        "id": str(saved.get("id") or ""),
        "filename": str(saved.get("filename") or filename),
        "mime": str(saved.get("mime") or _DOCUMENT_EXPORT_MIMES[ext.lstrip(".")]),
        "size": int(saved.get("size") or len(data)),
        "source": "generated",
        "versionNo": int(saved.get("versionNo") or 1),
        "deliverable": True,
        "previewOnly": preview_only,
        "origin": {
            "runId": str(runtime.get("run_id") or ""),
            "tool": "builtin.template_fill",
            "sourceFileId": source_file_id,
        },
    }
    if not receipt["id"]:
        raise ValueError("模板填写后的文件保存未返回 file_id")
    unmatched = [key for key in fields if key not in applied]
    return {
        "message": f"已填写《{receipt['filename']}》并保存到我的文件。",
        "file": receipt,
        "applied_fields": applied,
        "unmatched_fields": unmatched,
        "_generated_file_receipt": receipt,
    }


async def execute_builtin_tool(tool_id: str, args: dict, runtime: dict | None = None) -> Any:
    if tool_id == "builtin.datetime":
        fmt = str(args.get("format") or "yyyy-MM-dd HH:mm:ss")
        return _format_datetime(datetime_from_timestamp_in_agent_timezone(time.time()), fmt)

    if tool_id == "builtin.timestamp":
        unit = _normalize_timestamp_unit(args.get("unit"))
        now = time.time()
        return str(int(now * 1000) if unit == "milliseconds" else int(now))

    if tool_id == "builtin.time_convert":
        source_value = args.get("source_value")
        target_format = str(args.get("target_format") or "yyyy-MM-dd HH:mm:ss")
        parsed = _parse_datetime_value(source_value, str(args.get("source_format") or "") or None)
        return _format_converted_time(parsed, target_format)

    if tool_id == "builtin.timezone_convert":
        from_zone = _timezone_from_name(args.get("from_timezone"))
        to_zone = _timezone_from_name(args.get("to_timezone"))
        parsed = _parse_datetime_value(args.get("time_str"), str(args.get("source_format") or "") or None)
        source_time = parsed.replace(tzinfo=from_zone) if parsed.tzinfo is None else parsed.astimezone(from_zone)
        utc_time = source_time.astimezone(timezone.utc)
        target_time = utc_time.astimezone(to_zone)
        formatted = _format_datetime(target_time, str(args.get("target_format") or "yyyy-MM-dd HH:mm:ss"))
        return json.dumps(
            {
                "target_time": formatted,
                "target_timezone": _timezone_label(to_zone),
                "utc_timestamp": int(utc_time.timestamp()),
            },
            ensure_ascii=False,
        )

    if tool_id == "builtin.weekday":
        parsed = _parse_datetime_value(args.get("date_str"), str(args.get("source_format") or "") or None).date()
        start_index = _week_start_index(args.get("start_week"))
        return json.dumps(
            {
                "chinese_weekday": _CHINESE_WEEKDAYS[parsed.weekday()],
                "weekday_number": parsed.weekday() + 1,
                "week_index": _week_index_of_year(parsed, start_index),
            },
            ensure_ascii=False,
        )

    if tool_id == "builtin.json_extract":
        raw = args.get("json")
        data = raw if isinstance(raw, (dict, list)) else json.loads(str(raw or "null"))
        value = _extract_path(data, str(args.get("path") or ""))
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

    if tool_id == "builtin.json_parse":
        return _parse_json_object_or_array(args.get("json"))

    if tool_id == "builtin.document_export":
        return await _export_document_to_user_files(args, runtime)

    if tool_id == "builtin.document_typeset":
        from app.services.skills.document_typeset import typeset_document

        return await typeset_document(args, runtime)

    if tool_id == "builtin.document_convert":
        from app.services.skills.document_typeset import convert_document

        return await convert_document(args, runtime)

    if tool_id == "builtin.mindmap_export":
        return await _export_mindmap_to_user_files(args, runtime)

    if tool_id == "builtin.template_fill":
        return await _fill_template_to_user_files(args, runtime)

    if tool_id == "builtin.web_search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("搜索关键词不能为空")
        from app.services.knowledge import web_search_service

        audit_runtime = runtime or {}
        audit_kwargs = {
            "caller_user_id": str(audit_runtime.get("user_id") or ""),
            "caller_run_id": str(
                audit_runtime.get("audit_run_id") or audit_runtime.get("run_id") or ""
            ),
            "caller_thread_id": str(audit_runtime.get("thread_id") or ""),
            "caller_root_run_id": str(audit_runtime.get("audit_root_run_id") or ""),
            "caller_tool_call_id": str(audit_runtime.get("audit_parent_tool_call_id") or ""),
            "caller_parent_logical_call_id": str(
                audit_runtime.get("audit_parent_logical_call_id") or ""
            ),
            "caller_execution_segment": str(
                audit_runtime.get("audit_execution_segment") or ""
            ),
            "caller_newapi_key": str(audit_runtime.get("api_key") or ""),
        }
        audit_kwargs = {key: value for key, value in audit_kwargs.items() if value}

        return await web_search_service.search_web(
            query,
            start_index=1,
            with_images=_bool_arg(args.get("with_images")),
            **audit_kwargs,
        )

    if tool_id in {tool["id"] for tool in DATA_BUILTIN_TOOLS}:
        return await execute_data_builtin_tool(tool_id, args, runtime)

    raise ValueError(f"未知系统工具: {tool_id}")
