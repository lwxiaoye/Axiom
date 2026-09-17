from app.services.agent_harness.orchestrator import _unavailable_selected_attachments


def test_only_missing_selected_files_trigger_task_gate():
    missing = {
        "file_id": "gone-1", "filename": "旧方案.pptx", "status": "failed",
        "note": "文件不可用（可能已删除或过期）",
    }
    parse_failed = {
        "file_id": "exists-1", "filename": "扫描件.pdf", "status": "failed",
        "note": "文件解析失败",
    }
    upload_failed = {"filename": "临时上传.pdf", "status": "failed", "note": "解析失败"}
    assert _unavailable_selected_attachments([missing, parse_failed, upload_failed]) == [missing]
