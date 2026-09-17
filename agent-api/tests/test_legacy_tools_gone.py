# -*- coding: utf-8 -*-
"""旧 execute_in_sandbox 工具族彻底下线的门禁（2026-07-29 用户拍板"只保留现在的 bash"）。

这个文件的方向与 `test_prompt_tool_consistency.py` 互补：那边守"提示词提到的工具必须
真的注册"，这边守"**已删除的工具名不得以任何方式回来**"。

为什么要有它：本仓库的历史教训是**退休一个工具会同时断掉五处**（提示词/控制器分支/
任务模式白名单/前端分支/SKILL.md），而且删除型改动的失败形态是"某条链路没人承担了
而所有测试照绿"——测试通常只测"存在的东西对不对"，不测"消失的东西谁接手"。
所以这里既断言"消失了"，也断言"承接者在"。

⚠️ 一个必须记住的区分：`sandbox_executor.execute_in_sandbox()` 是**沙箱内核函数**，bash 自己就走它
（`language="bash"`）。它**不在**本文件的清理范围内，删它等于把 bash 一起废掉。
这里管的是"暴露给模型的工具名"。
"""
import pytest

from app.services.chat.tools import build_tools
from app.services.chat.turn_context_builder import _build_system_prompt

# 彻底消失的工具名（不再有任何注册路径）
GONE = [
    "execute_in_sandbox",           # 执行能力 → bash
    "create_file",        # → write_file（路径版）
    "update_file",        # → edit_file（路径版）
    "list_files",         # → glob（路径版没有 list_files）
    # office 族（2026-07-27 已退休，这里一并锁死）
    "build_docx", "edit_docx", "build_presentation", "apply_presentation_patch",
    "inspect_workbook", "apply_excel_patch", "convert_to_pdf", "verify_artifact",
]

# 承接者：必须同时在场，否则"删掉了"就等于"能力丢了"
SUCCESSORS = ["bash", "write_file", "edit_file", "read_file", "glob", "download_url"]

# 覆盖多种授权/意图组合——历史上就出现过"某个 scope 分支下漏摘/漏加"的缺陷
COMBOS = [
    pytest.param("帮我做一份PPT", "execution", "mutate", id="执行轮-PPT"),
    pytest.param("写份周报保存为 a.md", "execution", "mutate", id="执行轮-写文件"),
    pytest.param("把那个报告改简洁一点", "revise", "mutate", id="修订轮"),
    pytest.param("分析一下这段代码", "conversation", "inspect", id="只读轮"),
]


async def _names(msg: str, intent: str, authority: str) -> set:
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u-gone",
        thread_id="th-gone", newapi_key="k", run_id="r-gone",
        user_message=msg, turn_intent=intent, action_authority=authority,
    )
    return {t.name for t in tools}


@pytest.mark.asyncio
@pytest.mark.parametrize("msg,intent,authority", COMBOS)
async def test_removed_tools_never_register(msg, intent, authority):
    names = await _names(msg, intent, authority)
    leaked = sorted(set(GONE) & names)
    assert not leaked, (
        f"已删除的工具又出现在工具集里：{leaked}。"
        "模型会被教一套不存在的用法，或拿到没有提示词支撑的旧工具——"
        "这正是 2026-07-27 那次「提示词先行、注册滞后」事故的反向形态。"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("msg,intent,authority", COMBOS)
async def test_successors_are_present_where_they_should_be(msg, intent, authority):
    """阳性对照：删除不能把能力一起删掉。

    只读轮按设计物理摘掉写工具，所以那一轮只要求读侧承接者在场——
    否则这条断言会把「只读边界」误判成缺陷。
    """
    names = await _names(msg, intent, authority)
    expected = ["read_file", "glob"] if authority != "mutate" else SUCCESSORS
    missing = [n for n in expected if n not in names]
    assert not missing, f"承接者缺席：{missing}——删掉旧工具后这条能力没人接了"


def test_base_prompt_teaches_no_removed_tool():
    prompt = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="")
    taught = [n for n in GONE if n in prompt]
    assert not taught, (
        f"系统提示词还在教已删除的工具：{taught}——模型照做会收到「未知工具」并把轮次烧光"
    )


def test_workspace_module_no_longer_defines_legacy_tools():
    """结构断言：定义侧也要真的没了（只靠 build_tools 过滤会留下"注册了再摘掉"的隐患）。

    用 AST 而不是文本搜索：注释与 docstring 里逐字写着这些名字（解释为什么删的），
    grep 必然数到说明自己（本仓库当天栽过至少六次）。
    """
    import ast
    import inspect

    from app.services.chat.tools import workspace

    tree = ast.parse(inspect.getsource(workspace))
    registered = set()
    for node in ast.walk(tree):
        # MainTool(name="xxx", ...) 的 name= 关键字实参
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "MainTool":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    registered.add(kw.value.value)
    assert registered, "AST 没解析出任何 MainTool——判据此刻是瞎的，先修这条辅助"
    leaked = sorted(set(GONE) & registered)
    assert not leaked, f"workspace.py 仍在定义已删除的工具：{leaked}"
    assert "use_skill" in registered, (
        "use_skill 被误删了——它不属于旧 execute_in_sandbox 族，是技能加载的唯一入口"
    )


# ---------- 同名但不同实现:名字判据盖不住的那一层（2026-07-30） ----------
#
# 中心调度会话在核这批改动时踩了一次：它把 read_file/edit_file 也列进"应该消失"的名单，
# 于是报出「残留旧工具: read_file, edit_file」——看起来像我删漏了两个。
# 真相是它俩是**路径版**，名字与被退休的 file_id 版一模一样，本来就该留。
#
# 这暴露了上面那批断言的盲区：它们测的是"名字在不在"，而两套实现共用名字 ——
#   · read_file/edit_file 绝不能进 GONE（否则门禁永久变红）
#   · 反过来，**若有人误把路径版换回 file_id 版，名字判据一个字都不会报警**
# 要覆盖这个方向只能断言**行为**：参数 schema 收 path、不收 file_id。

@pytest.mark.asyncio
async def test_same_named_tools_are_the_path_addressed_implementation():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u-addr",
        thread_id="th-addr", newapi_key="k", run_id="r-addr",
        user_message="读一下报告", turn_intent="execution", action_authority="mutate",
    )
    by_name = {t.name: t for t in tools}
    for name in ("read_file", "edit_file", "write_file"):
        tool = by_name.get(name)
        assert tool is not None, f"{name} 不在工具集里——承接者缺席"
        props = ((tool.parameters or {}).get("properties") or {})
        assert "path" in props, (
            f"{name} 的参数里没有 path —— 它可能被换回了 file_id 版实现，"
            "而名字判据对这种回退完全无感"
        )
        assert "file_id" not in props, (
            f"{name} 的参数里出现了 file_id —— 两个地址空间又并存了，"
            "模型脑子里会重新有两套寻址（这正是统一文件系统要消掉的东西）"
        )


def test_gone_list_excludes_names_that_still_live():
    """防呆：别把仍在服役的名字写进 GONE（那会让门禁永久变红，然后被人整条注释掉）。"""
    still_alive = {"read_file", "edit_file", "write_file", "glob", "bash", "use_skill"}
    overlap = sorted(set(GONE) & still_alive)
    assert not overlap, f"GONE 里混进了仍在服役的工具名：{overlap}"
