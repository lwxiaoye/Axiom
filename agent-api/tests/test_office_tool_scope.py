import pytest

from app.services.agent_harness import model_driver
from app.services.agent_harness.contracts import AgentMode, RunPhase, RunSnapshot
from app.services.agent_harness.policy import HarnessPolicy


def _inspect_visible(tools):
    run = RunSnapshot(
        run_id="r", thread_id="t", user_id="u",
        agent_mode=AgentMode.STANDARD, phase=RunPhase.EXECUTING,
        capability_scope="inspect", state_version=0, goal_revision=0,
        plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    policy = HarnessPolicy()
    return [tool for tool in tools if policy.evaluate(tool.spec, run).allowed]

# 2026-07-27：io / SimpleNamespace / office_executor / _xlsx_bytes() 一并删除 ——
# 本文件在 Office 工具退休后已改写成三条**反向断言**（工具不得复活 / 通用执行器要兜底 /
# 只读轮次不得有写工具），不再需要真实 xlsx 夹具，留着就是无人使用的残留。


@pytest.mark.asyncio
async def test_office_tools_no_longer_in_main_chat():
    """2026-07-27 用户拍板「不需要给主对话太多余的工具，越简单越好」：Office 专用工具全部退休。

    前提已成立——沙箱镜像里 python-docx / openpyxl / python-pptx / pypdf 全部可用，bash 一条
    命令就能做它们做的事且更灵活。「每格式一个动词」本来就是 execute_in_sandbox 只能跑 Python 时代的
    产物，那个限制已经没了。`_office` 模块本身保留（工作流节点侧仍在用），只是不进主对话清单。
    """
    for message in ("写一份项目周报word文档", "把数据整理成excel表格", "帮我做一个PPT"):
        tools = await model_driver.build_tools(
            token="", knowledge_ids=None, web_enabled=False, user_id="u-office",
            user_message=message, turn_intent="execution", action_authority="mutate",
        )
        names = {tool.name for tool in tools}
        assert not (names & {
            "build_docx", "edit_docx", "inspect_workbook", "apply_excel_patch",
            "build_presentation", "apply_presentation_patch", "convert_to_pdf",
        }), f"{message}：Office 专用工具已退休，不该再出现"


@pytest.mark.asyncio
async def test_advanced_workbook_request_keeps_generic_executor_fallback():
    """原意图保留：高级表格需求必须有一个通用执行器兜底。原为 execute_in_sandbox，现为 bash。"""
    tools = await model_driver.build_tools(
        token="", knowledge_ids=None, web_enabled=False, user_id="u-office",
        user_message="用数据透视和公式做一份复杂的销售分析表格", turn_intent="execution",
        action_authority="mutate",
    )
    names = {tool.name for tool in tools}
    assert "bash" in names, "高级需求必须保留通用执行器兜底"


@pytest.mark.asyncio
async def test_inspect_authority_never_exposes_write_tools():
    """原意图完整保留，只是对象从 Office 写工具换成了新的写工具集合。

    Harness：工具集合本身即授权边界，不能只靠 prompt 约束。
    """
    tools = await model_driver.build_tools(
        token="", knowledge_ids=None, web_enabled=False, user_id="u-office",
        user_message="帮我看看这个表格怎么改比较好", turn_intent="conversation",
        action_authority="inspect",
    )
    visible = _inspect_visible(tools)
    names = {tool.name for tool in visible}
    assert not (names & {
        "write_file", "edit_file",
        "build_docx", "edit_docx", "apply_excel_patch", "convert_to_pdf",
    }), "只读/勘查轮次不得暴露任何写工具"
    assert {"read_file", "glob", "bash"} <= names, "读取与临时执行工具应该还在"
    bash = next(tool for tool in visible if tool.name == "bash")
    assert bash.spec.effect_scope.value == "scratch"
    assert "artifact_producer" not in bash.spec.semantic_tags
