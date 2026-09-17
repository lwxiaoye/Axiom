"""Stable, read-only access to the configured business clock.

Exact minute/second values intentionally stay out of the stable system prompt.  A model that
actually needs the current instant can call this always-registered tool without perturbing every
other request's prompt-cache prefix.
"""

from __future__ import annotations

from app.services.chat.turn_context_builder import current_time_fact

from .base import MainTool, ToolValue, text_tool_body


def configured_business_time() -> dict[str, str]:
    """Return one internally consistent clock snapshot in the configured timezone."""
    fact = current_time_fact().as_dict()
    iso8601 = str(fact["current_time"])
    return {
        "iso8601": iso8601,
        "current_date": iso8601[:10],
        "timezone": str(fact["timezone"]),
    }


def build_time_tools() -> list[MainTool]:
    async def _get_current_time(_args: dict) -> str:
        snapshot = configured_business_time()
        return (
            f"当前时间：{snapshot['iso8601']}\n"
            f"当前日期：{snapshot['current_date']}\n"
            f"时区：{snapshot['timezone']}"
        )

    return [
        MainTool(
            name="get_current_time",
            description=(
                "仅在任务确实需要精确当前时刻、当天日期或时区换算时调用。"
                "返回平台配置时区下的 ISO-8601 时间、日期和时区；"
                "普通任务不要为了确认时间而额外调用。"
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute=text_tool_body(_get_current_time),
            output_model=ToolValue,
            public_action="获取当前时间",
            capability="time.read",
            effect_scope="none",
            readonly=True,
            parallel_safe=True,
            idempotent=False,
            visible_to_user=False,
        )
    ]


__all__ = ["build_time_tools", "configured_business_time"]
