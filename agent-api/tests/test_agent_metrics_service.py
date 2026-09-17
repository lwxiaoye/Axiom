from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.workflows.agent_metrics_service import (
    build_metrics_payload,
    collect_app_metrics,
    resolve_metrics_window,
)


def test_last_seven_days_includes_today():
    window = resolve_metrics_window("last_7_days", datetime(2026, 9, 4, 9, 0, 0))

    assert (window.start.isoformat(), window.end.isoformat()) == ("2026-08-29", "2026-09-04")


def test_payload_uses_range_unique_users_and_average_messages_per_session():
    window = resolve_metrics_window("today", datetime(2026, 9, 4, 9, 0, 0))
    payload = build_metrics_payload(
        window,
        True,
        [
            {
                "date": "2026-09-04",
                "sessions": 2,
                "activeUsers": 1,
                "interactions": 3,
                "messages": 3,
                "newUsers": 1,
                "returningUsers": 0,
            }
        ],
        unique_users=1,
        new_users=1,
        returning_users=0,
    )

    assert payload["totals"] == {
        "sessions": 2,
        "activeUsers": 1,
        "newUsers": 1,
        "returningUsers": 0,
        "averageMessages": 1.5,
        "messages": 3,
    }


def test_average_messages_is_available_for_non_conversational_apps_and_empty_days_are_zero():
    payload = build_metrics_payload(
        resolve_metrics_window("today", datetime(2026, 9, 4, 9, 0, 0)),
        False,
        [],
        0,
        new_users=0,
        returning_users=0,
    )

    assert payload["totals"]["averageMessages"] == 0
    assert payload["daily"] == [
        {
            "date": "2026-09-04",
            "sessions": 0,
            "activeUsers": 0,
            "interactions": 0,
            "messages": 0,
            "newUsers": 0,
            "returningUsers": 0,
        }
    ]


def test_invalid_range_is_rejected():
    with pytest.raises(ValueError, match="range"):
        resolve_metrics_window("tomorrow", datetime(2026, 9, 4, 9, 0, 0))


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, *row_sets):
        self.row_sets = list(row_sets)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows(self.row_sets.pop(0))


@pytest.mark.asyncio
async def test_collect_metrics_counts_only_eligible_completed_human_pairs():
    session = _Session(
        [
            {
                "thread_id": "thread-1",
                "user_id": "user-1",
                "created_at": datetime(2026, 9, 4, 10, 0, 0),
            },
            {
                "thread_id": "thread-1",
                "user_id": "user-1",
                "created_at": datetime(2026, 9, 4, 11, 0, 0),
            },
        ],
        [{"user_id": "user-1", "first_active_at": datetime(2026, 9, 4, 10, 0, 0)}],
    )

    payload = await collect_app_metrics(
        session,
        SimpleNamespace(id="app-1", ai_app_type="chatAgent"),
        "today",
        now=datetime(2026, 9, 4, 12, 0, 0),
    )

    assert payload["totals"] == {
        "sessions": 1,
        "activeUsers": 1,
        "newUsers": 1,
        "returningUsers": 0,
        "averageMessages": 2,
        "messages": 2,
    }
    statement_params = {
        item
        for value in session.statements[0].compile().params.values()
        for item in (value if isinstance(value, list) else [value])
    }
    assert "delegation" in statement_params
    assert "superseded" in statement_params
    assert "archived" in statement_params
    statement_sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "run_id" not in statement_sql
    assert "created_at <=" in statement_sql


@pytest.mark.asyncio
async def test_collect_metrics_splits_new_and_returning_users_by_first_valid_interaction():
    session = _Session(
        [
            {"thread_id": "thread-new", "user_id": "user-new", "created_at": datetime(2026, 9, 4, 10, 0, 0)},
            {"thread_id": "thread-returning", "user_id": "user-returning", "created_at": datetime(2026, 9, 3, 10, 0, 0)},
        ],
        [
            {"user_id": "user-new", "first_active_at": datetime(2026, 9, 4, 10, 0, 0)},
            {"user_id": "user-returning", "first_active_at": datetime(2026, 8, 20, 10, 0, 0)},
        ],
    )

    payload = await collect_app_metrics(
        session,
        SimpleNamespace(id="app-1", ai_app_type="chatAgent"),
        "last_7_days",
        now=datetime(2026, 9, 4, 12, 0, 0),
    )

    assert payload["totals"]["newUsers"] == 1
    assert payload["totals"]["returningUsers"] == 1
    assert {item["date"]: (item["newUsers"], item["returningUsers"]) for item in payload["daily"]} == {
        "2026-08-29": (0, 0),
        "2026-08-30": (0, 0),
        "2026-08-31": (0, 0),
        "2026-09-01": (0, 0),
        "2026-09-02": (0, 0),
        "2026-09-03": (0, 1),
        "2026-09-04": (1, 0),
    }
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_collect_metrics_counts_a_user_who_returns_after_first_use_within_the_selected_range():
    session = _Session(
        [
            {"thread_id": "thread-1", "user_id": "user-1", "created_at": datetime(2026, 9, 3, 10, 0, 0)},
            {"thread_id": "thread-1", "user_id": "user-1", "created_at": datetime(2026, 9, 4, 10, 0, 0)},
        ],
        [{"user_id": "user-1", "first_active_at": datetime(2026, 9, 3, 10, 0, 0)}],
    )

    payload = await collect_app_metrics(
        session,
        SimpleNamespace(id="app-1", ai_app_type="chatAgent"),
        "last_7_days",
        now=datetime(2026, 9, 4, 12, 0, 0),
    )

    assert payload["totals"]["activeUsers"] == 1
    assert payload["totals"]["newUsers"] == 1
    assert payload["totals"]["returningUsers"] == 1
    assert {item["date"]: (item["newUsers"], item["returningUsers"]) for item in payload["daily"]} == {
        "2026-08-29": (0, 0),
        "2026-08-30": (0, 0),
        "2026-08-31": (0, 0),
        "2026-09-01": (0, 0),
        "2026-09-02": (0, 0),
        "2026-09-03": (1, 0),
        "2026-09-04": (0, 1),
    }
