from app.services.agent_harness.model_driver import _provisional_plan_steps
import inspect
from app.services.agent_harness import model_driver


def test_research_multi_triggers_provisional_gate_v293():
    """Standard 模式不因检索次数注入临时计划。"""
    src = inspect.getsource(model_driver.drive_model)
    assert "_research_multi" not in src
    assert "_prior_research" not in src
    assert "_batch_research" not in src


def test_provisional_plan_steps_from_tools():
    steps = _provisional_plan_steps(["use_skill", "bash", "write_file", "update_plan"])
    assert len(steps) >= 2
    assert steps[0]["status"] in {"in_progress", "running"}
    titles = [s["title"] for s in steps]
    # v2.56+ 人话映射：use_skill →「准备要用的能力」，不再用「加载技能」
    assert any(("能力" in t) or ("技能" in t) for t in titles)
    assert all(s["status"] in {"in_progress", "pending"} for s in steps)


def test_provisional_plan_steps_dedupes():
    steps = _provisional_plan_steps(["bash", "bash", "bash"])
    assert len(steps) == 2  # bash + 完成交付
