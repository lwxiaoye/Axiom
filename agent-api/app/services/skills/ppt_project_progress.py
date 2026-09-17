"""Objective authoring progress from a captured PPTD tree, never delivery proof.

This reads the checkpoint already collected for recovery; it does not run another
executor or trust command stdout. Missing/invalid snapshots produce no completion
evidence and must not prevent the underlying tool or publisher from proceeding.
"""

from __future__ import annotations

import io
import math
import re
import tarfile
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

import yaml

from .pptd_layout_lint_runtime import lint_project_data


_MAX_FILES = 10_000
_MAX_BYTES = 128 * 1024 * 1024
_MAX_TEXT = 1024 * 1024
_MAX_PAGES = 256
_SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml")
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix() if path.as_posix() != "." else ""


def _yaml_object(data: bytes) -> dict:
    if not data or len(data) > _MAX_TEXT:
        return {}
    try:
        value = yaml.safe_load(data)
        return value if isinstance(value, dict) else {}
    except (ValueError, yaml.YAMLError, RecursionError):
        return {}


def _image_paths(page: dict) -> set[str] | None:
    """Follow groups too; cyclic/oversized YAML is not evidence of a ready page."""
    queue: list[Any] = [page]
    seen: set[int] = set()
    paths: set[str] = set()
    while queue:
        item = queue.pop()
        if not isinstance(item, (dict, list)) or id(item) in seen:
            continue
        seen.add(id(item))
        if len(seen) > _MAX_FILES:
            return None
        if isinstance(item, list):
            queue.extend(item)
            continue
        if item.get("elementType") == "image":
            path = _relative_path(item.get("src"))
            if not path:
                return None
            paths.add(path)
        queue.extend(item.values())
    return paths


def _valid_pptx(data: bytes, expected_pages: int) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = zf.infolist()
            if len(entries) > _MAX_FILES or sum(item.file_size for item in entries) > _MAX_BYTES:
                return False
            names = zf.namelist()
            if len(set(names)) != len(names):
                return False
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                return False
            slides = [name for name in names if _SLIDE_RE.fullmatch(name)]
            if len(slides) != expected_pages or zf.testzip() is not None:
                return False
            if ElementTree.fromstring(zf.read("ppt/presentation.xml")).tag != f"{{{_PRESENTATION_NS}}}presentation":
                return False
            return all(ElementTree.fromstring(zf.read(name)).tag == f"{{{_PRESENTATION_NS}}}sld" for name in slides)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, ElementTree.ParseError):
        return False


def ppt_project_progress(blob: bytes | None) -> dict[str, Any]:
    """Return a typed receipt without file_id/id (scratch is not My Files)."""
    receipt: dict[str, Any] = {
        "kind": "artifact_progress",
        "artifact_type": "pptx",
        "checked": False,
        "stages": [],
        "expected_pages": 0,
        "written_pages": 0,
    }
    if not blob:
        return receipt
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            files: dict[str, tarfile.TarInfo] = {}
            contents: dict[str, bytes] = {}
            total = 0
            for index, member in enumerate(tf):
                total += member.size
                if total > _MAX_BYTES or index >= _MAX_FILES:
                    return receipt
                path = _relative_path(member.name)
                if member.isfile() and path:
                    if path in files:
                        return receipt
                    files[path] = member
                    # Read selected members while streaming forward. Repeated
                    # extractfile seeks would re-inflate media for every page.
                    limit = _MAX_BYTES if path.endswith(".pptx") else _MAX_TEXT
                    needed = path.endswith((".pptd", ".page", ".pptx")) or PurePosixPath(path).name == "DESIGN.md"
                    if needed and 0 < member.size <= limit:
                        stream = tf.extractfile(member)
                        if stream is not None:
                            contents[path] = stream.read(limit + 1)

            def read(path: str, limit: int = _MAX_BYTES) -> bytes:
                member = files.get(path)
                if member is None or member.size <= 0 or member.size > limit:
                    return b""
                return contents.get(path, b"")

            receipt["checked"] = True
            manifests = [path for path in files if path.endswith(".pptd")]
            if len(manifests) != 1:
                return receipt
            manifest_path = manifests[0]
            root = PurePosixPath(manifest_path).parent
            manifest = _yaml_object(read(manifest_path, _MAX_TEXT))
            if manifest.get("version") != "v2":
                return receipt
            size = manifest.get("size")
            if (
                not isinstance(size, list)
                or len(size) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in size)
            ):
                return receipt
            raw_pages = manifest.get("pages")
            if not isinstance(raw_pages, list) or not 0 < len(raw_pages) <= _MAX_PAGES:
                return receipt
            pages = [_relative_path(value) for value in raw_pages]
            if not all(pages) or len(set(pages)) != len(pages):
                return receipt
            receipt["expected_pages"] = len(pages)
            source_paths = {manifest_path}
            design = (root / "DESIGN.md").as_posix()
            if read(design, _MAX_TEXT).strip():
                receipt["stages"].append("design")
                source_paths.add(design)
            page_data: dict[str, dict] = {}
            for rel in pages:
                path = (root / rel).as_posix()
                page = _yaml_object(read(path, _MAX_TEXT))
                elements = page.get("elements")
                if not isinstance(elements, list) or not elements or not all(isinstance(item, dict) for item in elements):
                    continue
                images = _image_paths(page)
                if images is None:
                    continue
                image_paths = {(root / image).as_posix() for image in images}
                if any(name not in files or files[name].size <= 0 for name in image_paths):
                    continue
                receipt["written_pages"] += 1
                page_data[rel] = page
                source_paths.update([path, *image_paths])
            if receipt["written_pages"] != len(pages):
                return receipt
            receipt["stages"].append("source")
            newest_source = max(files[path].mtime for path in source_paths)
            for path, member in files.items():
                if not path.endswith(".pptx") or not PurePosixPath(path).is_relative_to(root):
                    continue
                if member.mtime < newest_source:
                    continue
                if _valid_pptx(read(path), len(pages)):
                    receipt["stages"].append("export")
                    issues = lint_project_data(manifest, page_data.get)
                    if not issues:
                        receipt["stages"].append("validation")
                    else:
                        receipt["validation_issues"] = issues[:8]
                    break
            return receipt
    except (OSError, ValueError, TypeError, AttributeError, OverflowError, EOFError, tarfile.TarError, yaml.YAMLError, RecursionError):
        return {**receipt, "checked": False, "stages": []}
