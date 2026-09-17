#!/usr/bin/env python3
"""Build a QA-friendly PPTD v2 deck from a compact JSON art-direction spec.

This is the preferred authoring path for text models: they choose content,
images and layout names; this script owns the fragile PPTD field structure.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import yaml


SIZE = [960, 540]
BASE_TYPOGRAPHY = {
    "latin_font": "Helvetica Neue",
    "ea_font": "Microsoft YaHei",
    "display_latin_font": "Helvetica Neue",
    "display_ea_font": "Microsoft YaHei",
}
PALETTES = {
    "light": {
        "background": "#F4F0E8",
        "surface": "#E6E0D6",
        "foreground": "#11161C",
        "muted": "#626B75",
        "accent": "#176BFF",
    },
    "warm": {
        "background": "#F3EEE5",
        "surface": "#DED5C7",
        "foreground": "#1A1714",
        "muted": "#6D645B",
        "accent": "#B9482E",
    },
    "dark": {
        "background": "#0A0C10",
        "surface": "#11161D",
        "foreground": "#F4F1EA",
        "muted": "#9DA4AE",
        "accent": "#E87A2B",
    },
}

# Flash models should not have to read tens of thousands of design-system tokens
# before making one coherent art-direction decision. These compact presets compile
# the reusable visual grammar into the deterministic authoring path.
STYLE_PRESETS = {
    "editorial-silver": {
        "background": "#F6F6F3", "surface": "#DFDFDA", "foreground": "#171717",
        "muted": "#6E6E6A", "accent": "#9D5A37", "display_latin_font": "Georgia",
    },
    "editorial-ivory": {
        "background": "#F5F0E6", "surface": "#DDD4C5", "foreground": "#181512",
        "muted": "#70675F", "accent": "#B54830", "display_latin_font": "Georgia",
    },
    "swiss-blue": {
        "background": "#F7F5EF", "surface": "#D9DCE4", "foreground": "#101010",
        "muted": "#62656C", "accent": "#0038B8",
    },
    "consulting-red": {
        "background": "#FFFFFF", "surface": "#E7E7E5", "foreground": "#151515",
        "muted": "#686868", "accent": "#C91920",
    },
    "natural-sage": {
        "background": "#EEECE2", "surface": "#D8D5C6", "foreground": "#24342D",
        "muted": "#687067", "accent": "#8D5F47", "display_latin_font": "Georgia",
    },
    "tech-cobalt": {
        "background": "#F3F6FA", "surface": "#DCE5F1", "foreground": "#0D1A2A",
        "muted": "#5D6978", "accent": "#176BFF",
    },
    "cultural-cinnabar": {
        "background": "#F1ECE2", "surface": "#E0D5C4", "foreground": "#1E1B18",
        "muted": "#716A62", "accent": "#B5382C", "display_latin_font": "Georgia",
    },
    "night-copper": {
        "background": "#0C0D0F", "surface": "#1B1D20", "foreground": "#F5F1E8",
        "muted": "#AAA39A", "accent": "#C78946", "display_latin_font": "Georgia",
    },
}

STYLE_ALIASES = {
    "editorial-minimal": "editorial-silver",
    "editorial-magazine": "editorial-silver",
    "bright-luxury": "editorial-ivory",
    "swiss-minimal": "swiss-blue",
    "consulting-data": "consulting-red",
    "organic-natural": "natural-sage",
    "tech-industrial": "tech-cobalt",
    "cultural-contemporary": "cultural-cinnabar",
    "cinematic-editorial": "night-copper",
}

SCENE_PRESETS = {
    "analysis": "consulting-red",
    "business": "editorial-ivory",
    "report": "natural-sage",
    "academic": "swiss-blue",
    "education": "editorial-ivory",
    "tech": "tech-cobalt",
    "brand": "editorial-silver",
}


def _s(value: Any) -> str:
    return str(value or "").strip()


def _html(value: Any) -> str:
    return html.escape(_s(value)).replace("\n", "<br/>")


def _text(
    element_id: str,
    bounds: Sequence[float],
    value: Any,
    *,
    size: float,
    color: str,
    theme: Dict[str, str],
    bold: bool = False,
    align: Sequence[str] = ("left", "top"),
    line_height: float = 1.25,
    letter_spacing: float = 0,
    display: bool = False,
) -> Dict[str, Any]:
    latin_font = theme["display_latin_font"] if display else theme["latin_font"]
    ea_font = theme["display_ea_font"] if display else theme["ea_font"]
    content: Dict[str, Any] = {
        "align": list(align),
        "fontFamily": {"latin": latin_font, "ea": ea_font},
        "fontSize": size,
        "color": color,
        "text": f"<p>{_html(value)}</p>",
    }
    if bold:
        content["bold"] = True
    if line_height:
        content["lineHeight"] = line_height
    if letter_spacing:
        content["letterSpacing"] = letter_spacing
    return {
        "elementId": element_id,
        "elementType": "text",
        "bounds": list(bounds),
        "content": content,
    }


def _image(element_id: str, bounds: Sequence[float], src: str) -> Dict[str, Any]:
    return {
        "elementId": element_id,
        "elementType": "image",
        "bounds": list(bounds),
        "src": src,
        "fit": {"mode": "cover"},
    }


def _shape(
    element_id: str,
    bounds: Sequence[float],
    fill: str,
    *,
    opacity: float = 1,
) -> Dict[str, Any]:
    return {
        "elementId": element_id,
        "elementType": "shape",
        "bounds": list(bounds),
        "shapeName": "rect",
        "fill": fill,
        "opacity": opacity,
    }


def _animations(elements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = [
        _s(item.get("elementId"))
        for item in elements
        if item.get("elementType") == "text" and _s((item.get("content") or {}).get("text"))
    ]
    out: List[Dict[str, Any]] = []
    for index, element_id in enumerate(ids[:3]):
        row: Dict[str, Any] = {
            "elementId": element_id,
            "effect": "fade-in" if index == 0 else "float-in",
            "trigger": "afterPrevious",
            "durationMs": 420 if index else 520,
        }
        if index:
            row["direction"] = "up"
        out.append(row)
    return out


def _body_text(page: Dict[str, Any]) -> str:
    body = page.get("body") or page.get("bullets") or []
    if isinstance(body, list):
        return "\n".join(f"{index + 1:02d}  {_s(item)}" for index, item in enumerate(body[:5]))
    return _s(body)


def _theme_for_spec(spec: Dict[str, Any]) -> Dict[str, str]:
    """Resolve an executable style preset without making Flash read the full library.

    Explicit theme tokens remain the final authority. Otherwise an explicit preset
    or style family wins, followed by an explicit dark direction, then a scene-aware
    default. This avoids pushing every topic into one palette.
    """
    direction = " ".join([
        _s(spec.get("tone")),
        _s(spec.get("style_family")),
        _s(spec.get("visual_motif")),
    ]).lower()
    requested = _s(spec.get("preset") or spec.get("style_preset")).lower()
    family = _s(spec.get("style_family")).lower()
    if requested in STYLE_PRESETS:
        preset = requested
    elif family in STYLE_PRESETS:
        preset = family
    elif family in STYLE_ALIASES:
        preset = STYLE_ALIASES[family]
    elif any(word in direction for word in ("dark", "night", "noir", "黑", "夜")):
        preset = "night-copper"
    elif any(word in direction for word in ("warm", "sand", "earth", "暖", "沙", "大地")):
        preset = "editorial-ivory"
    else:
        preset = SCENE_PRESETS.get(_s(spec.get("scene")).lower(), "editorial-silver")
    overrides = {k: _s(v) for k, v in (spec.get("theme") or {}).items() if _s(v)}
    resolved = {**BASE_TYPOGRAPHY, **STYLE_PRESETS[preset], **overrides}
    resolved["preset_id"] = preset
    return resolved


def _fullbleed(page: Dict[str, Any], theme: Dict[str, str], *, quote: bool = False) -> Dict[str, Any]:
    image = _s(page.get("image"))
    elements: List[Dict[str, Any]] = []
    if image:
        elements.append(_image("hero", [0, 0, 960, 540], image))
    # 图上文字必须使用可预测的深色压图层 + 浅字。此前浅色主题把奶油白半透明层和
    # 深色标题直接压在复杂照片上，封面/收束页在实际预览中对比不足，却可能被视觉模型
    # 误判为“配色统一”。浅色 deck 的摄影高潮页仍可局部变暗，不等于整本回落黑色模板。
    photo_text = bool(image)
    veil_fill = _s(page.get("veil_color")) or ("#10151C" if photo_text else theme["background"])
    foreground = "#F7F5EF" if photo_text else theme["foreground"]
    muted = "#D2D7DE" if photo_text else theme["muted"]
    elements.append(_shape("veil", [0, 0, 960, 540], veil_fill, opacity=float(page.get("veil", 0.48))))
    if quote:
        elements.extend([
            _text("quote", [84, 174, 792, 150], page.get("title"), size=34,
                  color=foreground, theme=theme, bold=True, align=("center", "middle"), display=True),
            _text("credit", [120, 338, 720, 34], page.get("subtitle"), size=13,
                  color=muted, theme=theme, align=("center", "middle"), letter_spacing=1.5),
        ])
    else:
        elements.extend([
            _text("kicker", [58, 302, 690, 28], page.get("kicker"), size=12,
                  color=theme["accent"], theme=theme, letter_spacing=3),
            _text("title", [54, 338, 820, 92], page.get("title"), size=float(page.get("title_size", 52)),
                  color=foreground, theme=theme, bold=True, display=True),
            _text("subtitle", [58, 444, 720, 50], page.get("subtitle"), size=16,
                  color=muted, theme=theme, line_height=1.4),
        ])
    return {"pageType": page.get("page_type", "content"), "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _split(page: Dict[str, Any], theme: Dict[str, str], *, image_left: bool) -> Dict[str, Any]:
    ix = 0 if image_left else 480
    tx = 526 if image_left else 56
    elements: List[Dict[str, Any]] = [
        _image("photo", [ix, 0, 480, 540], _s(page.get("image"))),
        _text("kicker", [tx, 120, 378, 26], page.get("kicker"), size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.4),
        _text("title", [tx - 4, 158, 386, 104], page.get("title"), size=34,
              color=theme["foreground"], theme=theme, bold=True, display=True),
        _text("body", [tx, 282, 374, 150], _body_text(page), size=15,
              color=theme["muted"], theme=theme, line_height=1.55),
    ]
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _giant(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    elements = [
        _text("stat", [52, 102, 856, 205], page.get("stat"), size=132,
              color=theme["foreground"], theme=theme, bold=True, align=("left", "middle"), display=True),
        _text("title", [60, 326, 760, 42], page.get("title"), size=22,
              color=theme["accent"], theme=theme, bold=True),
        _text("body", [60, 386, 740, 66], page.get("subtitle") or _body_text(page), size=14,
              color=theme["muted"], theme=theme, line_height=1.45),
    ]
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _editorial_cover(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    """Magazine-like cover: typography owns the page; photography is an optional panel."""
    image = _s(page.get("image"))
    text_width = 548 if image else 820
    elements: List[Dict[str, Any]] = [
        _shape("accent_rule", [58, 62, 4, 390], theme["accent"]),
        _text("kicker", [88, 66, text_width, 28], page.get("kicker") or "EDITORIAL / 01", size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.6),
        _text("title", [84, 128, text_width, 180], page.get("title"),
              size=float(page.get("title_size", 54)), color=theme["foreground"], theme=theme,
              bold=True, line_height=1.05, display=True),
        _text("subtitle", [88, 338, text_width - 28, 76], page.get("subtitle"), size=16,
              color=theme["muted"], theme=theme, line_height=1.45),
        _text("folio", [88, 466, 240, 24], page.get("folio") or "AURORA / 2026", size=10,
              color=theme["muted"], theme=theme, letter_spacing=1.8),
    ]
    if image:
        elements.insert(0, _image("photo", [650, 0, 310, 540], image))
        elements.append(_shape("photo_edge", [642, 0, 8, 540], theme["surface"]))
    else:
        elements.extend([
            _shape("field_a", [716, 72, 170, 170], theme["surface"], opacity=0.88),
            _shape("field_b", [650, 242, 236, 6], theme["accent"]),
            _shape("field_c", [786, 248, 100, 214], theme["surface"], opacity=0.55),
        ])
    return {"pageType": "cover", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _statement(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    """Low-density typographic breath page with no photographic dependency."""
    marker = page.get("stat") or page.get("marker") or "01"
    elements = [
        _shape("axis", [58, 74, 844, 2], theme["surface"]),
        _text("marker", [690, 84, 220, 112], marker, size=92,
              color=theme["surface"], theme=theme, bold=True, align=("right", "top"), display=True),
        _text("kicker", [60, 102, 500, 24], page.get("kicker") or "POINT OF VIEW", size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.4),
        _text("title", [56, 176, 790, 144], page.get("title"), size=float(page.get("title_size", 52)),
              color=theme["foreground"], theme=theme, bold=True, line_height=1.08, display=True),
        _shape("accent", [60, 352, 132, 5], theme["accent"]),
        _text("body", [60, 386, 650, 84], page.get("subtitle") or _body_text(page), size=15,
              color=theme["muted"], theme=theme, line_height=1.5),
    ]
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _gallery(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    images = list(page.get("images") or [])[:2]
    while len(images) < 2:
        images.append(page.get("image") or "")
    captions = list(page.get("captions") or [])[:2]
    while len(captions) < 2:
        captions.append("")
    elements = [
        _text("kicker", [56, 46, 500, 24], page.get("kicker"), size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.2),
        _text("title", [52, 78, 820, 62], page.get("title"), size=34,
              color=theme["foreground"], theme=theme, bold=True, display=True),
        _image("photo_a", [56, 168, 408, 268], _s(images[0])),
        _image("photo_b", [496, 168, 408, 268], _s(images[1])),
        _text("caption_a", [56, 452, 408, 36], captions[0], size=12,
              color=theme["muted"], theme=theme),
        _text("caption_b", [496, 452, 408, 36], captions[1], size=12,
              color=theme["muted"], theme=theme),
    ]
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _timeline(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    items = list(page.get("items") or page.get("milestones") or [])[:4]
    elements: List[Dict[str, Any]] = [
        _text("kicker", [56, 48, 500, 24], page.get("kicker"), size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.2),
        _text("title", [52, 82, 830, 60], page.get("title"), size=34,
              color=theme["foreground"], theme=theme, bold=True, display=True),
        _shape("axis", [74, 286, 812, 2], theme["muted"], opacity=0.45),
    ]
    width = 812 / max(1, len(items))
    for index, raw in enumerate(items):
        item = raw if isinstance(raw, dict) else {"label": str(raw)}
        x = 74 + width * index
        elements.extend([
            _shape(f"tick_{index}", [x, 274, 3, 26], theme["accent"]),
            _text(f"year_{index}", [x - 8, 210, width - 12, 34], item.get("year") or item.get("label"),
                  size=18, color=theme["foreground"], theme=theme, bold=True),
            _text(f"note_{index}", [x - 8, 316, width - 18, 82], item.get("text") or item.get("note"),
                  size=12, color=theme["muted"], theme=theme, line_height=1.4),
        ])
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _diagram(page: Dict[str, Any], theme: Dict[str, str]) -> Dict[str, Any]:
    # Flash models sometimes put the three diagram labels in ``body`` even when
    # the compact contract says ``items``.  Treat that common shape as data for
    # the native diagram instead of silently exporting a title-only blank page.
    items = list(page.get("items") or page.get("body") or page.get("bullets") or [])[:3]
    elements: List[Dict[str, Any]] = [
        _text("kicker", [56, 46, 500, 24], page.get("kicker"), size=11,
              color=theme["accent"], theme=theme, letter_spacing=2.2),
        _text("title", [52, 80, 830, 60], page.get("title"), size=34,
              color=theme["foreground"], theme=theme, bold=True, display=True),
    ]
    for index, raw in enumerate(items):
        item = raw if isinstance(raw, dict) else {"title": str(raw)}
        x = 56 + index * 294
        elements.extend([
            _shape(f"rule_{index}", [x, 184, 260, 3], theme["surface"], opacity=0.95),
            _shape(f"tick_{index}", [x, 184, 36, 3], theme["accent"]),
            _text(f"num_{index}", [x, 210, 210, 32], f"0{index + 1}", size=14,
                  color=theme["accent"], theme=theme, bold=True),
            _text(f"item_{index}", [x, 258, 236, 58], item.get("title") or item.get("label"),
                  size=21, color=theme["foreground"], theme=theme, bold=True),
            _text(f"desc_{index}", [x, 330, 236, 74], item.get("text") or item.get("note") or item.get("desc"),
                  size=12, color=theme["muted"], theme=theme, line_height=1.4),
        ])
    return {"pageType": "content", "background": {"type": "solid", "color": theme["background"]},
            "elements": elements, "animations": _animations(elements)}


def _available_media(output_dir: Path, spec: Dict[str, Any]) -> List[str]:
    """返回可复用的工程图片池。

    Flash 模型有时已把图片下到 ``media/``，却漏填 gallery.images；
    让确定性生成器完成这个机械映射，不要再花一轮模型手改 YAML。
    """
    explicit = [_s(item) for item in (spec.get("assets") or []) if _s(item)]
    discovered = [
        path.relative_to(output_dir).as_posix()
        for path in sorted((output_dir / "media").glob("*"))
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    ]
    return list(dict.fromkeys([*explicit, *discovered]))


def _fill_missing_images(page: Dict[str, Any], layout: str, assets: List[str], cursor: int) -> tuple[Dict[str, Any], int]:
    page = dict(page)
    if not assets:
        return page, cursor
    if layout == "gallery":
        images = [_s(item) for item in (page.get("images") or []) if _s(item)]
        while len(images) < 2:
            images.append(assets[cursor % len(assets)])
            cursor += 1
        page["images"] = images[:2]
    elif layout in {"cover", "editorial_cover", "editorial-cover", "fullbleed", "closing", "quote", "split_left", "split-left", "split_right", "split-right"}:
        if not _s(page.get("image")):
            page["image"] = assets[cursor % len(assets)]
            cursor += 1
    return page, cursor


def build(spec: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pages").mkdir(exist_ok=True)
    (output_dir / "media").mkdir(exist_ok=True)
    theme = _theme_for_spec(spec)
    pages = list(spec.get("pages") or [])
    if not pages:
        raise ValueError("spec.pages must contain at least one page")
    assets = _available_media(output_dir, spec)
    asset_cursor = 0

    manifest_pages: List[str] = []
    storyboard: List[str] = []
    for index, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            raise ValueError(f"pages[{index - 1}] must be an object")
        layout = _s(page.get("layout") or ("cover" if index == 1 else "split_left")).lower()
        page, asset_cursor = _fill_missing_images(page, layout, assets, asset_cursor)
        if layout in {"cover", "fullbleed", "closing"}:
            rendered = _fullbleed({**page, "page_type": "cover" if index == 1 else "content"}, theme)
        elif layout in {"editorial_cover", "editorial-cover"}:
            rendered = _editorial_cover(page, theme)
        elif layout in {"statement", "editorial", "breath"}:
            rendered = _statement(page, theme)
        elif layout == "quote":
            rendered = _fullbleed(page, theme, quote=True)
        elif layout in {"split_left", "split-left"}:
            rendered = _split(page, theme, image_left=True)
        elif layout in {"split_right", "split-right"}:
            rendered = _split(page, theme, image_left=False)
        elif layout in {"giant", "stat", "giant_number"}:
            rendered = _giant(page, theme)
        elif layout == "gallery":
            rendered = _gallery(page, theme)
        elif layout == "timeline":
            rendered = _timeline(page, theme)
        elif layout in {"diagram", "structure"}:
            rendered = _diagram(page, theme)
        else:
            raise ValueError(f"unsupported layout: {layout}")
        rel = f"pages/{index:02d}.page"
        (output_dir / rel).write_text(
            yaml.safe_dump(rendered, allow_unicode=True, sort_keys=False, width=120),
            encoding="utf-8",
        )
        manifest_pages.append(rel)
        storyboard.append(
            f"P{index} | {layout} | {_s(page.get('title')) or '未命名'} | 读者任务：{_s(page.get('reader_task')) or '看懂本页唯一结论'}"
        )

    manifest = {"version": "v2", "title": _s(spec.get("title")) or "Untitled", "size": SIZE, "pages": manifest_pages}
    manifest_path = output_dir / "deck.pptd"
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8"
    )
    design = "\n".join([
        f"场景: {_s(spec.get('scene')) or 'brand'}",
        f"设计模式: {_s(spec.get('design_mode')) or 'auto'}",
        f"风格族: {_s(spec.get('style_family')) or 'editorial-minimal'}",
        f"轻量风格预设: {theme['preset_id']}",
        f"基调: {_s(spec.get('tone')) or 'light'}",
        f"视觉母题: {_s(spec.get('visual_motif')) or '编辑式排版、真实素材与原生图解交替'}",
        f"字体策略: {theme['display_latin_font']} / {theme['latin_font']} + {theme['ea_font']}，56/34/22/15/12 阶梯",
        f"配图计划: {_s(spec.get('imagery_plan')) or '主题相关真实摄影，满幅与齐边裁切交替'}",
        "页面节奏:",
        *storyboard,
        f"禁用模式: {_s(spec.get('avoid')) or '黑金卡片墙、重复骨架、廉价渐变、大段堆字'}",
        f"动效: {_s(spec.get('motion')) or 'on'}",
        *([f"参考 DNA: {_s(spec.get('reference_dna'))}", "不得复制项: 水印、商标、原文、原图与具体版式"] if _s(spec.get("reference_dna")) else []),
        "",
    ])
    (output_dir / "DESIGN.md").write_text(design, encoding="utf-8")
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create PPTD v2 from a compact JSON spec")
    parser.add_argument("spec", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    manifest = build(spec, args.output_dir)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
