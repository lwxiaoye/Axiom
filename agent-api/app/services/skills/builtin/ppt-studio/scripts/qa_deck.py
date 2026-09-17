#!/usr/bin/env python3
"""Cross-style static QA for a PPTD project.

Called by export_pptx.py before WASM write.  It validates the selected visual
direction instead of forcing every deck through one cinematic/dark template.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:
    print("qa_deck: PyYAML missing. pip install --user pyyaml", file=sys.stderr)
    raise SystemExit(2)

try:
    from PIL import Image
except ImportError:  # 导出镜像正常预装；缺失时保留其余静态 QA。
    Image = None  # type: ignore[assignment]

WALLPAPER_RE = re.compile(
    r"(nebul|starfield|star-?field|galaxy|fog|particle|wallpaper|cosmos|aurora|"
    r"星空|雾气|星云|渐变墙)",
    re.I,
)
SOURCE_RE = re.compile(r"来源\s*[:：]")
SCENE_RE = re.compile(r"场景\s*[：:]\s*([a-zA-Z_]+)")
STYLE_RE = re.compile(r"风格族\s*[：:]\s*([a-zA-Z0-9_-]+)")
TONE_RE = re.compile(r"基调\s*[：:]\s*(light|dark|color)", re.I)
MOTION_RE = re.compile(r"动效\s*[：:]\s*(on|off)", re.I)
SPACE_TOPIC_RE = re.compile(r"(航天|太空|天文|卫星|火箭|space|aerospace|astronomy)", re.I)
PHOTO_FIRST_STYLES = {
    "cinematic-editorial", "bright-luxury", "editorial-magazine",
    "organic-natural", "tech-industrial", "cultural-contemporary",
}
DESIGN_REQUIRED_LABELS = (
    "场景", "设计模式", "风格族", "基调", "视觉母题", "字体策略",
    "配图计划", "页面节奏", "禁用模式", "动效",
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
PORTABLE_EA_FONTS = {
    "microsoft yahei", "microsoft yahei ui", "微软雅黑",
    "microsoft jhenghei", "pingfang sc", "苹方", "苹方-简",
    "noto sans cjk sc", "noto serif cjk sc", "noto sans sc", "noto serif sc",
}
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve_project(source: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    source = source.expanduser().resolve()
    if source.is_dir():
        pptds = sorted(source.glob("*.pptd"))
        if len(pptds) != 1:
            raise RuntimeError(f"工程目录里需要恰好 1 个 .pptd，实际 {len(pptds)}: {source}")
        manifest = pptds[0]
        root = source
    else:
        manifest = source
        root = source.parent
    data = _load_yaml(manifest)
    if not isinstance(data, dict):
        raise RuntimeError(f"无法解析 {manifest}")
    return root, manifest, data


def _design_text(root: Path) -> str:
    design = root / "DESIGN.md"
    if not design.is_file():
        return ""
    return design.read_text(encoding="utf-8", errors="replace")


def _design_value(pattern: re.Pattern[str], text: str, fallback: str = "") -> str:
    match = pattern.search(text)
    return match.group(1).strip().lower() if match else fallback


def space_topic_of(root: Path, manifest: Dict[str, Any]) -> bool:
    """只有主题本身为航天/天文时，才允许一张真实任务图带有 space 语义。"""
    design = root / "DESIGN.md"
    design_text = design.read_text(encoding="utf-8", errors="replace") if design.is_file() else ""
    return bool(SPACE_TOPIC_RE.search(f"{manifest.get('title') or ''}\n{design_text}"))


def _bounds(el: Dict[str, Any]) -> Tuple[float, float, float, float]:
    raw = el.get("bounds") or [0, 0, 0, 0]
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return 0.0, 0.0, 0.0, 0.0
    return tuple(float(raw[i]) for i in range(4))  # type: ignore[return-value]


def _texts(el: Dict[str, Any]) -> str:
    content = el.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        return content
    return str(el.get("text") or "")


def _src(el: Dict[str, Any]) -> str:
    return str(el.get("src") or el.get("source") or "")


def _background_src(page: Dict[str, Any]) -> str:
    background = page.get("background")
    return str(background.get("src") or background.get("source") or "") if isinstance(background, dict) else ""


def _schema_issues(rel: str, page: Dict[str, Any]) -> List[str]:
    """拒绝旧 HTML/DSL 的字段，避免模型看似生成了页面却没走 PPTD 模板。"""
    issues: List[str] = []
    background = page.get("background")
    if isinstance(background, dict) and (background.get("type") == "image" or _background_src(page)):
        issues.append(f"{rel} 使用了旧式 background image；英雄图必须放进 elements 的 elementType: image。")
    for index, el in enumerate(page.get("elements") or [], 1):
        if not isinstance(el, dict):
            issues.append(f"{rel} 第 {index} 个元素不是对象。")
            continue
        if not el.get("elementType"):
            issues.append(f"{rel} 第 {index} 个元素缺少 PPTD v2 的 elementType（疑似旧 type/style/text 格式）。")
    return issues


def _has_required_animation(page: Dict[str, Any]) -> bool:
    text_elements = [el for el in page.get("elements") or [] if el.get("elementType") == "text" and _texts(el).strip()]
    if not text_elements:
        return True
    animations = page.get("animations") or []
    if not isinstance(animations, list) or not animations:
        return False
    first = animations[0] if isinstance(animations[0], dict) else {}
    element_ids = {str(el.get("elementId") or "") for el in page.get("elements") or []}
    return bool(first.get("elementId") in element_ids and first.get("trigger") == "afterPrevious")


def _font_size(el: Dict[str, Any]) -> float:
    content = el.get("content")
    if isinstance(content, dict):
        try:
            return float(content.get("fontSize") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _east_asia_font(el: Dict[str, Any]) -> str:
    content = el.get("content")
    family = content.get("fontFamily") if isinstance(content, dict) else None
    if isinstance(family, dict):
        return str(family.get("ea") or "").strip()
    return ""


def _text_color(el: Dict[str, Any]) -> str:
    content = el.get("content")
    return str(content.get("color") or "") if isinstance(content, dict) else ""


def _is_light_color(value: str) -> bool:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6 or raw.startswith("$"):
        return True
    try:
        rgb = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return True
    return sum(rgb) / (3 * 255) >= 0.65


def _background_color(page: Dict[str, Any]) -> str:
    background = page.get("background")
    if isinstance(background, str):
        return background
    if isinstance(background, dict):
        return str(background.get("fill") or background.get("color") or "")
    return ""


def _is_dark_background(page: Dict[str, Any]) -> bool:
    color = _background_color(page).strip().lstrip("#")
    if len(color) != 6 or color.startswith("$"):
        return False
    try:
        rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return sum(rgb) / (3 * 255) <= 0.24


def _is_fullbleed(el: Dict[str, Any]) -> bool:
    x, y, w, h = _bounds(el)
    return x <= 8 and y <= 8 and w >= 944 and h >= 524


def _is_dark_veil(el: Dict[str, Any]) -> bool:
    if el.get("elementType") != "shape" or not _is_fullbleed(el):
        return False
    fill = str(el.get("fill") or "").strip().lstrip("#")
    try:
        rgb = tuple(int(fill[i:i + 2], 16) for i in (0, 2, 4))
        luminance = sum(rgb) / (3 * 255)
        opacity = float(el.get("opacity") or 0)
    except (TypeError, ValueError):
        return False
    return luminance <= 0.28 and 0.25 <= opacity <= 0.80


def _fullbleed_text_without_veil(page: Dict[str, Any]) -> bool:
    """Reject text directly over an arbitrary full-bleed image.

    Static QA cannot know whether a downloaded image is bright, dark, or has a logo on
    the text area.  Requiring a dark veil is deterministic and matches the bundled
    cinematic skeleton.  It catches the white DeepSeek logo pages whose warm-white text
    became invisible even though every individual element was schema-valid.
    """
    elements = [el for el in page.get("elements") or [] if isinstance(el, dict)]
    image_indexes = [
        index for index, el in enumerate(elements)
        if el.get("elementType") == "image" and _is_fullbleed(el)
    ]
    for image_index in image_indexes:
        text_indexes = [
            index for index, el in enumerate(elements)
            if index > image_index and el.get("elementType") == "text" and _texts(el).strip()
            and _is_light_color(_text_color(el))
        ]
        if not text_indexes:
            continue
        first_text = min(text_indexes)
        if not any(_is_dark_veil(el) for el in elements[image_index + 1:first_text]):
            return True
    return False


def chrome_of(page: Dict[str, Any]) -> str:
    images = [el for el in page.get("elements") or [] if el.get("elementType") == "image"]
    texts = [el for el in page.get("elements") or [] if el.get("elementType") == "text"]
    shapes = [el for el in page.get("elements") or [] if el.get("elementType") == "shape"]
    # Hairlines, ticks and rules are editorial structure, not cards.  The old
    # heuristic counted any four shapes and therefore misclassified timelines,
    # line-based diagrams and typography-first covers as a card wall.
    panel_shapes = [
        el for el in shapes
        if _bounds(el)[2] >= 110 and _bounds(el)[3] >= 48
    ]
    data_elements = [
        el for el in page.get("elements") or []
        if el.get("elementType") in {"chart", "table", "diagram"}
    ]
    for el in images:
        x, y, w, h = _bounds(el)
        if x <= 8 and y <= 8 and w >= 900 and h >= 500:
            return "fullbleed"
    if len(images) >= 2:
        return "gallery"
    if data_elements:
        return "data"
    for el in images:
        x, _y, w, h = _bounds(el)
        if x >= 380 and w <= 580 and h >= 400:
            return "split_right"
        if x <= 80 and w <= 580 and h >= 400:
            return "split_left"
    if any(_font_size(el) >= 88 for el in texts) and len(texts) <= 3:
        return "giant"
    if len(texts) <= 2 and not images:
        return "quote"
    if len(panel_shapes) >= 3 and len(texts) >= 4 and not images:
        return "card_grid"
    if len(shapes) >= 3 and not images:
        return "diagram"
    return "other"


def load_pages(root: Path, manifest: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    pages: List[Tuple[str, Dict[str, Any]]] = []
    for entry in manifest.get("pages") or []:
        rel = str(entry)
        path = (root / rel).resolve()
        if not path.is_file():
            raise RuntimeError(f"deck.pptd 引用了不存在的页: {rel}")
        data = _load_yaml(path)
        if not isinstance(data, dict):
            raise RuntimeError(f"无法解析页文件: {rel}")
        pages.append((rel, data))
    if not pages:
        raise RuntimeError("deck.pptd 没有 pages")
    return pages


def qa_project(source: Path) -> List[str]:
    root, _manifest_path, manifest = resolve_project(source)
    pages = load_pages(root, manifest)
    design_text = _design_text(root)
    scene = _design_value(SCENE_RE, design_text)
    style = _design_value(STYLE_RE, design_text)
    tone = _design_value(TONE_RE, design_text)
    motion = _design_value(MOTION_RE, design_text)
    issues: List[str] = []
    space_topic = space_topic_of(root, manifest)

    schema_issues = [issue for rel, page in pages for issue in _schema_issues(rel, page)]
    if schema_issues:
        issues.append("页面不是 PPTD v2。不要混用旧技能格式：\n  - " + "\n  - ".join(schema_issues[:8]))

    missing_media: List[str] = []
    for rel, page in pages:
        for el in page.get("elements") or []:
            if not isinstance(el, dict):
                continue
            if el.get("elementType") != "image":
                continue
            src = _src(el)
            if not src or src.startswith(("http://", "https://", "data:")):
                continue
            if not (root / src).is_file():
                missing_media.append(f"{rel} → {src}")
    if missing_media:
        issues.append("配图文件不存在（先下到 media/ 再导出）:\n  - " + "\n  - ".join(missing_media[:8]))

    unsafe_fonts = []
    for rel, page in pages:
        for el in page.get("elements") or []:
            if not isinstance(el, dict) or el.get("elementType") != "text":
                continue
            if not CJK_RE.search(_texts(el)):
                continue
            east_asia = _east_asia_font(el)
            if east_asia.lower() not in PORTABLE_EA_FONTS:
                unsafe_fonts.append(f"{rel} → {east_asia or '未显式指定 ea'}")
    if unsafe_fonts:
        issues.append(
            "中文文字必须显式使用可移植东亚字体 `Microsoft YaHei`；"
            "沙箱会映射到 Noto CJK，PowerPoint/WPS 终端也能稳定回退。\n  - "
            + "\n  - ".join(unsafe_fonts[:8])
        )

    if not design_text:
        issues.append("缺少 DESIGN.md。写页前必须先完成视觉导演和逐页 storyboard。")
    else:
        missing_labels = [label for label in DESIGN_REQUIRED_LABELS if label not in design_text]
        if missing_labels:
            issues.append("DESIGN.md 缺少必填项：" + "、".join(missing_labels))
        storyboard_pages = set(re.findall(r"(?m)^\s*P(\d+)\s*\|", design_text, re.I))
        if len(storyboard_pages) < min(len(pages), 4):
            issues.append(
                "DESIGN.md 的页面节奏没有逐页 storyboard；至少写出 P1..P4 的"
                "页型、主视觉和读者任务。"
            )
        if re.search(r"设计模式\s*[：:]\s*reference", design_text, re.I) and "参考 DNA" not in design_text:
            issues.append("参考图模式必须在 DESIGN.md 记录“参考 DNA”和不得复制项。")
    if not style:
        issues.append("DESIGN.md 未声明风格族；不允许默认回落电影黑底。")
    if tone not in {"light", "dark", "color"}:
        issues.append("DESIGN.md 的基调必须是 light / dark / color 之一。")

    uncovered_fullbleed = [rel for rel, page in pages if _fullbleed_text_without_veil(page)]
    if uncovered_fullbleed:
        issues.append(
            "满幅图上有文字但缺少深色遮罩，会出现白底白字/局部对比度崩坏。"
            "在 hero 图之后、文字之前加全幅深色 rect，opacity 0.35–0.60： "
            + ", ".join(uncovered_fullbleed[:8])
        )

    sparse_content_pages: List[str] = []
    for rel, page in pages:
        elements = [item for item in page.get("elements") or [] if isinstance(item, dict)]
        text_count = sum(
            1 for item in elements
            if item.get("elementType") == "text" and _texts(item).strip()
        )
        substantive_count = sum(
            1 for item in elements
            if item.get("elementType") in {"image", "chart", "table", "diagram"}
        )
        if str(page.get("pageType") or "content").lower() != "cover" and text_count < 3 and substantive_count == 0:
            sparse_content_pages.append(f"{rel} → 仅 {text_count} 个有效文本元素")
    if sparse_content_pages:
        issues.append(
            "内容页不得只有标题或大面积空白；这类空页视觉模型可能误判为极简。"
            "补齐图解节点、证据、正文或拆页：\n  - " + "\n  - ".join(sparse_content_pages[:8])
        )

    n = len(pages)
    chromes = [chrome_of(page) for _rel, page in pages]
    fullbleed = sum(1 for c in chromes if c == "fullbleed")
    unique = len(set(chromes))
    dark_pages = sum(1 for _rel, page in pages if _is_dark_background(page))
    if tone in {"light", "color"} and dark_pages > max(2, int(n * 0.60)):
        issues.append(
            f"DESIGN.md 选择基调 {tone}，但 {dark_pages}/{n} 页仍是近黑背景。"
            "这是默认黑色模板回落，需按选定方向重建色彩与影像节奏。"
        )

    if style == "cinematic-editorial":
        need_full = 3 if n >= 8 else (1 if n >= 4 else 0)
    elif style in PHOTO_FIRST_STYLES:
        need_full = 1 if n >= 5 else 0
    else:
        need_full = 0
    if fullbleed < need_full:
        issues.append(
            f"风格族 {style} 需要至少 {need_full} 张满幅主视觉页，现在只有 {fullbleed}。"
        )

    split_pages = sum(1 for c in chromes if c in {"split_left", "split_right"})
    if split_pages > max(2, n // 3):
        issues.append(
            f"左右分屏页有 {split_pages}/{n}，占比过高。改用满幅、数据、图解、"
            "巨号数字或低密度呼吸页建立节奏。"
        )

    run = 1
    for i in range(1, len(chromes)):
        if chromes[i] == chromes[i - 1]:
            run += 1
            if run >= 3:
                issues.append(
                    f"连续 {run} 页同一构图（{chromes[i]}）。同一顶栏/分屏骨架不得连续超过 2 页。"
                )
                break
        else:
            run = 1

    if n >= 8 and unique < 4:
        issues.append(
            f"8 页以上至少 4 种页型，现在只有 {unique} 种（{sorted(set(chromes))}）。"
        )

    card_pages = sum(1 for c in chromes if c == "card_grid")
    card_limit = 1 if n < 7 else max(1, int(n * 0.20))
    if card_pages > card_limit:
        issues.append(
            f"卡片宫格页 {card_pages}/{n} 超过上限 {card_limit}。用大图、图表、"
            "结构图或留白页替换，不要把卡片当成默认容器。"
        )

    image_sources = [
        _src(el) for _rel, page in pages for el in page.get("elements") or []
        if isinstance(el, dict) and el.get("elementType") == "image" and _src(el)
    ]
    substantive_sources = [
        src for src in image_sources
        if not re.search(r"(^|[/_.-])(icon|logo|avatar|badge|qr)([/_.-]|$)", src, re.I)
    ]
    image_count = len(substantive_sources)
    raw_image_count = len(image_sources)
    if style in PHOTO_FIRST_STYLES:
        need_images = 6 if n >= 8 else (3 if n >= 4 else 0)
    else:
        need_images = 2 if n >= 8 else 0
    if image_count < need_images:
        issues.append(
            f"风格族 {style} 的实质图片只有 {image_count} 张"
            f"（全部 image 元素 {raw_image_count}），{n} 页至少要 {need_images} 张。"
            "用真实产品、人物、场景、界面或证据图；图标和 Logo 不算。"
        )

    # 图片出现次数不等于不同素材数。按文件内容指纹计数，改文件名也不能绕过；
    # 这会拦住封面、场景页、收束页用同一张图三连的廉价复用。
    local_assets: List[Tuple[str, Path, str]] = []
    for src in substantive_sources:
        if src.startswith(("http://", "https://", "data:")):
            continue
        path = (root / src).resolve()
        if not path.is_file() or path.suffix.lower() not in RASTER_SUFFIXES:
            continue
        try:
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        local_assets.append((src, path, fingerprint))

    fingerprint_counts = Counter(item[2] for item in local_assets)
    unique_image_count = len(fingerprint_counts)
    need_unique_images = 4 if n >= 8 and style in PHOTO_FIRST_STYLES else 0
    if need_unique_images and unique_image_count < need_unique_images:
        issues.append(
            f"{n} 页照片主导稿至少要 {need_unique_images} 张不同的有效真图，"
            f"按文件内容去重后只有 {unique_image_count} 张。缺图页改用原生图解/数据页，"
            "不要重复同一主视觉凑数。"
        )

    overused = [
        next(src for src, _path, fp in local_assets if fp == fingerprint)
        for fingerprint, count in fingerprint_counts.items() if count > 2
    ]
    if overused:
        issues.append(
            "同一原图最多出现 2 页；以下素材被重复 3 次以上： "
            + ", ".join(overused[:6])
        )

    low_resolution: List[str] = []
    if Image is not None:
        checked_fingerprints = set()
        for src, path, fingerprint in local_assets:
            if fingerprint in checked_fingerprints:
                continue
            checked_fingerprints.add(fingerprint)
            try:
                with Image.open(path) as image:
                    width, height = image.size
            except (OSError, ValueError):
                low_resolution.append(f"{src} → 无法读取像素尺寸")
                continue
            short_side, long_side = sorted((int(width), int(height)))
            if short_side < 600 or long_side < 900:
                low_resolution.append(f"{src} → {width}×{height}")
    if low_resolution:
        issues.append(
            "主视觉/分屏照片分辨率不足（短边至少 600px、长边至少 900px），"
            "低清截图会直接破坏高级感：\n  - " + "\n  - ".join(low_resolution[:8])
        )

    animation_gaps = [rel for rel, page in pages if not _has_required_animation(page)]
    if motion == "on" and animation_gaps:
        issues.append(
            "DESIGN.md 选择了动效 on，有文字的页面必须有入场动画，"
            "首组 trigger 要是 afterPrevious： "
            + ", ".join(animation_gaps[:8])
        )

    wallpaper_hits: List[str] = []
    for rel, page in pages:
        background_src = _background_src(page)
        if background_src and WALLPAPER_RE.search(background_src):
            wallpaper_hits.append(f"{rel} → background: {background_src}")
        for el in page.get("elements") or []:
            if not isinstance(el, dict):
                continue
            src = _src(el)
            if src and WALLPAPER_RE.search(src):
                wallpaper_hits.append(f"{rel} → {src}")
    if wallpaper_hits and not space_topic:
        issues.append(
            "非航天/天文主题禁止星空/星云/雾气/粒子墙纸。换人物/产品/城市/界面真图；"
            "高级感来自主题相关主视觉，不来自通用深色背景。\n  - " + "\n  - ".join(wallpaper_hits[:6])
        )
    if space_topic and len(wallpaper_hits) > 1:
        issues.append(
            "航天/天文主题最多 1 张带星空语义的真实任务图，不能反复把它当墙纸：\n  - "
            + "\n  - ".join(wallpaper_hits[:6])
        )

    source_pages = []
    for idx, (rel, page) in enumerate(pages):
        blob = " ".join(_texts(el) for el in page.get("elements") or [])
        if SOURCE_RE.search(blob) and idx < n - 1:
            source_pages.append(rel)
    if source_pages:
        issues.append("来源只放末页。内容页删掉「来源：」脚注： " + ", ".join(source_pages[:6]))

    return issues


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QA a PPTD deck before export")
    parser.add_argument("input", type=Path, help=".pptd or project directory")
    args = parser.parse_args(argv)
    try:
        issues = qa_project(args.input)
    except (RuntimeError, OSError) as exc:
        print(f"qa_deck failed: {exc}", file=sys.stderr)
        return 2
    if issues:
        print("QA 未通过，禁止导出。按下面改页后再跑 export_pptx.py：", file=sys.stderr)
        for i, issue in enumerate(issues, 1):
            print(f"\n[{i}] {issue}", file=sys.stderr)
        return 1
    print("qa_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
