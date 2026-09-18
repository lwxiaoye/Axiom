# -*- coding: utf-8 -*-
"""Skill 链路的「静默失败」族回归（2026-07-28）。

这一族 bug 的共同形态：**模型被告知成功、实际什么都没落地**。它比崩溃更伤——崩溃会被看见，
静默不会，用户拿到的是一句"已交付"和一个不存在的文件。

所以下面每一条的断言口径都是同一个：**失败时模型收到的必须是失败**。不是"日志里有"，
不是"没抛异常"，而是回执文本里那句话真的在。

覆盖：
1. 取包失败三层静默吞掉（bridge 整包丢弃 / provider 异常缓存空 / bash 异常空列表）；
2. 写到 /workspace/outputs 的产物既不收集也不报告；
3. execute_in_sandbox 退休后「PPT 声称交付却没有 pptx」的存在性闸消失；
4. 未挂载文件清单只在 use_skill 路径回执里说（@ 选中 / 自动预加载这条路是哑的）；
5. use_skill 成功后清空整轮包缓存 → 全量重取 → 重取失败让已挂载技能凭空消失。
"""
from unittest.mock import patch

import pytest

from app.services.chat.tools import shell as shell_tools
from app.services.chat.tools.base import ToolSoftError, _model_text, _pop_validity_gate
from app.services.chat.tools.shell import build_shell_tools
from app.services.skills import skill_package_bridge as bridge


def _txt(value):
    """bash execute may return ToolObservation; assertions read model text."""
    return _model_text(value)


class _Res:
    """sandbox_executor.execute_in_sandbox 的返回替身（只带 bash 会读的字段）。"""

    def __init__(self, *, ok=True, stdout="", stderr="", exit_code=0, error=None,
                 truncated=False, review=None, workspace_changes=None,
                 workspace_deleted=None, workspace_oversized=None,
                 outputs_migrated=None, outputs_conflicts=None):
        self.ok, self.stdout, self.stderr = ok, stdout, stderr
        self.exit_code, self.error, self.truncated = exit_code, error, truncated
        self.review = review
        self.workspace_changes = workspace_changes or []
        self.workspace_deleted = workspace_deleted or []
        self.workspace_oversized = workspace_oversized or []
        self.outputs_migrated = outputs_migrated or []
        self.outputs_conflicts = outputs_conflicts or []


class _Sync:
    """WorkspaceSync 的替身：只需要能报出「本次落库了哪些文件」。"""

    def __init__(self, saved=None):
        self._saved = saved or []
        self.load_called = False

    async def load(self, mirror=None):
        self.load_called = True
        return {"files": {}, "stamps": {}}

    async def prepare(self):
        return {}

    async def persist(self, _changes):
        return list(self._saved)

    def notice(self):
        return ""

    def persist_notice(self):
        return ""


def _stub_run(monkeypatch, res, captured=None):
    async def fake_execute_in_sandbox(command, **kwargs):
        if captured is not None:
            captured["command"] = command
            captured.update(kwargs)
        return res

    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)


def _stub_sync(monkeypatch, sync):
    monkeypatch.setattr(shell_tools, "build_sync", lambda *a, **kw: sync)


def _unavailable(name="ppt-studio", *, has_scripts=True, known=True, reason="文件树取不到"):
    return {
        "skillId": "s1", "recordId": "rec-1", "name": name, "slug": name,
        "files": {}, "entrypoint": None,
        "hasScripts": has_scripts, "scriptsKnown": known,
        "unmounted": {}, "unavailable": True, "reason": reason,
    }


def _mounted(name="ppt-studio", *, unmounted=None):
    return {
        "skillId": "s1", "recordId": "rec-1", "name": name, "slug": name,
        "files": {"SKILL.md": b"# doc"}, "entrypoint": None,
        "hasScripts": False, "scriptsKnown": True,
        "unmounted": unmounted or {},
    }


# ============ 1. 取包失败不得被静默吞掉 ============
# 2026-09-18：取包改为进程内（skill_catalog），这里用 load_skill_package_source 替身模拟三种失败形态。


def _stub_source(monkeypatch, result):
    """skill_catalog.load_skill_package_source 替身：result 可为 dict / None / 异常实例。"""
    from app.services.skills import skill_catalog

    async def fake(_record_id, _token):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(skill_catalog, "load_skill_package_source", fake)
    bridge._package_fetch_cache.clear()
    bridge._package_fetch_inflight.clear()


@pytest.mark.asyncio
async def test_tree_fetch_failure_yields_placeholder_not_silence(monkeypatch):
    """整包取不到时**必须回一条占位记录**，不能返回空列表。

    空列表与「用户没选技能」在调用方眼里完全一样——而系统提示词此时已经写着
    「它们已挂在沙箱内 /workspace/skills/ 下」。这就是整族 bug 的源头。
    """
    _stub_source(monkeypatch, RuntimeError("connection reset"))
    pkgs = await bridge.fetch_skill_packages(
        [{"id": "s1", "record_id": "rec-1", "name": "ppt-studio"}], "tok")

    assert len(pkgs) == 1, "取包失败不得静默丢弃整包"
    assert bridge.is_unavailable(pkgs[0])
    assert pkgs[0]["files"] == {}
    # 连文件树都没拿到 → 含不含脚本未知 → 按最坏情况算含脚本
    assert pkgs[0]["hasScripts"] is True
    assert pkgs[0]["scriptsKnown"] is False


@pytest.mark.asyncio
async def test_all_files_fail_keeps_declared_scripts_flag(monkeypatch):
    """文件树拿到了、字节全取不回来：**知道**这包本该含脚本，不该按"未知"处理。"""
    _stub_source(monkeypatch, {
        "files": {}, "entrypoint": None,
        "error": "ZIP 有 2 个条目，但没有一个文件解出成功",
        "declared_scripts": True,
        "unmounted": {"binary": [], "over_file_limit": [], "over_byte_budget": [],
                      "fetch_failed": ["SKILL.md", "scripts/build.py"]},
        "channel": "zip",
    })
    pkgs = await bridge.fetch_skill_packages(
        [{"id": "s1", "record_id": "rec-1", "name": "ppt-studio"}], "tok")

    assert bridge.is_unavailable(pkgs[0])
    assert pkgs[0]["hasScripts"] is True and pkgs[0]["scriptsKnown"] is True
    assert "没有一个文件解出成功" in pkgs[0]["reason"]


@pytest.mark.asyncio
async def test_doc_only_skill_failure_is_marked_degradable(monkeypatch):
    """纯说明书类技能（树里没有脚本）取包失败可以降级——回执措辞也该不同。"""
    _stub_source(monkeypatch, {
        "files": {}, "entrypoint": None, "error": "技能说明书为空",
        "declared_scripts": False,
        "unmounted": {"binary": [], "over_file_limit": [], "over_byte_budget": [], "fetch_failed": []},
        "channel": "local",
    })
    pkgs = await bridge.fetch_skill_packages(
        [{"id": "s1", "record_id": "rec-1", "name": "写作规范"}], "tok")

    assert bridge.is_unavailable(pkgs[0])
    assert pkgs[0]["hasScripts"] is False


@pytest.mark.asyncio
async def test_missing_catalog_record_yields_placeholder(monkeypatch):
    """目录里根本没有这条记录（已删除/无权限）：同样占位而不是消失。"""
    _stub_source(monkeypatch, None)
    pkgs = await bridge.fetch_skill_packages(
        [{"id": "s1", "record_id": "gone", "name": "ppt-studio"}], "tok")

    assert bridge.is_unavailable(pkgs[0])
    assert "没有这条记录" in pkgs[0]["reason"]
    assert pkgs[0]["scriptsKnown"] is False


@pytest.mark.asyncio
async def test_bash_receipt_says_scripted_skill_is_unavailable(monkeypatch):
    """含脚本技能没挂上 → bash 回执必须点名说"本轮不可用、别执行它的脚本"。"""
    _stub_run(monkeypatch, _Res(stdout="hi"))

    async def resolve():
        return [_unavailable("ppt-studio")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    text = _txt(await tool.execute({"command": "echo hi"}))

    assert "ppt-studio" in text
    assert "本轮没有挂进沙箱" in text
    assert "不要去执行它的脚本" in text and "不要假装执行过" in text


@pytest.mark.asyncio
async def test_bash_hard_fails_when_command_reaches_into_missing_skill(monkeypatch):
    """命令正要跑技能目录里的脚本、而技能根本没挂上 → 明确失败（ADR-043）。

    只在回执里说一句是不够的：命令会以一个语义无关的 "No such file or directory" 收场，
    模型会当成路径写错反复瞎试。
    """
    _stub_run(monkeypatch, _Res(stdout=""))

    async def resolve():
        return [_unavailable("ppt-studio")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    with pytest.raises(ToolSoftError) as excinfo:
        await tool.execute({"command": "python3 /workspace/skills/ppt-studio/build.py"})
    assert "本轮没有挂进沙箱" in str(excinfo.value)
    assert "不要假装脚本已运行" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bash_does_not_hard_fail_unrelated_commands(monkeypatch):
    """技能没挂上**不该**让所有 bash 调用全废——模型还得靠它做别的事。"""
    _stub_run(monkeypatch, _Res(stdout="ok"))

    async def resolve():
        return [_unavailable("ppt-studio")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    text = _txt(await tool.execute({"command": "ls /workspace/files"}))
    assert "ok" in text  # 命令照常执行


@pytest.mark.asyncio
async def test_a_mounted_sibling_skill_is_still_usable(monkeypatch):
    """多选技能时 A 挂上了、B 没挂上：`ls /workspace/skills/A` 完全合法，不该被拦。"""
    _stub_run(monkeypatch, _Res(stdout="SKILL.md"))

    async def resolve():
        return [_unavailable("ppt-studio"), _mounted("writer")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    text = _txt(await tool.execute({"command": "ls /workspace/skills/writer"}))
    assert "SKILL.md" in text


@pytest.mark.asyncio
async def test_bash_reports_resolver_blowup(monkeypatch):
    """解析器整个炸掉（最后一层沉默）也要说出来。"""
    _stub_run(monkeypatch, _Res(stdout="hi"))

    async def resolve():
        raise RuntimeError("boom")

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    text = _txt(await tool.execute({"command": "echo hi"}))
    assert "技能包解析失败" in text and "本轮不成立" in text


@pytest.mark.asyncio
async def test_unavailable_packages_are_never_mounted(monkeypatch):
    """占位记录不得传给 sandbox_executor —— 它会把空 slug 记进 session.written_skills，
    同一 Run 里后续真取到包的那次会被整包跳过。"""
    captured: dict = {}
    _stub_run(monkeypatch, _Res(stdout="ok"), captured)

    async def resolve():
        return [_unavailable("ppt-studio"), _mounted("writer")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    await tool.execute({"command": "ls"})
    mounted = captured.get("skill_packages") or []
    assert [p["name"] for p in mounted] == ["writer"]


@pytest.mark.asyncio
async def test_skill_gap_is_said_once_not_every_call(monkeypatch):
    """每次 bash 都重复一遍告警 = 真告警被自己的噪音稀释。"""
    _stub_run(monkeypatch, _Res(stdout="ok"))

    async def resolve():
        return [_unavailable("ppt-studio")]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    first = _txt(await tool.execute({"command": "echo 1"}))
    second = _txt(await tool.execute({"command": "echo 2"}))
    assert "本轮没有挂进沙箱" in first
    assert "本轮没有挂进沙箱" not in second


# ============ 2. 写到 /workspace/outputs 的产物 ============

@pytest.mark.asyncio
async def test_bash_asks_runner_to_migrate_outputs(monkeypatch):
    """outputs/ 必须接上：_mkdir_cmd 无条件建了这个目录，往那里写永远成功。"""
    captured: dict = {}
    _stub_run(monkeypatch, _Res(stdout="ok"), captured)
    _stub_sync(monkeypatch, _Sync())

    tool = build_shell_tools(user_id="u1")[0]
    await tool.execute({"command": "python3 gen.py"})
    assert captured["migrate_outputs"] is True
    assert captured["collect_outputs"] is False


@pytest.mark.asyncio
async def test_outputs_written_files_are_reported_and_delivered(monkeypatch):
    """搬过来了也要**说**：不说的话模型下次还写 outputs/。"""
    _stub_run(monkeypatch, _Res(
        stdout="完成：/workspace/outputs/汇报.pptx",
        outputs_migrated=["汇报.pptx"], workspace_changes=[{"path": "汇报.pptx", "data": b"PK"}]))
    _stub_sync(monkeypatch, _Sync(saved=[{"filename": "汇报.pptx"}]))

    tool = build_shell_tools(user_id="u1")[0]
    text = _txt(await tool.execute({"command": "python3 gen.py"}))
    assert "汇报.pptx" in text
    assert "那个目录不会交付给用户" in text
    assert "以后请直接写 /workspace/files/" in text
    assert "已保存到「我的文件」" in text


@pytest.mark.asyncio
async def test_outputs_name_conflict_is_reported_not_silently_dropped(monkeypatch):
    """同名不覆盖用户已有文件，但**没交付**这件事必须说，否则又是一次静默。"""
    _stub_run(monkeypatch, _Res(stdout="", outputs_conflicts=["汇报.pptx"]))
    _stub_sync(monkeypatch, _Sync())

    tool = build_shell_tools(user_id="u1")[0]
    text = _txt(await tool.execute({"command": "python3 gen.py"}))
    assert "没有移动、也没有交付" in text


@pytest.mark.asyncio
async def test_no_outputs_no_noise(monkeypatch):
    """没往 outputs/ 写就别提这茬。"""
    _stub_run(monkeypatch, _Res(stdout="hi"))
    _stub_sync(monkeypatch, _Sync())
    text = _txt(await build_shell_tools(user_id="u1")[0].execute({"command": "echo hi"}))
    assert "/workspace/outputs/" not in text


# ============ 3. PPT 客观检查边界 ============

@pytest.mark.asyncio
async def test_ppt_turn_without_pptx_is_not_failed_by_keyword(monkeypatch):
    """普通 bash 不因用户文字包含 PPT 就伪造一个失败检查。"""
    _stub_run(monkeypatch, _Res(stdout="done"))
    _stub_sync(monkeypatch, _Sync())

    tool = build_shell_tools(user_id="u1", user_message="帮我做一份10页的PPT")[0]
    text = _txt(await tool.execute({"command": "echo done"}))
    body, status = _pop_validity_gate(text)
    assert status is None
    assert body == "done"


@pytest.mark.asyncio
async def test_artifact_profile_bash_does_not_spend_publish_rework_budget(monkeypatch):
    """Strict PPT authoring uses bash for staging, never for delivery.

    A normal page-writing command must therefore remain neutral until the
    exact publish tool performs layout, image, font, visual, and persistence
    checks.
    """
    _stub_run(monkeypatch, _Res(stdout="page written"))
    _stub_sync(monkeypatch, _Sync())

    profile = {
        "id": "artifact_coding",
        "artifact_kind": "presentation",
        "authoring_backend": "pptd",
    }
    tool = build_shell_tools(
        user_id="u1",
        user_message="帮我做一份10页的PPT",
        execution_profile=profile,
    )[0]
    body, status = _pop_validity_gate(
        await tool.execute({"command": "mkdir -p /workspace/tmp/ppt-project/pages"}),
    )
    assert status in ("", None)
    assert "PPT 任务未完成" not in body


@pytest.mark.asyncio
async def test_ppt_keyword_does_not_create_a_rework_gate(monkeypatch):
    """没有客观检查结果时，普通 bash 回执保持中性，不创建返工额度。"""
    _stub_run(monkeypatch, _Res(stdout="x"))
    _stub_sync(monkeypatch, _Sync())

    tool = build_shell_tools(user_id="u1", user_message="做个PPT")[0]
    _, first = _pop_validity_gate(await tool.execute({"command": "ls"}))
    text2 = _txt(await tool.execute({"command": "cat SKILL.md"}))
    body2, second = _pop_validity_gate(text2)
    assert first is None
    assert second is None
    assert "PPT 任务未完成" not in body2


@pytest.mark.asyncio
async def test_ppt_gate_clears_once_a_pptx_lands(monkeypatch):
    """真交付了就放行，且**整轮**记账（模型分步做时后续调用不该再报未交付）。"""
    _stub_sync(monkeypatch, _Sync(saved=[{"filename": "汇报.pptx"}]))
    _stub_run(monkeypatch, _Res(stdout="built", review={"status": "passed"}))
    tool = build_shell_tools(user_id="u1", user_message="做个PPT")[0]

    body, status = _pop_validity_gate(await tool.execute({"command": "python3 build.py"}))
    assert status == "passed" and "PPT 任务未完成" not in body

    # 后续一次没有新产物的调用也不该再判未交付
    _stub_sync(monkeypatch, _Sync(saved=[]))
    _stub_run(monkeypatch, _Res(stdout="ok"))
    tool2_text = _txt(await tool.execute({"command": "ls"}))
    assert "PPT 任务未完成" not in tool2_text


@pytest.mark.asyncio
async def test_ppt_multiturn_context_does_not_create_a_keyword_gate(monkeypatch):
    """跨轮上下文也不能让普通 bash 伪造 PPT 失败回执。"""
    _stub_run(monkeypatch, _Res(stdout="x"))
    _stub_sync(monkeypatch, _Sync())

    tool = build_shell_tools(
        user_id="u1",
        user_message="主题是关于kimi k3的模型能力宣讲，需要10页，受众是ai开发重度使用者",
        recent_user_messages=["我想做一份ppt"])[0]
    body, status = _pop_validity_gate(await tool.execute({"command": "ls"}))
    assert status is None
    assert body == "x"


@pytest.mark.asyncio
async def test_non_ppt_turn_is_untouched(monkeypatch):
    """不是 PPT 任务就别插嘴——误报会让模型去做一件用户没要的事。"""
    _stub_run(monkeypatch, _Res(stdout="hi"))
    _stub_sync(monkeypatch, _Sync())
    text = _txt(await build_shell_tools(user_id="u1", user_message="看看有哪些文件")[0].execute(
        {"command": "ls"}))
    assert "PPT 任务未完成" not in text
    assert _pop_validity_gate(text)[1] is None


@pytest.mark.asyncio
async def test_ppt_gate_needs_persistence_visibility(monkeypatch):
    """统一文件系统关闭时看不到落库结果 —— 宁可不报也不误报。"""
    _stub_run(monkeypatch, _Res(stdout="x"))
    monkeypatch.setattr(shell_tools, "build_sync", lambda *a, **kw: None)
    text = _txt(await build_shell_tools(user_message="做个PPT")[0].execute({"command": "ls"}))
    assert "PPT 任务未完成" not in text


# ============ 4. 未挂载清单要在 @ 选中 / 自动预加载这条路上也说 ============

@pytest.mark.asyncio
async def test_unmounted_assets_reported_on_the_autopreload_path(monkeypatch):
    """ppt-studio 走的正是自动预加载，从来不经过 use_skill —— 此前这条路完全是哑的。

    模型收到「自带脚本已挂在 /workspace/skills/」，然后按 SKILL.md 去引用一个不存在的
    png/字体，在「文件不存在」里反复打转。
    """
    _stub_run(monkeypatch, _Res(stdout="ok"))

    async def resolve():
        return [_mounted("ppt-studio", unmounted={
            "binary": ["assets/cover.png", "fonts/Inter.ttf"],
            "over_file_limit": [], "over_byte_budget": [],
        })]

    tool = build_shell_tools(resolve_skill_packages=resolve)[0]
    text = _txt(await tool.execute({"command": "ls /workspace/skills"}))
    assert "cover.png" in text and "二进制资源没有挂进沙箱" in text


@pytest.mark.asyncio
async def test_shared_seen_set_prevents_double_reporting(monkeypatch):
    """use_skill 已经念过的包，bash 不要再念一遍（两个工具共享同一个集合）。"""
    _stub_run(monkeypatch, _Res(stdout="ok"))
    seen = {"rec-1"}

    async def resolve():
        return [_mounted("ppt-studio", unmounted={"binary": ["a.png"]})]

    tool = build_shell_tools(resolve_skill_packages=resolve, skill_notice_seen=seen)[0]
    text = _txt(await tool.execute({"command": "ls"}))
    assert "a.png" not in text


# ============ 5. use_skill 不再清空整轮包缓存 ============

async def _fake_trusted(ids, _token):
    return [{"id": ids[0], "record_id": "rec-new", "name": "新技能",
             "description": "d", "instructions": "# 新技能"}]


@pytest.mark.asyncio
async def test_use_skill_does_not_invalidate_the_turn_cache(monkeypatch):
    """清缓存会让下一次 bash 把每个 @ 选中的技能**逐文件**回源 Java 一遍（200 文件=200 次
    HTTP），而重取一旦失败，之前挂好的技能会在后续 bash 里集体消失。

    逻辑上也完全不必要——解析结果是 `list(base) + _dynamic_skill_pkgs` 两段拼的，
    新包本来就单独挂在后面。
    """
    from app.services.agent_harness import model_driver

    calls = {"n": 0}

    async def provider():
        calls["n"] += 1
        return [_mounted("已选技能")]

    async def fake_pkgs(_skills, _token):
        return [_mounted("新技能")]

    _stub_run(monkeypatch, _Res(stdout="ok"))
    tools = await model_driver.build_tools(
        token="tok", knowledge_ids=None, web_enabled=False, user_id="u1",
        skill_packages_provider=provider,
    )
    use_skill = next(t for t in tools if t.name == "use_skill")
    bash = next(t for t in tools if t.name == "bash")

    with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_trusted), \
         patch("app.services.skills.skill_package_bridge.fetch_skill_packages", fake_pkgs):
        await bash.execute({"command": "ls"})
        assert calls["n"] == 1
        await use_skill.execute({"skill_id": "new-skill"})
        await bash.execute({"command": "ls"})

    assert calls["n"] == 1, "use_skill 之后不得让 @ 选中的技能全量重取"


@pytest.mark.asyncio
async def test_both_selected_and_dynamic_skills_stay_mounted(monkeypatch):
    """清缓存最狠的后果：重取失败 → 之前挂好的技能在后续 bash 里全部消失且无提示。

    钉住正确行为：@ 选中的与 use_skill 加载的必须**同时**在挂载集里。
    """
    from app.services.agent_harness import model_driver

    captured: dict = {}
    _stub_run(monkeypatch, _Res(stdout="ok"), captured)

    async def provider():
        return [_mounted("已选技能")]

    async def fake_pkgs(_skills, _token):
        return [_mounted("新技能")]

    tools = await model_driver.build_tools(
        token="tok", knowledge_ids=None, web_enabled=False, user_id="u1",
        skill_packages_provider=provider,
    )
    use_skill = next(t for t in tools if t.name == "use_skill")
    bash = next(t for t in tools if t.name == "bash")

    with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_trusted), \
         patch("app.services.skills.skill_package_bridge.fetch_skill_packages", fake_pkgs):
        await use_skill.execute({"skill_id": "new-skill"})
        await bash.execute({"command": "ls"})

    names = sorted(p["name"] for p in (captured.get("skill_packages") or []))
    assert names == sorted(["已选技能", "新技能"])


@pytest.mark.asyncio
async def test_use_skill_receipt_does_not_claim_a_mount_that_failed(monkeypatch):
    """取包失败是结构化软失败，不能返回一条看起来像已加载的 SKILL.md 回执。"""
    from app.services.agent_harness import model_driver

    async def fake_pkgs(_skills, _token):
        return [_unavailable("ppt-studio")]

    tools = await model_driver.build_tools(
        token="tok", knowledge_ids=None, web_enabled=False, user_id="u1")
    use_skill = next(t for t in tools if t.name == "use_skill")

    with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_trusted), \
         patch("app.services.skills.skill_package_bridge.fetch_skill_packages", fake_pkgs):
        with pytest.raises(ToolSoftError) as first:
            await use_skill.execute({"skill_id": "sk-1"})
        assert first.value.code == "skill_package_unavailable"
        assert first.value.retryable is True
        assert first.value.details["skill_id"] == "sk-1"
        assert "未加载" in str(first.value)
        # 失败没有进入进程内已加载集，后续调用仍会重新取包。
        with pytest.raises(ToolSoftError) as second:
            await use_skill.execute({"skill_id": "sk-1"})
        assert second.value.code == "skill_package_unavailable"


# ============ 6. slug 碰撞 / 口径统一 / 运行中追加 Skill ============

def test_slug_collision_between_lossy_and_plain_names():
    """《a/b》与《a_b》以前都落成 `a_b`：同一目录后写覆盖先写，而 written_skills 又按 slug
    去重 —— 复用沙箱时第二个包连写都不写。"""
    lossy = bridge._slugify("a/b", "rec-1")
    plain = bridge._slugify("a_b", "rec-2")
    assert lossy != plain
    assert plain == "a_b", "没被改写过的名字要保持原样（路径对模型要好读）"


def test_slug_is_stable_across_turns():
    """摘要取 record_id 而不是随机数：同一技能每轮必须拿到同一个目录。"""
    assert bridge._slugify("a/b", "rec-1") == bridge._slugify("a/b", "rec-1")
    assert bridge._slugify("ppt-studio", "rec-1") == "ppt-studio"


def test_slug_length_capped():
    assert len(bridge._slugify("名" * 200, "rec-1")) <= 64
    assert bridge._slugify("!!!", "rec-1")  # 全是不可用字符也要给得出目录名


def test_tool_scope_ppt_matches_turn_prepare_lookback():
    """tool_scope 只看当轮、turn_prepare 看最近 3 轮 —— 两处口径必须一致。"""
    from app.services.chat.tools.tool_scope import resolve_tool_scope

    work_turn = "主题是关于kimi k3的模型能力宣讲，需要10页，受众是ai开发重度使用者"
    assert resolve_tool_scope(message=work_turn).requires_ppt_skill is False
    assert resolve_tool_scope(
        message=work_turn, recent_user_messages=["我想做一份ppt"]).requires_ppt_skill is True


def test_runtime_skill_append_exposes_unloaded_capability_fact():
    """运行中追加 Skill 只提供能力事实，不强迫模型调用某个工具。"""
    from app.services.agent_harness.model_driver import _format_run_input

    text = _format_run_input("接着做", [{
        "kind": "turn_context",
        "context": {"skills": [{"id": "sk-1", "name": "ppt-studio"}]},
    }])
    assert "id=sk-1" in text
    assert "尚未把该能力包挂入" in text
