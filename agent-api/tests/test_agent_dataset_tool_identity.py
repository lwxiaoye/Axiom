"""已发布智能体的知识库工具必须把 agent_id / 当前用户带给检索核心（可访问性校验依赖它们）。"""
from types import SimpleNamespace

import pytest

from app.services.agents import agent_executor
from app.services.agent_harness import model_driver


@pytest.mark.asyncio
async def test_published_agent_dataset_tool_passes_agent_and_current_user(monkeypatch):
    calls = []

    class _Engine:
        ctx = SimpleNamespace(token="token-a", app_id="agent-a", user_id="user-a", preview_only=False)

        @staticmethod
        def input_value(_node, key, default=None):
            if key == "agent_datasetParams":
                return {"datasets": [{"datasetId": "kb-a"}], "similarity": 0.4}
            return default

    async def fake_retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "chunks": [], "error": None}

    monkeypatch.setattr(model_driver, "retrieve_knowledge", fake_retrieve)
    tools, _notes = await agent_executor.build_tools(_Engine(), {})

    await tools[0].execute({"query": "图书馆开放时间"})

    assert calls[0][1]["agent_id"] == "agent-a"
    assert calls[0][1]["agent_user_id"] == "user-a"
