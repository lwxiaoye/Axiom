"""Codex-aligned Plan-mode content contract and public block extraction."""

from __future__ import annotations

import re


PROPOSED_PLAN_OPEN = "<proposed_plan>"
PROPOSED_PLAN_CLOSE = "</proposed_plan>"

_COMPLETE_PLAN_BLOCK_RE = re.compile(
    r"<proposed_plan>\s*\n(?P<body>[\s\S]*?)\n\s*</proposed_plan>",
    re.I,
)
_OPEN_PLAN_BLOCK_RE = re.compile(r"<proposed_plan>\s*(?:\n|$)", re.I)


def extract_proposed_plan(text: str) -> str | None:
    """Return one official plan body, tolerating a missing close tag at EOF.

    The provider stream is already terminal-complete when this runs. Text outside
    the block is intentionally excluded from the Plan card, matching Codex's
    first-class Plan item instead of leaking transport tags into assistant text.
    """
    raw = str(text or "")
    complete = list(_COMPLETE_PLAN_BLOCK_RE.finditer(raw))
    if len(complete) == 1:
        body = complete[0].group("body").strip()
        return body or None
    if len(complete) > 1:
        return None
    opened = _OPEN_PLAN_BLOCK_RE.search(raw)
    if opened is None:
        return None
    body = raw[opened.end():]
    body = re.sub(r"\s*</proposed_plan>\s*$", "", body, flags=re.I).strip()
    return body or None


def public_plan_text(text: str) -> str:
    """Strip Plan transport tags while retaining legacy untagged reports."""
    return extract_proposed_plan(text) or str(text or "").strip()


PLAN_CONTENT_CONTRACT = """\
计划内容按三个阶段搭建，只有达到“交给另一位工程师即可直接实施、无需再替你做产品或技术决策”时才定稿：
1. **环境落地（先查后问）**：至少做一轮有针对性的只读勘查，核对相关入口、配置、接口、类型、现状与约束。能从代码、文件、系统或已有材料确认的事实自己确认，不向用户提问。
2. **意图收敛**：确认目标与成功标准、受众、范围内/范围外、约束、当前状态，以及会实质改变方案的偏好或取舍。只有不可通过勘查得到、且会显著改变计划的事项才一次性调用 `ask_user_choice` 询问；有合理默认值时给出推荐默认，并在最终计划中记为假设。
3. **实施收敛**：把方案补到决策完备，覆盖实现路径、关键接口/API/schema/type 或 I/O 变化、数据流、兼容与迁移、边界/失败模式、测试与验收；不需要的章节不要为了格式硬凑。

正式计划必须且只能输出一个以下形式的完整块，标签独占一行、标签保持英文，块外不要写寒暄、过程复盘或“是否继续”：
<proposed_plan>
# 清楚的一句话标题
## Summary
用一小段说明目标、当前事实和采用的总体方案。
## Key Changes
- 按子系统或行为分组写 3–7 个高信号实施项；与 `update_plan` 的步骤语义一致。
- 涉及公开 API、接口、schema、type、I/O 或兼容边界时明确写出；不涉及则直接说明“无公开接口变化”。
## Test Plan
- 写可执行的关键场景、失败场景与客观验收标准。
## Assumptions
- 只写仍需采用的假设、默认值和明确不做的范围；没有则写“无”。
</proposed_plan>

默认保持 3–5 个短章节；按行为/子系统组织，不做逐文件流水账。除非避免误解所必需，路径不超过 3 个。计划要简洁但决策完备；修订时输出一整份完整替代计划，不能只给增量补丁。"""
