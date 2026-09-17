from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml
import pytest


RUN_EXPORT = (
    Path(__file__).parents[1]
    / "app/services/skills/builtin/ppt-studio/scripts/run_export.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("ppt_run_export", RUN_EXPORT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_project_repairs_common_model_shorthand(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        """version: v2
size: {width: 960, height: 540}
theme:
  textStyles:
    title: {fontFamily: Noto Sans SC, fontSize: 48}
    body: {fontFamily: Noto Serif SC, fontSize: 18}
pages: [pages/01.page]
""",
        encoding="utf-8",
    )
    (pages / "01.page").write_text(
        """background: {color: '#F2EFE8'}
elements:
  - elementId: title
    elementType: text
    bounds: {x: 60, y: 80, width: 500, height: 80}
    style: '$title'
    content: {text: 节能减排}
  - elementId: panel
    elementType: rect
    bounds: {x: 60, y: 200, width: 300, height: 120}
    fill: {color: '#2E7D5B'}
  - elementId: photo
    elementType: image
    bounds: [400, 160, 480, 300]
    fit: cover
    src: media/energy.jpg
  - elementId: rule
    elementType: line
    bounds: [60, 350, 160, 0]
""",
        encoding="utf-8",
    )

    changed = _load_module()._normalize_project(tmp_path)

    assert changed is True
    manifest = yaml.safe_load((tmp_path / "deck.pptd").read_text(encoding="utf-8"))
    assert manifest["size"] == [960, 540]
    assert manifest["theme"]["textStyles"]["title"]["fontFamily"] == "Noto Sans CJK SC"
    assert manifest["theme"]["textStyles"]["body"]["fontFamily"] == "Noto Serif CJK SC"
    page = yaml.safe_load((pages / "01.page").read_text(encoding="utf-8"))
    assert page["background"] == {"type": "solid", "color": "#F2EFE8"}
    assert page["elements"][0]["bounds"] == [60, 80, 500, 80]
    assert page["elements"][0]["content"]["style"] == "$title"
    assert page["elements"][1]["elementType"] == "shape"
    assert page["elements"][1]["shapeName"] == "rect"
    assert page["elements"][2]["fit"] == {"mode": "cover"}
    assert page["elements"][3]["bounds"] == [60, 350, 160, 1]


def test_normalize_project_aliases_microsoft_yahei_stack(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        """version: v2
size: [960, 540]
theme:
  textStyles:
    title: {fontFamily: {latin: Helvetica Neue, ea: Microsoft YaHei}, fontSize: 40}
    body: {fontFamily: 微软雅黑, fontSize: 16}
pages: [pages/01.page]
""",
        encoding="utf-8",
    )
    (pages / "01.page").write_text("elements: []\n", encoding="utf-8")
    assert _load_module()._normalize_project(tmp_path) is True
    manifest = yaml.safe_load((tmp_path / "deck.pptd").read_text(encoding="utf-8"))
    assert manifest["theme"]["textStyles"]["title"]["fontFamily"]["ea"] == "Noto Sans CJK SC"
    assert manifest["theme"]["textStyles"]["body"]["fontFamily"] == "Noto Sans CJK SC"


def test_normalize_project_is_idempotent(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        "version: v2\nsize: [960, 540]\npages: [pages/01.page]\n",
        encoding="utf-8",
    )
    (pages / "01.page").write_text(
        "background: {type: solid, color: '#fff'}\nelements: []\n",
        encoding="utf-8",
    )
    module = _load_module()
    assert module._normalize_project(tmp_path) is False


@pytest.mark.parametrize("size", [None, [0, 720], [1280], [1280, -1], ["wide", 720], {"width": 1280}, [True, 720], [float("nan"), 720]])
def test_export_rejects_missing_or_invalid_canvas_instead_of_silently_cropping(tmp_path, size):
    manifest = {"version": "v2", "pages": ["pages/01.page"]}
    if size is not None:
        manifest["size"] = size
    source = yaml.safe_dump(manifest)
    (tmp_path / "deck.pptd").write_text(source)
    with pytest.raises(ValueError, match="size"):
        _load_module()._normalize_project(tmp_path)
    assert (tmp_path / "deck.pptd").read_text() == source


def test_export_preserves_explicit_large_canvas(tmp_path):
    source = "version: v2\nsize: [1280, 720]\npages: []\n"
    (tmp_path / "deck.pptd").write_text(source)
    assert _load_module()._normalize_project(tmp_path) is False
    assert (tmp_path / "deck.pptd").read_text() == source
