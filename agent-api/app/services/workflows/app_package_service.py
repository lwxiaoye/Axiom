import json
from typing import Any

from fastapi import HTTPException


PACKAGE_FORMAT = "axiom.agent-app"
PACKAGE_VERSION = 1
AGENT_PACKAGE_TYPES = {"simple", "chatAgent", "workflow"}


def _read_attr(source: Any, attr: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(attr, default)
    return getattr(source, attr, default)


def build_export_package(app: Any, definition: Any = None) -> dict:
    ai_app_type = _read_attr(app, "ai_app_type") or _read_attr(app, "aiAppType") or "workflow"
    if ai_app_type not in AGENT_PACKAGE_TYPES:
        raise HTTPException(400, "仅支持导出智能体应用")
    package = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "app": {
            "aiAppType": ai_app_type,
            "name": _read_attr(app, "name") or "未命名智能体",
            "description": _read_attr(app, "description") or "",
            "appCategory": _read_attr(app, "app_category") or _read_attr(app, "appCategory"),
            "appIcon": _read_attr(app, "app_icon") or _read_attr(app, "appIcon") or "",
            "configJson": _read_attr(app, "config_json") or _read_attr(app, "configJson") or "{}",
        },
        "definition": {
            "draftJson": None,
            "publishedJson": None,
            "publishedVersion": 0,
        },
    }
    if definition:
        package["definition"] = {
            "draftJson": _read_attr(definition, "draft_json") or _read_attr(definition, "draftJson"),
            "publishedJson": _read_attr(definition, "published_json") or _read_attr(definition, "publishedJson"),
            "publishedVersion": int(
                _read_attr(definition, "published_version") or _read_attr(definition, "publishedVersion") or 0
            ),
        }
    return package


def parse_import_package(raw: bytes | str | dict) -> dict:
    if isinstance(raw, bytes):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(400, "导入文件必须是 UTF-8 JSON") from exc
    elif isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception as exc:
            raise HTTPException(400, "导入文件必须是 JSON") from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise HTTPException(400, "导入文件格式不正确")
    if payload.get("format") != PACKAGE_FORMAT or payload.get("version") != PACKAGE_VERSION:
        raise HTTPException(400, "不支持的智能体配置包版本")

    app = payload.get("app")
    if not isinstance(app, dict):
        raise HTTPException(400, "导入文件缺少应用信息")
    ai_app_type = app.get("aiAppType") or "workflow"
    if ai_app_type not in AGENT_PACKAGE_TYPES:
        raise HTTPException(400, "仅支持导入智能体应用")
    name = str(app.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "导入文件缺少智能体名称")

    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    draft_json = definition.get("draftJson") or definition.get("publishedJson")
    return {
        "app": {
            "aiAppType": ai_app_type,
            "name": name,
            "description": str(app.get("description") or "").strip(),
            "appCategory": app.get("appCategory"),
            "appIcon": app.get("appIcon") or "",
            "configJson": app.get("configJson") or "{}",
        },
        "definition": {
            "draftJson": draft_json,
            "publishedJson": None,
            "publishedVersion": 0,
        },
    }


def make_copy_name(name: str | None) -> str:
    base = str(name or "").strip() or "未命名智能体"
    return f"{base} 副本"


def make_import_name(name: str | None, timestamp: str) -> str:
    base = str(name or "").strip() or "未命名智能体"
    return f"{base}_导入_{timestamp}"
