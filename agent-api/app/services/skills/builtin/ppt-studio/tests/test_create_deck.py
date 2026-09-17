import importlib.util
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


create_deck = _load("ppt_create_deck", ROOT / "scripts" / "create_deck.py")
qa_deck = _load("ppt_qa_deck", ROOT / "scripts" / "qa_deck.py")


def test_eight_page_cinematic_scaffold_passes_static_qa(tmp_path: Path) -> None:
    project = tmp_path / "orbital"
    media = project / "media"
    media.mkdir(parents=True)
    for index in range(1, 8):
        Image.new("RGB", (1200, 800), (16 * index, 24, 34)).save(media / f"p{index}.jpg")

    spec = {
        "title": "ORBITAL FRONTIER 可重复使用运载系统",
        "scene": "tech",
        "style_family": "cinematic-editorial",
        "tone": "dark",
        "visual_motif": "暖橙色发射弧光穿过深蓝黑天际线",
        "pages": [
            {"layout": "cover", "title": "ORBITAL FRONTIER", "subtitle": "可重复使用运载系统", "image": "media/p1.jpg"},
            {"layout": "fullbleed", "title": "让轨道不再遥远", "subtitle": "从单次发射到稳定运力", "image": "media/p2.jpg"},
            {"layout": "split_left", "title": "三段协同", "body": ["一级返回", "二级入轨", "地面快速周转"], "image": "media/p3.jpg"},
            {"layout": "split_right", "title": "热结构核心", "body": ["发动机", "热防护", "智能导航"], "image": "media/p4.jpg"},
            {"layout": "giant", "stat": "24h", "title": "目标周转周期", "subtitle": "从回收到再次起飞"},
            {"layout": "gallery", "title": "从沿海发射到轨道服务", "images": ["media/p5.jpg", "media/p6.jpg"], "captions": ["发射场", "海上回收"]},
            {"layout": "timeline", "title": "三步建立运力", "items": [{"year": "2027", "text": "完成验证"}, {"year": "2029", "text": "实现复用"}, {"year": "2032", "text": "规模运营"}]},
            {"layout": "closing", "title": "NEXT ORBIT", "subtitle": "每一次返回，都为下一次出发", "image": "media/p7.jpg"},
        ],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    create_deck.main([str(spec_path), str(project)])

    assert qa_deck.qa_project(project / "deck.pptd") == []


def test_missing_page_image_fields_are_filled_from_media_pool(tmp_path: Path) -> None:
    project = tmp_path / "autofill"
    media = project / "media"
    media.mkdir(parents=True)
    for index in range(4):
        Image.new("RGB", (1200, 800), (30 + index * 20, 40, 50)).save(media / f"asset{index}.jpg")
    spec = {
        "title": "Autofill",
        "pages": [
            {"layout": "cover", "title": "Cover"},
            {"layout": "fullbleed", "title": "Vision"},
            {"layout": "split_left", "title": "System", "body": ["A", "B"]},
            {"layout": "split_right", "title": "Tech", "body": ["C", "D"]},
            {"layout": "giant", "stat": "24h", "title": "Metric"},
            {"layout": "gallery", "title": "Scenes"},
            {"layout": "timeline", "title": "Roadmap", "items": [{"year": "2027", "text": "Go"}]},
            {"layout": "closing", "title": "End"},
        ],
    }
    create_deck.build(spec, project)
    assert qa_deck.qa_project(project / "deck.pptd") == []


def test_unspecified_tone_defaults_to_light_editorial_palette() -> None:
    theme = create_deck._theme_for_spec({"style_family": "premium-editorial"})
    assert theme["preset_id"] == "editorial-silver"
    assert theme["background"] == "#F6F6F3"
    assert theme["foreground"] == "#171717"


def test_dark_palette_requires_explicit_dark_direction() -> None:
    theme = create_deck._theme_for_spec({"tone": "dark"})
    assert theme["preset_id"] == "night-copper"
    assert theme["background"] == "#0C0D0F"
    custom = create_deck._theme_for_spec({"tone": "light", "theme": {"accent": "#00AEEF"}})
    assert custom["background"] == "#F6F6F3"
    assert custom["accent"] == "#00AEEF"


def test_flash_style_presets_are_scene_aware_and_explicitly_selectable() -> None:
    tech = create_deck._theme_for_spec({"scene": "tech"})
    consulting = create_deck._theme_for_spec({"scene": "analysis"})
    named = create_deck._theme_for_spec({"scene": "tech", "preset": "cultural-cinnabar"})

    assert tech["preset_id"] == "tech-cobalt"
    assert tech["accent"] == "#176BFF"
    assert consulting["preset_id"] == "consulting-red"
    assert consulting["accent"] == "#C91920"
    assert named["preset_id"] == "cultural-cinnabar"
    assert named["display_latin_font"] == "Georgia"


def test_editorial_preset_applies_display_font_only_to_headlines(tmp_path: Path) -> None:
    project = tmp_path / "display-font"
    create_deck.build({
        "title": "Editorial",
        "preset": "editorial-silver",
        "pages": [{
            "layout": "statement",
            "title": "A considered headline",
            "subtitle": "Body copy remains neutral and portable.",
        }],
    }, project)
    page = __import__("yaml").safe_load((project / "pages/01.page").read_text(encoding="utf-8"))
    by_id = {item["elementId"]: item for item in page["elements"]}
    assert by_id["title"]["content"]["fontFamily"]["latin"] == "Georgia"
    assert by_id["body"]["content"]["fontFamily"]["latin"] == "Helvetica Neue"


def test_light_deck_photo_pages_use_dark_veil_and_light_text(tmp_path: Path) -> None:
    project = tmp_path / "photo-contrast"
    media = project / "media"
    media.mkdir(parents=True)
    Image.new("RGB", (1200, 800), (30, 40, 50)).save(media / "hero.jpg")
    create_deck.build({
        "title": "Light editorial",
        "tone": "light",
        "pages": [{
            "layout": "cover",
            "title": "AURORA",
            "subtitle": "下一代城市空中交通",
            "image": "media/hero.jpg",
        }],
    }, project)
    page = __import__("yaml").safe_load((project / "pages/01.page").read_text(encoding="utf-8"))
    by_id = {item["elementId"]: item for item in page["elements"]}
    assert by_id["veil"]["fill"] == "#10151C"
    assert by_id["title"]["content"]["color"] == "#F7F5EF"
    assert by_id["subtitle"]["content"]["color"] == "#D2D7DE"


def test_editorial_cover_and_statement_do_not_require_photo_background(tmp_path: Path) -> None:
    project = tmp_path / "editorial-native"
    create_deck.build({
        "title": "Editorial",
        "tone": "light",
        "style_family": "editorial-minimal",
        "pages": [
            {"layout": "editorial_cover", "title": "AURORA", "subtitle": "轻量封面"},
            {"layout": "statement", "title": "城市向上生长", "subtitle": "排版本身承担视觉", "stat": "01"},
        ],
    }, project)
    cover = __import__("yaml").safe_load((project / "pages/01.page").read_text(encoding="utf-8"))
    statement = __import__("yaml").safe_load((project / "pages/02.page").read_text(encoding="utf-8"))
    assert not any(item["elementType"] == "image" for item in cover["elements"])
    assert not any(item["elementType"] == "image" for item in statement["elements"])
    assert any(item["elementId"] == "accent_rule" for item in cover["elements"])
    assert any(item["elementId"] == "marker" for item in statement["elements"])


def test_editorial_rules_and_timeline_ticks_are_not_misclassified_as_cards(tmp_path: Path) -> None:
    project = tmp_path / "not-cards"
    create_deck.build({
        "title": "Native layouts",
        "pages": [
            {"layout": "editorial_cover", "title": "Cover"},
            {"layout": "diagram", "title": "System", "items": ["A", "B", "C"]},
            {"layout": "timeline", "title": "Roadmap", "items": [
                {"year": "01", "text": "A"}, {"year": "02", "text": "B"},
                {"year": "03", "text": "C"}, {"year": "04", "text": "D"},
            ]},
        ],
    }, project)
    pages = [
        __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((project / "pages").glob("*.page"))
    ]
    assert [qa_deck.chrome_of(page) for page in pages] == ["diagram", "diagram", "diagram"]


def test_diagram_accepts_flash_body_list_and_does_not_export_blank_page(tmp_path: Path) -> None:
    project = tmp_path / "diagram-body"
    create_deck.build({
        "title": "Diagram body fallback",
        "pages": [{
            "layout": "diagram",
            "title": "三道质量门禁",
            "body": ["结构校验", "视觉复检", "真实产物"],
        }],
    }, project)
    page = __import__("yaml").safe_load((project / "pages/01.page").read_text(encoding="utf-8"))
    labels = {
        item["content"]["text"] for item in page["elements"]
        if item.get("elementId", "").startswith("item_")
    }
    assert len(labels) == 3
    assert any("视觉复检" in label for label in labels)


def test_static_qa_rejects_title_only_content_page(tmp_path: Path) -> None:
    project = tmp_path / "sparse-page"
    create_deck.build({
        "title": "Sparse",
        "pages": [{"layout": "diagram", "title": "只有标题"}],
    }, project)
    issues = "\n".join(qa_deck.qa_project(project / "deck.pptd"))
    assert "内容页不得只有标题" in issues


def test_static_qa_rejects_three_image_reuse_for_eight_page_photo_deck(tmp_path: Path) -> None:
    project = tmp_path / "three-images"
    media = project / "media"
    media.mkdir(parents=True)
    for index in range(3):
        Image.new("RGB", (1200, 800), (50 + index * 20, 60, 70)).save(media / f"asset{index}.jpg")
    spec = {
        "title": "Three images",
        "style_family": "cinematic-editorial",
        "pages": [
            {"layout": "cover", "title": "Cover"},
            {"layout": "fullbleed", "title": "Vision"},
            {"layout": "split_left", "title": "System", "body": ["A", "B"]},
            {"layout": "split_right", "title": "Tech", "body": ["C", "D"]},
            {"layout": "giant", "stat": "24h", "title": "Metric"},
            {"layout": "gallery", "title": "Scenes"},
            {"layout": "timeline", "title": "Roadmap", "items": [{"year": "2027", "text": "Go"}]},
            {"layout": "closing", "title": "End"},
        ],
    }
    create_deck.build(spec, project)

    issues = "\n".join(qa_deck.qa_project(project / "deck.pptd"))
    assert "至少要 4 张不同" in issues
    assert "重复 3 次以上" in issues


def test_static_qa_rejects_low_resolution_photo_assets(tmp_path: Path) -> None:
    project = tmp_path / "low-resolution"
    media = project / "media"
    media.mkdir(parents=True)
    for index in range(4):
        size = (640, 853) if index == 0 else (1200, 800)
        Image.new("RGB", size, (80 + index * 20, 90, 100)).save(media / f"asset{index}.jpg")
    spec = {
        "title": "Low resolution",
        "pages": [
            {"layout": "cover", "title": "Cover"},
            {"layout": "fullbleed", "title": "Vision"},
            {"layout": "split_left", "title": "System", "body": ["A"]},
            {"layout": "split_right", "title": "Tech", "body": ["B"]},
            {"layout": "giant", "stat": "24h", "title": "Metric"},
            {"layout": "gallery", "title": "Scenes"},
            {"layout": "timeline", "title": "Roadmap", "items": [{"year": "2027", "text": "Go"}]},
            {"layout": "closing", "title": "End"},
        ],
    }
    create_deck.build(spec, project)

    issues = "\n".join(qa_deck.qa_project(project / "deck.pptd"))
    assert "分辨率不足" in issues
    assert "640×853" in issues
