import json
from types import SimpleNamespace

from app.services.workflows.admin_governance_service import (
    build_admin_app_summary,
    build_version_diff,
)


def _version(model: str, node_types: list[str]):
    return SimpleNamespace(
        version_no=1,
        status="approved",
        change_note="update",
        submitted_by_name="author",
        submitted_at=None,
        reviewed_at=None,
        config_json=json.dumps({"model": model, "apiKey": "do-not-leak"}),
        definition_json=json.dumps({"nodes": [{"type": value} for value in node_types]}),
        visible_role_ids='["role-1"]',
        visible_dept_ids='["dept-1"]',
    )


def test_admin_summary_keeps_dependencies_and_masks_sensitive_values():
    summary = build_admin_app_summary(
        SimpleNamespace(config_json=json.dumps({"model": "gpt", "headers": {"Authorization": "Bearer secret"}})),
        SimpleNamespace(
            published_json=json.dumps(
                {
                    "nodes": [
                        {"type": "http", "config": {"url": "https://api.example", "token": "secret"}},
                        {"type": "knowledge", "config": {"datasetId": "dataset-1"}},
                    ]
                }
            ),
            published_version=3,
        ),
        _version("gpt", ["http", "knowledge"]),
    )

    assert summary["model"] == "gpt"
    assert summary["nodeTypes"] == ["http", "knowledge"]
    assert summary["dependencyIds"] == ["dataset-1"]
    assert summary["liveVersion"]["versionNo"] == 1
    assert "secret" not in json.dumps(summary)
    assert "do-not-leak" not in json.dumps(summary)


def test_version_diff_reports_summary_field_changes_without_raw_json():
    diff = build_version_diff(_version("gpt-4o", ["http"]), _version("gpt-5", ["http", "knowledge"]))

    assert diff["baseVersionNo"] == 1
    assert diff["targetVersionNo"] == 1
    assert diff["changedFields"] == ["model", "nodeTypes"]
    assert "definitionJson" not in diff
    assert "do-not-leak" not in json.dumps(diff)
