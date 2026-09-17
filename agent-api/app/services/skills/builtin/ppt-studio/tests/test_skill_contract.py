import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL_PATH = ROOT / "SKILL.md"
SKILL_TEXT = SKILL_PATH.read_text(encoding="utf-8")
NORMALIZER = ROOT / "scripts/local-export/normalize-rich-text.mjs"


def _node_normalize(value):
    script = f"""
import {{ normalizeRichText, normalizePptdRichTextForWasm }} from {json.dumps(NORMALIZER.as_uri())};
const input = JSON.parse(process.argv[1]);
const output = input.kind === 'text'
  ? normalizeRichText(input.value)
  : normalizePptdRichTextForWasm(input.value);
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(value)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _load_package_builder():
    path = Path(__file__).parents[6] / "scripts" / "build_ppt_skill_package.py"
    spec = importlib.util.spec_from_file_location("build_ppt_skill_package", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_md_keeps_platform_alias_and_visual_quality_routing() -> None:
    assert SKILL_TEXT.startswith("---\nname: ppt-studio\n")
    assert "reference/visual-direction.md" in SKILL_TEXT
    assert "recipes/person-profile.md" in SKILL_TEXT
    assert "recipes/cinematic-keynote.md" in SKILL_TEXT
    assert "examples/cinematic/" in SKILL_TEXT
    assert "create `DESIGN.md`" in SKILL_TEXT
    assert "One claim per page" in SKILL_TEXT
    assert "at least four composition skeletons" in SKILL_TEXT
    assert "must use the `$name` form" in SKILL_TEXT


def test_agentic_authoring_contract_is_preserved() -> None:
    assert "read `reference/pptd.md`" in SKILL_TEXT
    assert "`reference/slides_categories.md`" in SKILL_TEXT
    assert "Fix issues in the corresponding `.page` file" in SKILL_TEXT
    assert "complete editable PPTD project directory" in SKILL_TEXT
    assert "create_deck.py" not in SKILL_TEXT
    assert "spec.json" not in SKILL_TEXT


def test_skill_md_export_loop_is_agent_executable() -> None:
    assert "publish_ppt_artifact" in SKILL_TEXT
    assert "scripts/run_export.py" in SKILL_TEXT
    assert "/workspace/tmp/ppt-project" in SKILL_TEXT
    assert "Do not export the PPTX until the visual review passes" not in SKILL_TEXT
    assert "npx open-kimi-ppt-skill serve" not in SKILL_TEXT
    assert "<exact-loaded-skill-slug>" not in SKILL_TEXT
    assert "confirm with the user" not in SKILL_TEXT
    assert "Needs Chromium" not in SKILL_TEXT
    assert "Do **not** `find /`" in SKILL_TEXT
    assert "`read_file` / `edit_file` / `write_file`" in SKILL_TEXT
    assert "no `write_file`" not in SKILL_TEXT
    assert "bounds` is always `[x, y, width, height]" in SKILL_TEXT
    assert "skip by default" in SKILL_TEXT
    assert "only canonical verification call" in SKILL_TEXT
    assert "Noto Sans CJK SC" in SKILL_TEXT


def test_platform_metadata_pins_the_upstream_commit_and_license() -> None:
    metadata = json.loads((ROOT / "skill.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "ppt-studio"
    assert metadata["version"] == "3.0.7"
    assert metadata["upstream"]["commit"] == "07eeaadcb04c32c9adb107eb5c8608e6be4e1008"
    assert "MIT License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_square_package_includes_quality_guides_without_old_template_generator() -> None:
    builder = _load_package_builder()
    rels = {path.relative_to(ROOT).as_posix() for path in builder.package_files()}
    assert "SKILL.md" in rels
    assert "scripts/export_pptx.py" in rels
    assert "scripts/export_images.py" in rels
    assert "scripts/run_export.py" in rels
    assert "scripts/local-export/normalize-theme-styles.mjs" in rels
    assert "scripts/local-export/normalize-rich-text.mjs" in rels
    assert "reference/pptd.md" in rels
    assert "reference/visual-direction.md" in rels
    assert "recipes/person-profile.md" in rels
    assert "recipes/cinematic-keynote.md" in rels
    assert "recipes/work-report.md" in rels
    assert "recipes/business-pitch.md" in rels
    assert "examples/cinematic/README.md" in rels
    assert "examples/cinematic/01_cover.page" in rels
    assert "examples/cinematic/02_split.page" in rels
    assert "examples/cinematic/03_giant_number.page" in rels
    assert "examples/cinematic/04_quote.page" in rels
    assert "scripts/create_deck.py" not in rels
    assert "scripts/qa_deck.py" not in rels
    assert not any(
        rel.startswith("examples/") and not rel.startswith("examples/cinematic/")
        for rel in rels
    )


def test_rich_text_normalizer_repairs_plain_shorthand_and_is_idempotent() -> None:
    cases = {
        "600 → <100": "<p>600 → &lt;100</p>",
        "A < B & C > D": "<p>A &lt; B &amp; C &gt; D</p>",
        "600 → &lt;100": "<p>600 → &lt;100</p>",
        "line 1\r\nline 2": "<p>line 1</p><p>line 2</p>",
        '<p data-x="a > b">600 → <100 &amp; more</p>': (
            '<p data-x="a > b">600 → &lt;100 &amp; more</p>'
        ),
    }
    for raw, expected in cases.items():
        normalized = _node_normalize({"kind": "text", "value": raw})
        assert normalized == expected
        assert _node_normalize({"kind": "text", "value": normalized}) == expected


def test_rich_text_normalizer_only_touches_text_elements_and_table_cells() -> None:
    deck = {
        "pages": [{
            "elements": [
                {"elementType": "text", "content": {"text": "A < B"}},
                {"elementType": "table", "rows": [[{"text": "600 → &lt;100"}]]},
                {"elementType": "chart", "title": {"text": "Revenue < Cost"}},
            ],
        }],
    }
    normalized = _node_normalize({"kind": "deck", "value": deck})
    assert normalized["pages"][0]["elements"][0]["content"]["text"] == "<p>A &lt; B</p>"
    assert normalized["pages"][0]["elements"][1]["rows"][0][0]["text"] == (
        "<p>600 → &lt;100</p>"
    )
    assert normalized["pages"][0]["elements"][2]["title"]["text"] == "Revenue < Cost"
