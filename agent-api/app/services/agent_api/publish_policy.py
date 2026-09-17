"""Pure validation for the publisher-owned API release channel.

The checks here run when a version snapshot is created, rather than at every
execution.  Runtime enforcement remains fail-closed in the API execution
service added later.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException


ALLOWED_CHANNELS = frozenset({"marketplace", "api"})
MAX_EMBED_ORIGINS = 10


class ApiPublishCapabilityError(ValueError):
    """A workflow cannot safely run without a platform-user interaction."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _items(raw: object) -> Iterable[Any]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        value = raw.strip()
        if value.startswith("["):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return (raw,)
            return decoded if isinstance(decoded, list) else (decoded,)
        return (raw,)
    if isinstance(raw, (list, tuple, set, frozenset)):
        return raw
    return (raw,)


def normalize_publish_channels(raw: object) -> tuple[str, ...]:
    """Return the ordered, de-duplicated allowed channels for a snapshot."""
    channels = tuple(dict.fromkeys(
        str(item).strip().lower() for item in _items(raw) if str(item).strip()
    ))
    if not channels or not set(channels).issubset(ALLOWED_CHANNELS):
        raise HTTPException(400, "发布通道必须为 marketplace、api 中至少一项")
    return channels


def published_channels(raw: object) -> tuple[str, ...]:
    """Read a stored channel snapshot, preserving pre-migration marketplace apps."""
    if raw is None or raw == "":
        return ("marketplace",)
    return normalize_publish_channels(raw)


def has_publish_channel(raw: object, channel: str) -> bool:
    return channel in published_channels(raw)


def normalize_embed_origins(raw: object) -> tuple[str, ...]:
    """Normalize exact HTTPS origins; wildcard and path matching are forbidden."""
    result: list[str] = []
    for item in _items(raw):
        origin = str(item or "").strip()
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(400, "嵌入来源必须是精确的 HTTPS 域名") from exc
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not host
            or "*" in host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise HTTPException(400, "嵌入来源必须是精确的 HTTPS 域名")
        host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
        canonical = f"https://{host_part}"
        if port not in (None, 443):
            canonical = f"{canonical}:{port}"
        if canonical not in result:
            result.append(canonical)
    if len(result) > MAX_EMBED_ORIGINS:
        raise HTTPException(400, f"嵌入来源最多 {MAX_EMBED_ORIGINS} 个")
    return tuple(result)


_RESTRICTED_NODE_TYPES = {
    "userselect": ("hitl", "API 发布不支持 hitl 交互节点"),
    "forminput": ("hitl", "API 发布不支持 hitl 交互节点"),
    "customfeedback": ("hitl", "API 发布不支持 hitl 交互节点"),
    "readfiles": ("user_file", "API 发布不支持调用者 user_file"),
    "browser": ("browser", "API 发布不支持 browser 工具"),
    "desktop": ("desktop", "API 发布不支持 desktop 工具"),
    "computer": ("desktop", "API 发布不支持 desktop 工具"),
}
_RESTRICTED_TEXT_MARKERS = (
    ("oauth", "oauth", "API 发布不支持 oauth 用户连接器"),
    ("browser", "browser", "API 发布不支持 browser 工具"),
    ("desktop", "desktop", "API 发布不支持 desktop 工具"),
    ("workspace", "workspace", "API 发布不支持调用者 workspace"),
    ("userfile", "user_file", "API 发布不支持调用者 user_file"),
    ("user_file", "user_file", "API 发布不支持调用者 user_file"),
    ("approval", "approval", "API 发布不支持需要审批的副作用"),
    ("human_in_the_loop", "hitl", "API 发布不支持 hitl 交互节点"),
    ("hitl", "hitl", "API 发布不支持 hitl 交互节点"),
)


def _raise_for_restricted_marker(text: str) -> None:
    lowered = text.lower()
    for marker, code, message in _RESTRICTED_TEXT_MARKERS:
        if marker in lowered:
            raise ApiPublishCapabilityError(code, message)


def _workflow_graph(workflow_json: object) -> dict[str, Any]:
    if isinstance(workflow_json, str):
        try:
            workflow_json = json.loads(workflow_json)
        except json.JSONDecodeError as exc:
            raise ApiPublishCapabilityError("invalid_workflow", "API 发布工作流 JSON 无效") from exc
    if not isinstance(workflow_json, dict):
        raise ApiPublishCapabilityError("invalid_workflow", "API 发布工作流必须是对象")
    graph = workflow_json.get("fastgpt", workflow_json)
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise ApiPublishCapabilityError("invalid_workflow", "API 发布工作流缺少节点")
    return graph


def validate_api_workflow_capabilities(workflow_json: object, *, external_context_ready: bool = False) -> None:
    """Reject static capabilities that require the platform user's presence.

    This intentionally errs on the restrictive side.  A future execution
    service still verifies runtime-only capabilities before starting a node.
    """
    graph = _workflow_graph(workflow_json)
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("flowNodeType") or node.get("nodeType") or "").strip().lower()
        restricted = _RESTRICTED_NODE_TYPES.get(node_type)
        if restricted and not external_context_ready:
            raise ApiPublishCapabilityError(*restricted)
        # Do not scan ordinary prompt/input text: an agent may legitimately
        # discuss a browser or workspace.  Capability markers only matter in
        # tool/connector configuration or in an explicit user-file reference.
        for config_key in (
            "toolConfig", "pluginConfig", "connectorConfig", "authConfig",
            "authType", "toolType", "capability", "provider",
        ):
            if config_key in node and not external_context_ready:
                _raise_for_restricted_marker(json.dumps(node[config_key], ensure_ascii=False, default=str))
        for input_item in node.get("inputs") or []:
            if not isinstance(input_item, dict):
                continue
            input_key = str(input_item.get("key") or "")
            if not external_context_ready and any(marker in input_key.lower() for marker in ("workspace", "userfile", "user_file")):
                _raise_for_restricted_marker(input_key)
            value = input_item.get("value")
            if not external_context_ready and (
                isinstance(value, list)
                and len(value) == 2
                and all(isinstance(item, str) for item in value)
            ):
                _raise_for_restricted_marker(" ".join(value))
