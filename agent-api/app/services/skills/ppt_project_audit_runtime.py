"""Deterministic source-level quality gates for staged PPTD projects.

This audit complements geometry lint and rendered visual review.  It checks two
failure modes that a contact-sheet review cannot prove reliably: whether a deck
declares an intentional typography system, and whether assets fetched for a
concrete presentation are actually referenced by a substantive image element.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import zipfile
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree

import yaml


_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS = {"a": _DRAWING_NS, "p": _PRESENTATION_NS}
_RICH_FONT_SIZE_RE = re.compile(
    r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*(?:px|pt)?",
    re.IGNORECASE,
)
ASSET_PROVENANCE_FILENAME = ".ppt-asset-provenance.json"
_PHOTOGRAPHIC_SOURCE_KINDS = {"search_result", "public_url", "user_upload"}
_PROVENANCE_FIELDS = (
    "filename", "sha256", "source_kind", "source_url", "source_page", "source_title",
)


def _normalized_provenance_entry(item: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(item, dict):
        return None, "photographic asset provenance contains a non-object entry"
    normalized = {
        field: str(item.get(field) or "").strip()
        for field in _PROVENANCE_FIELDS
    }
    filename = normalized["filename"]
    if not filename or pathlib.PurePosixPath(filename).name != filename:
        return None, "photographic asset provenance contains an unsafe filename"
    source_kind = normalized["source_kind"]
    if source_kind not in _PHOTOGRAPHIC_SOURCE_KINDS:
        return None, (
            f"{filename} has unsupported photographic source kind: "
            f"{source_kind or '<empty>'}"
        )
    sha256 = normalized["sha256"].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None, f"{filename} has an invalid photographic asset sha256"
    normalized["sha256"] = sha256
    return normalized, None


def _provenance_map(
    assets: Iterable[Any],
    *,
    label: str,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    mapped: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for item in assets:
        normalized, issue = _normalized_provenance_entry(item)
        if issue:
            issues.append(issue)
            continue
        assert normalized is not None
        filename = normalized["filename"]
        if filename in mapped:
            issues.append(f"{label} contains duplicate filename: {filename}")
            continue
        mapped[filename] = normalized
    return mapped, issues


def _source_domain(receipt: dict[str, str]) -> str:
    """Prefer the publishing page over a CDN URL when counting source diversity."""
    for field in ("source_page", "source_url"):
        raw = str(receipt.get(field) or "").strip()
        try:
            parsed = urlparse(raw)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        host = parsed.hostname.rstrip(".").lower()
        return host[4:] if host.startswith("www.") else host
    return ""


def _verified_photographic_assets(
    root: pathlib.Path,
    trusted_photo_assets: Iterable[dict[str, Any]] = (),
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Return byte-verified assets backed by platform-owned fetch receipts.

    The project provenance file is model-writable. It is useful for keeping the
    source project self-describing, but it is not authority: every entry must
    exactly match a receipt retained by ``fetch_ppt_asset`` outside the sandbox.
    """
    path = root / ASSET_PROVENANCE_FILENAME
    if not path.is_file():
        return {}, [
            "photographic asset provenance is missing; real photos must be staged with "
            "fetch_ppt_asset instead of being drawn or copied into media/ by bash"
        ]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {}, [f"photographic asset provenance is unreadable: {exc}"]
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        return {}, ["photographic asset provenance has no assets list"]

    project_assets, issues = _provenance_map(assets, label="project provenance")
    trusted_assets, trusted_issues = _provenance_map(
        trusted_photo_assets,
        label="platform fetch receipts",
    )
    issues.extend(trusted_issues)
    for filename in sorted(project_assets.keys() - trusted_assets.keys()):
        issues.append(
            f"project provenance is not backed by a platform fetch receipt: {filename}"
        )
    for filename in sorted(trusted_assets.keys() - project_assets.keys()):
        issues.append(
            f"platform fetch receipt is missing from project provenance: {filename}"
        )

    verified: dict[str, dict[str, str]] = {}
    for filename in sorted(project_assets.keys() & trusted_assets.keys()):
        project_entry = project_assets[filename]
        trusted_entry = trusted_assets[filename]
        if project_entry != trusted_entry:
            issues.append(
                f"project provenance does not match the platform fetch receipt: {filename}"
            )
            continue
        candidate = root / "media" / filename
        if not candidate.is_file():
            issues.append(f"provenance-backed photographic asset is missing: media/{filename}")
            continue
        actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual_sha != trusted_entry["sha256"]:
            issues.append(f"photographic asset bytes do not match provenance: media/{filename}")
            continue
        verified[filename] = trusted_entry
    return verified, issues


def _verified_user_photo_assets(
    root: pathlib.Path,
    names: Iterable[str],
    user_assets_root: pathlib.Path | None,
) -> tuple[set[str], list[str]]:
    """Verify copied user photos by matching them byte-for-byte to /workspace/files."""
    requested = {
        pathlib.PurePosixPath(str(name).replace("\\", "/")).name
        for name in names
        if str(name).strip()
    }
    if not requested:
        return set(), []
    if user_assets_root is None or not user_assets_root.is_dir():
        return set(), ["user photographic assets root is unavailable for byte verification"]
    verified: set[str] = set()
    issues: list[str] = []
    for filename in sorted(requested):
        original = user_assets_root / filename
        staged = root / "media" / filename
        if not original.is_file() or not staged.is_file():
            issues.append(
                f"user photographic asset must be copied unchanged to media/{filename}"
            )
            continue
        if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(staged.read_bytes()).digest():
            issues.append(f"user photographic asset bytes changed: media/{filename}")
            continue
        verified.add(filename)
    return verified, issues


def _positive_size(value: object) -> float | None:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    return size if 0 < size <= 1000 else None


def _source_typography_sizes(manifest: dict, root: pathlib.Path) -> list[float]:
    """Collect sizes that text in this project actually asks the writer to use.

    Unreferenced theme roles are deliberately excluded. Bare style names are
    invalid PPTD and are handled by the layout lint; accepting them here would
    recreate the false pass that caused the writer to fall back to 16pt while
    the audit assumed the theme had been applied.
    """
    styles = ((manifest.get("theme") or {}).get("textStyles") or {})
    sizes: list[float] = []

    def collect(holder: object, *, style_key: str) -> None:
        if not isinstance(holder, dict):
            return
        size = _positive_size(holder.get("fontSize"))
        style_ref = holder.get(style_key)
        if size is None and isinstance(style_ref, str) and style_ref.startswith("$"):
            style = styles.get(style_ref[1:])
            if isinstance(style, dict):
                size = _positive_size(style.get("fontSize"))
        if size is not None:
            sizes.append(size)
        text = holder.get("text")
        if isinstance(text, str):
            sizes.extend(
                float(match.group(1))
                for match in _RICH_FONT_SIZE_RE.finditer(text)
                if _positive_size(match.group(1)) is not None
            )

    for page_ref in manifest.get("pages") or []:
        page_path = root / str(page_ref)
        if not page_path.is_file():
            continue
        page = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        for element in page.get("elements") or []:
            if not isinstance(element, dict):
                continue
            if str(element.get("elementType") or "") == "text":
                collect(element.get("content"), style_key="style")
            elif str(element.get("elementType") or "") == "table":
                for row in element.get("rows") or []:
                    for cell in row or []:
                        collect(cell, style_key="textStyle")
    return sizes


def _pptx_typography_sizes(
    pptx_path: pathlib.Path,
) -> tuple[list[float], list[float], int]:
    """Return representative sizes, all explicit sizes, and text-owner count.

    Run sizes inherit from paragraph defaults when possible. Values that only
    exist in a slide master/layout are intentionally not guessed: low explicit
    coverage makes the gate abstain instead of rejecting a valid template deck.
    """
    representatives: list[float] = []
    explicit: list[float] = []
    text_owner_count = 0
    slide_name = re.compile(r"ppt/slides/slide[0-9]+\.xml$")
    with zipfile.ZipFile(pptx_path) as archive:
        for name in archive.namelist():
            if not slide_name.fullmatch(name):
                continue
            root = ElementTree.fromstring(archive.read(name))
            owners = root.findall(".//p:sp", _NS) + root.findall(".//a:tc", _NS)
            for owner in owners:
                if not any((node.text or "").strip() for node in owner.findall(".//a:t", _NS)):
                    continue
                text_owner_count += 1
                owner_sizes: list[float] = []
                for paragraph in owner.findall(".//a:p", _NS):
                    default = paragraph.find("./a:pPr/a:defRPr", _NS)
                    default_size = _positive_size(
                        int(default.get("sz")) / 100
                        if default is not None and default.get("sz") else None
                    )
                    runs = paragraph.findall("./a:r", _NS) + paragraph.findall("./a:fld", _NS)
                    for run in runs:
                        props = run.find("./a:rPr", _NS)
                        run_size = _positive_size(
                            int(props.get("sz")) / 100
                            if props is not None and props.get("sz") else None
                        )
                        effective = run_size if run_size is not None else default_size
                        if effective is not None:
                            owner_sizes.append(effective)
                            explicit.append(effective)
                    if (
                        not runs
                        and default_size is not None
                        and paragraph.find("./a:endParaRPr", _NS) is not None
                    ):
                        owner_sizes.append(default_size)
                        explicit.append(default_size)
                if owner_sizes:
                    representatives.append(
                        collections.Counter(owner_sizes).most_common(1)[0][0]
                    )
    return representatives, explicit, text_owner_count


def _significant_size_count(values: Iterable[float]) -> int:
    """Treat sub-2pt variants as one step, so 15/16/17 is not a hierarchy."""
    anchors: list[float] = []
    for value in sorted(set(round(float(item), 1) for item in values)):
        if not anchors or value - anchors[-1] >= 2.0:
            anchors.append(value)
    return len(anchors)


def _typography_export_issues(
    source_sizes: list[float],
    pptx_path: pathlib.Path,
) -> list[str]:
    # Small/simple decks do not provide enough evidence for a distribution
    # comparison. The 36pt floor also avoids policing document-like slides.
    if (
        len(source_sizes) < 6
        or max(source_sizes, default=0) < 36
        or _significant_size_count(source_sizes) < 3
    ):
        return []
    representatives, explicit, text_owner_count = _pptx_typography_sizes(pptx_path)
    explicit_coverage = len(representatives) / max(1, text_owner_count)
    if len(representatives) < 5 or not explicit or explicit_coverage < 0.60:
        return []

    source_counts = collections.Counter(round(size, 1) for size in source_sizes)
    actual_counts = collections.Counter(round(size, 1) for size in representatives)
    actual_mode, actual_mode_count = actual_counts.most_common(1)[0]
    actual_mode_ratio = actual_mode_count / len(representatives)
    source_mode_ratio = source_counts.get(actual_mode, 0) / len(source_sizes)
    distribution_collapsed = (
        actual_mode_ratio >= 0.85
        and actual_mode_ratio - source_mode_ratio >= 0.35
    )
    expected_max = max(source_sizes)
    actual_max = max(explicit)
    scale_collapsed = actual_max < expected_max * 0.70
    if not (distribution_collapsed or scale_collapsed):
        return []

    reasons: list[str] = []
    if distribution_collapsed:
        reasons.append(
            f"{actual_mode:g}pt occupies {actual_mode_ratio:.0%} of exported text "
            f"shapes but only {source_mode_ratio:.0%} of PPTD text"
        )
    if scale_collapsed:
        reasons.append(
            f"exported maximum is {actual_max:g}pt versus {expected_max:g}pt in PPTD"
        )
    return [
        "exported PPTX typography hierarchy collapsed: " + "; ".join(reasons)
        + ". Theme text styles or inline font sizes did not survive export"
    ]


def _font_names(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip().strip("'\"") for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_font_names(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_font_names(item))
        return out
    return []


def audit_project(
    root: pathlib.Path,
    *,
    pptx_path: pathlib.Path | None = None,
    require_images: bool = False,
    require_photo_provenance: bool = False,
    min_images: int = 0,
    min_photo_sources: int = 0,
    expected_assets: Iterable[str] = (),
    trusted_photo_assets: Iterable[dict[str, Any]] = (),
    user_photo_assets: Iterable[str] = (),
    user_assets_root: pathlib.Path | None = None,
    check_installed_fonts: bool = False,
) -> list[str]:
    manifests = list(root.glob("*.pptd"))
    if len(manifests) != 1:
        return [f"expected exactly one .pptd, got {len(manifests)}"]
    manifest = yaml.safe_load(manifests[0].read_text(encoding="utf-8")) or {}
    issues: list[str] = []

    if pptx_path is not None:
        if not pptx_path.is_file():
            issues.append(f"PPTX typography audit target does not exist: {pptx_path}")
        else:
            issues.extend(
                _typography_export_issues(
                    _source_typography_sizes(manifest, root), pptx_path,
                )
            )

    styles = ((manifest.get("theme") or {}).get("textStyles") or {})
    explicit_roles = {
        str(role): _font_names(style.get("fontFamily"))
        for role, style in styles.items()
        if isinstance(style, dict) and _font_names(style.get("fontFamily"))
    }
    if len(explicit_roles) < 2:
        issues.append(
            "typography system is implicit: declare fontFamily on at least two "
            "theme.textStyles roles (for example display/title and body)"
        )
    if check_installed_fonts and explicit_roles:
        try:
            completed = subprocess.run(
                ["fc-list", ":", "family"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            issues.append(f"font availability preflight failed: {exc}")
        else:
            if completed.returncode != 0:
                issues.append("font availability preflight failed: fc-list returned non-zero")
            else:
                installed = {
                    family.strip().casefold()
                    for line in completed.stdout.splitlines()
                    for family in line.split(",")
                    if family.strip()
                }
                missing_roles = sorted({
                    role
                    for role, families in explicit_roles.items()
                    if families and not any(family.casefold() in installed for family in families)
                })
                if missing_roles:
                    missing = sorted({
                        family
                        for role in missing_roles
                        for family in explicit_roles[role]
                    })
                    issues.append(
                        "declared fonts are unavailable in the export environment: "
                        + ", ".join(missing)
                        + "; choose exact families reported by `fc-list : family`"
                    )

    size = manifest.get("size") or [960, 540]
    try:
        slide_area = max(1.0, float(size[0]) * float(size[1]))
    except (TypeError, ValueError, IndexError):
        slide_area = 960.0 * 540.0

    expected = {
        pathlib.PurePosixPath(str(name).replace("\\", "/")).name
        for name in expected_assets
        if str(name).strip()
    }
    used_assets: set[str] = set()
    substantive_images = 0
    substantive_sources: set[str] = set()
    substantive_asset_names: set[str] = set()
    for page_ref in manifest.get("pages") or []:
        page_path = root / str(page_ref)
        if not page_path.is_file():
            continue
        page = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        for element in page.get("elements") or []:
            if not isinstance(element, dict) or str(element.get("elementType") or "") != "image":
                continue
            src = str(element.get("src") or "").strip()
            if not src:
                issues.append(f"{page_ref}:{element.get('elementId') or '<unnamed>'} image has no src")
                continue
            if src.startswith(("http://", "https://")):
                issues.append(
                    f"{page_ref}:{element.get('elementId') or '<unnamed>'} uses remote image "
                    f"source {src}; stage it with fetch_ppt_asset and reference "
                    "media/<filename> so offline export embeds the actual image"
                )
                continue
            basename = pathlib.PurePosixPath(src.split("?", 1)[0]).name
            if basename:
                used_assets.add(basename)
            if not src.startswith("data:"):
                candidate = (root / src).resolve()
                try:
                    candidate.relative_to(root.resolve())
                except ValueError:
                    issues.append(f"{page_ref}:{src} escapes the PPTD project")
                    continue
                if not candidate.is_file():
                    issues.append(f"{page_ref}:missing image dependency {src}")
                    continue
            bounds = element.get("bounds")
            try:
                area = max(0.0, float(bounds[2])) * max(0.0, float(bounds[3]))
            except (TypeError, ValueError, IndexError):
                area = 0.0
            # Tiny logos, icons and decorative texture chips do not prove that
            # searched photographic material entered the visual narrative.
            if area / slide_area >= 0.025:
                substantive_images += 1
                substantive_sources.add(src.split("?", 1)[0])
                if basename:
                    substantive_asset_names.add(basename)

    required_count = max(
        1 if (require_images or require_photo_provenance) else 0,
        int(min_images or 0),
    )
    if required_count and len(substantive_sources) < required_count:
        issues.append(
            f"this task requires at least {required_count} distinct substantive images "
            f"(>=2.5% of a slide), but the deck references {len(substantive_sources)} "
            f"distinct sources across {substantive_images} placements"
        )
    if require_photo_provenance:
        verified_receipts, provenance_issues = _verified_photographic_assets(
            root, trusted_photo_assets,
        )
        verified_user_assets, user_asset_issues = _verified_user_photo_assets(
            root, user_photo_assets, user_assets_root,
        )
        if verified_user_assets and not (root / ASSET_PROVENANCE_FILENAME).is_file():
            provenance_issues = [
                issue for issue in provenance_issues
                if not issue.startswith("photographic asset provenance is missing")
            ]
        issues.extend(provenance_issues)
        issues.extend(user_asset_issues)
        verified_asset_names = set(verified_receipts) | verified_user_assets
        verified_used = substantive_asset_names & verified_asset_names
        if len(verified_used) < required_count:
            issues.append(
                f"this task requires at least {required_count} distinct searched/official "
                "photographs with verified provenance, but the deck substantively uses "
                f"{len(verified_used)}: {', '.join(sorted(verified_used)) or '<none>'}"
            )
        required_sources = max(0, int(min_photo_sources or 0))
        if required_sources:
            used_source_domains = {
                domain
                for filename in verified_used
                if filename in verified_receipts
                for domain in [_source_domain(verified_receipts[filename])]
                if domain
            }
            if len(used_source_domains) < required_sources:
                issues.append(
                    f"this task requires at least {required_sources} distinct photographic "
                    "source domains, but the deck substantively uses verified photos from "
                    f"{len(used_source_domains)}: "
                    f"{', '.join(sorted(used_source_domains)) or '<none>'}"
                )
    unused_expected = sorted(expected - used_assets)
    if unused_expected:
        issues.append(
            "downloaded PPT assets are unused: every staged asset must be referenced by "
            "an elementType: image src; missing: " + ", ".join(unused_expected)
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir", type=pathlib.Path)
    parser.add_argument("--pptx-path", type=pathlib.Path)
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--require-photo-provenance", action="store_true")
    parser.add_argument("--min-images", type=int, default=0)
    parser.add_argument("--min-photo-sources", type=int, default=0)
    parser.add_argument("--check-installed-fonts", action="store_true")
    parser.add_argument("--expected-assets-json", default="[]")
    parser.add_argument("--trusted-photo-assets-json", default="[]")
    parser.add_argument("--user-photo-assets-json", default="[]")
    parser.add_argument("--user-assets-root", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        expected = json.loads(args.expected_assets_json)
        if not isinstance(expected, list):
            raise ValueError("expected assets must be a JSON list")
        trusted_photo_assets = json.loads(args.trusted_photo_assets_json)
        if not isinstance(trusted_photo_assets, list):
            raise ValueError("trusted photo assets must be a JSON list")
        user_photo_assets = json.loads(args.user_photo_assets_json)
        if not isinstance(user_photo_assets, list):
            raise ValueError("user photo assets must be a JSON list")
        issues = audit_project(
            args.project_dir,
            pptx_path=args.pptx_path,
            require_images=bool(args.require_images),
            require_photo_provenance=bool(args.require_photo_provenance),
            min_images=max(0, int(args.min_images or 0)),
            min_photo_sources=max(0, int(args.min_photo_sources or 0)),
            expected_assets=expected,
            trusted_photo_assets=trusted_photo_assets,
            user_photo_assets=user_photo_assets,
            user_assets_root=args.user_assets_root,
            check_installed_fonts=bool(args.check_installed_fonts),
        )
    except Exception as exc:  # malformed YAML/dependency problems are infra failures
        print(f"PPTD project audit infrastructure failure: {exc}", file=sys.stderr)
        return 2
    if issues:
        print("PPTD project audit failed:\n- " + "\n- ".join(issues[:30]), file=sys.stderr)
        return 1
    print("PPTD project audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
