from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_harness.research import kernel
from app.services.agent_harness.research.contracts import ResearchLedger, ResearchStage, ResearchTopic


@pytest.mark.asyncio
async def test_research_checkpoint_is_model_authored_from_real_ledger(monkeypatch) -> None:
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return "定义与边界已经形成初步证据覆盖，剩余评测和官方口径仍需交叉核对。证据补齐后会转入综合写作。"

    monkeypatch.setattr(kernel, "generate_public_commentary", fake_generate)
    ledger = ResearchLedger(
        stage=ResearchStage.RESEARCHING,
        query="Kimi 的发展史",
        search_calls=2,
        topics=[
            ResearchTopic(topic_id="t1", title="定义与边界", status="completed"),
            ResearchTopic(topic_id="t2", title="方案对比与评测", status="pending"),
        ],
    )
    env = SimpleNamespace(
        message="深度研究 Kimi 的发展史",
        resolved_model="deepseek-v4-flash",
        newapi_key="key",
        run_id="research-run",
        thread_id="research-thread",
    )

    text = await kernel._research_progress_commentary(
        env, ledger, phase="首个研究主题已形成证据覆盖",
    )

    assert text.startswith("定义与边界已经形成")
    assert captured["model"] == "deepseek-v4-flash"
    assert "已完成主题：定义与边界" in captured["developer_prompt"]
    assert "待覆盖主题：方案对比与评测" in captured["developer_prompt"]
    assert "一个已经确认的具体结果" in captured["developer_prompt"]
    assert captured["run_id"] == "research-run"
    assert captured["thread_id"] == "research-thread"
    assert captured["purpose"] == "research_commentary"
    assert captured["purpose_detail"] == "首个研究主题已形成证据覆盖"


def test_research_kernel_emits_sparse_commentary_at_phase_boundaries() -> None:
    source = Path(kernel.__file__).read_text(encoding="utf-8")
    assert 'phase="首个研究主题已形成证据覆盖"' in source
    assert 'phase="恢复上次研究现场"' in source
    assert '"最低证据覆盖已满足，转入缺口判断与综合"' in source
    assert source.count("yield channel.message_commentary(commentary)") == 1
    assert source.count("yield env.channel.message_commentary(commentary)") == 2
