"""P1.6 regression: observation.ui must not drop sink files/action."""

from app.services.chat.main_tool_turn import _merge_tool_result_meta
from app.services.chat.tools.base import ToolMetaSink
from app.services.chat.tools import base as tools_base


def test_merge_prefers_sink_files_and_keeps_ui_summary():
    sink = ToolMetaSink()
    token = tools_base.CURRENT_TOOL_CALL_ID.set("call-1")
    try:
        sink["write_file"] = {
            "files": [{"id": "f1", "filename": "a.md", "size": 12}],
            "action": {
                "operation": "write",
                "target": "a.md",
                "file_id": "f1",
                "added": 3,
                "removed": 0,
            },
        }
    finally:
        tools_base.CURRENT_TOOL_CALL_ID.reset(token)

    meta = _merge_tool_result_meta(
        {
            "call_id": "call-1",
            "name": "write_file",
            "observation": {
                "structured_data": {
                    "ui": {"summary": "已新建 a.md", "detail": "ok", "action": "write"},
                },
                "artifact_refs": [{"filename": "a.md", "file_id": "f1", "bytes": 12}],
            },
        },
        sink,
    )
    assert meta is not None
    assert meta["files"][0]["id"] == "f1"
    assert meta["action"]["operation"] == "write"
    assert meta["summary"] == "已新建 a.md"


def test_merge_artifacts_fill_files_when_sink_empty():
    meta = _merge_tool_result_meta(
        {
            "call_id": "c2",
            "name": "bash",
            "observation": {
                "structured_data": {"ui": {"summary": "已运行命令", "action": "bash"}},
                "artifact_refs": [{"filename": "out.txt", "file_id": "x9", "bytes": 4}],
            },
        },
        {},
    )
    assert meta["files"][0]["filename"] == "out.txt"
    assert meta["files"][0]["id"] == "x9"
    assert meta["summary"] == "已运行命令"


def test_merge_explicit_meta_wins():
    meta = _merge_tool_result_meta({"meta": {"count": 3}}, {"write_file": {"files": []}})
    assert meta == {"count": 3}
