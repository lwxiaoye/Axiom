# -*- coding: utf-8 -*-
"""交付物判据定向测试（2026-07-27 用户拍板）。

盯的是真机踩出来的那件事：做一份 PPT 时模型先写 `build.py` 生成脚本，用户收到的却是一张
`workspace_tmp_build.py`（PY · 15.1 KB · 已保存到我的文件）的产物卡，真正的 pptx 混在里面。

三条断言最要紧，缺一条闸就形同虚设：
- **模型侧看到的仍是全量**：`list_files()` 默认不过滤。过滤了会让沙箱里凭空少文件，
  `read_file` 说「不存在」而 bash 里明明看得见——比噪音严重得多的故障。
- **上传件永远可见**：用户自己传的 .py 是他自己的东西，没有资格藏。
- **文件夹计数与视图同口径**：藏了文件却按全量报数 = 点开是空文件夹。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models import AgentUserFile, AgentUserFolder, Base


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_as_text(element, compiler, **kw):  # noqa: ARG001
    # 测试用 sqlite 内存库承载 MySQL 模型：MEDIUMTEXT 按 TEXT 编译（与其它 DB 测试同款 shim）
    return "TEXT"
from app.services.files import user_file_service
from app.services.files.deliverable import filter_rows, is_deliverable


# ---------- 判据本体 ----------

class DeliverableClassifierTests(unittest.TestCase):
    def test_document_formats_are_deliverable(self):
        for name in (
            "季度报告.docx", "方案.doc", "汇报.pptx", "课件.ppt", "台账.xlsx", "明细.xls",
            "说明.pdf", "任务计划.md", "README.markdown", "文案.txt", "数据.csv", "表.tsv",
        ):
            self.assertTrue(is_deliverable(name), name)

    def test_web_and_images_stay_deliverable(self):
        """用户点名保留：平台本来就能预览 html；图片走到这一层已是最终产物。"""
        for name in ("index.html", "page.htm", "chart.png", "logo.svg", "photo.jpeg"):
            self.assertTrue(is_deliverable(name), name)

    def test_process_files_are_not_deliverable(self):
        for name in (
            "workspace_tmp_build.py",   # 真机那张卡就是它
            "create_ppt.py", "run.sh", "data.json", "deck.slides.json",
            "notes.log", "config.yaml", "bundle.zip", "README",  # 无扩展名不算文档
        ):
            self.assertFalse(is_deliverable(name), name)

    def test_research_html_is_deliverable_companion_markdown_is_not(self):
        self.assertTrue(is_deliverable("竞品分析.html", "research"))
        self.assertFalse(is_deliverable("竞品分析.research.md", "research"))
        self.assertFalse(is_deliverable("竞品分析.research.md", "generated"))

    def test_generated_ppt_source_bundle_is_not_user_visible(self):
        self.assertFalse(is_deliverable("季度汇报-source.zip"))
        self.assertFalse(is_deliverable("季度汇报-SOURCE.ZIP"))
        self.assertFalse(is_deliverable("季度汇报.zip"))
        self.assertFalse(is_deliverable("素材-source.zip", "material"))

    def test_batch_deliverables_zip_is_user_visible(self):
        self.assertTrue(is_deliverable("batch-deliverables.zip", "generated"))
        self.assertTrue(is_deliverable("batch-deliverables.zip"))
        self.assertFalse(is_deliverable("季度汇报.zip", "generated"))

    def test_extension_match_is_case_insensitive(self):
        self.assertTrue(is_deliverable("DECK.PPTX"))
        self.assertFalse(is_deliverable("BUILD.PY"))

    def test_uploaded_files_are_always_deliverable(self):
        """「我的文件」页主动上传的脚本是用户自己的文件，平台没资格因为后缀把它藏掉。"""
        self.assertTrue(is_deliverable("build.py", "uploaded"))
        self.assertFalse(is_deliverable("build.py", "generated"))

    def test_chat_composer_attachments_are_workspace_not_deliverable(self):
        """对话框放下的图片/PPT 是本轮素材，不得进「我的文件」。"""
        from app.services.files.deliverable import WORKSPACE_SOURCE, is_staging_checkpoint_source
        self.assertTrue(is_staging_checkpoint_source(WORKSPACE_SOURCE))
        self.assertFalse(is_deliverable("image.png", WORKSPACE_SOURCE))
        self.assertFalse(is_deliverable("参考稿.pptx", WORKSPACE_SOURCE))
        self.assertFalse(is_deliverable("粘贴的文本.txt", WORKSPACE_SOURCE))

    def test_staging_checkpoint_is_never_a_deliverable(self):
        from app.services.files.deliverable import (
            STAGING_CHECKPOINT_SOURCE,
            is_staging_checkpoint_source,
        )
        self.assertLessEqual(len(STAGING_CHECKPOINT_SOURCE), 16)
        self.assertTrue(is_staging_checkpoint_source("staging_ckpt"))
        self.assertTrue(is_staging_checkpoint_source("staging_checkpoint"))
        self.assertFalse(is_deliverable(".ppt-project-run.tgz", "staging_ckpt"))
        self.assertFalse(is_deliverable(".ppt-project-run.tgz", "staging_checkpoint"))
        self.assertFalse(is_deliverable("deck.pptx", "staging_ckpt"))
        self.assertFalse(is_deliverable("deck.pptx", "staging_checkpoint"))
        """真机第二例：「扫描一下这个 GitHub 仓库」逐个 download_url 取回 16 个源文件，
        其中 README.md / requirements.txt **是白名单格式**——只按后缀判拦不住，
        必须靠来源这条轴。"""
        for name in ("README.md", "requirements.txt", "spec.pdf", "logo.png"):
            self.assertTrue(is_deliverable(name), f"{name} 本身是文档格式")
            self.assertFalse(is_deliverable(name, "material"), f"{name} 取来的材料不算交付")

    def test_filter_rows_prefers_precomputed_flag_and_falls_back_to_name(self):
        rows = [
            {"filename": "a.docx"},                              # 无字段 → 按后缀
            {"filename": "b.py"},                                # 无字段 → 按后缀滤掉
            {"filename": "c.py", "deliverable": True},           # 字段优先（上传件已算好）
            {"filename": "d.docx", "deliverable": False},
            "不是 dict",
        ]
        self.assertEqual(
            [r["filename"] for r in filter_rows(rows)], ["a.docx", "c.py"]
        )


class RowDictTests(unittest.TestCase):
    def test_row_dict_carries_the_flag(self):
        """唯一的行字典出口算一次，所有读路径（清单/落库返回/版本详情）自动一致。"""
        row = SimpleNamespace(
            id="f1", filename="build.py", mime="text/x-python", size_bytes=10,
            source="generated", thread_id=None, folder_id=None,
            expires_at=None, created_at=None,
        )
        self.assertFalse(user_file_service._row_to_dict(row)["deliverable"])
        row.filename = "报告.docx"
        self.assertTrue(user_file_service._row_to_dict(row)["deliverable"])


# ---------- 「我的文件」清单 ----------

@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(user_file_service, "async_session", factory)

    async def _no_purge(_session, _uid):
        return None

    async def _all_rows(rows, *, source):  # 字节是否在盘上不是本测试的对象
        return list(rows)

    monkeypatch.setattr(user_file_service, "_purge_expired", _no_purge)
    monkeypatch.setattr(user_file_service, "_rows_with_existing_bytes", _all_rows)
    yield factory
    await engine.dispose()


async def _seed(factory, rows, folders=()):
    async with factory() as s:
        for fid, name in folders:
            s.add(AgentUserFolder(id=fid, user_id="u1", name=name))
        for i, (name, source, folder_id) in enumerate(rows):
            s.add(AgentUserFile(
                id=f"f{i}", user_id="u1", filename=name, mime="", size_bytes=10,
                source=source, storage_path=f"u1/f{i}", folder_id=folder_id,
            ))
        await s.commit()


@pytest.mark.asyncio
async def test_model_side_listing_is_still_full(db):
    """默认口径必须全量：workspace_sync/paths/list_files 工具都吃它，过滤=沙箱里凭空少文件。"""
    await _seed(db, [("报告.docx", "generated", None), ("build.py", "generated", None)])
    names = [f["filename"] for f in (await user_file_service.list_files("u1", "__all__"))["files"]]
    assert sorted(names) == ["build.py", "报告.docx"]


@pytest.mark.asyncio
async def test_chat_composer_attachments_never_appear_in_my_files(db):
    """主对话对话框放下的附件不得出现在「我的文件」，包括「显示全部」。"""
    await _seed(db, [
        ("汇报.pptx", "generated", None),
        ("对话里丢的课件.pptx", "workspace", None),
        ("粘贴的文本.txt", "workspace", None),
        ("截图.png", "workspace", None),
        ("我自己传的.py", "uploaded", None),
    ])
    shown = await user_file_service.list_files("u1", "__all__", deliverables_only=True)
    assert sorted(f["filename"] for f in shown["files"]) == ["我自己传的.py", "汇报.pptx"]
    everything = await user_file_service.list_files("u1", "__all__")
    assert sorted(f["filename"] for f in everything["files"]) == ["我自己传的.py", "汇报.pptx"]
    assert everything["quota"]["count"] == 5


@pytest.mark.asyncio
async def test_user_facing_listing_hides_process_files(db):
    await _seed(db, [
        ("报告.docx", "generated", None),
        ("workspace_tmp_build.py", "generated", None),
        ("我自己传的.py", "uploaded", None),
    ])
    listing = await user_file_service.list_files("u1", "__all__", deliverables_only=True)
    names = sorted(f["filename"] for f in listing["files"])
    assert names == sorted(["报告.docx", "我自己传的.py"])
    # 配额按全量算：字节真占着盘，藏起来不等于没占
    assert listing["quota"]["count"] == 3


@pytest.mark.asyncio
async def test_slides_editor_companion_survives_the_filter(db):
    """`<名>.slides.json` 不是交付物，但「我的文件」的 pptx 卡靠同一份清单配对到它才长出
    「编辑」能力。滤掉它 = 静默丢掉整个幻灯片手改功能（卡片还在，按钮没了）。"""
    await _seed(db, [("汇报.pptx", "generated", None), ("汇报.slides.json", "generated", None)])
    listing = await user_file_service.list_files("u1", "__all__", deliverables_only=True)
    names = sorted(f["filename"] for f in listing["files"])
    assert names == ["汇报.pptx", "汇报.slides.json"]
    # 但它自己带着 deliverable=false：产物卡与前端视图照旧把它挡在外面
    companion = next(f for f in listing["files"] if f["filename"].endswith(".slides.json"))
    assert companion["deliverable"] is False


@pytest.mark.asyncio
async def test_downloaded_material_is_hidden_but_not_deleted(db):
    """仓库扫描那 16 个文件全部消失在清单里，但 show_all（deliverables_only=False）拿得回来
    ——藏起来不等于删掉，否则「文件区统一」就变成了「文件会凭空消失」。"""
    await _seed(db, [
        ("README.md", "material", None),
        ("requirements.txt", "material", None),
        ("汇报.pptx", "generated", None),
    ])
    shown = await user_file_service.list_files("u1", "__all__", deliverables_only=True)
    assert [f["filename"] for f in shown["files"]] == ["汇报.pptx"]
    everything = await user_file_service.list_files("u1", "__all__")
    assert len(everything["files"]) == 3


@pytest.mark.asyncio
async def test_staging_checkpoint_hidden_from_default_listing(db):
    """PPTD 检查点不是交付物，模型 list_files / 我的文件默认清单都不得看见。"""
    await _seed(db, [
        ("汇报.pptx", "generated", None),
        (".ppt-project-run1.tgz", "staging_ckpt", None),
    ])
    everything = await user_file_service.list_files("u1", "__all__")
    assert [f["filename"] for f in everything["files"]] == ["汇报.pptx"]
    shown = await user_file_service.list_files("u1", "__all__", deliverables_only=True)
    assert [f["filename"] for f in shown["files"]] == ["汇报.pptx"]
    assert everything["quota"]["count"] == 2


@pytest.mark.asyncio
async def test_folder_count_matches_what_you_see_inside(db):
    """藏了文件却按全量报数 → 用户看到「3 个文件」的文件夹点开是空的。"""
    await _seed(
        db,
        [("a.py", "generated", "d1"), ("b.py", "generated", "d1"), ("c.docx", "generated", "d1")],
        folders=[("d1", "资料")],
    )
    listing = await user_file_service.list_files("u1", None, deliverables_only=True)
    assert [f["fileCount"] for f in listing["folders"]] == [1]
    inside = await user_file_service.list_files("u1", "d1", deliverables_only=True)
    assert [f["filename"] for f in inside["files"]] == ["c.docx"]



# 「任务模式交付清单」那一节随 DAG Runtime 一起删除（2026-07-27）：
# delivery_review 属于已下线的 task_graph 执行核心。判据本体 is_deliverable/filter_rows
# 在 app/services/files/deliverable.py，覆盖仍在上面那些用例里。
