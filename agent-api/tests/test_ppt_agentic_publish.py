"""PPT artifact-coding Profile 的发布边界。"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.services.chat.tools import shell as shell_tools
from app.services.chat.tools.image_fetch import _vet_prompt
from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.shell import build_shell_tools
from app.services.chat.main_tool_turn import map_tool_loop_events
from app.services.chat.types import TurnOutcome
from app.services.sse_protocol import HARNESS, SSEChannel
from app.services.skills.ppt_agentic_adapter import platform_overlay
from app.services.skills.ppt_project_audit_runtime import (
    ASSET_PROVENANCE_FILENAME,
    audit_project,
)
from app.services.agent_harness.model_driver import (
    _trace_has_profile_deliverable,
    _trace_has_published_ppt_artifact,
)


PROFILE = {
    "id": "artifact_coding",
    "artifact_kind": "presentation",
    "authoring_backend": "pptd",
}


def test_platform_overlay_exposes_layout_safety_contract_to_generator():
    overlay = platform_overlay()
    assert "600 → &lt;100" in overlay
    assert "lineHeightPx" in overlay
    assert "letterSpacing" in overlay
    assert "wrap: false" in overlay
    assert "确定性排版检查" in overlay
    assert "size: [width, height]" in overlay
    assert "不得省略 size" in overlay
    assert "scripts/export_pptx.py" in overlay
    assert "fetch_ppt_asset" in overlay
    assert "Noto Sans CJK SC" in overlay
    assert "不要重复运行\n`fc-list : family`" in overlay
    assert "至少两个字体角色" in overlay
    assert "禁止为了绕过" in overlay
    assert "python-pptx/PptxGenJS" in overlay
    assert "最终只向\n  用户交付 .pptx" in overlay
    assert "不要压缩、发布或在最终回答中列出 source ZIP" in overlay
    assert "publish_ppt_artifact" in overlay
    assert "禁止 ls/find" in overlay
    assert "不要等视觉审查通过再导出" in overlay
    assert "跳过配图继续写页" in overlay
    assert "禁止 `find /`" in overlay
    assert "pptd_wasm_bg.wasm" in overlay
    assert "write_file" in overlay
    assert "不要把技能目录当工程" in overlay
    assert "media/" in overlay


def _run_layout_lint(tmp_path, page_body: str, *, size: str = "[960, 540]"):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        f"""version: v2
size: {size}
theme:
  textStyles:
    body: {{fontSize: 16}}
pages: [pages/01.page]
""",
        encoding="utf-8",
    )
    (pages / "01.page").write_text(page_body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", shell_tools._PPTD_LAYOUT_LINT, str(tmp_path)],
        text=True,
        capture_output=True,
    )


def test_pptd_layout_lint_accepts_non_overlapping_text(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: title
    elementType: text
    bounds: [80, 80, 700, 60]
    content: {style: '$body', fontSize: 32, text: '星舰工程'}
  - elementId: body
    elementType: text
    bounds: [80, 180, 700, 80]
    content: {style: '$body', text: '这是一段不会溢出的正文。'}
""")
    assert result.returncode == 0, result.stderr


def test_pptd_layout_lint_rejects_bare_theme_style_reference(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: title
    elementType: text
    bounds: [80, 80, 700, 60]
    content: {style: 'body', text: '星舰工程'}
""")
    assert result.returncode == 1
    assert "theme text style reference must start with '$'" in result.stderr
    assert "got 'body'" in result.stderr


def test_pptd_layout_lint_rejects_unknown_theme_style_reference(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: title
    elementType: text
    bounds: [80, 80, 700, 60]
    content: {style: '$missing', text: '星舰工程'}
""")
    assert result.returncode == 1
    assert "unknown theme text style reference '$missing'" in result.stderr


def test_pptd_layout_lint_does_not_count_yaml_block_terminator_as_a_line(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: kicker
    elementType: text
    bounds: [80, 80, 700, 18]
    content:
      fontSize: 12
      lineHeight: 1.2
      text: |
        SPACE SYSTEMS · ENGINEERING BRIEF
""")
    assert result.returncode == 0, result.stderr


def test_pptd_layout_lint_preserves_intentional_trailing_blank_line(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: body
    elementType: text
    bounds: [80, 80, 300, 18]
    content:
      fontSize: 12
      lineHeightPx: 16
      text: "A\\n\\n"
""")
    assert result.returncode == 1
    assert "body probable text overflow" in result.stderr


def test_pptd_layout_lint_reports_object_size_as_contract_error_not_infra(tmp_path):
    result = _run_layout_lint(tmp_path, "elements: []\n", size="{width: 960, height: 540}")
    assert result.returncode == 1
    assert "size must be [width, height]" in result.stderr
    assert "infrastructure failure" not in result.stderr


def test_pptd_layout_lint_rejects_overflow_and_text_collision(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: stage-title
    elementType: text
    bounds: [90, 200, 144, 26]
    content: {style: '$body', fontSize: 17, text: '阶段 1 · 近地轨道加注'}
  - elementId: stage-body
    elementType: text
    bounds: [73, 232, 154, 62]
    content: {style: '$body', fontSize: 12, text: '星舰之间转移液氧与甲烷。'}
  - elementId: label-a
    elementType: text
    bounds: [470, 330, 120, 44]
    content: {style: '$body', fontSize: 13, text: '土星五号\\n≈5000 美元/公斤'}
  - elementId: label-b
    elementType: text
    bounds: [547, 330, 120, 44]
    content: {style: '$body', fontSize: 13, text: '质子号\\n≈10000 美元/公斤'}
""")
    assert result.returncode != 0
    assert "stage-title probable text overflow" in result.stderr
    assert "label-a overlaps label-b" in result.stderr


@pytest.mark.parametrize("raw", ["<100", "600 → <100", "A<B"])
def test_pptd_layout_lint_rejects_unescaped_comparison_text(tmp_path, raw):
    result = _run_layout_lint(tmp_path, f"""elements:
  - elementId: metric
    elementType: text
    bounds: [80, 80, 700, 80]
    content: {{style: '$body', text: '{raw}'}}
""")
    assert result.returncode == 1
    assert "contains raw '<'" in result.stderr


def test_pptd_layout_lint_accepts_supported_rich_text_and_escaped_lt(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: rich
    elementType: text
    bounds: [80, 80, 160, 48]
    content:
      style: '$body'
      align: [center, middle]
      lineHeightPx: 20
      letterSpacing: 0.5
      text: '<p><span style="font-size:18px">Hello</span><br/>600 → &lt;100</p>'
""")
    assert result.returncode == 0, result.stderr


def test_pptd_layout_lint_honors_nowrap_and_ignores_invisible_text(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: hidden
    elementType: text
    opacity: 0
    bounds: [80, 80, 20, 10]
    content: {style: '$body', text: 'this hidden text is intentionally very long'}
  - elementId: nowrap
    elementType: text
    bounds: [80, 140, 70, 30]
    content:
      style: '$body'
      wrap: false
      letterSpacing: 2
      text: 'NO WRAPPING'
""")
    assert result.returncode == 1
    assert "nowrap text overflow" in result.stderr
    assert "hidden" not in result.stderr


def test_pptd_layout_lint_rejects_live_giant_serif_metric_wrap(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: num-old
    elementType: text
    bounds: [48, 138, 280, 230]
    content:
      fontFamily: Georgia
      fontSize: 160
      bold: true
      align: [left, middle]
      text: '600'
  - elementId: num-new
    elementType: text
    bounds: [452, 138, 340, 230]
    content:
      fontFamily: Georgia
      fontSize: 160
      bold: true
      align: [left, middle]
      text: '&lt;100'
""")
    assert result.returncode == 1
    assert "num-old probable text overflow" in result.stderr
    assert "num-new probable text overflow" in result.stderr


def test_pptd_layout_lint_calibrates_narrow_wide_and_cjk_glyphs(tmp_path):
    narrow = _run_layout_lint(tmp_path, """elements:
  - elementId: narrow
    elementType: text
    bounds: [80, 80, 80, 22]
    content: {fontSize: 20, wrap: false, text: 'iiiiiiiiii'}
  - elementId: cjk
    elementType: text
    bounds: [80, 140, 88, 22]
    content: {fontSize: 20, wrap: false, text: '测试文本'}
""")
    assert narrow.returncode == 0, narrow.stderr

    wide_root = tmp_path / "wide"
    wide_root.mkdir()
    wide = _run_layout_lint(wide_root, """elements:
  - elementId: wide
    elementType: text
    bounds: [80, 80, 70, 22]
    content: {fontSize: 20, wrap: false, text: 'WWWWW'}
""")
    assert wide.returncode == 1
    assert "wide nowrap text overflow" in wide.stderr


def test_pptd_layout_lint_does_not_flag_touching_or_short_text_boxes(tmp_path):
    result = _run_layout_lint(tmp_path, """elements:
  - elementId: left
    elementType: text
    bounds: [80, 80, 300, 40]
    content: {style: '$body', text: 'A'}
  - elementId: right
    elementType: text
    bounds: [200, 120, 300, 40]
    content: {style: '$body', text: 'B'}
""")
    assert result.returncode == 0, result.stderr


class _Result:
    ok = True
    error = None
    stdout = ""
    stderr = ""
    exit_code = 0
    truncated = False
    timed_out = False
    termination_reason = None
    workspace_deleted = []
    workspace_oversized = []
    outputs_migrated = []
    outputs_conflicts = []

    def __init__(self, *, review=None, workspace_changes=None):
        self.review = review
        self.workspace_changes = workspace_changes or []


class _Sync:
    persist_failed: list[str] = []

    def __init__(self):
        self.persist_calls = 0

    async def load(self, _state):
        return []

    async def persist(self, changes):
        self.persist_calls += 1
        return [
            {
                "id": f"file-{idx}",
                "filename": str(item["path"]),
                "size": len(item.get("data") or b""),
            }
            for idx, item in enumerate(changes, start=1)
        ]


def _by_name(tools, name):
    return next(tool for tool in tools if tool.name == name)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "stdout"), [
    ("write_file", {"path": "pages/01.page", "content": "elements: []"}, "WRITE_OK"),
    ("edit_file", {"path": "pages/01.page", "old_string": "old", "new_string": "new"}, "EDIT_OK"),
    ("bash", {"command": "export-command"}, "export ok"),
])
async def test_ppt_tools_forward_trusted_progress_without_promoting_checkpoint_to_delivery(monkeypatch, name, args, stdout):
    from app.services.agent_harness import artifact_checkpoint

    progress = {"kind": "artifact_progress", "artifact_type": "pptx", "checked": True, "stages": ["design"]}
    captures = []

    async def capture(**kwargs):
        captures.append(kwargs)
        return {"file_id": "hidden-checkpoint-id", "progress_receipt": progress}

    async def execute(*_args, **_kwargs):
        result = _Result()
        result.stdout = stdout
        return result

    monkeypatch.setattr(artifact_checkpoint, "capture_ppt_staging", capture)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", execute)
    monkeypatch.setattr(shell_tools, "build_sync", lambda *_a, **_k: _Sync())
    tool = _by_name(build_shell_tools(user_id="u1", thread_id="t1", run_id="r1", execution_profile=PROFILE), name)
    value = await tool.execute(args)
    assert value.status == "succeeded"
    assert value.receipts == [progress]
    assert value.artifacts == []
    assert "hidden-checkpoint-id" not in json.dumps(value.model_dump())
    assert captures == [{"run_id": "r1", "thread_id": "t1", "user_id": "u1", "force": True}]


@pytest.mark.asyncio
async def test_failed_checkpoint_does_not_fail_project_write_or_supply_completion_evidence(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint

    async def capture(**_kwargs):
        raise RuntimeError("checkpoint storage unavailable")

    async def execute(*_args, **_kwargs):
        result = _Result()
        result.stdout = "WRITE_OK"
        return result

    monkeypatch.setattr(artifact_checkpoint, "capture_ppt_staging", capture)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", execute)
    tool = _by_name(build_shell_tools(user_id="u1", run_id="r1", execution_profile=PROFILE), "write_file")
    value = await tool.execute({"path": "DESIGN.md", "content": "design"})
    assert value.status == "succeeded"
    assert value.receipts[0]["checked"] is False
    assert value.receipts[0]["stages"] == []


def test_publish_tool_only_exists_for_agentic_ppt_profile():
    normal = build_shell_tools(user_id="u1")
    artifact = build_shell_tools(user_id="u1", execution_profile=PROFILE)
    assert [tool.name for tool in normal] == ["bash"]
    assert [tool.name for tool in artifact] == [
        "bash", "glob", "read_file", "write_file", "edit_file",
        "fetch_ppt_asset", "publish_ppt_artifact",
    ]
    publish = _by_name(artifact, "publish_ppt_artifact")
    assert publish.readonly is False
    assert publish.parallel_safe is False
    assert {profile.value for profile in publish.spec.allowed_execution_profiles} == {
        "artifact_coding",
    }
    fetch = _by_name(artifact, "fetch_ppt_asset")
    assert {profile.value for profile in fetch.spec.allowed_execution_profiles} == {
        "artifact_coding",
    }
    bash = _by_name(artifact, "bash")
    assert {profile.value for profile in bash.spec.allowed_execution_profiles} == {
        "interactive", "artifact_coding",
    }
    for name in ("glob", "read_file", "write_file", "edit_file"):
        tool = _by_name(artifact, name)
        assert {profile.value for profile in tool.spec.allowed_execution_profiles} == {
            "artifact_coding",
        }
        assert "我的文件" in tool.description or "PPT 工程" in tool.description


def test_ppt_project_relpath_stays_inside_staging():
    from app.services.chat.tools.shell import ppt_project_relpath
    import pytest

    assert ppt_project_relpath("pages/01.page") == "pages/01.page"
    assert ppt_project_relpath("/workspace/tmp/ppt-project/DESIGN.md") == "DESIGN.md"
    with pytest.raises(ValueError):
        ppt_project_relpath("../skills/ppt-studio/SKILL.md")
    with pytest.raises(ValueError):
        ppt_project_relpath("/workspace/skills/ppt-studio/SKILL.md")
    with pytest.raises(ValueError):
        ppt_project_relpath("/workspace/files/deck.pptx")


@pytest.mark.asyncio
async def test_ppt_write_file_rejects_skill_and_my_files_paths():
    write = _by_name(
        build_shell_tools(user_id="u1", execution_profile=PROFILE),
        "write_file",
    )
    with pytest.raises(ToolSoftError):
        await write.execute({
            "path": "/workspace/skills/ppt-studio/SKILL.md",
            "content": "x",
        })
    with pytest.raises(ToolSoftError):
        await write.execute({
            "path": "/workspace/files/deck.pptx",
            "content": "x",
        })


@pytest.mark.asyncio
async def test_ppt_profile_path_tools_yield_to_project_file_tools():
    from app.services.agent_harness.tool_registry import assert_tool_specs
    from app.services.chat.tools import build_tools
    from app.services.chat.tools.paths import build_path_tools

    path_names = {
        tool.name for tool in build_path_tools(user_id="u1", execution_profile=PROFILE)
    }
    assert "write_file" not in path_names
    assert "read_file" not in path_names
    tools = await build_tools(
        token="t",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u1",
        user_message="做一份PPT",
        action_authority="mutate",
        execution_profile=PROFILE,
    )
    assert_tool_specs(tools)
    write = next(tool for tool in tools if tool.name == "write_file")
    assert "PPT 工程" in write.description or "tmp/ppt-project" in write.description


def _write_audit_fixture(tmp_path, *, images=False, fonts=True):
    pages = tmp_path / "pages"
    pages.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    styles = (
        "title: {fontFamily: 'Jersey15', fontSize: 48, bold: true}\n"
        "    body: {fontFamily: 'MiSans', fontSize: 18}"
        if fonts else "body: {fontSize: 18}"
    )
    (tmp_path / "deck.pptd").write_text(
        "version: v2\nsize: [960, 540]\ntheme:\n  textStyles:\n    "
        + styles + "\npages: [pages/01.page]\n",
        encoding="utf-8",
    )
    image = ""
    if images:
        (media / "action.jpg").write_bytes(b"image")
        image = """  - elementId: hero
    elementType: image
    bounds: [480, 40, 430, 430]
    src: media/action.jpg
"""
    (pages / "01.page").write_text(
        "elements:\n" + image + "  - elementId: title\n"
        "    elementType: text\n    bounds: [40, 40, 400, 80]\n"
        "    content: {style: '$title', text: 'WHY NOT?'}\n",
        encoding="utf-8",
    )


def _photo_receipt(
    tmp_path,
    filename="action.jpg",
    *,
    source_page="https://www.nba.com/game/curry",
    source_url="https://cdn.nba.com/curry.jpg",
):
    data = (tmp_path / "media" / filename).read_bytes()
    return {
        "filename": filename,
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_kind": "search_result",
        "source_url": source_url,
        "source_page": source_page,
        "source_title": f"Source for {filename}",
    }


def _write_photo_provenance(tmp_path, receipts):
    (tmp_path / ASSET_PROVENANCE_FILENAME).write_text(json.dumps({
        "version": 1,
        "assets": receipts,
    }), encoding="utf-8")


def _add_substantive_images(tmp_path, filenames):
    page = tmp_path / "pages" / "01.page"
    body = page.read_text(encoding="utf-8")
    rows = []
    for index, filename in enumerate(filenames, start=2):
        (tmp_path / "media" / filename).write_bytes(f"image-{index}".encode())
        rows.append(
            f"  - elementId: hero-{index}\n"
            "    elementType: image\n"
            f"    bounds: [{40 + index * 30}, 160, 260, 180]\n"
            f"    src: media/{filename}\n"
        )
    page.write_text(body + "".join(rows), encoding="utf-8")


def test_project_audit_rejects_implicit_typography(tmp_path):
    _write_audit_fixture(tmp_path, fonts=False)
    issues = audit_project(tmp_path)
    assert any("typography system is implicit" in issue for issue in issues)


def test_project_audit_requires_downloaded_asset_to_be_used(tmp_path):
    _write_audit_fixture(tmp_path, images=False)
    issues = audit_project(
        tmp_path, require_images=True, expected_assets=["action.jpg"],
    )
    assert any("at least 1 distinct substantive images" in issue for issue in issues)
    assert any("downloaded PPT assets are unused" in issue for issue in issues)


def test_project_audit_accepts_explicit_fonts_and_substantive_image(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    assert audit_project(
        tmp_path, require_images=True, expected_assets=["action.jpg"],
    ) == []


def test_project_audit_rejects_bash_created_jpeg_as_real_match_photo(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    issues = audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=1,
    )
    assert any("provenance is missing" in issue for issue in issues)
    assert any("verified provenance" in issue for issue in issues)


def test_project_audit_accepts_used_photo_with_matching_fetch_provenance(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    receipt = _photo_receipt(tmp_path)
    _write_photo_provenance(tmp_path, [receipt])
    assert audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=1,
        trusted_photo_assets=[receipt],
    ) == []


def test_project_audit_rejects_model_forged_provenance_without_fetch_receipt(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    _write_photo_provenance(tmp_path, [_photo_receipt(tmp_path)])

    issues = audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=1,
    )

    assert any("not backed by a platform fetch receipt" in issue for issue in issues)
    assert any("verified provenance" in issue for issue in issues)


def test_project_audit_rejects_tampered_project_provenance(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    receipt = _photo_receipt(tmp_path)
    forged = {**receipt, "source_page": "https://forged.example/story"}
    _write_photo_provenance(tmp_path, [forged])

    issues = audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=1,
        trusted_photo_assets=[receipt],
    )

    assert any("does not match the platform fetch receipt" in issue for issue in issues)


def test_project_audit_accepts_distinct_verified_source_domains(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    _add_substantive_images(tmp_path, ["second.jpg", "third.jpg"])
    receipts = [
        _photo_receipt(tmp_path),
        _photo_receipt(
            tmp_path, "second.jpg",
            source_page="https://www.espn.com/nba/story",
            source_url="https://cdn.espn.com/second.jpg",
        ),
        _photo_receipt(
            tmp_path, "third.jpg",
            source_page="https://sports.yahoo.com/nba/story",
            source_url="https://s.yimg.com/third.jpg",
        ),
    ]
    _write_photo_provenance(tmp_path, receipts)

    assert audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=3,
        min_photo_sources=3,
        trusted_photo_assets=receipts,
    ) == []


def test_project_audit_rejects_three_photos_from_one_source_domain(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    _add_substantive_images(tmp_path, ["second.jpg", "third.jpg"])
    receipts = [
        _photo_receipt(tmp_path, filename, source_page=f"https://nba.com/story/{index}")
        for index, filename in enumerate(("action.jpg", "second.jpg", "third.jpg"), start=1)
    ]
    _write_photo_provenance(tmp_path, receipts)

    issues = audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=3,
        min_photo_sources=3,
        trusted_photo_assets=receipts,
    )

    assert any("at least 3 distinct photographic source domains" in issue for issue in issues)


def test_project_audit_accepts_unchanged_user_uploaded_photo(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    user_files = tmp_path / "user-files"
    user_files.mkdir()
    (user_files / "action.jpg").write_bytes(
        (tmp_path / "media" / "action.jpg").read_bytes()
    )
    assert audit_project(
        tmp_path,
        require_images=True,
        require_photo_provenance=True,
        min_images=1,
        user_photo_assets=["action.jpg"],
        user_assets_root=user_files,
    ) == []


@pytest.mark.parametrize("src", ["https://cdn.example/curry.jpg", "http://cdn.example/curry.jpg"])
def test_project_audit_rejects_remote_image_sources(tmp_path, src):
    _write_audit_fixture(tmp_path, images=False)
    page = tmp_path / "pages" / "01.page"
    page.write_text(
        "elements:\n  - elementId: hero\n    elementType: image\n"
        "    bounds: [480, 40, 430, 430]\n"
        f"    src: {src}\n",
        encoding="utf-8",
    )

    issues = audit_project(tmp_path, require_images=True)

    assert any("fetch_ppt_asset" in issue and "media/<filename>" in issue for issue in issues)


def test_project_audit_enforces_distinct_requested_image_count(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    issues = audit_project(tmp_path, require_images=True, min_images=4)
    assert any("at least 4 distinct substantive images" in issue for issue in issues)


def test_project_audit_requires_every_downloaded_asset_to_be_used(tmp_path):
    _write_audit_fixture(tmp_path, images=True)
    issues = audit_project(
        tmp_path,
        require_images=True,
        expected_assets=["action.jpg", "unused.jpg"],
    )
    assert any("unused.jpg" in issue for issue in issues)


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        ("必须使用至少 4 张真实比赛照片", 4),
        ("不少于四张高清图片", 4),
        ("use at least 5 photos", 5),
        ("要包含几张库里的比赛照片", 3),
        ("至少 3 张不同来源的库里 NBA 真实比赛现场照片", 3),
        ("使用四张来自官方媒体的真实赛事照片", 4),
        ("需要真实比赛照片", 0),
    ],
)
def test_requested_ppt_image_count(brief, expected):
    assert shell_tools._requested_ppt_image_count(brief) == expected


def test_semantic_photo_vet_prompt_is_explicitly_per_image():
    prompt = _vet_prompt("至少 3 张不同来源的库里真实比赛照片")
    assert "当前只输入一张图是正常的" in prompt
    assert "不要检查整份 PPT 的图片数量" in prompt
    assert "未提供 PPTX" in prompt


def test_bundled_pyyaml_runtime_parses_yaml_without_site_packages(tmp_path):
    runtime = tmp_path / shell_tools._PYYAML_RUNTIME_FILENAME
    runtime.write_bytes(shell_tools._pyyaml_runtime_zip())
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-P",
            "-c",
            "import yaml; assert yaml.safe_load('value: 3')['value'] == 3",
        ],
        env={"PYTHONPATH": str(runtime)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_project_audit_accepts_font_stack_if_any_family_is_installed(tmp_path, monkeypatch):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        "version: v2\nsize: [960, 540]\ntheme:\n  textStyles:\n"
        "    title: {fontFamily: {latin: 'Helvetica Neue', ea: 'Noto Sans CJK SC'}, fontSize: 40}\n"
        "    body: {fontFamily: 'Noto Sans CJK SC', fontSize: 16}\n"
        "pages: [pages/01.page]\n",
        encoding="utf-8",
    )
    (pages / "01.page").write_text(
        "elements:\n  - elementId: title\n    elementType: text\n"
        "    bounds: [40, 40, 400, 80]\n    content: {style: '$title', text: '力学'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.skills.ppt_project_audit_runtime.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Noto Sans CJK SC\nNoto Serif CJK SC\n", stderr="",
        ),
    )
    issues = audit_project(tmp_path, check_installed_fonts=True)
    assert not any("unavailable" in issue for issue in issues)


def test_project_audit_rejects_declared_font_missing_from_exporter(tmp_path, monkeypatch):
    _write_audit_fixture(tmp_path, images=True)
    monkeypatch.setattr(
        "app.services.skills.ppt_project_audit_runtime.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Inter\nNoto Sans CJK SC\n", stderr="",
        ),
    )
    issues = audit_project(tmp_path, check_installed_fonts=True)
    assert any("Jersey15" in issue and "unavailable" in issue for issue in issues)


@pytest.mark.asyncio
async def test_fetch_ppt_asset_resolves_search_ref_and_stages_in_same_session(monkeypatch):
    captured = {}
    tool_meta = {}

    async def fake_fetch(items):
        captured["items"] = items
        return {"westbrook.jpg": b"\xff\xd8\xffphoto"}, []

    async def fake_vet(files, _key, **kwargs):
        captured["vet_kwargs"] = kwargs
        return files, []

    async def fake_execute_in_sandbox(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _Result()

    from app.services.chat.tools import image_fetch
    monkeypatch.setattr(image_fetch, "fetch_image_urls", fake_fetch)
    monkeypatch.setattr(image_fetch, "vet_images", fake_vet)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    fetch_profile = {
        **PROFILE,
        "qa_contract": {"image_requirement": {
            "mode": "searched_photos",
            "min_images": 1,
            "brief": "需要威斯布鲁克比赛照片",
        }},
    }
    fetch = _by_name(build_shell_tools(
        user_id="u1", run_id="run-1", execution_profile=fetch_profile,
        tool_meta_sink=tool_meta,
        image_sink=[{
            "url": "https://cdn.example/w.jpg",
            "source": "https://example/page",
            "title": "Westbrook game photo",
        }],
    ), "fetch_ppt_asset")
    value = await fetch.execute({"url": "[图1]", "filename": "westbrook"})
    assert value.status == "succeeded"
    assert captured["items"][0]["url"] == "https://cdn.example/w.jpg"
    assert captured["items"][0]["referer"] == "https://example/page"
    assert captured["session_key"] == "run-1"
    assert captured["input_files"]["westbrook.jpg"] == b"\xff\xd8\xffphoto"
    provenance = json.loads(
        captured["input_files"][".ppt-asset-provenance-entry.json"].decode("utf-8")
    )
    assert provenance["source_kind"] == "search_result"
    assert provenance["source_page"] == "https://example/page"
    assert provenance["source_title"] == "Westbrook game photo"
    assert captured["vet_kwargs"]["semantic_requirement"] == "需要威斯布鲁克比赛照片"
    assert tool_meta["fetch_ppt_asset"]["asset_receipt"] == provenance
    assert "/workspace/tmp/ppt-project/media/westbrook.jpg" in captured["command"]
    assert ASSET_PROVENANCE_FILENAME in captured["command"]


@pytest.mark.asyncio
async def test_fetch_receipt_is_forwarded_to_publish_audit_in_same_tool_session(monkeypatch):
    sync = _Sync()
    commands = []

    async def fake_fetch(_items):
        return {"curry-action.jpg": b"\xff\xd8\xffphoto"}, []

    async def fake_vet(files, _key, **_kwargs):
        return files, []

    async def fake_execute_in_sandbox(command, **_kwargs):
        commands.append(command)
        if len(commands) == 1:
            return _Result()
        return _Result(
            review={"status": "passed"},
            workspace_changes=[{"path": "result.pptx", "data": b"pptx"}],
        )

    from app.services.chat.tools import image_fetch
    monkeypatch.setattr(image_fetch, "fetch_image_urls", fake_fetch)
    monkeypatch.setattr(image_fetch, "vet_images", fake_vet)
    monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
    monkeypatch.setattr(
        shell_tools.sandbox_executor,
        "execute_in_sandbox",
        fake_execute_in_sandbox,
    )
    tools = build_shell_tools(
        user_id="u1",
        run_id="run-1",
        execution_profile={
            **PROFILE,
            "qa_contract": {"image_requirement": {
                "mode": "searched_photos",
                "min_images": 1,
                "min_sources": 1,
                "brief": "需要库里真实比赛照片",
            }},
        },
        image_sink=[{
            "url": "https://cdn.nba.com/curry-action.jpg",
            "source": "https://www.nba.com/game/curry",
            "title": "Stephen Curry game action",
        }],
    )

    await _by_name(tools, "fetch_ppt_asset").execute({
        "url": "[图1]",
        "filename": "curry-action",
    })
    value = await _by_name(tools, "publish_ppt_artifact").execute({
        "project_dir": "/workspace/tmp/ppt-project",
        "pptx_path": "/workspace/tmp/ppt-project/result.pptx",
        "filename": "result.pptx",
    })

    assert value.status == "succeeded"
    assert len(commands) == 2
    assert "--min-photo-sources 1" in commands[1]
    assert "--expected-assets-json '[\"curry-action.jpg\"]'" in commands[1]
    assert "--trusted-photo-assets-json" in commands[1]
    assert '"filename": "curry-action.jpg"' in commands[1]
    assert '"source_page": "https://www.nba.com/game/curry"' in commands[1]


def test_strict_profile_does_not_treat_staging_names_as_delivery():
    trace = [{
        "name": "bash",
        "status": "completed",
        "preview": (
            "SKILL.md reference/pptd.md pages/001.page "
            "/workspace/tmp/ppt-project/intermediate.pptx"
        ),
    }]
    assert _trace_has_published_ppt_artifact(trace) is False
    assert _trace_has_profile_deliverable(trace, PROFILE) is False


def test_strict_profile_requires_successful_structured_publish_receipt():
    failed = [{
        "name": "publish_ppt_artifact",
        "status": "completed",
        "observation": {
            "status": "failed",
            "artifact_refs": [{"filename": "result.pptx"}],
        },
    }]
    missing_artifact = [{
        "name": "publish_ppt_artifact",
        "status": "completed",
        "observation": {"status": "succeeded", "artifact_refs": []},
    }]
    succeeded = [{
        "name": "publish_ppt_artifact",
        "status": "completed",
        "observation": {
            "status": "succeeded",
            "artifact_refs": [
                {"filename": "result.pptx"},
            ],
        },
    }]
    assert _trace_has_published_ppt_artifact(failed) is False
    assert _trace_has_published_ppt_artifact(missing_artifact) is False
    assert _trace_has_published_ppt_artifact(succeeded) is True


@pytest.mark.asyncio
async def test_strict_event_mapping_does_not_promote_bash_to_delivery():
    async def source():
        yield {
            "type": "tool_result",
            "name": "bash",
            "status": "completed",
            "preview": "SKILL.md reference/pptd.md",
        }

    out = TurnOutcome()
    async for _frame in map_tool_loop_events(
        SSEChannel(HARNESS, "thread", "run"), source(), out,
        strict_ppt_publish=True,
    ):
        pass
    assert out.any_tool_succeeded is True
    assert out.write_tool_succeeded is False
    assert out.published_artifact_succeeded is False


@pytest.mark.asyncio
async def test_generic_event_mapping_does_not_promote_plain_bash_to_delivery():
    async def source():
        yield {
            "type": "tool_result",
            "name": "bash",
            "status": "completed",
            "preview": "ok",
            "observation": {
                "status": "succeeded",
                "structured_data": {"ui": {"summary": "已运行命令", "detail": "ok"}},
                "artifact_refs": [],
            },
        }

    out = TurnOutcome()
    async for _frame in map_tool_loop_events(
        SSEChannel(HARNESS, "thread", "run"), source(), out,
        strict_ppt_publish=False,
    ):
        pass
    assert out.any_tool_succeeded is True
    assert out.write_tool_succeeded is False
    assert out.published_artifact_succeeded is False


@pytest.mark.asyncio
async def test_strict_event_mapping_does_not_promote_fetched_asset_to_delivery():
    async def source():
        yield {
            "type": "tool_result",
            "name": "fetch_ppt_asset",
            "status": "completed",
            "preview": "media/westbrook.jpg",
        }

    out = TurnOutcome()
    async for _frame in map_tool_loop_events(
        SSEChannel(HARNESS, "thread", "run"), source(), out,
        strict_ppt_publish=True,
    ):
        pass
    assert out.any_tool_succeeded is True
    assert out.write_tool_succeeded is False
    assert out.published_artifact_succeeded is False


@pytest.mark.asyncio
async def test_strict_event_mapping_marks_only_publish_as_delivery():
    files = [
        {"filename": "result.pptx", "file_id": "ppt-1", "id": "ppt-1"},
    ]

    async def source():
        yield {
            "type": "tool_result",
            "name": "publish_ppt_artifact",
            "status": "completed",
            "preview": "published",
            "observation": {
                "status": "succeeded",
                "structured_data": {"ui": {"summary": "PPT 已发布"}},
                "artifact_refs": files,
            },
        }

    out = TurnOutcome()
    frames = [frame async for frame in map_tool_loop_events(
        SSEChannel(HARNESS, "thread", "run"), source(), out, {
            "publish_ppt_artifact": {"files": files},
        },
        strict_ppt_publish=True,
    )]
    assert out.write_tool_succeeded is True
    assert out.published_artifact_succeeded is True
    events = [json.loads(frame.removeprefix("data: ")) for frame in frames]
    saved = next(event for event in events if event["type"] == "artifact.saved")
    assert [item["filename"] for item in saved["data"]["files"]] == ["result.pptx"]


@pytest.mark.asyncio
async def test_artifact_bash_never_persists_intermediate_files(monkeypatch):
    sync = _Sync()
    captured = {}

    async def fake_execute_in_sandbox(command, **kwargs):
        captured.update(kwargs)
        return _Result(workspace_changes=[{"path": "half.pptx", "data": b"half"}])

    monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    bash = _by_name(
        build_shell_tools(user_id="u1", run_id="r1", execution_profile=PROFILE),
        "bash",
    )
    await bash.execute({"command": "touch /workspace/files/half.pptx"})
    assert captured["collect_workspace"] is False
    assert captured["migrate_outputs"] is False
    assert sync.persist_calls == 0


@pytest.mark.asyncio
async def test_publish_rejects_paths_outside_staging_before_execution(monkeypatch):
    called = False

    async def fake_execute_in_sandbox(command, **kwargs):
        nonlocal called
        called = True
        return _Result()

    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    publish = _by_name(
        build_shell_tools(user_id="u1", execution_profile=PROFILE),
        "publish_ppt_artifact",
    )
    with pytest.raises(ToolSoftError, match="/workspace/tmp/ppt-project"):
        await publish.execute({
            "pptx_path": "/workspace/files/bypass.pptx",
            "filename": "bypass.pptx",
        })
    assert called is False


@pytest.mark.asyncio
async def test_publish_persists_only_after_passed_review(monkeypatch):
    sync = _Sync()
    captured = {}
    tool_meta = {}

    async def fake_execute_in_sandbox(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _Result(
            review={"status": "passed"},
            workspace_changes=[
                {"path": "result.pptx", "data": b"pptx"},
                {"path": "stale-source.zip", "data": b"source"},
            ],
        )

    monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    photo_profile = {
        **PROFILE,
        "qa_contract": {"image_requirement": {
            "mode": "searched_photos",
            "min_images": 3,
            "min_sources": 3,
            "brief": "要包含几张库里的比赛照片",
        }},
    }
    publish = _by_name(
        build_shell_tools(
            user_id="u1", run_id="r1", execution_profile=photo_profile,
            tool_meta_sink=tool_meta,
        ),
        "publish_ppt_artifact",
    )
    value = await publish.execute({
        "project_dir": "/workspace/tmp/ppt-project",
        "pptx_path": "/workspace/tmp/ppt-project/result.pptx",
        "filename": "result.pptx",
    })
    assert value.status == "succeeded"
    assert sync.persist_calls == 1
    assert captured["collect_workspace"] is True
    assert captured["migrate_outputs"] is False
    assert captured["run_visual_review"] is False
    assert shell_tools._PYYAML_RUNTIME_FILENAME in captured["input_files"]
    assert "PYTHONPATH=/workspace/inputs/.platform-pyyaml.zip python3 -P" in captured["command"]
    assert "page count mismatch" in captured["command"]
    assert "--pptx-path /workspace/tmp/ppt-project/result.pptx" in captured["command"]
    assert "--require-photo-provenance" in captured["command"]
    assert "--min-images 3" in captured["command"]
    assert "--min-photo-sources 3" in captured["command"]
    assert "--trusted-photo-assets-json '[]'" in captured["command"]
    assert "zip -qFSr" not in captured["command"]
    assert [item["filename"] for item in value.artifacts] == ["result.pptx"]
    assert [
        item["filename"] for item in tool_meta["publish_ppt_artifact"]["files"]
    ] == ["result.pptx"]


@pytest.mark.asyncio
async def test_publish_without_photo_requirement_ignores_image_sink(monkeypatch):
    sync = _Sync()
    captured = {}

    async def fake_execute_in_sandbox(command, **kwargs):
        captured["command"] = command
        return _Result(
            review={"status": "passed"},
            workspace_changes=[{"path": "result.pptx", "data": b"pptx"}],
        )

    monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    publish = _by_name(
        build_shell_tools(
            user_id="u1", run_id="r1", execution_profile=PROFILE,
            image_sink=[{"url": "https://cdn.example/a.jpg"}],
        ),
        "publish_ppt_artifact",
    )
    await publish.execute({
        "project_dir": "/workspace/tmp/ppt-project",
        "pptx_path": "/workspace/tmp/ppt-project/result.pptx",
        "filename": "result.pptx",
    })
    assert "--require-images " not in captured["command"]
    assert "--require-photo-provenance " not in captured["command"]


@pytest.mark.asyncio
async def test_publish_rejects_missing_persisted_pptx(monkeypatch):
    sync = _Sync()

    async def fake_execute_in_sandbox(command, **kwargs):
        return _Result(
            review={"status": "passed"},
            workspace_changes=[{"path": "other.pptx", "data": b"pptx"}],
        )

    monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
    monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    publish = _by_name(
        build_shell_tools(user_id="u1", run_id="r1", execution_profile=PROFILE),
        "publish_ppt_artifact",
    )
    with pytest.raises(ToolSoftError, match="没有目标 PPTX"):
        await publish.execute({
            "project_dir": "/workspace/tmp/ppt-project",
            "pptx_path": "/workspace/tmp/ppt-project/result.pptx",
            "filename": "result.pptx",
        })


@pytest.mark.asyncio
async def test_publish_persists_when_review_is_warning_unknown_or_failed(monkeypatch):
    for status in ("warning", "unknown", "failed", None):
        sync = _Sync()

        async def fake_execute_in_sandbox(command, **kwargs):
            return _Result(
                review={"status": status} if status else None,
                workspace_changes=[{"path": "result.pptx", "data": b"pptx"}],
            )

        monkeypatch.setattr(shell_tools, "build_sync", lambda *args, **kwargs: sync)
        monkeypatch.setattr(shell_tools.sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
        publish = _by_name(
            build_shell_tools(user_id="u1", execution_profile=PROFILE),
            "publish_ppt_artifact",
        )
        result = await publish.execute({
            "pptx_path": "/workspace/tmp/ppt-project/result.pptx",
            "filename": "result.pptx",
        })
        assert result.status == "succeeded"
        assert sync.persist_calls == 1
        assert "未拦截交付" not in result.model_content
        assert "评分" not in result.model_content
