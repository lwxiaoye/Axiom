"""use_skill 工具（Phase 2：模型自主启用 Skill 广场技能）回归。

驱动真实 build_tools 拿到 use_skill 工具，mock Java 回源（_fetch_trusted_skills）与取包
（skill_package_bridge.fetch_skill_packages），验证：加载成功回 SKILL.md+挂包提示、未找到、
重复加载幂等。ACL 只认 id（前端名称不采信）由 _fetch_trusted_skills 保证，这里不重复测。
"""
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.workspace import build_workspace_tools


def _text(value):
    return str(getattr(value, "model_content", value) or "")


async def _fake_fetch_trusted_ok(ids, token):
    return [{
        "id": ids[0], "record_id": "rec-1", "name": "PPT大师",
        "description": "SVG→PPTX", "instructions": "# PPT大师\n1. 写 SVG\n2. 转 PPTX",
    }]


async def _fake_fetch_trusted_none(ids, token):
    return []


async def _fake_fetch_by_id(ids, token):
    """按真实 id 才给内容：模拟模型猜错 id（"pptx"）取空、解析到真实 id（"ppt-master"）才取到。"""
    if ids and ids[0] == "ppt-master":
        return [{"id": "ppt-master", "record_id": "rec-1", "name": "PPT大师",
                 "description": "SVG→PPTX", "instructions": "# PPT大师\n1. 写 SVG"}]
    return []


async def _fake_fetch_pkgs(skills, token):
    return [{"skillId": "s1", "name": "PPT大师", "slug": "ppt-master",
             "files": {"scripts/gen.py": b"print(1)"}, "hasScripts": True, "entrypoint": None}]


class UseSkillToolTests(unittest.IsolatedAsyncioTestCase):
    async def _use_skill_tool(self):
        tools = await model_driver.build_tools(
            token="tok", knowledge_ids=None, web_enabled=False, user_id="u1",
        )
        tool = next((t for t in tools if t.name == "use_skill"), None)
        self.assertIsNotNone(tool, "use_skill 工具未注册")
        return tool

    async def _use_preloaded_ppt_skill_tool(self):
        tools = await model_driver.build_tools(
            token="tok", knowledge_ids=None, web_enabled=False, user_id="u1",
            loaded_skills=[{
                "id": "ppt-master", "name": "pptx", "description": "SVG 转 PPTX",
            }],
            user_message="生成一份 7 页 PPT",
        )
        return next(t for t in tools if t.name == "use_skill")

    async def test_load_returns_skillmd_and_mounts_package(self):
        tool = await self._use_skill_tool()
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_trusted_ok), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _fake_fetch_pkgs):
            out = await tool.execute({"skill_id": "ppt-master"})
        out = _text(out)
        self.assertIn("已读取技能「PPT大师」", out)
        self.assertIn("PPT大师\n1. 写 SVG", out)
        self.assertIn("/workspace/skills/ppt-master/", out)
        self.assertIn("不要再调用 read_file/glob", out)

    async def test_dynamic_skill_persists_identity_version_package_and_source(self):
        captured = []

        async def _trusted(ids, token):
            return [{
                "id": ids[0], "record_id": "rec-9", "name": "动态技能",
                "version": "2026.08", "package_id": "pkg-9",
                "description": "demo", "instructions": "# dynamic",
            }]

        async def _persist(run_id, record):
            captured.append((run_id, dict(record)))
            return True

        tools = await model_driver.build_tools(
            token="tok", knowledge_ids=None, web_enabled=False,
            user_id="u1", run_id="run-dynamic",
        )
        tool = next(t for t in tools if t.name == "use_skill")
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _trusted), \
             patch("app.services.chat.turn_context_builder.persist_skill_state", _persist), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _fake_fetch_pkgs):
            await tool.execute({"skill_id": "dynamic-1"})

        assert captured
        loaded = captured[-1][1]
        assert captured[-1][0] == "run-dynamic"
        assert loaded["skill_id"] == "dynamic-1"
        assert loaded["record_id"] == "rec-9"
        assert loaded["version"] == "2026.08"
        assert loaded["package_id"] == "pkg-9"
        assert loaded["package_digest"]
        assert loaded["selection_source"] == "model"
        assert loaded["selection_method"] == "use_skill"
        assert loaded["status"] == "loaded"

    async def test_restored_model_skill_is_not_treated_as_process_preloaded(self):
        calls = {"n": 0}

        async def _trusted(ids, token):
            return [{
                "id": ids[0], "record_id": "rec-recover", "name": "恢复技能",
                "version": "v2", "package_id": "pkg-recover",
                "instructions": "# recovered",
            }]

        async def _pkgs(skills, token):
            calls["n"] += 1
            return [{"skillId": "recover-1", "recordId": "rec-recover",
                     "name": "恢复技能", "slug": "recover", "files": {"run.py": b"pass"},
                     "hasScripts": True}]

        restored = [{
            "skill_id": "recover-1", "record_id": "rec-recover",
            "selection_source": "model", "selection_method": "use_skill",
            "status": "loaded",
        }]
        with patch("app.services.chat.turn_context_builder.get_persisted_skill_state", return_value=restored), \
             patch("app.services.chat.turn_context_builder.persist_skill_state", return_value=True), \
             patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _trusted), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _pkgs):
            tools = await model_driver.build_tools(
                token="tok", knowledge_ids=None, web_enabled=False, user_id="u1",
                run_id="run-recover", loaded_skills=[{
                    "id": "recover-1", "record_id": "rec-recover", "name": "恢复技能",
                }],
            )
            tool = next(t for t in tools if t.name == "use_skill")
            out = await tool.execute({"skill_id": "recover-1"})

        assert calls["n"] == 1
        assert "已读取技能" in _text(out)

    async def test_idempotent_second_load(self):
        tool = await self._use_skill_tool()
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_trusted_ok), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _fake_fetch_pkgs):
            await tool.execute({"skill_id": "ppt-master"})
            out2 = await tool.execute({"skill_id": "ppt-master"})
        out2 = _text(out2)
        self.assertIn("本轮已读取", out2)
        self.assertIn("不要再用 read_file/glob", out2)

    async def test_opaque_ppt_studio_id_unlocks_stable_profile_alias_after_read(self):
        """A trusted opaque catalog id must unlock the stable ppt-studio tool gate.

        Selection alone is not enough; the semantic alias becomes loaded only
        after use_skill has completed ACL validation and package retrieval.
        """
        canonical_id = "extract_4560f90de0c74a4b934d0235fba91bd7"
        exports = {}
        tools = build_workspace_tools(
            user_id="u1",
            token="tok",
            selected_skills=[{"id": canonical_id, "name": "ppt-studio"}],
            exports=exports,
        )
        tool = next(t for t in tools if t.name == "use_skill")

        async def _trusted(ids, token):
            self.assertEqual(ids, [canonical_id])
            return [{
                "id": canonical_id,
                "record_id": "rec-ppt-studio",
                "name": "ppt-studio",
                "instructions": "# ppt-studio\nCreate an editable PPTD project.",
            }]

        async def _pkgs(skills, token):
            return [{
                "skillId": canonical_id,
                "recordId": "rec-ppt-studio",
                "name": "ppt-studio",
                "slug": "ppt-studio",
                "files": {"reference/pptd.md": b"# PPTD"},
                "hasScripts": True,
            }]

        self.assertFalse(exports["is_skill_loaded"](canonical_id))
        self.assertFalse(exports["is_skill_loaded"]("ppt-studio"))
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _trusted), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _pkgs):
            out = await tool.execute({"skill_id": canonical_id})

        self.assertIn("已读取技能「ppt-studio」", _text(out))
        self.assertTrue(exports["is_skill_loaded"](canonical_id))
        self.assertTrue(exports["is_skill_loaded"]("ppt-studio"))

    async def test_unknown_skill(self):
        tool = await self._use_skill_tool()
        async def _resolve_none(q, token):
            return None, []
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_trusted_none), \
             patch("app.services.chat.turn_context_builder._resolve_skill_id", _resolve_none):
            # 软失败（2026-07-22）：未找到技能不再是「返回字符串」，而是 raise ToolSoftError，
            # 由 _run_one_tool 转成 failed=True，避免被渲染成完成态。
            with self.assertRaises(model_driver.ToolSoftError) as ctx:
                await tool.execute({"skill_id": "nope"})
        self.assertIn("未找到可用技能", str(ctx.exception))

    async def test_wrong_id_auto_resolves_to_real_skill(self):
        # 模型把《SVG转PPTX工作流》猜成 "pptx"：精确取空 → 模糊解析到真实 id → 自动改用真实 id 加载成功
        tool = await self._use_skill_tool()
        async def _resolve_hit(q, token):
            return "ppt-master", []
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_by_id), \
             patch("app.services.chat.turn_context_builder._resolve_skill_id", _resolve_hit), \
             patch("app.services.skills.skill_package_bridge.fetch_skill_packages", _fake_fetch_pkgs):
            out = await tool.execute({"skill_id": "pptx"})
        self.assertIn("已读取技能「PPT大师」", _text(out))

    async def test_invented_alias_of_preloaded_ppt_skill_is_idempotent(self):
        tool = await self._use_preloaded_ppt_skill_tool()

        async def _resolve_hit(q, token):
            self.assertEqual(q, "ppt_skill")
            return "ppt-master", []

        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_trusted_none), \
             patch("app.services.chat.turn_context_builder._resolve_skill_id", _resolve_hit):
            out = await tool.execute({"skill_id": "ppt_skill"})
        out = _text(out)
        self.assertIn("技能「ppt-master」本轮已读取", out)
        self.assertNotIn("未找到可用技能", out)

    async def test_ambiguous_lists_candidates(self):
        # 多命中：不擅自替模型选，把候选（带真实 id）列给模型二选一
        tool = await self._use_skill_tool()
        async def _resolve_ambiguous(q, token):
            return None, ["[ppt-a] PPT甲", "[ppt-b] PPT乙"]
        with patch("app.services.chat.turn_context_builder._fetch_trusted_skills", _fake_fetch_trusted_none), \
             patch("app.services.chat.turn_context_builder._resolve_skill_id", _resolve_ambiguous):
            # 软失败（2026-07-22）：多命中候选列表也是软失败——raise ToolSoftError，failed=True
            with self.assertRaises(model_driver.ToolSoftError) as ctx:
                await tool.execute({"skill_id": "ppt"})
        out = str(ctx.exception)
        self.assertIn("[ppt-a] PPT甲", out)
        self.assertIn("[ppt-b] PPT乙", out)
        self.assertIn("重新调用", out)

    async def test_missing_id(self):
        tool = await self._use_skill_tool()
        # 软失败（2026-07-22）：缺少 skill_id 现在 raise ToolSoftError，不再是普通字符串返回
        with self.assertRaises(model_driver.ToolSoftError) as ctx:
            await tool.execute({})
        self.assertIn("需要 skill_id", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


# ---------- 技能包挂载缺口必须如实告知（2026-07-27） ----------

def test_binary_assets_are_not_silently_corrupted():
    """取包通道走 Java 的 JSON 接口，二进制字节在服务端就丢了。

    原实现 `str(content).encode("utf-8")` 会把损坏固定下来并挂进沙箱：模型引用一个坏掉的
    png/ttf，产物里是空白或乱码字形，而且没有任何报错。挂坏的比不挂更糟。
    """
    from app.services.skills.skill_package_bridge import _BINARY_SUFFIXES

    for name in ("cover.png", "logo.JPG", "font.ttf", "assets.zip", "doc.pdf", "clip.mp4"):
        assert name.lower().endswith(_BINARY_SUFFIXES), f"{name} 应被识别为二进制"
    for name in ("SKILL.md", "build.py", "template.html", "design.md", "run.sh", "data.json"):
        assert not name.lower().endswith(_BINARY_SUFFIXES), f"{name} 是文本，不该被排除"


def test_unmounted_files_are_reported_to_the_model():
    """不说 = 模型按 SKILL.md 引用一个不存在的文件，然后在「文件不存在」里反复打转。"""
    from app.services.chat.tools.workspace import _unmounted_note

    note = _unmounted_note([{
        "unmounted": {
            "binary": ["assets/cover.png", "fonts/Inter.ttf"],
            "over_file_limit": ["designs/999.md"],
            "over_byte_budget": [],
        }
    }])
    assert "cover.png" in note and "二进制资源没有挂进沙箱" in note
    assert "incomplete" in note
    assert "不要引用它们" in note
    # 只陈述事实，不替技能猜替代方案（A/B 分层口径）。初版写「需要图就 search_web +
    # download_url 另取」——对字体是错的：ttf 下进「我的文件」在沙箱里同样用不上，
    # PPTX 的字体必须是打开文件那台机器上已安装的。
    assert "download_url" not in note and "search_web" not in note
    assert "999.md" in note and "超出单次挂载上限" in note


def test_no_note_when_everything_mounted():
    """全挂上了就别啰嗦——多余的告警会稀释真正的告警。"""
    from app.services.chat.tools.workspace import _unmounted_note

    assert _unmounted_note([{"unmounted": {}}]) == ""
    assert _unmounted_note([]) == ""
