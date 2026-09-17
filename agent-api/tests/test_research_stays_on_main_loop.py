"""Deep Research: GoalContract still seeds; coverage is owned by research.kernel."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCH = (ROOT / "app/services/agent_harness/orchestrator.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "app/services/agent_harness/research/engine.py").read_text(encoding="utf-8")
KERNEL = (ROOT / "app/services/agent_harness/research/kernel.py").read_text(encoding="utf-8")
RESEARCH_DIR = ROOT / "app/services/agent_harness/research"


def test_research_profile_dispatches_research_kernel():
    assert "is_research_profile" in ORCH
    assert "seed_research_state(run_id, message)" in ORCH
    assert "run_research_turn" in ORCH
    assert "from app.services.agent_harness.research.kernel import run as run_research_turn" in ORCH
    assert "async for payload in run_research_turn(env):" in ORCH
    assert "async for payload in main_tool_turn.run_agent_turn(env):" in ORCH
    assert "RESEARCH_ENGINE_ENABLED" not in ORCH


def test_research_turn_still_seeds_goal_contract():
    assert "from app.services.agent_harness.goal_contract import seed_goal_contract" in ORCH
    assert "goal_contract_prompt = seed_goal_contract(" in ORCH
    assert 'turn_route = "agent"' in ORCH
    assert "research_stage_prompt" in ORCH
    assert "goal_contract_prompt," in ORCH


def test_research_kernel_is_not_a_second_runtime():
    assert "class HarnessKernel" not in ENGINE
    assert "class HarnessKernel" not in KERNEL
    assert "async def run(" in KERNEL
    assert "main_tool_turn.run_agent_turn" in KERNEL
    assert "search_web" in KERNEL
    for path in RESEARCH_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "app.services.agent_harness.orchestrator" not in text
        assert "from app.services.agent_harness.kernel import" not in text
