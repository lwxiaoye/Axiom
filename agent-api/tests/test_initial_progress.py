"""首帧不代写：可见内容只由模型流式事件产生。"""

from app.services.agent_harness.orchestrator import _goal_next_action_text


def test_goal_next_action_always_empty_model_driven():
    """规则代写开场已下线：天气/文档/调研一律空，交给模型自己说话。"""
    for msg in (
        "你帮我看看今天漳州市的天气状况",
        "用 word 文档整理 Agent 市场简报给我",
        "帮我写一个贪吃蛇的html",
        "帮我写一个登录页面的html",
        "我需要做一个市场调研，内容是关于现在的网页端的agent",
        "我想做一个深度研究，是关于如何搭建知识库的内容",
        "继续",
    ):
        assert _goal_next_action_text(msg, pure_qa=False) == ""
        assert _goal_next_action_text(msg, pure_qa=True) == ""


def test_pure_qa_next_action_is_empty():
    assert _goal_next_action_text("只回复一个字：好", pure_qa=True) == ""


def test_current_time_fact_is_pure_and_kept_out_of_world_state():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat.turn_context_builder import (
        build_current_date_world_state,
        build_current_time_fact,
    )

    now = datetime(2026, 8, 28, 10, 1, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
    world_state = build_current_date_world_state(now, "Asia/Shanghai")
    exact = build_current_time_fact(now, "Asia/Shanghai")

    assert world_state.as_context_section() == {
        "current_date": "2026-08-28",
        "timezone": "Asia/Shanghai",
    }
    assert exact.as_dict() == {
        "current_time": "2026-08-28T10:01:02+08:00",
        "timezone": "Asia/Shanghai",
    }
    assert "10:01" not in world_state.render()


def test_system_prompt_is_identical_across_minutes_and_has_only_date_timezone(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat import turn_context_builder

    monkeypatch.setattr(
        turn_context_builder,
        "_now_in_agent_tz",
        lambda: (
            datetime(2026, 8, 28, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            "Asia/Shanghai",
        ),
    )
    first = turn_context_builder._build_system_prompt(memory_block="memory")
    monkeypatch.setattr(
        turn_context_builder,
        "_now_in_agent_tz",
        lambda: (
            datetime(2026, 8, 28, 10, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
            "Asia/Shanghai",
        ),
    )
    second = turn_context_builder._build_system_prompt(memory_block="memory")

    assert first == second
    assert first.startswith("你是 Agent 综合平台的智能助手")
    parts = turn_context_builder.split_system_prompt_context(first)
    assert "memory" not in parts.stable_base
    assert parts.as_context_section()["memory"] == "memory"
    assert "<current_date>2026-08-28</current_date>" in parts.world_state
    assert "<timezone>Asia/Shanghai</timezone>" in parts.world_state
    assert "10:01" not in first and "10:59" not in first


def test_date_change_only_changes_world_state_tail(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.chat import turn_context_builder

    monkeypatch.setattr(
        turn_context_builder,
        "_now_in_agent_tz",
        lambda: (
            datetime(2026, 8, 28, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
            "Asia/Shanghai",
        ),
    )
    before = turn_context_builder.split_system_prompt_context(
        turn_context_builder._build_system_prompt()
    )
    monkeypatch.setattr(
        turn_context_builder,
        "_now_in_agent_tz",
        lambda: (
            datetime(2026, 8, 29, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            "Asia/Shanghai",
        ),
    )
    after = turn_context_builder.split_system_prompt_context(
        turn_context_builder._build_system_prompt()
    )

    assert before.stable_base == after.stable_base
    assert before.world_state != after.world_state
