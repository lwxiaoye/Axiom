"""任务计划工具：创建或语义修订用户可读的 TODO/checklist。

步骤状态和证据由 Harness 根据真实工具回执自动推进；update_plan
不是下一个工具或最终回答的前置门槛。
"""
from typing import Any, Dict, List

from .base import MainTool, ToolValue, text_tool_body


def _normalize_plan_steps(raw: Any) -> List[Dict[str, Any]]:
    """Normalize model and persisted plan rows without reviving terminal steps."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:12]:  # 步数上限，防滥用
        detail = ""
        reason = ""
        key = ""
        required = True
        acceptance = ""
        if isinstance(item, str):
            title, status = item, "pending"
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("step") or item.get("name") or "").strip()
            status = str(item.get("status") or "pending").strip().lower()
            detail = str(item.get("detail") or item.get("intent") or "").strip()
            reason = str(item.get("reason") or item.get("status_reason") or "").strip()
            key = str(item.get("key") or item.get("step_key") or "").strip()
            required = bool(item.get("required", True))
            raw_acc = item.get("acceptance")
            if raw_acc is None:
                raw_acc = item.get("acceptance_criteria")
            if isinstance(raw_acc, list):
                acceptance = str(raw_acc[0] if raw_acc else "")
            else:
                acceptance = str(raw_acc or "")
        else:
            continue
        if not title:
            continue
        if status in ("in_progress", "running", "active", "doing", "ongoing"):
            status = "running"
        elif status in ("completed", "done", "finished", "complete", "ok"):
            status = "completed"
        elif status in ("failed", "error", "cancelled", "canceled"):
            status = "failed"
        elif status == "skipped":
            status = "skipped"
        elif status == "invalidated":
            status = "invalidated"
        else:
            status = "pending"
        row: Dict[str, Any] = {
            "title": title[:80], "status": status, "detail": detail[:240],
            "key": key[:64], "required": required, "reason": reason[:240],
        }
        acceptance = acceptance.strip()[:60]
        if acceptance:
            row["acceptance"] = acceptance
            row["acceptance_criteria"] = [acceptance]
        depends_on = item.get("depends_on") if isinstance(item, dict) else None
        if isinstance(depends_on, (list, tuple)):
            row["depends_on"] = [str(dep).strip()[:128] for dep in depends_on if str(dep).strip()][:12]
        requires = item.get("requires") if isinstance(item, dict) else None
        if isinstance(requires, (list, tuple)):
            row["requires"] = [str(tag).strip() for tag in requires if str(tag).strip()][:8]
        out.append(row)
    return out


def build_plan_tools() -> List[MainTool]:
    tools: List[MainTool] = []
    # update_plan 只负责创建/语义修订任务计划；运行时进度由工具 observation
    # 经 Plan Controller 推进。它不进工具时间线，也不阻塞下一个真实动作。
    async def _update_plan_noop(_args: dict) -> str:
        return "计划已更新。"

    tools.append(
        MainTool(
            name="update_plan",
            description=(
                "任务计划工具：多步/有产出物的任务（做PPT、写文档、查资料整理、多轮加工等）"
                "动手前先拆解、执行中持续维护进度。\n"
                "拆解方法（先想清楚再列）：①识别用户真正要的最终结果，而不只是表面动作；"
                "②提取时间/对象/格式/范围等约束，没写明的按合理假设（假设在回答正文中说明，不要当成事实）；"
                "③从最终产物倒推它由哪些部分组成；④按执行先后排成数组——"
                "第 1 条是第一步，最后一条是收尾，先做会影响后续的步骤必须写在前面；"
                "⑤控制在 3~7 步，每步是用户能看懂、互不重复的产出动作（title 写动作，detail 写该步预期产出）。\n"
                "高质量例子（写 Word）：确定文档结构 → 撰写正文 → 质检并交付。"
                "高质量例子（做 PPT）：理清演示结构 → 制作幻灯片 → 保存并交付。"
                "低质量：把后做的「撰写并生成全文」写到「确定大纲」前面；"
                "或同一件事拆成「撰写并生成」和「生成 Word」两步；"
                "或用「加载技能/检索资料/执行命令」这类内部话术充数。\n"
                "例如用户明确要文件时写「梳理报告结构」「撰写正文」「保存并交付」；"
                "若用户只是调研/分析/看值不值得、**未**要求写报告或保存文件，收尾步骤写「对话回复」，"
                "不要写「写入文件/保存并交付」；禁止工具名，不要拆得过细。\n"
                "使用：真正动手前（含做 PPT/演示稿）调用一次列出全部步骤"
                "（第 1 条 in_progress、其后 pending），让「任务协作」立刻可见。"
                "数组顺序就是执行顺序。工具成功/失败后，平台会依据真实回执自动维护"
                " completed / in_progress 和证据，不要为了同步状态反复调用本工具。"
                "当前未完成步骤里最多一个 in_progress，且必须是最早那一步。"
                "整表回传时按新的执行顺序重写完整清单；被替换的旧步骤不要再列入，"
                "不要把新步骤插到最前却把旧步骤留在后面。只有步骤结构、顺序、标题、"
                "完成标准或执行方向真正变化时才再调用；不得把 update_plan 当成下一个工具"
                "或最终回答的前置条件。"
                "不要拆「理解需求」「对话答复」这类空步骤。你只负责维护步骤文本和 pending / in_progress / completed 状态；"
                "可选 depends_on / requires 只是依赖与类型元数据，不是另一套调度。"
                "断点续做时优先更新未完成步骤的标题与验收，不要把已完成步骤改回 pending。一句话即可答的简单问题不要调用；单文件新建/改写（路径与内容一句话说清）也不要调用，直接 write_file/edit_file/bash 交付。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": (
                            "有序步骤清单，每次整表回传（非增量）。"
                            "数组下标 0 是第一步，最后一项是收尾；按这个顺序执行。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "步骤动作，一句话，用户可读"},
                                "detail": {"type": "string", "description": "该步的预期产出/关键点，≤40字，可选"},
                                "acceptance": {
                                    "type": "string",
                                    "description": "该步可核验的完成标准，≤60字；交付型步骤必填",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "pending 待开始 / in_progress 当前步骤 / completed 已完成",
                                },
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "可选：必须先完成的步骤 key，只作依赖元数据，不是调度图",
                                },
                                "requires": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "可选：该步需要的工具类型，如 investigate / productive",
                                },
                            },
                            "required": ["title", "status"],
                        },
                    },
                },
                "required": ["steps"],
            },
            execute=text_tool_body(_update_plan_noop),
            public_action="更新任务计划",
            output_model=ToolValue,
            capability="control.plan.update",
            effect_scope="none",
            idempotent=True,
            control_command=True,
        )
    )
    return tools
