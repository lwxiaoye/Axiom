"""Agent 业务时区工具。

容器和服务器可能使用 UTC，但面向用户的「当前时间」应按平台业务时区输出。
"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

DEFAULT_AGENT_TIMEZONE = "Asia/Shanghai"
AGENT_TIMEZONE_ALIASES = {
    "utc": "UTC",
    "gmt": "UTC",
    "北京时间": "Asia/Shanghai",
    "北京": "Asia/Shanghai",
    "中国标准时间": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
}


def agent_timezone() -> ZoneInfo:
    raw = str(getattr(settings, "AGENT_TIMEZONE", "") or DEFAULT_AGENT_TIMEZONE).strip()
    zone_name = AGENT_TIMEZONE_ALIASES.get(raw.lower()) or AGENT_TIMEZONE_ALIASES.get(raw) or raw
    try:
        return ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_AGENT_TIMEZONE)


def agent_timezone_label() -> str:
    return agent_timezone().key


def now_in_agent_timezone() -> datetime:
    return datetime.now(agent_timezone())


def datetime_from_timestamp_in_agent_timezone(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, agent_timezone())


def format_agent_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_in_agent_timezone().strftime(fmt)
