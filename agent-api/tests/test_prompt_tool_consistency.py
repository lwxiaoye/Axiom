# -*- coding: utf-8 -*-
"""提示词与工具集的一致性门禁（2026-07-27）。

为什么需要这条：2026-07-27 退休 10 个工具那天，系统提示词一个字都没改。实测基础提示词里
`execute_in_sandbox` 出现 6 次、`create_file` 3 次，而 `bash`/`write_file`/`glob` **各 0 次** ——
模型被反复教一套不存在的工具，调用后收到"未知工具"+failed，叠加同类错误止损会烧完轮次
然后向用户宣布做不到。整整一天没人发现，因为没有任何断言看着这件事。

工具描述由代码生成、提示词是手写长字符串，两者天然会漂移；靠人读是读不出来的。
"""
import pytest

from app.core.config import settings
from app.services.chat.tools import build_tools
from app.services.chat.turn_context_builder import _build_system_prompt

# 浏览器四工具只在 BROWSER_SERVICE_URL 非空时注册，提示词里那段也只在非空时出现。
# 门禁必须自己钉住这个前提，否则这条断言的结论取决于跑测试那台机器的 .env——
# 一台配了、一台没配，两边"绿"的含义完全不同（2026-07-28）。
_BROWSER_URL = "http://agent-browser:8089/mcp"

# ---- 只剩一种形态了（2026-07-29 用户拍板"只保留现在的 bash"）----
#
# 这里原先按 SANDBOX_WORKSPACE_SYNC 分两支断言：开关关闭时 execute_in_sandbox / list_files /
# update_file / create_file 是**真实注册**的活工具，execute_in_sandbox + /workspace/outputs/ 更是那
# 一支里唯一会把产物回收进「我的文件」的通道，所以那批名字当时只能"有条件退休"。
#
# 现在开关连同回退形态整个消失，沙箱同步是唯一形态：那批名字**在任何配置下都不存在**，
# 清单因此收成一张无条件的 RETIRED，参数化 `_FORMS` 一并去掉。
# 留下的教训不变：门禁要自己钉住前提，不能按跑测试那台机器的 env 判定（见上方
# BROWSER_SERVICE_URL 那段——它是本文件里仍然需要 monkeypatch 的那个）。
#
# ⚠️ 与 `sandbox_executor.execute_in_sandbox()` 无关：那是沙箱内核函数，bash 自己就走它。这里管的
# 是"提示词教给模型的工具名"。
RETIRED = [
    # 执行/文件族（2026-07-29 下线）→ bash / write_file / edit_file / glob
    "execute_in_sandbox", "create_file", "update_file", "list_files",
    # 随 execute_in_sandbox 同进退的参数名与目录约定
    "fetch_urls", "file_ids", "/workspace/outputs", "/workspace/inputs",
    # Office 专用工具（2026-07-27 拍板全部退休，chat/tools/office.py 整模块已删）
    "build_docx", "edit_docx", "build_presentation", "apply_presentation_patch",
    "inspect_workbook", "apply_excel_patch", "convert_to_pdf", "verify_artifact",
]

# 真正注册、且提示词必须提到的现役工具（实测口径：build_tools 的 execution/mutate 轮）。
CURRENT_TOOLS = ["bash", "write_file", "glob", "read_file", "download_url", "edit_file"]


def _base_prompt() -> str:
    return _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="")


@pytest.mark.parametrize("name", RETIRED)
def test_base_prompt_teaches_no_retired_tool(name):
    """无条件（2026-07-29）：这批名字在任何配置下都不注册，提示词一次都不该教。"""
    assert name not in _base_prompt(), (
        f"系统提示词还在教已退休的 {name!r}——"
        "模型照做会收到「未知工具」并把轮次烧光。退休一个工具时必须同步改提示词，"
        "否则表面无异常、实际每轮都在诱导幻觉调用。"
    )


def test_base_prompt_mentions_current_tools():
    """现役工具必须被提到，否则模型不知道自己有这些手段。"""
    missing = [n for n in CURRENT_TOOLS if n not in _base_prompt()]
    assert not missing, f"提示词一次都没提 {missing}，模型不会知道该用它们"


@pytest.mark.parametrize("name", ["browser_fetch", "browser_open", "browser_act", "browser_close"])
def test_base_prompt_teaches_browser_tools_when_service_configured(monkeypatch, name):
    """浏览器四工具必须在提示词里有用法说明（2026-07-28 补）。

    此前全库只有 turn_context_builder 的 intent 清单里出现过**一次** browser_fetch，
    browser_open/act/close 一次都没提——模型因此不知道自己能「读这个指定链接」，
    只会 search_web 搜关键词；也不知道打开的页面能跨轮接着操作。
    功能上线了、提示词零指导，等于没上线，而且没有任何断言看着这件事。
    """
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", _BROWSER_URL, raising=False)
    assert name in _base_prompt(), f"提示词一次都没教 {name!r}，模型不会知道该用它"


def test_browser_block_absent_when_service_unconfigured(monkeypatch):
    """反过来同样重要：服务没配时这四个工具不注册，提示词就不该教它们。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "", raising=False)
    prompt = _base_prompt()
    for name in ("browser_open", "browser_act", "browser_close"):
        assert name not in prompt, (
            f"BROWSER_SERVICE_URL 为空时 {name!r} 根本不注册，提示词还在教它 = 诱导幻觉调用")


# 提示词里可能以「调用 X」「用 X」形式点名的工具全集（人工维护，出现新说法时一起加）。
# **退休名字故意留在这张表里**：mentioned 由真实提示词文本算出，所以留着它们只会让
# 检查更宽进、断言更严出——哪天提示词又把 execute_in_sandbox 写回去（而它已经不注册了），
# 下面那条 `mentioned - registered` 立刻红，不必等有人想起来加断言。
_NAMED_IN_PROMPT = {
    "bash", "search_web", "update_plan", "use_skill", "read_file", "edit_file",
    "write_file", "glob", "download_url",
    # 已退休（2026-07-29）：留作"提示词别把它教回来"的诱饵，不是现役清单
    "execute_in_sandbox", "list_files", "update_file", "create_file",
    # 浏览器四件套（2026-07-28 纳入）：漏了它们等于这条门禁对整个浏览器
    # 能力面失明——上线 12 天零指导正是这么漏过去的
    "browser_fetch", "browser_open", "browser_act", "browser_close",
}


@pytest.mark.asyncio
async def test_every_prompt_mentioned_tool_actually_exists(monkeypatch):
    """反向门禁：提示词里点名要求调用的工具，必须真的注册了。

    比"没提退休工具"更强——它能抓住"提示词里写了个从来不存在的工具名"这种笔误。

    2026-07-29 去掉 SANDBOX_WORKSPACE_SYNC 参数化：开关连同回退形态一起消失，只剩一种
    形态。补上参数化的原因仍然记在这里——此前这条只按跑测试那台机器的 env 判定，本机
    .env 恰好写了 true，于是"提示词无条件教 write_file/glob/download_url、而关闭形态下这
    三个根本不注册"这个 P0 从它眼皮底下整段漏过。BROWSER_SERVICE_URL 还是配置项，所以
    它那两条 monkeypatch 必须留着。
    """
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", _BROWSER_URL, raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r1", user_message="做一份 PPT",
        turn_intent="execution", action_authority="mutate",
    )
    registered = {t.name for t in tools}
    prompt = _base_prompt()
    mentioned = {n for n in _NAMED_IN_PROMPT if n in prompt}
    missing = mentioned - registered
    assert not missing, f"提示词点名了这些工具但它们没注册：{missing}"
    assert {"browser_fetch", "browser_open", "browser_act", "browser_close"} <= mentioned, (
        "浏览器四工具必须都被提示词点名（否则这条门禁形同虚设）")
    # 现役特征工具必须真的被提到——否则"提到的都存在"可以靠什么都不提来蒙混过关。
    signature = {"bash", "write_file", "glob", "download_url"}
    assert signature <= mentioned, (
        f"提示词没提现役的 {signature - mentioned}，这条门禁会退化成空断言")


def _skill_prompt() -> str:
    """带 Skill 的系统提示词（夹具里刻意含旧工具名 execute_in_sandbox）。"""
    return _build_system_prompt(
        agents=[], trusted_skills=[{"name": "测试技能", "instructions": "第一步：用 execute_in_sandbox 执行"}],
        selected_knowledge=[], knowledge_ids=[], memory_block="")


def test_third_party_skill_block_is_not_silently_translated():
    """Third-party contracts stay byte-semantic; incompatible packages are rejected upstream."""
    prompt = _skill_prompt()
    assert "第一步：用 execute_in_sandbox 执行" in prompt
    assert "第一步：用 bash 执行" not in prompt


# ---- Skill 块的交付口径（2026-07-29 起无条件）----
#
# 这两条原先按 SANDBOX_WORKSPACE_SYNC 分两支：关闭时 build_sync() 返回 None → shell.py 把
# collect_workspace 置 False → files/ 下的产物连读都不读，于是提示词绝不能宣称"写到 files/
# 就等于存进了我的文件"（模型照做会宣布已交付而用户文件区空空）。开关与那一支已随旧
# execute_in_sandbox 族一起下线，同步成为唯一形态，所以这句宣称现在**必须无条件在场**——反过来的
# 那条用例（sync_off 不许宣称）连同它断言的 /workspace/outputs 通道一并删除。
_DELIVERY_CLAIM = "写到 /workspace/files/ 就等于存进了用户「我的文件」"


def test_skill_block_states_the_real_delivery_channel():
    """files/ 即「我的文件」这句必须在：它是模型判断"东西交出去了没有"的唯一依据。"""
    assert _DELIVERY_CLAIM in _skill_prompt(), (
        "Skill 块不再告诉模型产物写哪儿才算交付——模型会把产物写进 tmp/ 之类不回收的目录，"
        "然后宣布已完成（静默丢产物）"
    )


@pytest.mark.asyncio
async def test_use_skill_receipt_does_not_translate_third_party_contract(monkeypatch):
    from app.services.agent_harness import model_driver
    from app.services.chat import turn_context_builder
    from app.services.skills import skill_package_bridge

    async def _fetch_trusted(ids, token):
        return [{"id": ids[0], "record_id": "rec-1", "name": "测试技能",
                 "description": "d", "instructions": "# 测试技能\n1. 用 execute_in_sandbox 执行"}]

    async def _fetch_pkgs(skills, token):
        return [{"skillId": "s1", "name": "测试技能", "slug": "demo-skill",
                 "files": {"scripts/gen.py": b"x"}, "hasScripts": True, "entrypoint": None}]

    monkeypatch.setattr(turn_context_builder, "_fetch_trusted_skills", _fetch_trusted)
    monkeypatch.setattr(skill_package_bridge, "fetch_skill_packages", _fetch_pkgs)
    tools = await model_driver.build_tools(
        token="tok", knowledge_ids=None, web_enabled=False, user_id="u-skill")
    use_skill = next(t for t in tools if t.name == "use_skill")
    out = await use_skill.execute({"skill_id": "demo-skill"})
    text = out.model_content
    assert "/workspace/skills/demo-skill/" in text, "夹具没走到挂包分支，本条什么也没测"
    assert "1. 用 execute_in_sandbox 执行" in text
    assert "1. 用 bash 执行" not in text
