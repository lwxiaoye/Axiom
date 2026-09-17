#!/usr/bin/env python3
"""Thin wrapper: resolve skill dir and forward to scripts/export_pptx.py.

Usage (in sandbox):
  python3 /workspace/skills/<slug>/scripts/run_export.py \\
    /workspace/tmp/ppt-xxx/deck.pptd \\
    --output /workspace/files/out.pptx --force
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


_FONT_ALIASES = {
    "Noto Sans SC": "Noto Sans CJK SC",
    "Noto Serif SC": "Noto Serif CJK SC",
    "Microsoft YaHei": "Noto Sans CJK SC",
    "Microsoft YaHei UI": "Noto Sans CJK SC",
    "微软雅黑": "Noto Sans CJK SC",
    "PingFang SC": "Noto Sans CJK SC",
    "苹方": "Noto Sans CJK SC",
    "苹方-简": "Noto Sans CJK SC",
}


def _alias_font_value(value: object) -> object:
    if isinstance(value, str):
        return _FONT_ALIASES.get(value, value)
    if isinstance(value, dict):
        return {key: _alias_font_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_alias_font_value(item) for item in value]
    return value


def _bounds(value: object) -> object:
    if isinstance(value, dict):
        return [
            value.get("x", 0),
            value.get("y", 0),
            value.get("width", value.get("w", 0)),
            value.get("height", value.get("h", 0)),
        ]
    return value


def _normalize_element(element: Any) -> bool:
    if not isinstance(element, dict):
        return False
    changed = False
    normalized_bounds = _bounds(element.get("bounds"))
    if normalized_bounds != element.get("bounds"):
        element["bounds"] = normalized_bounds
        changed = True

    kind = str(element.get("elementType") or "").strip().lower()
    if kind in {"rect", "rectangle"}:
        element["elementType"] = "shape"
        element.setdefault("shapeName", "rect")
        kind = "shape"
        changed = True

    if kind == "image" and isinstance(element.get("fit"), str):
        element["fit"] = {"mode": element["fit"]}
        changed = True

    bounds = element.get("bounds")
    if kind == "line" and isinstance(bounds, list) and len(bounds) == 4:
        repaired = list(bounds)
        if float(repaired[2] or 0) <= 0:
            repaired[2] = 1
        if float(repaired[3] or 0) <= 0:
            repaired[3] = 1
        if repaired != bounds:
            element["bounds"] = repaired
            changed = True

    if kind == "text" and isinstance(element.get("content"), dict):
        content = element["content"]
        if not content.get("style") and isinstance(element.get("style"), str):
            content["style"] = element["style"]
            changed = True
    return changed


def _normalize_project(input_path: Path) -> bool:
    """Canonicalize common model shorthand before export and publish lint.

    The exporter used to repair some fields only in memory, so the PPTX could
    export while the editable source still failed the publisher. Persisting the
    small, deterministic repairs keeps both artifacts on the same v2 contract.
    """
    path = input_path.resolve()
    if path.is_dir():
        manifests = sorted(path.glob("*.pptd"))
        if len(manifests) != 1:
            return False
        manifest_path = manifests[0]
    elif path.suffix.lower() == ".pptd" and path.is_file():
        manifest_path = path
    else:
        return False

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    manifest_changed = False
    size = manifest.get("size")
    if isinstance(size, dict):
        manifest["size"] = [
            size.get("width", size.get("w")),
            size.get("height", size.get("h")),
        ]
        manifest_changed = True
        size = manifest["size"]
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in size)
    ):
        raise ValueError(
            "PPTD manifest requires an explicit size: [width, height] with positive numbers. "
            "Set it to the canvas used in DESIGN.md and page bounds, then re-export; "
            "an omitted size can silently crop pages to the exporter's default canvas."
        )

    styles = ((manifest.get("theme") or {}).get("textStyles") or {})
    if isinstance(styles, dict):
        for style in styles.values():
            if not isinstance(style, dict):
                continue
            family = style.get("fontFamily")
            aliased = _alias_font_value(family)
            if aliased != family:
                style["fontFamily"] = aliased
                manifest_changed = True

    if manifest_changed:
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    page_changed = False
    for rel in manifest.get("pages") or []:
        page_path = manifest_path.parent / str(rel)
        if not page_path.is_file():
            continue
        page = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        changed = False
        background = page.get("background")
        if isinstance(background, dict) and background.get("color") and not background.get("type"):
            page["background"] = {"type": "solid", "color": background["color"]}
            changed = True
        for element in page.get("elements") or []:
            changed = _normalize_element(element) or changed
        if changed:
            page_path.write_text(
                yaml.safe_dump(page, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            page_changed = True
    return manifest_changed or page_changed


def main() -> int:
    skill_dir = Path(__file__).resolve().parent.parent
    export_py = skill_dir / "scripts" / "export_pptx.py"
    wasm_candidates = (
        skill_dir / "scripts" / "local-export" / "pptd_wasm_bg.wasm",
        Path("/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm"),
    )
    if not export_py.is_file():
        print(f"[ppt-studio] missing {export_py}", file=sys.stderr)
        return 2
    if not any(path.is_file() for path in wasm_candidates):
        print(
            "[ppt-studio] missing wasm in skill package and /opt/open-kimi-ppt. "
            "Do not find / or copy a random .wasm; stop and report that export cannot run.",
            file=sys.stderr,
        )
        return 3
    # Node preflight
    try:
        out = subprocess.check_output(["node", "--version"], text=True, stderr=subprocess.STDOUT)
        major = int(out.strip().lstrip("v").split(".")[0])
        if major < 18:
            print(f"[ppt-studio] Node 18+ required, got {out.strip()}", file=sys.stderr)
            return 4
    except FileNotFoundError:
        print("[ppt-studio] node not found in PATH (need Node 18+ in sandbox image)", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"[ppt-studio] node check failed: {exc}", file=sys.stderr)
        return 4

    if len(sys.argv) < 2:
        print("[ppt-studio] missing PPTD project path", file=sys.stderr)
        return 5
    try:
        if _normalize_project(Path(sys.argv[1])):
            print("[ppt-studio] normalized common PPTD v2 shorthand", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[ppt-studio] PPTD normalization failed: {exc}", file=sys.stderr)
        return 5

    env = os.environ.copy()
    cmd = [sys.executable, str(export_py), *sys.argv[1:]]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
