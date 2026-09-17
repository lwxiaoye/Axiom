"""Safe read models and audit writes for workflow administrator governance."""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from app.models import WorkflowAdminAudit


_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "api_key", "headers")
_DEPENDENCY_KEY_PARTS = ("dataset", "knowledge", "skill", "tool", "agent", "app")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _is_sensitive_key(key: str) -> bool:
    return any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)


def redact_sensitive_values(value: Any, key: str = "") -> Any:
    """Return a safe representation without credential-bearing config values."""
    if key and _is_sensitive_key(key):
        return "***"
    if isinstance(value, Mapping):
        return {str(child_key): redact_sensitive_values(child, str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_values(child) for child in value]
    return value


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _model_name(config: Mapping[str, Any]) -> str:
    for key in ("model", "modelId", "model_id"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _node_summary(definition_json: Any) -> tuple[list[str], list[str]]:
    definition = _json_object(definition_json)
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return [], []
    node_types: list[str] = []
    dependency_ids: list[str] = []

    def add_dependency(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, (str, int)) and str(item).strip() and str(item) not in dependency_ids:
                dependency_ids.append(str(item))

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                child_key_str = str(child_key)
                lowered = child_key_str.lower()
                if any(part in lowered for part in _DEPENDENCY_KEY_PARTS) and (lowered.endswith("id") or lowered.endswith("ids")):
                    add_dependency(child_value)
                visit(child_value, child_key_str)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)

    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_type = node.get("type") or node.get("flowNodeType") or node.get("nodeType")
        if isinstance(node_type, str) and node_type.strip() and node_type not in node_types:
            node_types.append(node_type)
        visit(node)
    return node_types, dependency_ids


def _version_summary(version: Any) -> dict[str, Any] | None:
    if version is None:
        return None
    config = _json_object(getattr(version, "config_json", None))
    node_types, dependency_ids = _node_summary(getattr(version, "definition_json", None))
    return {
        "versionNo": int(getattr(version, "version_no", 0) or 0),
        "status": str(getattr(version, "status", "") or ""),
        "changeNote": str(getattr(version, "change_note", "") or ""),
        "model": _model_name(config),
        "nodeTypes": node_types,
        "dependencyIds": dependency_ids,
        "visibleRoleIds": _string_list(getattr(version, "visible_role_ids", None)),
        "visibleDeptIds": _string_list(getattr(version, "visible_dept_ids", None)),
    }


def build_admin_app_summary(app: Any, definition: Any, live_version: Any) -> dict[str, Any]:
    """Build a compact, credential-safe administration read model."""
    config = _json_object(getattr(app, "config_json", None))
    node_types, dependency_ids = _node_summary(getattr(definition, "published_json", None))
    live_summary = _version_summary(live_version)
    return {
        "model": _model_name(config) or (live_summary or {}).get("model", ""),
        "nodeTypes": node_types or (live_summary or {}).get("nodeTypes", []),
        "dependencyIds": dependency_ids or (live_summary or {}).get("dependencyIds", []),
        "liveVersion": live_summary,
        "publishedVersion": int(getattr(definition, "published_version", 0) or 0),
        "sanitizedConfig": redact_sensitive_values(config),
    }


def build_version_diff(base_version: Any, target_version: Any) -> dict[str, Any]:
    """Compare two immutable versions through their safe summaries only."""
    base = _version_summary(base_version) or {}
    target = _version_summary(target_version) or {}
    compared_fields = ("model", "nodeTypes", "dependencyIds", "visibleRoleIds", "visibleDeptIds")
    changed_fields = [field for field in compared_fields if base.get(field) != target.get(field)]
    return {
        "baseVersionNo": base.get("versionNo", 0),
        "targetVersionNo": target.get("versionNo", 0),
        "changedFields": changed_fields,
        "base": base,
        "target": target,
    }


def record_app_audit(
    session: Any,
    *,
    app: Any,
    action: str,
    actor: Any,
    reason: str = "",
    target_user_id: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> WorkflowAdminAudit:
    """Attach one credential-safe application operation to the caller's transaction."""
    audit = WorkflowAdminAudit(
        id=uuid.uuid4().hex,
        tenant_id=str(getattr(app, "tenant_id", "0") or "0"),
        app_id=str(getattr(app, "id", "")),
        action=action,
        actor_user_id=str(getattr(actor, "user_id", "")),
        actor_username=str(getattr(actor, "real_name", "") or getattr(actor, "username", "")),
        target_user_id=target_user_id,
        reason=reason.strip()[:512],
        before_json=json.dumps(redact_sensitive_values(dict(before or {})), ensure_ascii=False),
        after_json=json.dumps(redact_sensitive_values(dict(after or {})), ensure_ascii=False),
    )
    session.add(audit)
    return audit


# 保留旧名称，避免正在演进的调用方在本次语义升级期间失效。
record_admin_audit = record_app_audit
