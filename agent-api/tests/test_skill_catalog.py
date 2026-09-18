"""Skill 广场目录注入（让模型看得见有哪些技能）回归。

用假的进程内目录读取（替换 turn_context_builder._load_catalog_records，2026-09-18 起目录由
agent-api 自持，不再回源 auth-api）验证 _fetch_skill_catalog_block：
列出全部 enabled 技能、标注已选中、空/失败降级为 ""（不阻断对话）。
"""
import unittest
from unittest.mock import patch

from app.services.chat import turn_context_builder as tcb


class FakeCatalog:
    """_load_catalog_records 替身：records 直接给记录列表；raise_exc 模拟 DB 抖动。"""
    records: list = []
    raise_exc = False
    calls = 0

    @classmethod
    async def load(cls, _token):
        cls.calls += 1
        if cls.raise_exc:
            raise RuntimeError("db down")
        return list(cls.records)


LIST_RECORDS = [
    {"skillId": "ppt-master", "name": "PPT大师", "description": "SVG手写转原生可编辑PPTX", "enabled": 1},
    {"skillId": "frontend-design", "name": "前端设计", "description": "distinctive UI 设计指导", "enabled": 1},
    {"skillId": "disabled-one", "name": "停用的", "description": "x", "enabled": 0},
]


class SkillCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tcb._skill_catalog_cache.clear()  # 模块级 TTL 缓存跨用例，逐例清空防串台
        FakeCatalog.calls = 0

    async def _run(self, selected=None, token="tok"):
        with patch("app.services.chat.turn_context_builder._load_catalog_records", FakeCatalog.load):
            return await tcb._fetch_skill_catalog_block(token, selected)

    async def test_lists_enabled_skills_and_guidance(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = LIST_RECORDS
        block = await self._run()
        self.assertIn("PPT大师", block)
        self.assertIn("SVG手写转原生可编辑PPTX", block)
        self.assertIn("前端设计", block)
        self.assertNotIn("停用的", block)  # enabled=0 不列
        self.assertIn("供模型按当前目标自主发现", block)  # Skill 由模型按目标自主发现
        self.assertIn("[ppt-master]", block)  # 每项带 id，供 use_skill 调用
        self.assertIn("use_skill", block)  # 指引模型用 use_skill 自主启用

    async def test_marks_selected_skill(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = LIST_RECORDS
        block = await self._run(selected=["ppt-master"])
        # 已选中的那行带「已选中」标注，未选中的不带
        ppt_line = next(ln for ln in block.splitlines() if "PPT大师" in ln)
        design_line = next(ln for ln in block.splitlines() if "前端设计" in ln)
        self.assertIn("已选中", ppt_line)
        self.assertNotIn("已选中", design_line)

    async def test_empty_and_failure_degrade_to_blank(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = []
        self.assertEqual(await self._run(token="empty-tok"), "")
        FakeCatalog.raise_exc = True
        self.assertEqual(await self._run(token="fail-tok"), "")

    async def test_ttl_cache_avoids_repeat_catalog_read(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = LIST_RECORDS
        await self._run()
        await self._run()  # 60s 内二次调用应命中缓存、不再查库
        self.assertEqual(FakeCatalog.calls, 1)


class SkillIdMatchTests(unittest.TestCase):
    """_match_skill_records：模型把 skill_id 猜错时按 id/名称模糊解析真实 id（use_skill 兜底）。"""

    RECORDS = [
        {"skillId": "svg-to-pptx", "name": "SVG转PPTX工作流", "description": "x", "enabled": 1},
        {"skillId": "frontend-design", "name": "前端设计", "description": "x", "enabled": 1},
        {"skillId": "ppt-master", "name": "PPT大师", "description": "x", "enabled": 0},  # 停用
    ]

    def test_exact_id_case_insensitive(self):
        self.assertEqual(tcb._match_skill_records(self.RECORDS, "SVG-TO-PPTX"), ("svg-to-pptx", []))

    def test_keyword_in_name_resolves_unique(self):
        # 模型猜 "pptx"：唯一 enabled 技能名含 PPTX → 自动解析到真实 id
        self.assertEqual(tcb._match_skill_records(self.RECORDS, "pptx"), ("svg-to-pptx", []))

    def test_model_invented_ppt_skill_alias_resolves_platform_pptx(self):
        records = [
            {"skillId": "extract_random_id", "name": "pptx", "description": "SVG 转 PPTX", "enabled": 1},
            {"skillId": "frontend-design", "name": "前端设计", "enabled": 1},
        ]
        self.assertEqual(
            tcb._match_skill_records(records, "ppt_skill"),
            ("extract_random_id", []),
        )

    def test_disabled_skill_not_matched(self):
        # "ppt-master" 已停用：即便精确给这个 id 也不命中（enabled 过滤）
        rid, cands = tcb._match_skill_records(self.RECORDS, "ppt-master")
        self.assertIsNone(rid)
        self.assertEqual(cands, [])

    def test_ambiguous_returns_candidates(self):
        recs = [
            {"skillId": "ppt-a", "name": "PPT甲", "enabled": 1},
            {"skillId": "ppt-b", "name": "PPT乙", "enabled": 1},
        ]
        rid, cands = tcb._match_skill_records(recs, "ppt")
        self.assertIsNone(rid)  # 多命中不擅自替模型选
        self.assertEqual(len(cands), 2)
        self.assertTrue(all(c.startswith("[") for c in cands))

    def test_no_match_returns_empty(self):
        self.assertEqual(tcb._match_skill_records(self.RECORDS, "excel宏"), (None, []))

    def test_blank_query(self):
        self.assertEqual(tcb._match_skill_records(self.RECORDS, "  "), (None, []))


class SkillResolveTests(unittest.IsolatedAsyncioTestCase):
    """_resolve_skill_id：读目录后走 _match_skill_records，命中 TTL 缓存不重复查库。"""

    def setUp(self):
        tcb._skill_catalog_cache.clear()
        FakeCatalog.calls = 0

    # 复刻用户截图场景：技能名《SVG转PPTX工作流》含 PPTX，模型却把 id 猜成 "pptx"
    RESOLVE_RECORDS = [
        {"skillId": "svg-to-pptx", "name": "SVG转PPTX工作流", "description": "SVG手写转原生可编辑PPTX", "enabled": 1},
        {"skillId": "frontend-design", "name": "前端设计", "description": "UI 设计指导", "enabled": 1},
    ]

    async def test_resolve_via_fetch(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = self.RESOLVE_RECORDS
        with patch("app.services.chat.turn_context_builder._load_catalog_records", FakeCatalog.load):
            rid, _ = await tcb._resolve_skill_id("pptx", "tok")
        self.assertEqual(rid, "svg-to-pptx")  # 名含 PPTX，唯一 enabled 命中，自动解析真实 id

    async def test_resolve_shares_catalog_cache(self):
        FakeCatalog.raise_exc = False
        FakeCatalog.records = self.RESOLVE_RECORDS
        with patch("app.services.chat.turn_context_builder._load_catalog_records", FakeCatalog.load):
            await tcb._fetch_skill_catalog_block("tok")   # 目录注入先读一次
            await tcb._resolve_skill_id("pptx", "tok")     # 兜底解析复用同一缓存
        self.assertEqual(FakeCatalog.calls, 1)


class TrustedSkillsInProcessTests(unittest.IsolatedAsyncioTestCase):
    """_fetch_trusted_skills：按 id 在自持目录里校验 enabled，并从目录取 SKILL.md 正文。"""

    async def test_trusted_skill_uses_catalog_and_readme(self):
        async def load_records(_token):
            return [
                {"id": "ppt-studio", "skillId": "ppt-studio", "name": "ppt-studio",
                 "description": "做 PPT", "enabled": 1, "version": "3.0.7"},
                {"id": "off", "skillId": "off", "name": "停用", "enabled": 0},
            ]

        async def load_readme(record_id, _token):
            return "---\nname: ppt-studio\n---\n# 正文" if record_id == "ppt-studio" else ""

        with patch("app.services.chat.turn_context_builder._load_catalog_records", load_records), \
             patch("app.services.skills.skill_catalog.load_skill_readme", load_readme):
            trusted = await tcb._fetch_trusted_skills(["ppt-studio", "off", "missing"], "tok")

        self.assertEqual([s["id"] for s in trusted], ["ppt-studio"])
        self.assertEqual(trusted[0]["record_id"], "ppt-studio")
        self.assertEqual(trusted[0]["version"], "3.0.7")
        self.assertTrue(trusted[0]["is_ppt_skill"])
        self.assertIn("# 正文", trusted[0]["instructions"])
        self.assertNotIn("name: ppt-studio", trusted[0]["instructions"])  # frontmatter 已剥


if __name__ == "__main__":
    unittest.main()
