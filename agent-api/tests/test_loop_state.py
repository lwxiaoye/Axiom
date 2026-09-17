"""LoopState 安全网状态机单测（结构手术 Phase 1a 产物）。

拆分前这组状态是 drive_model 的散装局部变量,无法单测;拆出后直接验证
三张安全网的触发条件与 resume 反推回填,与 test_drive_model_persistence
的集成路径互补。
"""
import json
import unittest

from app.services.agent_harness.model_driver import (
    LoopState, _VALIDITY_EXHAUSTED_PREFIX, _VALIDITY_RETRY_PREFIX,
    _budget_notice, _final_loop_event, _recover_loop_state,
)
from app.services.chat.tools.base import MainTool


async def _ok(_args):
    return "ok"


_RECOVERY_TOOLS = {
    "bash": MainTool(
        "bash", "bash", {}, _ok, effect_scope="user_files",
        semantic_tags=("artifact_producer", "revision_targeted"),
    ),
    "update_plan": MainTool(
        "update_plan", "plan", {}, _ok, internal=True, control_command=True,
    ),
}


class LoopStateTests(unittest.TestCase):
    def test_defaults_allow_natural_finish(self):
        st = LoopState()
        self.assertFalse(st.plan_incomplete)
        self.assertTrue(st.repair_budget_left)
        self.assertFalse(st.artifact_review_pending)

    def test_plan_incomplete_by_status(self):
        st = LoopState()
        st.latest_plan_steps = [{"title": "a", "status": "completed"}]
        self.assertFalse(st.plan_incomplete)
        st.latest_plan_steps.append({"title": "b", "status": "running"})
        self.assertTrue(st.plan_incomplete)
        st.latest_plan_steps = [{"title": "c", "status": "pending"}]
        self.assertTrue(st.plan_incomplete)

    def test_repair_budget_exhaustion_paths(self):
        # 工具侧重跑额度耗尽 → 不再推回
        st = LoopState(quality_rework_rounds=LoopState.QUALITY_REWORK_MAX)
        self.assertFalse(st.repair_budget_left)
        # 自愿停手推回额度耗尽 → 同样不再推回（转草稿收尾）
        st = LoopState(artifact_repair_prompts=LoopState.ARTIFACT_REPAIR_MAX)
        self.assertFalse(st.repair_budget_left)
        # 各差一次 → 仍可推回
        st = LoopState(
            quality_rework_rounds=LoopState.QUALITY_REWORK_MAX - 1,
            artifact_repair_prompts=LoopState.ARTIFACT_REPAIR_MAX - 1,
        )
        self.assertTrue(st.repair_budget_left)

    def _messages_with_executor_result(self, content: str) -> list:
        """夹具的工具名必须是**现役执行器**（2026-07-29）。

        `_recover_loop_state` 只对 `source in _ARTIFACT_EXECUTORS` 的回执反推门禁状态，
        而下线的 execute_in_sandbox 已不在那个集合里 —— 用它当夹具，这三条 resume 反推用例测的就变成
        "不该被反推的工具确实没被反推"，与标题声称的相反，且只表现为一句 False is not true。
        """
        return [
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": content},
        ]

    def test_recover_from_retry_prefix(self):
        """resume 反推:最近一次 bash 带「仍在修复内」前缀 → 门禁挂起但额度未耗尽。"""
        msgs = self._messages_with_executor_result(_VALIDITY_RETRY_PREFIX + "溢出 2 处]")
        st = LoopState.recover_from(_recover_loop_state(msgs, _RECOVERY_TOOLS))
        self.assertTrue(st.artifact_review_pending)
        self.assertTrue(st.repair_budget_left)

    def test_recover_from_exhausted_prefix(self):
        """「额度已耗尽」前缀 → 回填为满额度:resume 后不得再静默返工。"""
        msgs = self._messages_with_executor_result(_VALIDITY_EXHAUSTED_PREFIX + "，请收尾]")
        st = LoopState.recover_from(_recover_loop_state(msgs, _RECOVERY_TOOLS))
        self.assertTrue(st.artifact_review_pending)
        self.assertFalse(st.repair_budget_left)
        self.assertEqual(st.quality_rework_rounds, LoopState.QUALITY_REWORK_MAX)
        self.assertEqual(st.artifact_repair_prompts, LoopState.ARTIFACT_REPAIR_MAX)

    def test_recover_clean_executor_result_resets_gate(self):
        """最近一次 bash 无内部前缀（过检/未触发）→ 门禁清零。"""
        msgs = (self._messages_with_executor_result(_VALIDITY_RETRY_PREFIX + "x]")
                + self._messages_with_executor_result("产物已保存"))
        st = LoopState.recover_from(_recover_loop_state(msgs, _RECOVERY_TOOLS))
        self.assertFalse(st.artifact_review_pending)

    def test_recover_restores_plan_steps(self):
        msgs = [{
            "role": "assistant", "tool_calls": [{
                "id": "p1", "type": "function",
                "function": {"name": "update_plan", "arguments": json.dumps(
                    {"steps": [{"title": "写大纲", "status": "in_progress"}]},
                    ensure_ascii=False)},
            }],
        }]
        st = LoopState.recover_from(_recover_loop_state(msgs, _RECOVERY_TOOLS))
        self.assertTrue(st.plan_incomplete)
        self.assertEqual(st.latest_plan_steps[0]["title"], "写大纲")
        self.assertEqual(st.latest_plan_steps[0]["status"], "running")

    def test_recover_does_not_turn_plan_bookkeeping_into_an_execution_gate(self):
        steps = [{"title": "写大纲", "status": "in_progress"}]
        msgs = [
            {"role": "assistant", "tool_calls": [{
                "id": "p1", "type": "function",
                "function": {
                    "name": "update_plan",
                    "arguments": json.dumps({"steps": steps}, ensure_ascii=False),
                },
            }]},
            {"role": "tool", "tool_call_id": "p1", "content": "计划已更新。"},
            {"role": "assistant", "tool_calls": [{
                "id": "b1", "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})},
            }]},
            {"role": "tool", "tool_call_id": "b1", "content": "执行完成"},
        ]
        recovered = _recover_loop_state(msgs, _RECOVERY_TOOLS)
        st = LoopState.recover_from(recovered)
        self.assertNotIn("plan_sync_required", recovered)
        self.assertEqual(st.latest_plan_steps[0]["title"], "写大纲")


class SteeringBudgetCapTests(unittest.TestCase):
    """插话额度保留为有界 telemetry，不改变任务终态契约。"""

    def test_steering_extra_is_bounded(self):
        st = LoopState()
        quality_cap = 20
        for _ in range(50):  # 模拟用户反复插话
            st.steering_extra_used = min(
                st.steering_extra_used + 2, LoopState.STEERING_EXTRA_MAX)
            st.extra_budget = min(
                st.extra_budget + 2, quality_cap + LoopState.STEERING_EXTRA_MAX)
        self.assertEqual(st.steering_extra_used, LoopState.STEERING_EXTRA_MAX)
        self.assertEqual(st.extra_budget, quality_cap + LoopState.STEERING_EXTRA_MAX)
        self.assertLessEqual(st.extra_budget, 40, "熔断线不得被插话推到离谱的高度")


class BudgetNoticeTests(unittest.TestCase):
    """兼容预算字段只做单调 telemetry bookkeeping，不注入模型指令。"""

    def test_no_notice_while_budget_ample(self):
        st = LoopState()
        self.assertEqual(_budget_notice(step=0, budget_total=40, state=st), "")
        self.assertEqual(st.budget_notice_level, 0)

    def test_half_then_low_each_fires_once(self):
        st = LoopState()
        first = _budget_notice(step=20, budget_total=40, state=st)   # 剩 50%
        self.assertIn("已用过半", first)
        self.assertEqual(st.budget_notice_level, 1)
        # 同一档再问不重复发
        self.assertEqual(_budget_notice(step=21, budget_total=40, state=st), "")
        low = _budget_notice(step=30, budget_total=40, state=st)     # 剩 25%
        self.assertIn("只剩", low)
        self.assertEqual(st.budget_notice_level, 2)
        self.assertEqual(_budget_notice(step=35, budget_total=40, state=st), "")

    def test_low_reached_directly_skips_half(self):
        """预算很小的场景直接落到见底档,不该先补一条「过半」。"""
        st = LoopState()
        msg = _budget_notice(step=9, budget_total=10, state=st)
        self.assertIn("只剩", msg)
        self.assertEqual(st.budget_notice_level, 2)

    def test_extra_budget_growth_does_not_regress_level(self):
        """返工/插话把分母抬大后剩余比例回升,档位不回退、也不重复提醒。"""
        st = LoopState()
        _budget_notice(step=30, budget_total=40, state=st)
        self.assertEqual(st.budget_notice_level, 2)
        self.assertEqual(_budget_notice(step=30, budget_total=60, state=st), "")
        self.assertEqual(st.budget_notice_level, 2)

    def test_zero_budget_is_silent(self):
        st = LoopState()
        self.assertEqual(_budget_notice(step=0, budget_total=0, state=st), "")


class LoopSafetyResumeTests(unittest.TestCase):
    def test_recover_from_never_restores_force_converge(self):
        st = LoopState.recover_from({
            "latest_plan_steps": [{"title": "写大纲", "status": "running"}],
            "artifact_review_pending": False,
            "repair_exhausted": False,
            "mutated": True,
            "revision_epoch": 2,
            "revision_open": True,
            "revision_requires_mutation": True,
            "revision_mutation_verified": False,
            "locked_subagent_id": "",
            "subagent_call_count": 0,
            "disabled_tools_seen": [],
        })
        self.assertEqual(st.force_converge, "")
        self.assertTrue(st.revision_open)
        self.assertEqual(st.revision_epoch, 2)
        self.assertTrue(st.latest_plan_steps)

    def test_apply_persisted_safety_restores_budget_not_force_converge(self):
        st = LoopState()
        st.force_converge = "rounds"
        st.apply_persisted_safety({
            "extra_budget": 4,
            "quality_extra_used": 2,
            "steering_extra_used": 2,
            "bare_confirm_only": True,
            "resume_started_incomplete": True,
            "revision_open": True,
            "revision_epoch": 1,
            "force_converge": "stagnation",
            "budget_notice_level": 2,
        })
        self.assertEqual(st.extra_budget, 4)
        self.assertEqual(st.quality_extra_used, 2)
        self.assertFalse(st.bare_confirm_only)
        self.assertTrue(st.resume_started_incomplete)
        self.assertEqual(st.force_converge, "")
        self.assertEqual(st.budget_notice_level, 2)

    def test_safety_snapshot_omits_force_converge(self):
        st = LoopState(force_converge="token", extra_budget=3)
        snap = st.safety_snapshot(steps_used=9)
        self.assertNotIn("force_converge", snap)
        self.assertEqual(snap["extra_budget"], 3)
        self.assertEqual(snap["steps_used"], 9)

    def test_convergence_reason_is_telemetry_only(self):
        st = LoopState()
        self.assertTrue(st.request_convergence("stagnation"))
        self.assertEqual(st.force_converge, "stagnation")
        event = _final_loop_event(
            st, answer="继续", trace=[], usage_prompt_tokens=0, step=41,
        )
        self.assertNotIn("task_outcome", event)
        self.assertNotIn("force_converge", event)

    def test_download_url_args_are_not_the_same_stagnation_hash(self):
        from app.services.agent_harness.model_driver import _args_repeat_hash
        one = _args_repeat_hash({"url": "https://cdn.example/图1.png"})
        two = _args_repeat_hash({"url": "https://cdn.example/图2.png"})
        self.assertNotEqual(one, two)


if __name__ == "__main__":
    unittest.main()
