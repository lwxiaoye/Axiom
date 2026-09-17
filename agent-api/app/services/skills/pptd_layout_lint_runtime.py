"""Deterministic PPTD v2 layout checks executed inside the artifact sandbox.

This module intentionally depends only on Python's standard library plus PyYAML,
which is already part of the PPT sandbox.  The server injects this file's source
into ``python -c`` before publishing so the check runs against the exact staged
project, without relying on the selected Skill package to police itself.

The estimates are deliberately conservative.  They block only geometry defects
that are strong enough to reproduce across renderers; visual taste stays with the
independent rendered-image review.
"""
from __future__ import annotations

import html
import math
import pathlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterable

import yaml


SUPPORTED_TAGS = {
    "p", "span", "strong", "em", "u", "s", "sup", "sub", "a",
    "ul", "ol", "li", "br",
}
_TAG_AT = re.compile(
    r"</?(?:p|span|strong|em|u|s|sup|sub|a|ul|ol|li|br)\b[^>]*>",
    re.IGNORECASE,
)
_STYLE_PART = re.compile(r"\s*([^:;]+)\s*:\s*([^;]+)\s*")
_PX = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*px\s*$", re.IGNORECASE)


def _number(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _style_map(raw: object) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        match = _STYLE_PART.fullmatch(part)
        if match:
            out[match.group(1).strip().lower()] = match.group(2).strip()
    return out


def _px(value: object, default: float) -> float:
    match = _PX.match(str(value or ""))
    return float(match.group(1)) if match else default


def _unsupported_lt_positions(text: str) -> list[int]:
    """Return raw ``<`` positions that are not valid PPTD rich-text tags.

    The official WASM writer treats raw ``<`` as markup and may silently drop
    text such as ``600 -> <100`` during export.  The local exporter normalizes
    the documented plain-text shorthand into rich text, but staged sources must
    still express literal comparison signs explicitly as ``&lt;``.
    """
    positions = []
    cursor = 0
    while True:
        index = text.find("<", cursor)
        if index < 0:
            return positions
        match = _TAG_AT.match(text, index)
        if match is None:
            positions.append(index)
            cursor = index + 1
        else:
            cursor = match.end()


@dataclass
class Run:
    text: str
    font_size: float


@dataclass
class LogicalLine:
    runs: list[Run] = field(default_factory=list)
    align: str | None = None
    line_height: float | None = None
    margin_top: float = 0.0

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


class _RichTextParser(HTMLParser):
    def __init__(self, base_font_size: float) -> None:
        super().__init__(convert_charrefs=True)
        self.base_font_size = base_font_size
        self.lines = [LogicalLine()]
        self._font_stack = [base_font_size]
        self._list_depth = 0

    def _new_line(self) -> None:
        if self.lines[-1].runs or self.lines[-1].align is not None:
            self.lines.append(LogicalLine())

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        attr_map = {str(key).lower(): value for key, value in attrs}
        styles = _style_map(attr_map.get("style"))
        if tag in {"p", "li"}:
            self._new_line()
            line = self.lines[-1]
            line.align = styles.get("text-align")
            raw_height = styles.get("line-height")
            if raw_height:
                line.line_height = (
                    _px(raw_height, -1.0)
                    if str(raw_height).strip().lower().endswith("px")
                    else _number(raw_height, 1.0) * self._font_stack[-1]
                )
            line.margin_top = max(0.0, _px(styles.get("margin-top"), 0.0))
            if tag == "li":
                line.runs.append(Run("\u2022 ", self._font_stack[-1]))
        elif tag == "br":
            self.lines.append(LogicalLine(align=self.lines[-1].align))
            return
        if tag == "span":
            self._font_stack.append(max(1.0, _px(styles.get("font-size"), self._font_stack[-1])))
        else:
            self._font_stack.append(self._font_stack[-1])
        if tag in {"ul", "ol"}:
            self._list_depth += 1

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() == "br":
            self.lines.append(LogicalLine(align=self.lines[-1].align))
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "li"}:
            self._new_line()
        if tag in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
        if len(self._font_stack) > 1:
            self._font_stack.pop()

    def handle_data(self, data: str) -> None:
        # Indentation/newlines between block tags are source formatting, not
        # extra visible paragraphs.  Non-whitespace newlines remain explicit.
        if not data.strip():
            return
        parts = data.split("\n")
        for index, part in enumerate(parts):
            if part:
                self.lines[-1].runs.append(Run(part, self._font_stack[-1]))
            if index < len(parts) - 1:
                self.lines.append(LogicalLine(align=self.lines[-1].align))


def _visible_lines(text: str, font_size: float) -> list[LogicalLine]:
    # YAML ``text: |`` scalars carry one terminal newline by default.  PPT
    # renderers treat that terminator as serialization whitespace rather than
    # an extra visible paragraph; counting it doubled one-line title heights.
    text = str(text or "")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if "<" not in text and "&" not in text:
        return [LogicalLine([Run(line, font_size)]) for line in text.split("\n")]
    parser = _RichTextParser(font_size)
    parser.feed(text)
    parser.close()
    lines = parser.lines
    while len(lines) > 1 and not lines[-1].runs and lines[-1].align is None:
        lines.pop()
    return lines or [LogicalLine([Run("", font_size)])]


def _char_width(char: str, font_size: float, letter_spacing: float) -> float:
    if char.isspace():
        unit = 0.35
    elif unicodedata.east_asian_width(char) in "WFA":
        unit = 1.0
    elif char in "ilI1|!.,'`":
        unit = 0.3
    elif char in "MW@%&#":
        unit = 0.9
    elif char.isdigit():
        unit = 0.62
    elif char.isupper():
        unit = 0.66
    elif char.islower():
        unit = 0.55
    elif char.isalnum():
        unit = 0.6
    else:
        unit = 0.5
    # PPTD explicitly defines 1 geometry px == 1 font pt.  Applying the old
    # 0.82 shrink factor made large serif metrics look ~18% narrower than their
    # actual PowerPoint boxes: the live ``600`` / ``<100`` regression wrapped
    # into two rows while lint reported it fit.  Keep family-independent
    # estimates conservative; renderer-specific taste remains visual QA's job.
    return unit * font_size + max(0.0, letter_spacing)


def _wrap_line(
    line: LogicalLine, width: float, *, wrap: bool, letter_spacing: float,
) -> list[tuple[float, float, str]]:
    glyphs: list[tuple[str, float, float]] = []
    for run in line.runs:
        glyphs.extend(
            (char, _char_width(char, run.font_size, letter_spacing), run.font_size)
            for char in run.text
        )
    if not glyphs:
        return [(0.0, max((run.font_size for run in line.runs), default=1.0), "")]
    if not wrap:
        return [(sum(item[1] for item in glyphs), max(item[2] for item in glyphs), line.text)]
    rows: list[tuple[float, float, str]] = []
    current_width = 0.0
    current_font = 1.0
    current_chars: list[str] = []
    for char, char_width, char_font in glyphs:
        if current_chars and current_width + char_width > width:
            rows.append((current_width, current_font, "".join(current_chars)))
            current_width, current_font, current_chars = 0.0, 1.0, []
        current_width += char_width
        current_font = max(current_font, char_font)
        current_chars.append(char)
    rows.append((current_width, current_font, "".join(current_chars)))
    return rows


def _alignment(content: dict, style: dict) -> tuple[str, str]:
    value = content.get("align")
    if not isinstance(value, list):
        value = style.get("align")
    if not isinstance(value, list):
        value = ["left", "top"]
    horizontal = str(value[0] if value else "left").lower()
    vertical = str(value[1] if len(value) > 1 else "top").lower()
    return horizontal, vertical


def _text_rects(
    element_id: str,
    text: str,
    bounds: tuple[float, float, float, float],
    content: dict,
    style: dict,
) -> tuple[list[tuple[str, float, float, float, float, str]], list[str]]:
    x, y, width, height = bounds
    base_font = max(1.0, _number(content.get("fontSize", style.get("fontSize", 18)), 18.0))
    letter_spacing = _number(content.get("letterSpacing", style.get("letterSpacing", 0)), 0.0)
    wrap = content.get("wrap", True) is not False
    horizontal, vertical = _alignment(content, style)
    logical_lines = _visible_lines(text, base_font)
    rows: list[tuple[float, float, str, str, float]] = []
    default_line_height_px = content.get("lineHeightPx", style.get("lineHeightPx"))
    default_line_height = _number(content.get("lineHeight", style.get("lineHeight", 1.0)), 1.0)
    for logical in logical_lines:
        for row_width, row_font, row_text in _wrap_line(
            logical, max(1.0, width * 0.94), wrap=wrap, letter_spacing=letter_spacing,
        ):
            if logical.line_height is not None:
                row_height = logical.line_height
            elif default_line_height_px is not None:
                row_height = max(1.0, _number(default_line_height_px, row_font))
            else:
                row_height = max(1.0, row_font * default_line_height)
            rows.append((row_width, row_height, row_text, logical.align or horizontal, logical.margin_top))
            logical.margin_top = 0.0
    total_height = sum(row[1] + row[4] for row in rows)
    tolerance = max(2.0, 0.12 * max((row[1] for row in rows), default=base_font))
    issues: list[str] = []
    if total_height > height + tolerance:
        issues.append(
            f"{element_id} probable text overflow "
            f"(needs {total_height:.1f}px height, box has {height:.1f}px)"
        )
    if not wrap and any(row[0] > width + 2.0 for row in rows):
        widest = max(row[0] for row in rows)
        issues.append(
            f"{element_id} nowrap text overflow "
            f"(needs {widest:.1f}px width, box has {width:.1f}px)"
        )
    cursor_y = y
    if vertical == "middle":
        cursor_y = y + max(0.0, (height - total_height) / 2)
    elif vertical == "bottom":
        cursor_y = y + max(0.0, height - total_height)
    rects = []
    for row_width, row_height, row_text, row_align, margin_top in rows:
        cursor_y += margin_top
        actual_width = min(width, row_width)
        row_x = x
        if row_align == "center":
            row_x = x + (width - actual_width) / 2
        elif row_align == "right":
            row_x = x + width - actual_width
        rects.append((element_id, row_x, cursor_y, actual_width, row_height, row_text))
        cursor_y += row_height
    return rects, issues


def lint_project(root: pathlib.Path) -> list[str]:
    manifests = list(root.glob("*.pptd"))
    if len(manifests) != 1:
        return [f"expected exactly one .pptd, got {len(manifests)}"]
    manifest = yaml.safe_load(manifests[0].read_text(encoding="utf-8")) or {}

    def read_page(page_ref: str) -> dict | None:
        page_path = root / page_ref
        if not page_path.is_file():
            return None
        return yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}

    return lint_project_data(manifest, read_page)


def lint_project_data(manifest: dict, read_page: Callable[[str], dict | None]) -> list[str]:
    """The same deterministic checks for a live project or an immutable snapshot."""
    if manifest.get("version") != "v2":
        return ["PPTD manifest version must be v2"]
    size = manifest.get("size") or [960, 540]
    issues: list[str] = []
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        page_width, page_height = [_number(value, 0.0) for value in size[:2]]
    elif isinstance(size, dict):
        page_width = _number(size.get("width"), 0.0)
        page_height = _number(size.get("height"), 0.0)
        issues.append("PPTD manifest size must be [width, height], not an object")
    else:
        return ["PPTD manifest size must be [width, height]"]
    if page_width <= 0 or page_height <= 0:
        return ["PPTD manifest size values must be positive numbers"]
    styles = ((manifest.get("theme") or {}).get("textStyles") or {})
    for page_ref in manifest.get("pages") or []:
        page = read_page(str(page_ref))
        if page is None:
            issues.append(f"missing page: {page_ref}")
            continue
        text_rects = []
        seen_text_bounds: set[tuple] = set()
        for element in page.get("elements") or []:
            if not isinstance(element, dict):
                continue
            element_id = str(element.get("elementId") or "<unnamed>")
            bounds = element.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4:
                issues.append(f"{page_ref}:{element_id} invalid bounds")
                continue
            try:
                x, y, width, height = [float(value) for value in bounds]
            except (TypeError, ValueError):
                issues.append(f"{page_ref}:{element_id} non-numeric bounds")
                continue
            if (
                width <= 0 or height <= 0 or x < 0 or y < 0
                or x + width > page_width or y + height > page_height
            ):
                issues.append(f"{page_ref}:{element_id} outside slide bounds {bounds}")
            if str(element.get("elementType") or "") != "text":
                continue
            if _number(element.get("opacity"), 1.0) <= 0:
                continue
            content = element.get("content")
            if not isinstance(content, dict) or not str(content.get("text") or "").strip():
                issues.append(f"{page_ref}:{element_id} text content must be a non-empty object")
                continue
            text = str(content.get("text") or "")
            invalid_lt = _unsupported_lt_positions(text)
            if invalid_lt:
                issues.append(
                    f"{page_ref}:{element_id} contains raw '<' outside a supported rich-text tag; "
                    "use &lt; for visible comparisons"
                )
            style = {}
            raw_style = content.get("style")
            style_ref = str(raw_style).strip() if raw_style is not None else ""
            if style_ref:
                if not style_ref.startswith("$"):
                    issues.append(
                        f"{page_ref}:{element_id} theme text style reference "
                        f"must start with '$' (got {style_ref!r})"
                    )
                else:
                    style_name = style_ref[1:]
                    if not isinstance(styles, dict) or style_name not in styles:
                        issues.append(
                            f"{page_ref}:{element_id} unknown theme text style "
                            f"reference {style_ref!r}"
                        )
                    else:
                        candidate = styles.get(style_name)
                        if isinstance(candidate, dict):
                            style = candidate
            rects, local_issues = _text_rects(
                element_id, text, (x, y, width, height), content,
                style if isinstance(style, dict) else {},
            )
            issues.extend(f"{page_ref}:{message}" for message in local_issues)
            rotation = abs(_number(element.get("rotation"), 0.0)) % 360
            visible = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
            dedupe_key = (round(x, 2), round(y, 2), round(width, 2), round(height, 2), visible)
            if rotation <= 0.01 and dedupe_key not in seen_text_bounds:
                text_rects.extend(rects)
                seen_text_bounds.add(dedupe_key)

        for index, first in enumerate(text_rects):
            first_id, ax, ay, aw, ah, _ = first
            if aw <= 0 or ah <= 0:
                continue
            for second in text_rects[index + 1:]:
                second_id, bx, by, bw, bh, _ = second
                if first_id == second_id or bw <= 0 or bh <= 0:
                    continue
                intersect_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
                intersect_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
                if intersect_w < 6.0 or intersect_h < 0.30 * min(ah, bh):
                    continue
                intersection = intersect_w * intersect_h
                smaller = min(aw * ah, bw * bh)
                if smaller > 0 and intersection / smaller >= 0.12:
                    issues.append(
                        f"{page_ref}:{first_id} overlaps {second_id} "
                        f"({intersection / smaller:.0%} of smaller text area)"
                    )
    return issues


def main(argv: Iterable[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        print("usage: pptd_layout_lint_runtime.py PROJECT_DIR", file=sys.stderr)
        return 2
    try:
        issues = lint_project(pathlib.Path(args[0]))
    except Exception as exc:  # invalid YAML/dependency errors are infrastructure failures
        print(f"PPTD layout lint infrastructure failure: {exc}", file=sys.stderr)
        return 2
    if issues:
        print("PPTD layout lint failed:\n- " + "\n- ".join(issues[:30]), file=sys.stderr)
        return 1
    print("PPTD layout lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
