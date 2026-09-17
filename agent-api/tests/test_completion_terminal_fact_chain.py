from __future__ import annotations

import json

import pytest

from app.services.agent_harness import run_store
from app.services.agent_harness import terminal_report


def test_model_claim_is_not_stored_as_terminal_reason():
    report = terminal_report.build_run_terminal_report(
        run_id="run-1",
        phase="waiting_system",
        run_disposition="waiting_system",
        terminal_reason="模型说它因为用户未确认而停止",
        fact_source="model",
        model_claim="模型说它因为用户未确认而停止",
    )
    assert report["terminal_reason"] == ""
    assert report["facts"]["reason"] == ""
    assert report["model_claim"]
    assert report["facts"]["source"] == "model"


def test_legacy_terminal_report_remains_readable_without_becoming_verified():
    fact = terminal_report.compact_terminal_fact({
        "event": "run_terminal_report",
        "run_id": "old-run",
        "phase": "failed",
        "run_disposition": "failed",
        "terminal_reason": "历史运行记录",
    })
    assert fact == {
        "source": "legacy_terminal_report",
        "verified": False,
        "resolution": "failed",
        "reason": "历史运行记录",
        "reason_codes": [],
        "existing_evidence": [],
        "unmet_conditions": [],
    }


@pytest.mark.asyncio
async def test_emit_run_terminal_report_persists_runstate_fact(monkeypatch):
    saved: dict = {}

    async def fake_enrich(_run_id):
        return {"event_types": [], "last_tool": {}}, {"resumed": False}, {}, 0, {}

    async def fake_patch(_run_id, patch, **_kwargs):
        saved.update(patch)
        return {"state": patch, "version": 2}

    monkeypatch.setattr(terminal_report, "_enrich_from_runtime", fake_enrich)
    monkeypatch.setattr(terminal_report, "log_run_terminal_report", lambda report: report)
    monkeypatch.setattr(run_store, "patch_run_state", fake_patch)

    report = await terminal_report.emit_run_terminal_report(
        "run-1",
        phase="waiting_system",
        run_disposition="waiting_system",
        terminal_reason="external_dependency_temporarily_unavailable",
        fact_source="completion_verifier",
        verified=False,
        reason_code="external_dependency_temporarily_unavailable",
        extra={
            "completion_verification": {
                "unmet_conditions": ["provider unavailable"],
                "evidence": [{"tool_name": "search_web", "error_code": "timeout"}],
            },
        },
    )

    assert report["schema_version"] == 2
    assert saved["terminal_report"]["facts"]["resolution"] == "waiting_system"
    projection = saved["completion_observation"]
    assert projection["kind"] == "run_terminal_report"
    assert projection["unmet_conditions"] == ["provider unavailable"]
    assert projection["existing_evidence"][0]["error_code"] == "timeout"
    # The stored JSON is actually serializable and does not rely on a log parser.
    json.dumps(saved["terminal_report"], ensure_ascii=False)
