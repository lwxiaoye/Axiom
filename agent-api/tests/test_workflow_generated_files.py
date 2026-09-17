import json
from types import SimpleNamespace

import pytest

from app.services.agents import agent_executor
from app.services.files import user_file_service


class _Sandbox:
    async def execute(self, _command, _options):
        return SimpleNamespace(
            stdout=json.dumps([
                "/workspace/outputs/final-report.md",
                "/workspace/outputs/working.py",
                "/workspace/files/chart.png",
            ]),
        )

    async def read_file(self, path):
        return f"data:{path}".encode()


@pytest.mark.asyncio
async def test_collect_sandbox_deliverables_filters_process_files_and_keeps_receipts(monkeypatch):
    saved = []

    async def _save(user_id, filename, data, *, thread_id=None, run_id=None):
        saved.append((user_id, filename, data, thread_id, run_id))
        return {
            "id": f"file-{filename}",
            "filename": filename,
            "mime": "text/markdown" if filename.endswith(".md") else "image/png",
            "size": len(data),
            "versionNo": 2,
        }

    monkeypatch.setattr(user_file_service, "save_generated_bytes", _save)
    engine = SimpleNamespace(ctx=SimpleNamespace(user_id="u1", thread_id="run-session", run_id="workflow-run"))

    receipts = await agent_executor._collect_sandbox_deliverables(engine, _Sandbox())

    assert [item[1] for item in saved] == ["final-report.md", "chart.png"]
    assert all(item[3] == "run-session" and item[4] == "workflow-run" for item in saved)
    assert receipts == [
        {
            "id": "file-final-report.md",
            "filename": "final-report.md",
            "mime": "text/markdown",
            "size": len(b"data:/workspace/outputs/final-report.md"),
            "source": "generated",
            "versionNo": 2,
            "deliverable": True,
            "previewOnly": False,
            "origin": {"runId": "workflow-run", "tool": "skill_sandbox"},
        },
        {
            "id": "file-chart.png",
            "filename": "chart.png",
            "mime": "image/png",
            "size": len(b"data:/workspace/files/chart.png"),
            "source": "generated",
            "versionNo": 2,
            "deliverable": True,
            "previewOnly": False,
            "origin": {"runId": "workflow-run", "tool": "skill_sandbox"},
        },
    ]
