"""v2.71: first-frame plan seed + quota fail-fast + hedge widen."""
from pathlib import Path

from app.services.chat.turn_finalizer import scrub_false_search_hedge, _trace_search_web_has_results
from app.services.agent_harness.orchestrator import _goal_next_action_text


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_accept_seeds_product_plan_not_lookup():
    # v2.89: 受理阶段一律不 seed 任务板；产品/检索都只发 next-action
    src = _src("app/services/agent_harness/orchestrator.py")
    accept = src[src.find("async def accept_harness_run"): src.find("async def start_resume_chat_run")]
    assert "task_plan_updated" not in accept
    assert "message_commentary" in accept
    assert accept.find("message_commentary") < accept.find("enqueue_job")


def test_plain_fallback_is_governed_by_runtime_policy_not_error_text():
    # 旧版按「额度不足」错误文案决定不回退普通问答；现役由 RuntimePolicy.allow_plain_fallback
    # 与 convert_loop_failure（内置助手把耗尽重试的失败改判为可读终态）统一裁定。
    src = _src("app/services/chat/main_tool_turn.py")
    assert "模型额度不足，工具循环直接失败（不回退普通问答）" not in src
    assert "free quota exhausted" not in src
    assert "if runtime_policy and not runtime_policy.allow_plain_fallback:" in src
    assert "converted = await convert_loop_failure(runtime_policy, env, e)" in src


def test_lookup_nudge_retired_from_tool_loop():
    # 「这是实时信息查询，先 search_web」关键词 nudge 已退役：检索与否由模型决定
    src = _src("app/services/agent_harness/model_driver.py")
    assert "net_lookup_first_nudge" not in src
    assert "这是实时信息查询" not in src


def test_weather_next_action():
    text = _goal_next_action_text("你帮我看看今天漳州市的天气状况", pure_qa=False)
    # 脚本化开场下线：空串，由模型自行产出
    assert text == ""


def test_short_preview_is_search_hit():
    assert _trace_search_web_has_results([
        {"name": "search_web", "status": "completed", "preview": "漳州 28℃"},
    ])


def test_completed_empty_preview_still_hit():
    assert _trace_search_web_has_results([
        {"name": "search_web", "status": "completed", "preview": ""},
    ])
