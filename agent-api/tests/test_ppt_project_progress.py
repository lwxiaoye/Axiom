import io
import tarfile
import zipfile

import pytest

from app.services.skills.ppt_project_progress import ppt_project_progress


def _pptx(slides=2):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
        for index in range(1, slides + 1):
            zf.writestr(f"ppt/slides/slide{index}.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    return buffer.getvalue()


def _tree(files, *, mtimes=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in files.items():
            payload = data.encode() if isinstance(data, str) else data
            entry = tarfile.TarInfo("./" + name)
            entry.size = len(payload)
            entry.mtime = (mtimes or {}).get(name, 20 if name.endswith(".pptx") else 10)
            tf.addfile(entry, io.BytesIO(payload))
    return buffer.getvalue()


def _files():
    return {
        "DESIGN.md": "Thunder colors, one topic per slide.",
        "deck.pptd": "version: v2\nsize: [1280, 720]\npages: [pages/01.page, pages/02.page]\n",
        "pages/01.page": "elements: [{elementId: title, elementType: text, bounds: [40, 40, 600, 100], content: {text: Title}}]\n",
        "pages/02.page": "elements: [{elementId: photo, elementType: image, bounds: [40, 40, 300, 200], src: media/game.jpg}]\n",
        "media/game.jpg": b"test-image",
    }


def test_progress_is_computed_from_actual_manifest_pages_and_export():
    files = _files()
    progress = ppt_project_progress(_tree({"media/game.jpg": files["media/game.jpg"]}))
    assert progress["stages"] == []
    progress = ppt_project_progress(_tree({key: value for key, value in files.items() if not key.startswith("pages/")}))
    assert progress["stages"] == ["design"]
    assert progress["expected_pages"] == 2
    assert progress["written_pages"] == 0
    progress = ppt_project_progress(_tree({key: value for key, value in files.items() if key != "pages/02.page"}))
    assert progress["stages"] == ["design"]
    assert progress["written_pages"] == 1
    progress = ppt_project_progress(_tree(files))
    assert progress["stages"] == ["design", "source"]
    assert progress["written_pages"] == 2
    files["deck.pptx"] = _pptx()
    progress = ppt_project_progress(_tree(files))
    assert progress["stages"] == ["design", "source", "export", "validation"]
    assert progress["artifact_type"] == "pptx"
    assert "file_id" not in progress and "id" not in progress


@pytest.mark.parametrize("problem", ["missing_image", "bad_yaml", "empty_page", "unsafe_page"])
def test_incomplete_source_is_not_reported_complete(problem):
    files = _files()
    if problem == "missing_image":
        del files["media/game.jpg"]
    elif problem == "bad_yaml":
        files["pages/02.page"] = "elements: [\n"
    elif problem == "empty_page":
        files["pages/02.page"] = "elements: []\n"
    else:
        files["deck.pptd"] = "pages: [../other.page]\n"
    assert "source" not in ppt_project_progress(_tree(files))["stages"]


@pytest.mark.parametrize("pptx", [b"not a zip", _pptx(slides=1), _pptx(slides=0)])
def test_pptx_name_alone_is_not_export_evidence(pptx):
    files = {**_files(), "deck.pptx": pptx}
    assert "export" not in ppt_project_progress(_tree(files))["stages"]


def test_pptx_older_than_source_is_not_evidence_for_current_export():
    files = {**_files(), "deck.pptx": _pptx()}
    progress = ppt_project_progress(_tree(files, mtimes={"pages/02.page": 30}))
    assert progress["stages"] == ["design", "source"]


def test_export_can_live_in_a_subdirectory_of_the_same_project():
    files = {**_files(), "exports/deck.pptx": _pptx()}
    assert "export" in ppt_project_progress(_tree(files))["stages"]


def test_snapshot_unavailable_or_unreadable_is_conservative():
    for blob in (None, b"", b"not a tar"):
        progress = ppt_project_progress(blob)
        assert progress["checked"] is False
        assert progress["stages"] == []


def test_multiple_manifests_do_not_mix_two_projects():
    files = {**_files(), "other.pptd": "pages: [pages/01.page]\n"}
    assert ppt_project_progress(_tree(files))["stages"] == []


def test_missing_canvas_cannot_prove_design_source_or_export_complete():
    files = {**_files(), "deck.pptx": _pptx()}
    files["deck.pptd"] = "version: v2\npages: [pages/01.page, pages/02.page]\n"
    assert ppt_project_progress(_tree(files))["stages"] == []


def test_valid_export_zip_does_not_prove_layout_validation_passed():
    files = {**_files(), "deck.pptx": _pptx()}
    files["pages/01.page"] = files["pages/01.page"].replace("[40, 40, 600, 100]", "[1250, 40, 600, 100]")
    progress = ppt_project_progress(_tree(files))
    assert progress["stages"] == ["design", "source", "export"]
    assert any("outside slide bounds" in issue for issue in progress["validation_issues"])


def test_malformed_theme_is_not_completion_evidence_and_does_not_raise():
    files = {**_files(), "deck.pptx": _pptx()}
    files["deck.pptd"] += "theme: invalid\n"
    progress = ppt_project_progress(_tree(files))
    assert progress["checked"] is False
    assert progress["stages"] == []
