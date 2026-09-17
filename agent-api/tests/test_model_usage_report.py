from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.agent_harness import model_usage_report as report_service


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def test_report_sql_projects_canonical_db_sequence_to_compatibility_key():
    assert "attempt.run_request_sequence AS run_sequence" in str(
        report_service._ATTEMPT_QUERY
    )


def _filters(**overrides) -> report_service.ModelUsageReportFilters:
    values = {
        "start_at": NOW - timedelta(hours=1),
        "end_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return report_service.ModelUsageReportFilters(**values)


def _logical(
    logical_id: str,
    *,
    purpose: str = "main_loop",
    root_run_id: str = "root-1",
    run_id: str = "root-1",
    outcome: str | None = "success",
    key_id: str | None = None,
) -> dict:
    return {
        "id": logical_id,
        "run_id": run_id,
        "root_run_id": root_run_id,
        "model": "deepseek-v4-flash",
        "transport": "responses",
        "purpose": purpose,
        "provider_key_fingerprint": key_id,
        "root_run_exists": True,
        "root_user_id": "user-1",
        "run_user_id": "user-1",
        "root_status": "completed",
        "root_outcome": outcome,
        "root_completed_at": NOW + timedelta(minutes=10),
        "created_at": NOW,
    }


def _attempt(
    attempt_id: str,
    logical_id: str,
    *,
    purpose: str = "main_loop",
    root_run_id: str = "root-1",
    run_id: str = "root-1",
    sequence: int = 1,
    created_at: datetime = NOW,
    outcome: str | None = "success",
    **overrides,
) -> dict:
    row = {
        "id": attempt_id,
        "logical_call_id": logical_id,
        "run_id": run_id,
        "root_run_id": root_run_id,
        "run_sequence": sequence,
        "attempt_kind": "initial",
        "model": "deepseek-v4-flash",
        "transport": "responses",
        "purpose": purpose,
        "provider_key_fingerprint": overrides.pop("key_id", None),
        "trusted_usage": True,
        "terminal_seen": True,
        "terminal_status": "completed",
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": None,
        "cache_read_tokens": 80,
        "cache_miss_tokens": 20,
        "cache_write_tokens": None,
        "provider_amount_raw": None,
        "provider_amount_unit": None,
        "unknown_provider_charge": False,
        "billed_but_not_committed": False,
        "prefix_diagnostics": {"available": False},
        "root_run_exists": True,
        "root_user_id": "user-1",
        "run_user_id": "user-1",
        "root_status": "completed",
        "root_outcome": outcome,
        "root_completed_at": NOW + timedelta(minutes=10),
        "created_at": created_at,
    }
    row.update(overrides)
    return row


def test_aggregation_preserves_null_usage_and_exact_amount_strings():
    attempt = _attempt(
        "at-1",
        "lg-1",
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        cache_read_tokens=None,
        cache_miss_tokens=None,
        cache_write_tokens=None,
        provider_amount_raw="0.0012300",
        provider_amount_unit="USD",
    )
    second = _attempt(
        "at-2",
        "lg-2",
        sequence=2,
        provider_amount_raw="1e-3",
        provider_amount_unit="credits",
    )
    result = report_service.aggregate_model_usage(
        [_logical("lg-1"), _logical("lg-2")],
        [attempt, second],
        _filters(),
    )

    root = result["roots"][0]
    assert root["tokens"]["reasoning_tokens"] == {
        "total": None,
        "reported_attempts": 0,
    }
    assert root["tokens"]["input_tokens"] == {
        "total": 100,
        "reported_attempts": 1,
    }
    assert root["uncached_equivalent"] is None
    assert root["provider_amounts"] == [
        {"raw": "0.0012300", "unit": "USD", "attempt_count": 1},
        {"raw": "1e-3", "unit": "credits", "attempt_count": 1},
    ]
    assert result["schema_capabilities"]["user_id"]["available"] is True
    assert result["schema_capabilities"]["key_id"]["available"] is True
    assert result["groups"][0]["key_id"] is None


def test_aggregation_classifies_auxiliary_descendant_post_terminal_unknown_and_lcp():
    completed_at = NOW - timedelta(minutes=1)
    main = _attempt(
        "at-main",
        "lg-main",
        prefix_diagnostics={
            "available": True,
            "previous_item_count": 8,
            "lcp_item_count": 7,
            "tool_schema_equal": True,
        },
        unknown_provider_charge=True,
        root_completed_at=completed_at,
    )
    auxiliary = [
        _attempt(
            f"at-aux-{index}",
            f"lg-aux-{index}",
            purpose="public_preamble" if index == 0 else "title",
            run_id="child-1" if index == 1 else "root-1",
            sequence=index + 2,
            created_at=(NOW if index == 2 else NOW - timedelta(minutes=2)),
            root_completed_at=completed_at,
            reasoning_tokens=5 if index == 0 else None,
        )
        for index in range(3)
    ]
    logical = [
        _logical("lg-main"),
        _logical("lg-aux-0", purpose="public_preamble"),
        _logical("lg-aux-1", purpose="title", run_id="child-1"),
        _logical("lg-aux-2", purpose="title"),
    ]
    result = report_service.aggregate_model_usage(logical, [main, *auxiliary], _filters())
    root = result["roots"][0]

    assert root["auxiliary_attempt_ratio"] == 0.75
    assert root["descendant_attempt_count"] == 1
    assert root["post_terminal_attempt_count"] == 2
    assert root["unknown_provider_charge_attempt_count"] == 1
    assert root["main_lcp_incomplete_attempt_count"] == 1
    assert root["lcp"]["complete_ratio"] == 0.0
    assert root["preamble_reasoning_tokens_total"] == 5
    codes = {alert["code"] for alert in result["alerts"]}
    assert "auxiliary_share_high" in codes
    assert "main_lcp_incomplete" in codes
    assert "preamble_reasoning" in codes
    assert "unknown_provider_charge" in codes


def test_uncached_equivalent_requires_complete_coverage_and_clamps_uncached_input():
    complete = report_service.aggregate_model_usage(
        [_logical("lg-1")],
        [_attempt(
            "at-1",
            "lg-1",
            input_tokens=100,
            cache_read_tokens=80,
            output_tokens=20,
        )],
        _filters(),
    )
    assert complete["groups"][0]["uncached_equivalent"] == 40
    assert complete["roots"][0]["uncached_equivalent"] == 40

    clamped = report_service.aggregate_model_usage(
        [_logical("lg-1")],
        [_attempt(
            "at-1",
            "lg-1",
            input_tokens=50,
            cache_read_tokens=80,
            output_tokens=10,
        )],
        _filters(),
    )
    assert clamped["roots"][0]["uncached_equivalent"] == 10

    partial = report_service.aggregate_model_usage(
        [_logical("lg-1")],
        [
            _attempt("at-1", "lg-1", input_tokens=100, cache_read_tokens=80, output_tokens=20),
            _attempt(
                "at-2",
                "lg-1",
                sequence=2,
                input_tokens=100,
                cache_read_tokens=None,
                output_tokens=20,
            ),
        ],
        _filters(),
    )
    assert partial["roots"][0]["uncached_equivalent"] is None

    mixed = report_service.aggregate_model_usage(
        [_logical("lg-1"), _logical("lg-2")],
        [
            _attempt(
                "at-1", "lg-1", input_tokens=50, cache_read_tokens=80, output_tokens=0
            ),
            _attempt(
                "at-2", "lg-2", sequence=2,
                input_tokens=100, cache_read_tokens=0, output_tokens=0,
            ),
        ],
        _filters(),
    )
    assert mixed["roots"][0]["uncached_equivalent"] == 100


def test_cache_read_ratio_requires_paired_coverage_for_every_attempt():
    complete = report_service.aggregate_model_usage(
        [_logical("lg-1"), _logical("lg-2")],
        [
            _attempt("at-1", "lg-1", cache_read_tokens=80, cache_miss_tokens=20),
            _attempt(
                "at-2", "lg-2", sequence=2,
                cache_read_tokens=45, cache_miss_tokens=5,
            ),
        ],
        _filters(),
    )
    assert complete["roots"][0]["cache_read_ratio"] == 125 / 150

    partial = report_service.aggregate_model_usage(
        [_logical("lg-1"), _logical("lg-2")],
        [
            _attempt("at-1", "lg-1", cache_read_tokens=80, cache_miss_tokens=None),
            _attempt(
                "at-2", "lg-2", sequence=2,
                cache_read_tokens=None, cache_miss_tokens=20,
            ),
        ],
        _filters(),
    )
    assert partial["roots"][0]["cache_read_ratio"] is None


def test_approved_alert_matrix_includes_all_fixed_conditions():
    root = {
        "root_run_id": "root-alert",
        "user_ids": ["user-1"],
        "models": ["m"],
        "outcomes": ["success"],
        "logical_call_count": 4,
        "attempt_count": 5,
        "attempt_per_logical": 1.25,
        "preamble_reasoning_tokens_total": 1,
        "terminal_usage_missing_attempt_count": 1,
        "main_lcp_incomplete_attempt_count": 1,
        "compaction_max_attempts_per_logical_call": 4,
        "compaction_attempts_by_logical_call": [
            {"logical_call_id": "lg-compact", "attempt_count": 4},
        ],
        "auxiliary_attempt_ratio": 0.6,
        "auxiliary_uncached_equivalent_ratio": 0.6,
        "auxiliary_uncached_equivalent": 60,
        "uncached_equivalent": 100,
        "auxiliary_attempt_count": 3,
        "unknown_provider_charge_attempt_count": 1,
        "orphan_root_attempt_count": 1,
        "orphan_root_logical_count": 0,
        "duplicate_run_sequences": [
            {"run_id": "run-1", "run_sequence": 2, "rows": 2},
        ],
    }
    alerts = report_service.calculate_approved_alerts([root])
    assert {item["code"] for item in alerts} == {
        "attempt_amplification",
        "preamble_reasoning",
        "terminal_usage_missing",
        "main_lcp_incomplete",
        "compaction_excess",
        "auxiliary_share_high",
        "unknown_provider_charge",
        "orphan_root",
        "duplicate_run_sequence",
    }
    assert all(item["policy"] == report_service.APPROVED_ALERT_POLICY for item in alerts)


def test_approved_alert_thresholds_are_strictly_greater_than():
    alerts = report_service.calculate_approved_alerts([{
        "root_run_id": "root-boundary",
        "attempt_per_logical": 1.0,
        "preamble_reasoning_tokens_total": 0,
        "terminal_usage_missing_attempt_count": 0,
        "main_lcp_incomplete_attempt_count": 0,
        "compaction_max_attempts_per_logical_call": 3,
        "compaction_attempts_by_logical_call": [
            {"logical_call_id": "lg-compact", "attempt_count": 3},
        ],
        "auxiliary_attempt_ratio": 0.5,
        "auxiliary_uncached_equivalent_ratio": 0.5,
        "unknown_provider_charge_attempt_count": 0,
        "orphan_root_attempt_count": 0,
        "orphan_root_logical_count": 0,
        "duplicate_run_sequences": [],
    }])
    assert alerts == []


def test_compaction_alert_counts_attempts_per_logical_call_not_root_call_count():
    distinct_logical = [
        _logical(f"lg-compact-{index}", purpose="compaction_live")
        for index in range(4)
    ]
    one_each = [
        _attempt(
            f"at-compact-{index}",
            f"lg-compact-{index}",
            purpose="compaction_live",
            sequence=index + 1,
        )
        for index in range(4)
    ]
    distinct = report_service.aggregate_model_usage(distinct_logical, one_each, _filters())
    distinct_root = distinct["roots"][0]
    assert distinct_root["compaction_logical_call_count"] == 4
    assert distinct_root["compaction_max_attempts_per_logical_call"] == 1
    assert "compaction_excess" not in {item["code"] for item in distinct["alerts"]}

    repeated = [
        _attempt(
            f"at-retry-{index}",
            "lg-compact",
            purpose="compaction_live",
            sequence=index + 1,
        )
        for index in range(4)
    ]
    amplified = report_service.aggregate_model_usage(
        [_logical("lg-compact", purpose="compaction_live")],
        repeated,
        _filters(),
    )
    amplified_root = amplified["roots"][0]
    assert amplified_root["compaction_logical_call_count"] == 1
    assert amplified_root["compaction_max_attempts_per_logical_call"] == 4
    alert = next(item for item in amplified["alerts"] if item["code"] == "compaction_excess")
    assert alert["root_run_id"] == "root-1"
    assert alert["evidence"]["logical_calls"] == [
        {"logical_call_id": "lg-compact", "attempt_count": 4},
    ]

    persisted_logical = _logical("lg-persisted", purpose="compaction_background")
    persisted_logical["attempt_count"] = 4
    persisted = report_service.aggregate_model_usage(
        [persisted_logical],
        [],
        _filters(),
    )
    persisted_alert = next(
        item for item in persisted["alerts"] if item["code"] == "compaction_excess"
    )
    assert persisted_alert["evidence"]["logical_calls"] == [
        {"logical_call_id": "lg-persisted", "attempt_count": 4},
    ]


def test_duplicate_sequence_and_orphan_root_are_computed_from_rows():
    first = _attempt("at-1", "lg-1", sequence=3)
    second = _attempt("at-2", "lg-2", sequence=3)
    orphan = _attempt(
        "at-3",
        "lg-3",
        root_run_id="missing-root",
        sequence=4,
        root_run_exists=False,
        root_user_id=None,
        run_user_id="known-child-user",
        outcome=None,
    )
    result = report_service.aggregate_model_usage([], [first, second, orphan], _filters())
    roots = {item["root_run_id"]: item for item in result["roots"]}
    assert roots["root-1"]["duplicate_run_sequences"] == [
        {"run_id": "root-1", "run_sequence": 3, "rows": 2},
    ]
    assert roots["missing-root"]["orphan_root_attempt_count"] == 1
    assert roots["missing-root"]["user_ids"] == ["known-child-user"]
    codes = {(item["root_run_id"], item["code"]) for item in result["alerts"]}
    assert ("root-1", "duplicate_run_sequence") in codes
    assert ("missing-root", "orphan_root") in codes


def test_key_filter_uses_non_reversible_provider_fingerprint():
    result = report_service.aggregate_model_usage(
        [
            _logical("lg-1", key_id="key-1"),
            _logical("lg-2", key_id="key-2"),
        ],
        [
            _attempt("at-1", "lg-1", key_id="key-1"),
            _attempt("at-2", "lg-2", sequence=2, key_id="key-2"),
        ],
        _filters(key_ids=("key-1",)),
    )
    assert result["status"] == "ok"
    assert result["source_rows"] == {"logical_calls": 1, "attempts": 1}
    assert result["groups"][0]["key_id"] == "key-1"
    assert result["warnings"] == []


def test_provider_amounts_keep_duplicate_attempt_count():
    result = report_service.aggregate_model_usage(
        [_logical("lg-1"), _logical("lg-2")],
        [
            _attempt(
                "at-1", "lg-1", provider_amount_raw="0.0012300",
                provider_amount_unit="USD",
            ),
            _attempt(
                "at-2", "lg-2", sequence=2,
                provider_amount_raw="0.0012300", provider_amount_unit="USD",
            ),
        ],
        _filters(),
    )
    assert result["roots"][0]["provider_amounts"] == [
        {"raw": "0.0012300", "unit": "USD", "attempt_count": 2},
    ]


def test_auxiliary_alert_uses_uncached_token_share_not_attempt_share():
    cheap_aux = report_service.aggregate_model_usage(
        [_logical("lg-main"), _logical("lg-title", purpose="title")],
        [
            _attempt(
                "at-main", "lg-main", input_tokens=1_000,
                cache_read_tokens=0, output_tokens=100,
            ),
            _attempt(
                "at-title", "lg-title", purpose="title", sequence=2,
                input_tokens=10, cache_read_tokens=0, output_tokens=1,
            ),
        ],
        _filters(),
    )
    cheap_root = cheap_aux["roots"][0]
    assert cheap_root["auxiliary_attempt_ratio"] == 0.5
    assert cheap_root["auxiliary_uncached_equivalent_ratio"] == 11 / 1111
    assert "auxiliary_share_high" not in {item["code"] for item in cheap_aux["alerts"]}

    expensive_aux_attempts = [
        _attempt(
            "at-compact", "lg-compact", purpose="compaction_live",
            input_tokens=2_000, cache_read_tokens=0, output_tokens=200,
        ),
        *[
            _attempt(
                f"at-main-{index}", f"lg-main-{index}", sequence=index + 2,
                input_tokens=10, cache_read_tokens=0, output_tokens=1,
            )
            for index in range(3)
        ],
    ]
    expensive = report_service.aggregate_model_usage(
        [
            _logical("lg-compact", purpose="compaction_live"),
            *[_logical(f"lg-main-{index}") for index in range(3)],
        ],
        expensive_aux_attempts,
        _filters(),
    )
    expensive_root = expensive["roots"][0]
    assert expensive_root["auxiliary_attempt_ratio"] == 0.25
    assert expensive_root["auxiliary_uncached_equivalent_ratio"] > 0.5
    assert "auxiliary_share_high" in {item["code"] for item in expensive["alerts"]}


def test_invalid_time_window_returns_structured_status():
    result = report_service.aggregate_model_usage(
        [],
        [],
        _filters(start_at=NOW, end_at=NOW),
    )
    assert result["status"] == "invalid_filter"
    assert result["warnings"] == ["invalid_time_window"]


def test_read_service_is_fail_open_without_runtime_database(monkeypatch):
    monkeypatch.setattr(report_service, "runtime_session", lambda: None)
    result = asyncio.run(report_service.build_model_usage_report(_filters()))
    assert result["status"] == "unavailable"
    assert result["source_rows"] == {"logical_calls": 0, "attempts": 0}
    assert result["alerts"] == []


def test_read_service_uses_selects_only_and_aggregates_mappings(monkeypatch):
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement, _params):
            self.calls += 1
            return Result(
                [_attempt("at-1", "lg-1")]
                if self.calls == 1
                else [_logical("lg-1")]
            )

    session = Session()
    monkeypatch.setattr(report_service, "runtime_session", lambda: lambda: session)
    result = asyncio.run(report_service.build_model_usage_report(_filters()))
    assert result["status"] == "ok"
    assert result["source_rows"] == {"logical_calls": 1, "attempts": 1}
    assert result["roots"][0]["tokens"]["input_tokens"]["total"] == 100
    assert session.calls == 2
