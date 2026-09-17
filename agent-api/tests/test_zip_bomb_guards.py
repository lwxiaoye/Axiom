"""解压炸弹防护 + 文档解析不阻塞事件循环（深度扫描 P0-1 / P0-2）。

覆盖：
- zip_guard 三道阈值（总量/单条目/条目数）与 ZipReadBudget 的分块实读预算；
- parse_upload 对 docx/pptx/xlsx 的解压体积闸（超限 failed，且**不**进解析器）；
- 同步解析（docx/pptx/xlsx/pdf）走 asyncio.to_thread，不独占事件循环；
- 技能包导入（HTTP 400）与挂载（明确抛错，不静默截断）两条路径的体积闸。
"""

import asyncio
import base64
import io
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services.files import document_parse_service
from app.services.platform import zip_guard


def _bomb_zip(entry_name: str, total_bytes: int, *, chunk_mb: int = 4) -> bytes:
    """造高压缩比 zip：解压后 total_bytes 字节的零，压缩后只有几 KB（分块写，测试自身不吃内存）。"""
    buf = io.BytesIO()
    chunk = b"\0" * (chunk_mb * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open(entry_name, "w") as fh:
            written = 0
            while written < total_bytes:
                fh.write(chunk)
                written += len(chunk)
    return buf.getvalue()


def _plain_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------- zip_guard 本体


class TestZipGuardHelper:
    def test_compression_ratio_of_fixture_is_bomb_like(self):
        """前提自检：4MB 零压出来只有几 KB —— 「压缩后体积」这道闸确实拦不住。"""
        raw = _bomb_zip("payload.bin", 4 * 1024 * 1024)
        assert len(raw) < 64 * 1024

    def test_entry_limit_rejected_by_declared_size(self):
        raw = _bomb_zip("payload.bin", 8 * 1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with pytest.raises(zip_guard.ZipBombError) as exc:
                zip_guard.ensure_zip_within_limits(archive, max_entry_bytes=1024 * 1024)
        assert "payload.bin" in exc.value.message
        assert "超限" in exc.value.reason

    def test_total_limit_rejected(self):
        raw = _plain_zip({f"f{i}.txt": b"a" * 200_000 for i in range(5)})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with pytest.raises(zip_guard.ZipBombError):
                zip_guard.ensure_zip_within_limits(
                    archive, max_total_bytes=500_000, max_entry_bytes=10 * 1024 * 1024
                )

    def test_entry_count_limit_rejected(self):
        raw = _plain_zip({f"f{i}.txt": b"x" for i in range(20)})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with pytest.raises(zip_guard.ZipBombError) as exc:
                zip_guard.ensure_zip_within_limits(archive, max_entries=5)
        assert "条目数" in exc.value.reason

    def test_normal_zip_passes_and_returns_total(self):
        raw = _plain_zip({"a.txt": b"a" * 10, "b.txt": b"b" * 20})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert zip_guard.ensure_zip_within_limits(archive) == 30

    def test_non_zip_bytes_are_passed_through(self):
        """体积闸不做格式校验：不是 zip 就放行，交给各自的解析器报自己的错。"""
        assert zip_guard.ensure_zip_bytes_within_limits(b"definitely not a zip") == 0

    def test_read_budget_stops_on_entry_over_limit(self):
        raw = _bomb_zip("payload.bin", 8 * 1024 * 1024)
        budget = zip_guard.ZipReadBudget(max_entry_bytes=1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with pytest.raises(zip_guard.ZipBombError):
                budget.read(archive, "payload.bin")

    def test_read_budget_accumulates_across_entries(self):
        raw = _plain_zip({"a.txt": b"a" * 400_000, "b.txt": b"b" * 400_000})
        budget = zip_guard.ZipReadBudget(max_total_bytes=600_000, max_entry_bytes=10 * 1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert len(budget.read(archive, "a.txt")) == 400_000
            with pytest.raises(zip_guard.ZipBombError) as exc:
                budget.read(archive, "b.txt")
        assert "总体积" in exc.value.reason

    def test_read_budget_returns_bytes_within_limit(self):
        raw = _plain_zip({"a.txt": b"hello"})
        budget = zip_guard.ZipReadBudget()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert budget.read(archive, "a.txt") == b"hello"
        assert budget.used == 5


# ---------------------------------------------------- P0-1：文档解析体积闸 + 不阻塞


class TestDocumentParseZipGuard:
    @pytest.mark.asyncio
    async def test_xlsx_bomb_rejected_before_parser_runs(self):
        """<1MB 的 xlsx 声明解压后 110MB：必须在 openpyxl 拿到它之前就判 failed。"""
        raw = _bomb_zip("xl/worksheets/sheet1.xml", 110 * 1024 * 1024)
        assert len(raw) < 1024 * 1024

        def _must_not_run(_content):  # noqa: ANN001
            raise AssertionError("解析器不应被调用：体积闸必须先拦住")

        with patch.object(document_parse_service, "_parse_xlsx", _must_not_run):
            parsed = await document_parse_service.parse_upload("bomb.xlsx", raw)

        assert parsed["status"] == "failed"
        assert parsed["chars"] == 0
        assert "超限" in (parsed["note"] or "")
        assert "拒绝解析" in parsed["text"]

    @pytest.mark.asyncio
    async def test_docx_bomb_rejected(self):
        raw = _bomb_zip("word/document.xml", 110 * 1024 * 1024)

        def _must_not_run(_content):  # noqa: ANN001
            raise AssertionError("解析器不应被调用")

        with patch.object(document_parse_service, "_parse_docx", _must_not_run):
            parsed = await document_parse_service.parse_upload("bomb.docx", raw)
        assert parsed["status"] == "failed"

    @pytest.mark.asyncio
    async def test_total_size_limit_uses_module_threshold(self):
        """多条目各自不超单条目上限、但总量越线 —— 走的是总量闸。"""
        raw = _plain_zip({f"xl/sheet{i}.xml": b"<c/>" * 60_000 for i in range(6)})
        with (
            patch.object(document_parse_service, "DOC_ZIP_MAX_TOTAL_BYTES", 500_000),
            patch.object(document_parse_service, "DOC_ZIP_MAX_ENTRY_BYTES", 10 * 1024 * 1024),
        ):
            parsed = await document_parse_service.parse_upload("many.xlsx", raw)
        assert parsed["status"] == "failed"
        assert "总体积" in (parsed["note"] or "")

    @pytest.mark.asyncio
    async def test_normal_xlsx_still_parses(self):
        """回归：正常表格不受闸门影响（真跑 openpyxl）。"""
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "数据"
        sheet.append(["姓名", "分数"])
        sheet.append(["张三", 91])
        out = io.BytesIO()
        workbook.save(out)

        parsed = await document_parse_service.parse_upload("score.xlsx", out.getvalue())
        assert parsed["status"] == "ok"
        assert "张三" in parsed["text"]
        assert "91" in parsed["text"]

    def test_embedded_media_extraction_respects_budget(self):
        """内嵌图片是可选增强：超预算时中止取图并保留已取到的，不抛给调用方。"""
        entries = {
            "word/media/image1.png": b"\x89PNG" + b"a" * 300_000,
            "word/media/image2.png": b"\x89PNG" + b"b" * 300_000,
        }
        raw = _plain_zip(entries)
        with patch.object(document_parse_service, "_MEDIA_BUDGET_TOTAL_BYTES", 400_000):
            images = document_parse_service._extract_docx_images(raw)
        assert len(images) == 1  # 第二张越线，停止取图但不炸

    @pytest.mark.asyncio
    async def test_sync_parse_runs_off_event_loop(self):
        """核心回归：同步解析必须在线程里跑 —— 期间事件循环还能继续调度别的协程。"""
        parse_seconds = 0.4

        def _slow_parse(_content):  # noqa: ANN001
            time.sleep(parse_seconds)  # 纯同步 CPU 密集的替身
            return "解析结果"

        ticks = 0

        async def _ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(_ticker())
        try:
            with patch.object(document_parse_service, "_parse_docx", _slow_parse):
                parsed = await document_parse_service.parse_upload(
                    "slow.docx", b"not-a-real-zip", ocr_embedded_images=False
                )
        finally:
            task.cancel()

        assert parsed["text"] == "解析结果"
        # 阻塞事件循环的话 ticks 会停在 0/1；正常调度下 0.4s 至少能跑十几个 10ms tick
        assert ticks >= 10, f"事件循环被解析独占了（ticks={ticks}）"


# ------------------------------------------------------------ P0-2：技能包体积闸


class TestSkillPackageZipGuard:
    def _upload(self, raw: bytes, filename: str = "skill.zip"):
        from fastapi import UploadFile

        return UploadFile(file=io.BytesIO(raw), filename=filename)

    @pytest.mark.asyncio
    async def test_import_rejects_decompression_bomb(self):
        from app.core.auth import UserContext
        from app.routers import agent_skill

        # 压缩后 <5MB（现有上传闸照旧放行），解压后单条目 60MB —— 越单条目闸
        buf = io.BytesIO()
        chunk = b"\0" * (4 * 1024 * 1024)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", "# 伪装成正常技能\n")
            with zf.open("payload.bin", "w") as fh:
                for _ in range(15):  # 15 × 4MB = 60MB > SKILL_ZIP_MAX_ENTRY_BYTES
                    fh.write(chunk)
        raw = buf.getvalue()
        assert len(raw) < 5 * 1024 * 1024

        user = UserContext(user_id="u1", username="tester")
        with pytest.raises(HTTPException) as exc:
            await agent_skill.import_skill(file=self._upload(raw), user=user)
        assert exc.value.status_code == 400
        assert "拒绝解析" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_import_accepts_normal_package(self):
        """回归：正常技能包仍能读出 skill.json / SKILL.md（只验解析段，不落库）。"""
        from app.core.auth import UserContext
        from app.routers import agent_skill

        raw = _plain_zip({
            "skill.json": b'{"name": "\\u6d4b\\u8bd5\\u6280\\u80fd", "description": "d"}',
            "SKILL.md": "# 测试技能\n步骤...".encode(),
            "scripts/run.py": b"print(1)",
        })
        user = UserContext(user_id="u1", username="tester")
        captured = {}

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def add(self, obj):
                captured.setdefault("added", []).append(obj)

            async def commit(self):
                return None

            async def refresh(self, obj):
                return None

        with patch.object(agent_skill, "async_session", _FakeSession):
            result = await agent_skill.import_skill(file=self._upload(raw), user=user)

        assert result["name"] == "测试技能"
        version = [o for o in captured["added"] if hasattr(o, "package_b64")][0]
        assert "# 测试技能" in (version.content or "")
        assert '"hasScripts": true' in version.import_source_json

    def test_mount_path_fails_loudly_on_bomb(self):
        """挂载路径（每次挂技能都重新解包）超限必须明确抛错，不静默截断。"""
        from app.routers import agent_skill

        raw = _bomb_zip("payload.bin", 60 * 1024 * 1024)
        version = SimpleNamespace(package_b64=base64.b64encode(raw).decode("ascii"), content="# 说明书")
        with pytest.raises(zip_guard.ZipBombError):
            agent_skill._extract_skill_files(version)

    def test_mount_path_normal_package_unchanged(self):
        from app.routers import agent_skill

        raw = _plain_zip({
            "my-skill/SKILL.md": b"# hi",
            "my-skill/entrypoint.sh": b"echo ok",
        })
        version = SimpleNamespace(package_b64=base64.b64encode(raw).decode("ascii"), content=None)
        files, entrypoint = agent_skill._extract_skill_files(version)
        assert set(files) == {"SKILL.md", "entrypoint.sh"}  # 公共顶层目录被剥掉
        assert entrypoint == "entrypoint.sh"
