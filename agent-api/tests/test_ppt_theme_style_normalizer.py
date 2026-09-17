from __future__ import annotations

import json
import subprocess
from pathlib import Path


NORMALIZER = (
    Path(__file__).parents[1]
    / "app/services/skills/builtin/ppt-studio/scripts/local-export/normalize-theme-styles.mjs"
)


def _normalize(deck: dict) -> dict:
    script = f"""
import {{ normalizePptdThemeStyleRefsForWasm }} from {json.dumps(NORMALIZER.as_uri())};
const value = JSON.parse(process.argv[1]);
normalizePptdThemeStyleRefsForWasm(value);
process.stdout.write(JSON.stringify(value));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(deck)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_normalizer_repairs_known_bare_theme_refs_without_touching_unknown_refs():
    deck = {
        "theme": {"textStyles": {"hero": {"fontSize": 64}, "body": {"fontSize": 18}}},
        "pages": [{
            "elements": [
                {"elementType": "text", "content": {"style": "hero", "text": "Hero"}},
                {"elementType": "text", "content": {"style": "$body", "text": "Body"}},
                {"elementType": "text", "content": {"style": "missing", "text": "Unknown"}},
                {"elementType": "table", "rows": [[{"style": "body", "text": "Cell"}]]},
            ],
        }],
    }

    normalized = _normalize(deck)
    elements = normalized["pages"][0]["elements"]
    assert elements[0]["content"]["style"] == "$hero"
    assert elements[1]["content"]["style"] == "$body"
    assert elements[2]["content"]["style"] == "missing"
    assert elements[3]["rows"][0][0]["style"] == "$body"


def test_normalizer_is_idempotent():
    deck = {
        "theme": {"textStyles": {"title": {"fontSize": 44}}},
        "pages": [{"elements": [
            {"elementType": "text", "content": {"style": "title", "text": "Title"}},
        ]}],
    }
    once = _normalize(deck)
    assert _normalize(once) == once


def test_normalizer_lifts_element_style_and_fills_solid_background():
    deck = {
        "theme": {"textStyles": {"title": {"fontSize": 36, "color": "#151515"}}},
        "pages": [{
            "background": {"color": "#F2EFE8"},
            "elements": [
                {
                    "elementType": "text",
                    "style": "$title",
                    "content": {"text": "节能减排"},
                },
            ],
        }],
    }
    normalized = _normalize(deck)
    page = normalized["pages"][0]
    assert page["background"] == {"type": "solid", "color": "#F2EFE8"}
    assert page["elements"][0]["content"]["style"] == "$title"
    assert page["elements"][0]["content"]["text"] == "节能减排"
