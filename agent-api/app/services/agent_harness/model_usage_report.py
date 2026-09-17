"""Read-only aggregation and approved alerts for the durable model-usage ledger.

The report deliberately keeps missing Provider usage as ``None`` and preserves
``provider_amount_raw`` as text.  Key grouping uses a non-reversible SHA-256 identifier;
the raw Provider credential never enters the Runtime ledger.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text

from app.core.runtime_db import runtime_session


REPORT_VERSION = "model-usage-report-v1"
APPROVED_ALERT_POLICY = "approved-model-usage-alerts-v1"
REPORT_QUERY_TIMEOUT_SECONDS = 2.0
RootUsageAggregate = dict[str, Any]

PRIMARY_PURPOSES = frozenset({
    "main_loop",
    "plain_answer",
    "subagent_model",
    "workflow_node",
})
AUXILIARY_PURPOSES = frozenset({
    "public_preamble",
    "research_commentary",
    "compaction_live",
    "compaction_preflight",
    "compaction_background",
    "router",
    "title",
    "memory_extract",
    "memory_summary",
    "paid_search",
    "browser_digest",
    "acceptance",
    "parent_summary",
    "tool_internal",
})
COMPACTION_PURPOSES = frozenset({
    "compaction_live",
    "compaction_preflight",
    "compaction_background",
})

TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_tokens",
    "cache_miss_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class ModelUsageReportFilters:
    """Inclusive start/exclusive end report filters."""

    start_at: datetime
    end_at: datetime
    user_ids: tuple[str, ...] = ()
    key_ids: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    root_run_ids: tuple[str, ...] = ()
    purposes: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()

    @classmethod
    def last_hours(
        cls,
        hours: float = 24.0,
        **dimensions: Any,
    ) -> "ModelUsageReportFilters":
        end_at = datetime.now(timezone.utc)
        return cls(
            start_at=end_at - timedelta(hours=max(0.01, float(hours))),
            end_at=end_at,
            **dimensions,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_at": _iso(self.start_at),
            "end_at": _iso(self.end_at),
            "user_ids": list(self.user_ids),
            "key_ids": list(self.key_ids),
            "models": list(self.models),
            "root_run_ids": list(self.root_run_ids),
            "purposes": list(self.purposes),
            "outcomes": list(self.outcomes),
        }


@dataclass(frozen=True)
class UsageAlert:
    code: str
    root_run_id: str | None
    severity: str
    observed: Any
    threshold: Any
    evidence: dict[str, Any] = field(default_factory=dict)
    policy: str = APPROVED_ALERT_POLICY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "code": self.code,
            "severity": self.severity,
            "root_run_id": self.root_run_id,
            "observed": self.observed,
            "threshold": self.threshold,
            "evidence": dict(self.evidence),
        }


_ATTEMPT_QUERY = text(
    """SELECT
        attempt.id,
        attempt.logical_call_id,
        attempt.run_id,
        attempt.root_run_id,
        attempt.run_request_sequence AS run_sequence,
        attempt.attempt_index,
        attempt.attempt_kind,
        attempt.model,
        attempt.transport,
        attempt.provider_key_fingerprint,
        attempt.purpose,
        attempt.terminal_seen,
        attempt.trusted_usage,
        attempt.terminal_status,
        attempt.input_tokens,
        attempt.output_tokens,
        attempt.reasoning_tokens,
        attempt.cache_read_tokens,
        attempt.cache_miss_tokens,
        attempt.cache_write_tokens,
        attempt.provider_amount_raw,
        attempt.provider_amount_unit,
        attempt.unknown_provider_charge,
        attempt.billed_but_not_committed,
        attempt.prefix_diagnostics,
        attempt.created_at,
        attempt.completed_at,
        root_run.id IS NOT NULL AS root_run_exists,
        root_run.user_id AS root_user_id,
        root_run.status AS root_status,
        root_run.outcome AS root_outcome,
        root_run.completed_at AS root_completed_at,
        own_run.user_id AS run_user_id
    FROM agent_model_attempt_audits AS attempt
    LEFT JOIN agent_runs AS root_run ON root_run.id = attempt.root_run_id
    LEFT JOIN agent_runs AS own_run ON own_run.id = attempt.run_id
    WHERE attempt.created_at >= :start_at AND attempt.created_at < :end_at
    ORDER BY attempt.created_at, attempt.id"""
)

_LOGICAL_QUERY = text(
    """SELECT
        logical.id,
        logical.run_id,
        logical.root_run_id,
        logical.parent_logical_call_id,
        logical.model,
        logical.transport,
        logical.provider_key_fingerprint,
        logical.purpose,
        logical.status AS logical_status,
        logical.attempt_count,
        logical.created_at,
        logical.completed_at,
        root_run.id IS NOT NULL AS root_run_exists,
        root_run.user_id AS root_user_id,
        root_run.status AS root_status,
        root_run.outcome AS root_outcome,
        root_run.completed_at AS root_completed_at,
        own_run.user_id AS run_user_id
    FROM agent_model_logical_calls AS logical
    LEFT JOIN agent_runs AS root_run ON root_run.id = logical.root_run_id
    LEFT JOIN agent_runs AS own_run ON own_run.id = logical.run_id
    WHERE logical.created_at >= :start_at AND logical.created_at < :end_at
    ORDER BY logical.created_at, logical.id"""
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _mapping(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if isinstance(mapping, Mapping) else {}


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _trusted_usage(row: Mapping[str, Any]) -> bool:
    # Hand-built/legacy report rows predate the column and remain trusted by compatibility.
    return _bool(row.get("trusted_usage")) if "trusted_usage" in row else True


def _json_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _utc_naive(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _after(left: Any, right: Any) -> bool:
    left_dt = _utc_naive(left)
    right_dt = _utc_naive(right)
    return bool(left_dt is not None and right_dt is not None and left_dt > right_dt)


def _row_user(row: Mapping[str, Any]) -> tuple[str | None, str]:
    root_user = str(row.get("root_user_id") or "").strip()
    if root_user:
        return root_user, "root_run"
    run_user = str(row.get("run_user_id") or "").strip()
    if run_user:
        return run_user, "run_fallback"
    return None, "unavailable"


def _root_id(row: Mapping[str, Any]) -> str | None:
    value = str(row.get("root_run_id") or "").strip()
    return value or None


def _root_exists(row: Mapping[str, Any]) -> bool:
    return bool(_root_id(row)) and _bool(row.get("root_run_exists"))


def _row_in_window(row: Mapping[str, Any], filters: ModelUsageReportFilters) -> bool:
    created_at = _utc_naive(row.get("created_at"))
    if created_at is None:
        # SQL already applies the window.  Keeping hand-built rows without timestamps makes the
        # pure aggregation API useful while never inventing a timestamp in the output.
        return True
    start_at = _utc_naive(filters.start_at)
    end_at = _utc_naive(filters.end_at)
    return bool(start_at is not None and end_at is not None and start_at <= created_at < end_at)


def _matches_dimensions(
    row: Mapping[str, Any], filters: ModelUsageReportFilters
) -> bool:
    if not _row_in_window(row, filters):
        return False
    user_id, _ = _row_user(row)
    comparisons = (
        (filters.user_ids, user_id),
        (filters.key_ids, str(row.get("provider_key_fingerprint") or "")),
        (filters.models, str(row.get("model") or "")),
        (filters.root_run_ids, _root_id(row)),
        (filters.purposes, str(row.get("purpose") or "")),
        (filters.outcomes, row.get("root_outcome")),
    )
    return all(not allowed or value in allowed for allowed, value in comparisons)


def _optional_sum(rows: Sequence[Mapping[str, Any]], field_name: str) -> tuple[int | None, int]:
    values = [
        parsed
        for row in rows
        if _trusted_usage(row)
        if (parsed := _optional_int(row.get(field_name))) is not None
    ]
    return (sum(values), len(values)) if values else (None, 0)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _cache_read_ratio(attempts: Sequence[Mapping[str, Any]]) -> float | None:
    if not attempts:
        return None
    pairs: list[tuple[int, int]] = []
    for row in attempts:
        if not _trusted_usage(row):
            return None
        cache_read = _optional_int(row.get("cache_read_tokens"))
        cache_miss = _optional_int(row.get("cache_miss_tokens"))
        if cache_read is None or cache_miss is None:
            return None
        pairs.append((cache_read, cache_miss))
    cache_read = sum(item[0] for item in pairs)
    cache_miss = sum(item[1] for item in pairs)
    if cache_read + cache_miss <= 0:
        return None
    return cache_read / (cache_read + cache_miss)


def _uncached_equivalent(attempts: Sequence[Mapping[str, Any]]) -> int | None:
    """Control-plane token equivalent; never a currency or Provider charge.

    Every physical attempt must report input, cache-read, and output.  Partial coverage cannot be
    completed with zeroes because that would silently understate the aggregate.
    """
    if not attempts:
        return None
    values = [_attempt_uncached_equivalent(row) for row in attempts]
    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values if value is not None)


def _attempt_uncached_equivalent(row: Mapping[str, Any]) -> int | None:
    if not _trusted_usage(row):
        return None
    input_tokens = _optional_int(row.get("input_tokens"))
    cache_read_tokens = _optional_int(row.get("cache_read_tokens"))
    output_tokens = _optional_int(row.get("output_tokens"))
    if input_tokens is None or cache_read_tokens is None or output_tokens is None:
        return None
    return max(input_tokens - cache_read_tokens, 0) + output_tokens


def _lcp_state(row: Mapping[str, Any]) -> tuple[bool, bool, int | None, int | None]:
    diagnostics = _json_mapping(row.get("prefix_diagnostics"))
    if not _bool(diagnostics.get("available")):
        return False, False, None, None
    previous_count = _optional_int(diagnostics.get("previous_item_count"))
    lcp_count = _optional_int(diagnostics.get("lcp_item_count"))
    tool_equal = diagnostics.get("tool_schema_equal")
    complete = (
        previous_count is not None
        and lcp_count is not None
        and lcp_count == previous_count
        and tool_equal is True
    )
    return True, complete, lcp_count, previous_count


def _amounts(attempts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    values: Counter[tuple[str, str | None]] = Counter()
    for row in attempts:
        if not _trusted_usage(row):
            continue
        raw = row.get("provider_amount_raw")
        if raw is None:
            continue
        # Do not strip or parse: the exact Provider representation is the fact being audited.
        rendered = str(raw)
        unit_raw = row.get("provider_amount_unit")
        unit = str(unit_raw) if unit_raw is not None else None
        values[(rendered, unit)] += 1
    return [
        {"raw": raw, "unit": unit, "attempt_count": count}
        for (raw, unit), count in sorted(
            values.items(), key=lambda item: (item[0][0], item[0][1] or "")
        )
    ]


def _counter(rows: Sequence[Mapping[str, Any]], field_name: str) -> dict[str, int]:
    counts = Counter(str(row.get(field_name) or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _build_aggregate(
    logical_rows: Sequence[Mapping[str, Any]],
    attempt_rows: Sequence[Mapping[str, Any]],
    *,
    dimensions: Mapping[str, Any],
) -> dict[str, Any]:
    logical_ids = {
        str(row.get("id") or "")
        for row in logical_rows
        if str(row.get("id") or "")
    }
    logical_ids.update(
        str(row.get("logical_call_id") or "")
        for row in attempt_rows
        if str(row.get("logical_call_id") or "")
    )
    attempt_count = len(attempt_rows)
    logical_count = len(logical_ids)

    tokens: dict[str, dict[str, int | None]] = {}
    for field_name in TOKEN_FIELDS:
        total, reported = _optional_sum(attempt_rows, field_name)
        tokens[field_name] = {
            "total": total,
            "reported_attempts": reported,
        }

    auxiliary_rows = [
        row for row in attempt_rows
        if str(row.get("purpose") or "") in AUXILIARY_PURPOSES
    ]
    auxiliary_count = len(auxiliary_rows)
    descendant_count = sum(
        1
        for row in attempt_rows
        if _root_id(row) is not None
        and str(row.get("run_id") or "") != str(_root_id(row) or "")
    )
    post_terminal_count = sum(
        1
        for row in attempt_rows
        if _after(row.get("created_at"), row.get("root_completed_at"))
    )
    unknown_charge_count = sum(
        1 for row in attempt_rows if _bool(row.get("unknown_provider_charge"))
    )
    terminal_usage_missing_count = sum(
        1
        for row in attempt_rows
        if _bool(row.get("terminal_seen"))
        and not any(row.get(field_name) is not None for field_name in TOKEN_FIELDS)
    )
    orphan_attempt_count = sum(1 for row in attempt_rows if not _root_exists(row))
    orphan_logical_count = sum(1 for row in logical_rows if not _root_exists(row))

    lcp_rows = [_lcp_state(row) for row in attempt_rows]
    lcp_observed = [item for item in lcp_rows if item[0]]
    lcp_complete = sum(1 for item in lcp_observed if item[1])
    lcp_counts = [item for item in lcp_observed if item[2] is not None and item[3] is not None]
    lcp_sum = sum(int(item[2]) for item in lcp_counts)
    previous_sum = sum(int(item[3]) for item in lcp_counts)

    sequence_counts = Counter(
        (str(row.get("run_id") or ""), _optional_int(row.get("run_sequence")))
        for row in attempt_rows
        if str(row.get("run_id") or "")
        and _optional_int(row.get("run_sequence")) is not None
    )
    duplicate_sequences = [
        {"run_id": run_id, "run_sequence": sequence, "rows": count}
        for (run_id, sequence), count in sorted(sequence_counts.items())
        if count > 1
    ]

    compaction_ids = {
        str(row.get("id") or "")
        for row in logical_rows
        if str(row.get("purpose") or "") in COMPACTION_PURPOSES
        and str(row.get("id") or "")
    }
    compaction_ids.update(
        str(row.get("logical_call_id") or "")
        for row in attempt_rows
        if str(row.get("purpose") or "") in COMPACTION_PURPOSES
        and str(row.get("logical_call_id") or "")
    )
    compaction_attempt_counts = Counter(
        str(row.get("logical_call_id") or "")
        for row in attempt_rows
        if str(row.get("purpose") or "") in COMPACTION_PURPOSES
        and str(row.get("logical_call_id") or "")
    )
    # A logical call created inside the window carries its transactionally maintained full
    # attempt_count.  Use it when it is larger than the rows visible in the selected attempt
    # window, while attempt rows remain the fallback for calls that began before the window.
    for row in logical_rows:
        logical_call_id = str(row.get("id") or "")
        persisted_count = _optional_int(row.get("attempt_count"))
        if (
            str(row.get("purpose") or "") in COMPACTION_PURPOSES
            and logical_call_id
            and persisted_count is not None
        ):
            compaction_attempt_counts[logical_call_id] = max(
                compaction_attempt_counts[logical_call_id], persisted_count
            )
    compaction_attempts_by_logical_call = [
        {"logical_call_id": logical_call_id, "attempt_count": count}
        for logical_call_id, count in sorted(compaction_attempt_counts.items())
    ]
    preamble_reasoning, preamble_reasoning_coverage = _optional_sum(
        [row for row in attempt_rows if str(row.get("purpose") or "") == "public_preamble"],
        "reasoning_tokens",
    )
    main_lcp_incomplete_count = sum(
        1
        for row in attempt_rows
        if str(row.get("purpose") or "") == "main_loop"
        and (lambda state: state[0] and not state[1])(_lcp_state(row))
    )

    uncached_equivalent = _uncached_equivalent(attempt_rows)
    auxiliary_uncached_values = [
        _attempt_uncached_equivalent(row) for row in auxiliary_rows
    ]
    auxiliary_uncached_equivalent = (
        sum(value for value in auxiliary_uncached_values if value is not None)
        if auxiliary_count > 0
        and all(value is not None for value in auxiliary_uncached_values)
        else (0 if auxiliary_count == 0 and uncached_equivalent is not None else None)
    )
    auxiliary_uncached_ratio = (
        auxiliary_uncached_equivalent / uncached_equivalent
        if auxiliary_uncached_equivalent is not None
        and uncached_equivalent is not None
        and uncached_equivalent > 0
        else None
    )

    return {
        **dict(dimensions),
        "logical_call_count": logical_count,
        "logical_rows_created_in_window": len(logical_rows),
        "attempt_count": attempt_count,
        "attempt_per_logical": _ratio(attempt_count, logical_count),
        "tokens": tokens,
        "uncached_equivalent": uncached_equivalent,
        "provider_amounts": _amounts(attempt_rows),
        "cache_read_ratio": _cache_read_ratio(attempt_rows),
        "auxiliary_attempt_count": auxiliary_count,
        "auxiliary_attempt_ratio": _ratio(auxiliary_count, attempt_count),
        "auxiliary_uncached_equivalent": auxiliary_uncached_equivalent,
        "auxiliary_uncached_equivalent_ratio": auxiliary_uncached_ratio,
        "descendant_attempt_count": descendant_count,
        "descendant_attempt_ratio": _ratio(descendant_count, attempt_count),
        "post_terminal_attempt_count": post_terminal_count,
        "post_terminal_attempt_ratio": _ratio(post_terminal_count, attempt_count),
        "unknown_provider_charge_attempt_count": unknown_charge_count,
        "unknown_provider_charge_attempt_ratio": _ratio(unknown_charge_count, attempt_count),
        "terminal_usage_missing_attempt_count": terminal_usage_missing_count,
        "terminal_usage_missing_attempt_ratio": _ratio(
            terminal_usage_missing_count, attempt_count
        ),
        "billed_but_not_committed_attempt_count": sum(
            1 for row in attempt_rows if _bool(row.get("billed_but_not_committed"))
        ),
        "orphan_root_attempt_count": orphan_attempt_count,
        "orphan_root_logical_count": orphan_logical_count,
        "duplicate_run_sequences": duplicate_sequences,
        "lcp": {
            "observed_attempts": len(lcp_observed),
            "complete_attempts": lcp_complete,
            "incomplete_attempts": len(lcp_observed) - lcp_complete,
            "complete_ratio": _ratio(lcp_complete, len(lcp_observed)),
            "lcp_item_count_sum": lcp_sum if lcp_counts else None,
            "previous_item_count_sum": previous_sum if lcp_counts else None,
            "item_ratio": (lcp_sum / previous_sum if previous_sum > 0 else None),
        },
        "main_lcp_incomplete_attempt_count": main_lcp_incomplete_count,
        "compaction_logical_call_count": len(compaction_ids),
        "compaction_max_attempts_per_logical_call": max(
            compaction_attempt_counts.values(), default=0
        ),
        "compaction_attempts_by_logical_call": compaction_attempts_by_logical_call,
        "preamble_reasoning_tokens_total": preamble_reasoning,
        "preamble_reasoning_reported_attempts": preamble_reasoning_coverage,
        "purpose_counts": _counter(attempt_rows, "purpose"),
        "attempt_kind_counts": _counter(attempt_rows, "attempt_kind"),
        "terminal_status_counts": _counter(attempt_rows, "terminal_status"),
    }


def calculate_approved_alerts(root_aggregates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the approved alert thresholds without mutating or auto-remediating anything."""
    alerts: list[UsageAlert] = []
    for aggregate in root_aggregates:
        root_run_id = aggregate.get("root_run_id") or None
        context = {
            "user_ids": list(aggregate.get("user_ids") or []),
            "models": list(aggregate.get("models") or []),
            "purposes": list(aggregate.get("purposes") or []),
            "outcomes": list(aggregate.get("outcomes") or []),
        }

        ratio = aggregate.get("attempt_per_logical")
        if isinstance(ratio, (int, float)) and ratio > 1.0:
            alerts.append(UsageAlert(
                code="attempt_amplification",
                root_run_id=root_run_id,
                severity="warning",
                observed=ratio,
                threshold={"operator": ">", "value": 1.0},
                evidence={
                    **context,
                    "logical_call_count": aggregate.get("logical_call_count"),
                    "attempt_count": aggregate.get("attempt_count"),
                },
            ))

        preamble_reasoning = aggregate.get("preamble_reasoning_tokens_total")
        if isinstance(preamble_reasoning, int) and preamble_reasoning > 0:
            alerts.append(UsageAlert(
                code="preamble_reasoning",
                root_run_id=root_run_id,
                severity="warning",
                observed=preamble_reasoning,
                threshold={"operator": ">", "value": 0},
                evidence=context,
            ))

        for code, field_name, severity in (
            ("terminal_usage_missing", "terminal_usage_missing_attempt_count", "critical"),
            ("main_lcp_incomplete", "main_lcp_incomplete_attempt_count", "warning"),
            ("unknown_provider_charge", "unknown_provider_charge_attempt_count", "critical"),
        ):
            observed = int(aggregate.get(field_name) or 0)
            if observed > 0:
                alerts.append(UsageAlert(
                    code=code,
                    root_run_id=root_run_id,
                    severity=severity,
                    observed=observed,
                    threshold={"operator": ">", "value": 0},
                    evidence=context,
                ))

        compaction_max_attempts = int(
            aggregate.get("compaction_max_attempts_per_logical_call") or 0
        )
        if compaction_max_attempts > 3:
            offenders = [
                dict(item)
                for item in aggregate.get("compaction_attempts_by_logical_call") or []
                if int(item.get("attempt_count") or 0) > 3
            ]
            alerts.append(UsageAlert(
                code="compaction_excess",
                root_run_id=root_run_id,
                severity="warning",
                observed=compaction_max_attempts,
                threshold={"operator": ">", "value": 3},
                evidence={**context, "logical_calls": offenders},
            ))

        auxiliary_ratio = aggregate.get("auxiliary_uncached_equivalent_ratio")
        if isinstance(auxiliary_ratio, (int, float)) and auxiliary_ratio > 0.5:
            alerts.append(UsageAlert(
                code="auxiliary_share_high",
                root_run_id=root_run_id,
                severity="warning",
                observed=auxiliary_ratio,
                threshold={"operator": ">", "value": 0.5},
                evidence={
                    **context,
                    "auxiliary_attempt_count": aggregate.get("auxiliary_attempt_count"),
                    "attempt_count": aggregate.get("attempt_count"),
                    "auxiliary_uncached_equivalent": aggregate.get(
                        "auxiliary_uncached_equivalent"
                    ),
                    "root_uncached_equivalent": aggregate.get("uncached_equivalent"),
                },
            ))

        orphan_count = int(aggregate.get("orphan_root_attempt_count") or 0) + int(
            aggregate.get("orphan_root_logical_count") or 0
        )
        if orphan_count > 0:
            alerts.append(UsageAlert(
                code="orphan_root",
                root_run_id=root_run_id,
                severity="critical",
                observed=orphan_count,
                threshold={"operator": ">", "value": 0},
                evidence=context,
            ))

        duplicates = list(aggregate.get("duplicate_run_sequences") or [])
        if duplicates:
            alerts.append(UsageAlert(
                code="duplicate_run_sequence",
                root_run_id=root_run_id,
                severity="critical",
                observed=len(duplicates),
                threshold={"operator": ">", "value": 0},
                evidence={**context, "duplicates": duplicates},
            ))

    return [
        alert.as_dict()
        for alert in sorted(alerts, key=lambda item: (item.root_run_id or "", item.code))
    ]


def _group_dimensions(row: Mapping[str, Any]) -> tuple[Any, ...]:
    user_id, user_source = _row_user(row)
    return (
        _root_id(row),
        user_id,
        user_source,
        str(row.get("provider_key_fingerprint") or "") or None,
        str(row.get("model") or ""),
        str(row.get("purpose") or ""),
        row.get("root_outcome"),
        row.get("root_status"),
    )


def _filter_error(filters: ModelUsageReportFilters) -> tuple[str, str] | None:
    start_at = _utc_naive(filters.start_at)
    end_at = _utc_naive(filters.end_at)
    if start_at is None or end_at is None or start_at >= end_at:
        return "invalid_filter", "invalid_time_window"
    return None


def aggregate_model_usage(
    logical_rows: Iterable[Mapping[str, Any]],
    attempt_rows: Iterable[Mapping[str, Any]],
    filters: ModelUsageReportFilters,
) -> dict[str, Any]:
    """Pure DB-fact aggregation used by both the service and focused tests."""
    filter_error = _filter_error(filters)
    if filter_error is not None:
        status, warning = filter_error
        return _empty_report(
            filters,
            status=status,
            warning=warning,
        )

    logical = [
        _mapping(row)
        for row in logical_rows
        if _matches_dimensions(_mapping(row), filters)
    ]
    attempts = [
        _mapping(row)
        for row in attempt_rows
        if _matches_dimensions(_mapping(row), filters)
    ]

    grouped_logical: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    grouped_attempts: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in logical:
        grouped_logical[_group_dimensions(row)].append(row)
    for row in attempts:
        grouped_attempts[_group_dimensions(row)].append(row)

    groups: list[dict[str, Any]] = []
    for key in sorted(set(grouped_logical) | set(grouped_attempts), key=lambda item: repr(item)):
        root_run_id, user_id, user_source, key_id, model, purpose, outcome, root_status = key
        groups.append(_build_aggregate(
            grouped_logical.get(key, []),
            grouped_attempts.get(key, []),
            dimensions={
                "root_run_id": root_run_id,
                "user_id": user_id,
                "user_id_source": user_source,
                "key_id": key_id,
                "model": model,
                "purpose": purpose,
                "outcome": outcome,
                "root_status": root_status,
            },
        ))

    root_logical: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    root_attempts: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for row in logical:
        root_logical[_root_id(row)].append(row)
    for row in attempts:
        root_attempts[_root_id(row)].append(row)

    roots: list[dict[str, Any]] = []
    for root_run_id in sorted(set(root_logical) | set(root_attempts), key=lambda item: item or ""):
        root_l = root_logical.get(root_run_id, [])
        root_a = root_attempts.get(root_run_id, [])
        all_rows = [*root_l, *root_a]
        user_ids = sorted({user for row in all_rows if (user := _row_user(row)[0])})
        outcomes = sorted({str(value) for row in all_rows if (value := row.get("root_outcome"))})
        statuses = sorted({str(value) for row in all_rows if (value := row.get("root_status"))})
        models = sorted({str(row.get("model") or "") for row in all_rows})
        key_ids = sorted({
            str(row.get("provider_key_fingerprint") or "")
            for row in all_rows
            if str(row.get("provider_key_fingerprint") or "")
        })
        purposes = sorted({str(row.get("purpose") or "") for row in all_rows})
        roots.append(_build_aggregate(
            root_l,
            root_a,
            dimensions={
                "root_run_id": root_run_id,
                "user_ids": user_ids,
                "key_ids": key_ids,
                "models": models,
                "purposes": purposes,
                "outcomes": outcomes,
                "root_statuses": statuses,
            },
        ))

    return {
        "report_version": REPORT_VERSION,
        "status": "ok",
        "window": {"start_at": _iso(filters.start_at), "end_at": _iso(filters.end_at)},
        "filters": filters.as_dict(),
        "schema_capabilities": _schema_capabilities(),
        "source_rows": {
            "logical_calls": len(logical),
            "attempts": len(attempts),
        },
        "groups": groups,
        "roots": roots,
        "alerts": calculate_approved_alerts(roots),
        "warnings": [],
    }


def _schema_capabilities() -> dict[str, Any]:
    return {
        "user_id": {
            "available": True,
            "source": "agent_runs.user_id",
            "fallback": "attempt/logical run agent_runs.user_id",
        },
        "key_id": {
            "available": True,
            "source": "provider_key_fingerprint",
            "privacy": "sha256 only; raw Provider key is never stored",
        },
        "outcome": {
            "available": True,
            "source": "root agent_runs.outcome",
            "missing_value": None,
        },
    }


def _empty_report(
    filters: ModelUsageReportFilters,
    *,
    status: str,
    warning: str,
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "status": status,
        "window": {"start_at": _iso(filters.start_at), "end_at": _iso(filters.end_at)},
        "filters": filters.as_dict(),
        "schema_capabilities": _schema_capabilities(),
        "source_rows": {"logical_calls": 0, "attempts": 0},
        "groups": [],
        "roots": [],
        "alerts": [],
        "warnings": [warning],
    }


async def build_model_usage_report(
    filters: ModelUsageReportFilters,
    *,
    timeout_seconds: float = REPORT_QUERY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read the Runtime ledger and return a fail-open structured report."""
    filter_error = _filter_error(filters)
    if filter_error is not None:
        status, warning = filter_error
        return _empty_report(
            filters,
            status=status,
            warning=warning,
        )
    try:
        factory = runtime_session()
    except Exception:  # noqa: BLE001 - reporting must not affect Agent execution
        factory = None
    if factory is None:
        return _empty_report(
            filters,
            status="unavailable",
            warning="runtime_database_unavailable",
        )

    async def _read() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with factory() as session:
            # Runtime audit timestamps are ``TIMESTAMP`` columns.  Bind UTC-naive boundaries so
            # PostgreSQL session timezone cannot silently shift an aware CLI datetime.
            params = {
                "start_at": _utc_naive(filters.start_at),
                "end_at": _utc_naive(filters.end_at),
            }
            attempt_result = await session.execute(_ATTEMPT_QUERY, params)
            logical_result = await session.execute(_LOGICAL_QUERY, params)
            attempt_rows = [dict(row) for row in attempt_result.mappings().all()]
            logical_rows = [dict(row) for row in logical_result.mappings().all()]
            return logical_rows, attempt_rows

    try:
        logical_rows, attempt_rows = await asyncio.wait_for(
            _read(), timeout=max(0.05, float(timeout_seconds))
        )
        return aggregate_model_usage(logical_rows, attempt_rows, filters)
    except Exception as exc:  # noqa: BLE001 - read-only observability is fail-open
        report = _empty_report(
            filters,
            status="unavailable",
            warning="model_usage_report_query_failed",
        )
        report["error_type"] = type(exc).__name__
        return report


__all__ = [
    "APPROVED_ALERT_POLICY",
    "AUXILIARY_PURPOSES",
    "COMPACTION_PURPOSES",
    "ModelUsageReportFilters",
    "PRIMARY_PURPOSES",
    "UsageAlert",
    "aggregate_model_usage",
    "build_model_usage_report",
    "calculate_approved_alerts",
]
