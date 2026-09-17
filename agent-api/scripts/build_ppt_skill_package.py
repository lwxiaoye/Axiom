"""Build the pinned upstream-first PPT Skill package for Skill Square."""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app/services/skills/builtin/ppt-studio"
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
ROOT_FILES = {"SKILL.md", "skill.json", "LICENSE", "ATTRIBUTION.md"}
SCRIPT_FILES = {
    "scripts/export_images.py",
    "scripts/export_pptx.py",
    "scripts/run_export.py",
    "scripts/local-export/README.md",
    "scripts/local-export/export-pptd.mjs",
    "scripts/local-export/normalize-theme-styles.mjs",
    "scripts/local-export/normalize-rich-text.mjs",
    "scripts/local-export/load_pptd.py",
}
MAX_FILES = 200
MAX_BYTES = 20 * 1024 * 1024
ARCHIVE_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _is_packaged(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return (
        rel in ROOT_FILES
        or rel in SCRIPT_FILES
        or rel.startswith("reference/")
        or rel.startswith("recipes/")
        or rel.startswith("examples/cinematic/")
    )


def package_files() -> list[Path]:
    files = [
        path for path in ROOT.rglob("*")
        if path.is_file()
        and _is_packaged(path)
        and not EXCLUDED_PARTS.intersection(path.relative_to(ROOT).parts)
        and path.suffix not in {".pyc", ".pyo"}
    ]
    files.sort(key=lambda path: path.relative_to(ROOT).as_posix())
    if not (ROOT / "SKILL.md").is_file():
        raise RuntimeError(f"missing {ROOT / 'SKILL.md'}")
    missing = sorted(
        rel for rel in ROOT_FILES | SCRIPT_FILES
        if not (ROOT / rel).is_file()
    )
    if missing:
        raise RuntimeError(f"missing required package files: {', '.join(missing)}")
    if len(files) > MAX_FILES:
        raise RuntimeError(f"package has {len(files)} files; bridge limit is {MAX_FILES}")
    size = sum(path.stat().st_size for path in files)
    if size > MAX_BYTES:
        raise RuntimeError(f"package has {size} bytes; bridge limit is {MAX_BYTES}")
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), ARCHIVE_TIMESTAMP)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    print(f"{output} files={len(files)} bytes={output.stat().st_size}")


if __name__ == "__main__":
    main()
