"""主对话运行期的统一生命周期裁决。

这里故意只做纯状态判定，不依赖模型、数据库或工具实现。主循环中有关“已有交付物还能否
继续调用工具”的规则必须先构造同一份 :class:`RunPolicySnapshot`，再使用这里的结论；禁止
各自重新拼 ``delivery_checked/revision_open/artifact_review_pending`` 布尔表达式。

这样做的目标不是增加一层抽象，而是让两个最容易互相打架的事实拥有明确优先级：

1. 新用户指令开启的修订，以及质量返工，优先于旧版本的交付观测；
2. 预算、停滞和重复调用只记录为诊断指标，不产生任务终态或工具禁用；
3. 权限、ToolSpec、授权和资源锁是工具边界，完成与否由 CompletionVerifier 依据证据判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from app.services.agent_harness.contracts import ToolSpec


class RunPolicyPhase(str, Enum):
    WORKING = "working"
    REVISING = "revising"
    REVIEWING = "reviewing"
    DELIVERED = "delivered"
    CONVERGING = "converging"


# 历史交付原因保留用于读取旧 Run 的诊断字段；新代码不得用它们收束主循环。
DELIVERY_DERIVED_CONVERGENCE_REASONS = frozenset({
    "product_delivered",
    "post_delivery_idle",
    "post_delivery_rework",
    "resume_bare_confirm",
    "resume_verify_done",
    "post_mutate_verify_done",
    "post_mutate_verify_nudge",
})

@dataclass(frozen=True)
class RunPolicySnapshot:
    """一次主循环边界上的不可变事实快照。"""

    revision_epoch: int = 0
    revision_open: bool = False
    revision_requires_mutation: bool = False
    revision_mutation_verified: bool = False
    artifact_review_pending: bool = False
    has_deliverable: bool = False
    delivery_checked: bool = False
    claimed_delivery: bool = False
    force_converge: str = ""
    bare_confirm_only: bool = False

    @property
    def delivery_override_open(self) -> bool:
        """新修订或质量返工是否覆盖旧交付闸。"""
        return self.revision_open or self.artifact_review_pending

    @property
    def revision_commit_ready(self) -> bool:
        """当前修订是否具备提交终答的事实条件。"""
        return (
            not self.revision_open
            or not self.revision_requires_mutation
            or self.revision_mutation_verified
        )

    @property
    def closed_deliverable(self) -> bool:
        """已有产物，且当前没有合法修订/返工窗口。"""
        return self.has_deliverable and not self.delivery_override_open

    @property
    def phase(self) -> RunPolicyPhase:
        # 真熔断优先级高于修订；旧交付派生熔断则由修订覆盖。
        if self.force_converge and not (
            self.delivery_override_open
            and self.force_converge in DELIVERY_DERIVED_CONVERGENCE_REASONS
        ):
            return RunPolicyPhase.CONVERGING
        if self.revision_open:
            return RunPolicyPhase.REVISING
        if self.artifact_review_pending:
            return RunPolicyPhase.REVIEWING
        if self.closed_deliverable and (self.delivery_checked or self.claimed_delivery):
            return RunPolicyPhase.DELIVERED
        return RunPolicyPhase.WORKING

    def blocks_deliverable_tool(self, spec: ToolSpec) -> bool:
        """Compatibility hook; Harness never hides a valid tool by delivery state."""
        return False

    def should_force_final_after_blocked_batch(self, blocked: Iterable[bool]) -> bool:
        # Retained for old callers/telemetry. A blocked batch is an observation;
        # CompletionVerifier or the model decides what happens next.
        list(blocked)
        return False

    def should_stop_post_delivery_batch(self, specs: Iterable[ToolSpec]) -> bool:
        """Compatibility hook; a delivered artifact does not remove tool agency."""
        list(specs)
        return False


@dataclass(frozen=True)
class RunPolicyRepair:
    """循环边界发现非法生命周期组合时的确定性修复。"""

    clear_delivery_checked: bool = False
    clear_force_converge: bool = False
    clear_bare_confirm_only: bool = False
    reasons: tuple[str, ...] = ()


def reconcile_run_policy(snapshot: RunPolicySnapshot) -> RunPolicyRepair:
    """修复旧交付状态污染新修订/质量返工的非法组合。

    这是防御性第二道闸：正常入口在打开修订时就应清状态；即使未来某个分支漏清，下一轮
    边界也会恢复到一致状态，而不是让 tool_choice 被旧 ``force_converge`` 压成 ``none``。
    """
    if not snapshot.delivery_override_open:
        return RunPolicyRepair()
    reasons: list[str] = []
    clear_delivery = bool(snapshot.delivery_checked)
    clear_bare = bool(snapshot.bare_confirm_only)
    clear_converge = snapshot.force_converge in DELIVERY_DERIVED_CONVERGENCE_REASONS
    if clear_delivery:
        reasons.append("delivery_checked_overridden")
    if clear_bare:
        reasons.append("bare_confirm_overridden")
    if clear_converge:
        reasons.append("delivery_convergence_overridden")
    return RunPolicyRepair(
        clear_delivery_checked=clear_delivery,
        clear_force_converge=clear_converge,
        clear_bare_confirm_only=clear_bare,
        reasons=tuple(reasons),
    )


def convergence_allowed(snapshot: RunPolicySnapshot, reason: str) -> bool:
    """交付派生收敛不能关闭一个仍开放的修订/返工窗口。"""
    normalized = str(reason or "").strip()
    if not normalized:
        return False
    if (
        snapshot.delivery_override_open
        and normalized in DELIVERY_DERIVED_CONVERGENCE_REASONS
    ):
        return False
    return True
