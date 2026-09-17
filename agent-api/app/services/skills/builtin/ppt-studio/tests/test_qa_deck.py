import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "qa_deck.py"
SPEC = importlib.util.spec_from_file_location("ppt_studio_qa_deck", MODULE_PATH)
assert SPEC and SPEC.loader
qa_deck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa_deck)


def _text(ea: str = "Microsoft YaHei") -> dict:
    return {
        "elementId": "title",
        "elementType": "text",
        "bounds": [60, 320, 760, 80],
        "content": {
            "fontFamily": {"latin": "Helvetica Neue", "ea": ea},
            "fontSize": 48,
            "color": "#F4F1EA",
            "text": "<p>主标题</p>",
        },
    }


def test_fullbleed_text_requires_dark_veil() -> None:
    page = {
        "elements": [
            {
                "elementId": "hero",
                "elementType": "image",
                "bounds": [0, 0, 960, 540],
                "src": "media/hero.jpg",
            },
            _text(),
        ],
    }

    assert qa_deck._fullbleed_text_without_veil(page) is True


def test_fullbleed_text_accepts_bundled_cinematic_veil() -> None:
    page = {
        "elements": [
            {
                "elementId": "hero",
                "elementType": "image",
                "bounds": [0, 0, 960, 540],
                "src": "media/hero.jpg",
            },
            {
                "elementId": "veil",
                "elementType": "shape",
                "shapeName": "rect",
                "bounds": [0, 0, 960, 540],
                "fill": "#07080A",
                "opacity": 0.45,
            },
            _text(),
        ],
    }

    assert qa_deck._fullbleed_text_without_veil(page) is False


def test_portable_east_asia_font_is_the_office_name() -> None:
    assert qa_deck._east_asia_font(_text()) == "Microsoft YaHei"
    assert qa_deck._east_asia_font(_text("Noto Sans CJK SC")).lower() in qa_deck.PORTABLE_EA_FONTS


def test_dark_background_detection_distinguishes_light_and_dark() -> None:
    assert qa_deck._is_dark_background({"background": {"color": "#080A0F"}}) is True
    assert qa_deck._is_dark_background({"background": {"color": "#F4F1EA"}}) is False


def test_layout_classifier_does_not_collapse_gallery_into_generic_split() -> None:
    page = {
        "elements": [
            {"elementType": "image", "bounds": [40, 80, 400, 360], "src": "a.jpg"},
            {"elementType": "image", "bounds": [500, 80, 400, 360], "src": "b.jpg"},
        ]
    }
    assert qa_deck.chrome_of(page) == "gallery"
