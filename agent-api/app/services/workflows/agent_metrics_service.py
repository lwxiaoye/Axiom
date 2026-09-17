"""Realtime, owner-facing metrics for a workflow application."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from app.models import ChatMessage, ChatThread, POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
METRIC_RANGE_KEYS = {
    "today",
    "last_7_days",
    "last_4_weeks",
    "last_3_months",
    "last_12_months",
    "month_to_date",
    "quarter_to_date",
    "year_to_date",
    "all_time",
}


@dataclass(frozen=True)
class MetricsWindow:
    key: str
    start: date
    end: date


def _shanghai_date(now: datetime | None) -> date:
    current = now or datetime.now(tz=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    return current.astimezone(SHANGHAI_TZ).date()


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_offset = divmod(month_index, 12)
    month = month_offset + 1
    month_ends = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(value.day, month_ends))


def resolve_metrics_window(range_key: str, now: datetime | None = None) -> MetricsWindow:
    if range_key not in METRIC_RANGE_KEYS:
        raise ValueError("不支持的 range 参数")

    today = _shanghai_date(now)
    if range_key == "today":
        start = today
    elif range_key == "last_7_days":
        start = today - timedelta(days=6)
    elif range_key == "last_4_weeks":
        start = today - timedelta(days=27)
    elif range_key == "last_3_months":
        start = _subtract_months(today, 3) + timedelta(days=1)
    elif range_key == "last_12_months":
        start = _subtract_months(today, 12) + timedelta(days=1)
    elif range_key == "month_to_date":
        start = today.replace(day=1)
    elif range_key == "quarter_to_date":
        start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    elif range_key == "year_to_date":
        start = date(today.year, 1, 1)
    else:
        # all_time narrows to the earliest qualifying interaction once the database is queried.
        start = today
    return MetricsWindow(key=range_key, start=start, end=today)


def _fill_missing_days(window: MetricsWindow, daily_rows: list[dict]) -> list[dict]:
    by_date = {str(row.get("date")): row for row in daily_rows}
    days: list[dict] = []
    current = window.start
    while current <= window.end:
        key = current.isoformat()
        row = by_date.get(key, {})
        days.append(
            {
                "date": key,
                "sessions": int(row.get("sessions") or 0),
                "activeUsers": int(row.get("activeUsers") or 0),
                "interactions": int(row.get("interactions") or 0),
                "messages": int(row.get("messages") or 0),
                "newUsers": int(row.get("newUsers") or 0),
                "returningUsers": int(row.get("returningUsers") or 0),
            }
        )
        current += timedelta(days=1)
    return days


def build_metrics_payload(
    window: MetricsWindow,
    is_conversational: bool,
    daily_rows: list[dict],
    unique_users: int,
    new_users: int,
    returning_users: int,
) -> dict:
    daily = _fill_missing_days(window, daily_rows)
    sessions = sum(row["sessions"] for row in daily)
    return {
        "range": window.key,
        "startDate": window.start.isoformat(),
        "endDate": window.end.isoformat(),
        "isConversational": is_conversational,
        "totals": {
            "sessions": sessions,
            "activeUsers": int(unique_users or 0),
            "newUsers": int(new_users or 0),
            "returningUsers": int(returning_users or 0),
            "averageMessages": round(sum(row["messages"] for row in daily) / sessions, 2) if sessions else 0,
            "messages": sum(row["messages"] for row in daily),
        },
        "daily": daily,
    }


def _local_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ).date()


def _query_window_bounds(window: MetricsWindow) -> tuple[datetime, datetime]:
    return (
        datetime.combine(window.start, time.min),
        datetime.combine(window.end + timedelta(days=1), time.min),
    )


async def collect_app_metrics(
    session: Any,
    app: Any,
    range_key: str,
    now: datetime | None = None,
    *,
    builtin_origin: str | None = None,
) -> dict:
    """Collect formal user/assistant exchanges for one application in real time."""
    window = resolve_metrics_window(range_key, now)
    assistant = aliased(ChatMessage)
    human = aliased(ChatMessage)
    thread_scope = (
        and_(
            ChatThread.app_id.is_(None),
            ChatThread.origin == builtin_origin,
            ChatThread.parent_thread_id.is_(None),
            ChatThread.subagent_id.is_(None),
        )
        if builtin_origin is not None
        else and_(
            ChatThread.app_id == str(app.id),
            or_(ChatThread.origin.is_(None), ChatThread.origin != "delegation"),
        )
    )
    human_conditions = [
        human.thread_id == assistant.thread_id,
        human.role == "user",
        or_(human.sender_type.is_(None), human.sender_type == "human"),
        or_(human.status.is_(None), human.status.notin_(("archived", "superseded", *POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES))),
        # Legacy standalone runs do not write a user run_id.
        human.created_at <= assistant.created_at,
    ]
    if builtin_origin is not None:
        # Harness messages have a shared run_id. A different successful question must
        # not validate the reply to an archived or rejected input in the same thread.
        human_conditions.append(or_(assistant.run_id.is_(None), human.run_id == assistant.run_id))
    human_match = exists(
        select(human.id).where(*human_conditions)
    )
    eligible_conditions = (
        thread_scope,
        assistant.role == "assistant",
        or_(assistant.status.is_(None), assistant.status == "completed"),
        assistant.content.is_not(None),
        assistant.content != "",
        human_match,
    )
    statement = (
        select(
            assistant.thread_id.label("thread_id"),
            ChatThread.user_id.label("user_id"),
            assistant.created_at.label("created_at"),
        )
        .join(ChatThread, ChatThread.id == assistant.thread_id)
        .where(*eligible_conditions)
    )
    if range_key != "all_time":
        start_at, end_at = _query_window_bounds(window)
        statement = statement.where(assistant.created_at >= start_at, assistant.created_at < end_at)

    result = await session.execute(statement)
    rows = result.mappings().all()
    first_activity_result = await session.execute(
        select(
            ChatThread.user_id.label("user_id"),
            func.min(assistant.created_at).label("first_active_at"),
        )
        .join(ChatThread, ChatThread.id == assistant.thread_id)
        .where(*eligible_conditions)
        .group_by(ChatThread.user_id)
    )
    first_activity_rows = first_activity_result.mappings().all()
    normalized: list[tuple[str, str, date]] = []
    for row in rows:
        created_day = _local_date(row.get("created_at"))
        thread_id = str(row.get("thread_id") or "")
        user_id = str(row.get("user_id") or "")
        if not created_day or not thread_id or not user_id:
            continue
        if range_key != "all_time" and not (window.start <= created_day <= window.end):
            continue
        normalized.append((thread_id, user_id, created_day))

    if range_key == "all_time" and normalized:
        window = MetricsWindow(key=window.key, start=min(item[2] for item in normalized), end=window.end)

    first_active_days: dict[str, date] = {}
    for row in first_activity_rows:
        user_id = str(row.get("user_id") or "")
        first_active_day = _local_date(row.get("first_active_at"))
        if user_id and first_active_day:
            first_active_days[user_id] = first_active_day

    daily_state: dict[date, dict[str, Any]] = {}
    unique_users: set[str] = set()
    returning_user_ids: set[str] = set()
    for thread_id, user_id, created_day in normalized:
        if created_day > window.end:
            continue
        state = daily_state.setdefault(
            created_day,
            {
                "sessions": set(),
                "users": set(),
                "interactions": 0,
                "messages": 0,
                "newUsers": set(),
                "returningUsers": set(),
            },
        )
        state["sessions"].add(thread_id)
        state["users"].add(user_id)
        state["interactions"] += 1
        state["messages"] += 1
        unique_users.add(user_id)
        first_active_day = first_active_days.get(user_id)
        if first_active_day == created_day:
            state["newUsers"].add(user_id)
        elif first_active_day and first_active_day < created_day:
            state["returningUsers"].add(user_id)
            returning_user_ids.add(user_id)

    new_user_ids: set[str] = set()
    for user_id in unique_users:
        first_active_day = first_active_days.get(user_id)
        if not first_active_day:
            continue
        if window.start <= first_active_day <= window.end:
            new_user_ids.add(user_id)

    daily_rows = [
        {
            "date": item_day.isoformat(),
            "sessions": len(state["sessions"]),
            "activeUsers": len(state["users"]),
            "interactions": state["interactions"],
            "messages": state["messages"],
            "newUsers": len(state["newUsers"]),
            "returningUsers": len(state["returningUsers"]),
        }
        for item_day, state in sorted(daily_state.items())
    ]
    return build_metrics_payload(
        window,
        builtin_origin is not None or str(getattr(app, "ai_app_type", "")) in {"simple", "chatAgent"},
        daily_rows,
        len(unique_users),
        len(new_user_ids),
        len(returning_user_ids),
    )
