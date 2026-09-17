"""Regression tests for PPTD-to-PPTX typography consistency."""
from __future__ import annotations

import zipfile

from app.services.skills.ppt_project_audit_runtime import audit_project


def _write_project(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (tmp_path / "deck.pptd").write_text(
        """version: v2
size: [960, 540]
theme:
  textStyles:
    display: {fontFamily: Outfit, fontSize: 52}
    title: {fontFamily: Outfit, fontSize: 32}
    body: {fontFamily: Inter, fontSize: 16}
pages: [pages/01.page]
""",
        encoding="utf-8",
    )
    roles = ["display", "display", "title", "title", "title"] + ["body"] * 5
    elements = [
        (
            f"  - elementId: text-{index}\n"
            "    elementType: text\n"
            f"    bounds: [40, {30 + index * 45}, 700, 40]\n"
            f"    content: {{style: '${role}', text: 'line {index}'}}\n"
        )
        for index, role in enumerate(roles, start=1)
    ]
    (pages / "01.page").write_text("elements:\n" + "".join(elements), encoding="utf-8")


def _write_pptx(path, sizes):
    shapes = []
    for index, size in enumerate(sizes, start=1):
        size_attr = "" if size is None else f' sz="{int(float(size) * 100)}"'
        shapes.append(
            "<p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>"
            f"<a:p><a:r><a:rPr{size_attr}/><a:t>line {index}</a:t></a:r></a:p>"
            "</p:txBody></p:sp>"
        )
    slide = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree>" + "".join(shapes) + "</p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)


def test_audit_rejects_uniform_16pt_export_of_multilevel_pptd(tmp_path):
    _write_project(tmp_path)
    pptx = tmp_path / "collapsed.pptx"
    _write_pptx(pptx, [16] * 10)

    issues = audit_project(tmp_path, pptx_path=pptx)

    assert any("typography hierarchy collapsed" in issue for issue in issues)
    assert any("16pt occupies 100%" in issue for issue in issues)


def test_audit_rejects_dominant_fallback_even_when_one_large_size_survives(tmp_path):
    _write_project(tmp_path)
    pptx = tmp_path / "mostly-collapsed.pptx"
    _write_pptx(pptx, [72] + [16] * 9)

    issues = audit_project(tmp_path, pptx_path=pptx)

    assert any("typography hierarchy collapsed" in issue for issue in issues)
    assert not any("exported maximum" in issue for issue in issues)


def test_audit_accepts_export_with_matching_typography_distribution(tmp_path):
    _write_project(tmp_path)
    pptx = tmp_path / "healthy.pptx"
    _write_pptx(pptx, [52, 52, 32, 32, 32, 16, 16, 16, 16, 16])

    assert audit_project(tmp_path, pptx_path=pptx) == []


def test_audit_abstains_when_pptx_sizes_are_only_inherited_from_master(tmp_path):
    _write_project(tmp_path)
    pptx = tmp_path / "template-driven.pptx"
    _write_pptx(pptx, [None] * 10)

    assert audit_project(tmp_path, pptx_path=pptx) == []


def test_audit_abstains_when_most_pptx_sizes_require_master_inheritance(tmp_path):
    _write_project(tmp_path)
    pptx = tmp_path / "mostly-template-driven.pptx"
    _write_pptx(pptx, [16] * 5 + [None] * 5)

    assert audit_project(tmp_path, pptx_path=pptx) == []


def test_audit_reports_missing_explicit_pptx_target(tmp_path):
    _write_project(tmp_path)

    issues = audit_project(tmp_path, pptx_path=tmp_path / "missing.pptx")

    assert any("audit target does not exist" in issue for issue in issues)
