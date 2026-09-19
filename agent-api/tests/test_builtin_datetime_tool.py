import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, _format_datetime, execute_builtin_tool


class BuiltinDatetimeToolTest(unittest.TestCase):
    def test_default_style_datetime_format_tokens(self):
        value = _format_datetime(datetime(2026, 7, 17, 9, 5, 6), "yyyy-MM-dd HH:mm:ss")
        self.assertEqual(value, "2026-07-17 09:05:06")

    def test_uses_user_supplied_frontend_style_format(self):
        value = _format_datetime(datetime(2026, 7, 17, 9, 5, 6, 123000), "yyyy/MM/dd HH:mm:ss.SSS")
        self.assertEqual(value, "2026/07/17 09:05:06.123")

    def test_current_datetime_uses_agent_business_timezone(self):
        fixed = datetime(2026, 7, 17, 1, 5, 6, tzinfo=timezone.utc).timestamp()

        with patch("app.services.agent_time.settings.AGENT_TIMEZONE", "Asia/Shanghai"), \
                patch("app.services.skills.builtin_tools.time.time", return_value=fixed):
            value = asyncio.run(execute_builtin_tool("builtin.datetime", {"format": "yyyy-MM-dd HH:mm:ss"}))

        self.assertEqual(value, "2026-07-17 09:05:06")

    def test_current_datetime_accepts_chinese_agent_timezone_alias(self):
        fixed = datetime(2026, 7, 17, 1, 5, 6, tzinfo=timezone.utc).timestamp()

        with patch("app.services.agent_time.settings.AGENT_TIMEZONE", "北京时间"), \
                patch("app.services.skills.builtin_tools.time.time", return_value=fixed):
            value = asyncio.run(execute_builtin_tool("builtin.datetime", {"format": "yyyy-MM-dd HH:mm:ss"}))

        self.assertEqual(value, "2026-07-17 09:05:06")

    def test_keeps_strftime_format_compatible(self):
        value = _format_datetime(datetime(2026, 7, 17, 9, 5, 6), "%Y/%m/%d %H:%M")
        self.assertEqual(value, "2026/07/17 09:05")

    def test_timestamp_tool_outputs_utc_seconds_and_milliseconds(self):
        fixed = datetime(2026, 7, 17, 9, 5, 6, 123000, tzinfo=timezone.utc).timestamp()

        with patch("app.services.skills.builtin_tools.time.time", return_value=fixed):
            seconds = asyncio.run(execute_builtin_tool("builtin.timestamp", {"unit": "秒"}))
            milliseconds = asyncio.run(execute_builtin_tool("builtin.timestamp", {"unit": "毫秒"}))

        self.assertEqual(seconds, "1784279106")
        self.assertEqual(milliseconds, "1784279106123")

    def test_time_convert_formats_timestamp_with_common_target_format(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.time_convert",
                {"source_value": 1784279106123, "target_format": "yyyy-MM-dd HH:mm:ss"},
            )
        )

        self.assertEqual(value, "2026-07-17 09:05:06")

    def test_time_convert_parses_custom_source_format(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.time_convert",
                {
                    "source_value": "2026/07/17 09:05:06",
                    "source_format": "yyyy/MM/dd HH:mm:ss",
                    "target_format": "yyyy-MM-dd",
                },
            )
        )

        self.assertEqual(value, "2026-07-17")

    def test_timezone_convert_returns_target_time_timezone_and_utc_timestamp(self):
        value = asyncio.run(
            execute_builtin_tool(
                "builtin.timezone_convert",
                {"time_str": "2026-07-17 09:05:06", "from_timezone": "北京时间", "to_timezone": "UTC"},
            )
        )
        data = json.loads(value)

        self.assertEqual(data["target_time"], "2026-07-17 01:05:06")
        self.assertEqual(data["target_timezone"], "UTC")
        self.assertEqual(data["utc_timestamp"], 1784250306)

    def test_weekday_calculator_outputs_chinese_weekday_number_and_week_index(self):
        value = asyncio.run(
            execute_builtin_tool("builtin.weekday", {"date_str": "2026-07-17", "start_week": "周一"})
        )
        data = json.loads(value)

        self.assertEqual(data["chinese_weekday"], "星期五")
        self.assertEqual(data["weekday_number"], 5)
        self.assertEqual(data["week_index"], 29)

    def test_new_time_tools_are_in_builtin_catalog(self):
        for tool_id in ("builtin.timestamp", "builtin.time_convert", "builtin.timezone_convert", "builtin.weekday"):
            self.assertIn(tool_id, BUILTIN_TOOL_MAP)


if __name__ == "__main__":
    unittest.main()
