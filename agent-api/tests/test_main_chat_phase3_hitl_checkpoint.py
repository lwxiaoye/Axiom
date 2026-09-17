# -*- coding: utf-8 -*-
"""阶段 3 最小：checkpoint 字段契约 + resume V2 源码锁（shipped）。"""
from pathlib import Path

from app.services.chat import run_hub
from app.services.agent_harness import run_store


def test_runtime_v2_new_run_state_includes_checkpoint_key():
    state = run_store.new_run_state(decision_route="agent")
    assert "last_checkpoint_sequence" in state
    assert int(state.get("last_checkpoint_sequence") or 0) == 0


def test_run_hub_pump_contains_checkpoint_write():
    src = Path(run_hub.__file__).read_text(encoding="utf-8")
    assert "last_checkpoint_sequence" in src


def test_frontend_resume_uses_v2_path():
    """前端在 monorepo 根 src/ 下；容器内仅有 agent-api 时跳过，由 host jest 覆盖。"""
    # agent-api/tests → agent-api → repo root
    root = Path(__file__).resolve().parents[2]
    agent_api = root / "src" / "views" / "peopleCenter" / "agentApi.ts"
    if not agent_api.is_file():
        # docker 仅挂 agent-api 目录时的诚实跳过（host 上 pytest 或 jest 仍覆盖）
        return
    text = agent_api.read_text(encoding="utf-8")
    assert "/chat/runs/" in text and "resume" in text
    assert "requestAgentApiStream('/chat/resume'" not in text
    assert "X-Protocol-Version" in text
