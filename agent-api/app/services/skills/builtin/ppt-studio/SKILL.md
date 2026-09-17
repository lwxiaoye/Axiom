---
name: ppt-studio
description: Author editable PPTD in the sandbox, export PPTX with run_export.py, and deliver only via publish_ppt_artifact. Do not probe Node/npm, do not wait on Chromium visual QA, and do not ship a PPTD source ZIP.
---

# Definition

open-kimi-ppt authors presentations as a YAML PPTD project (`.pptd` + `pages/` + `media/`) and exports a matching `.pptx`. Read `reference/pptd.md` only when you need a field you do not already know.

In this platform the **user-visible output is the PPTX in 我的文件**. Keep the complete editable PPTD project directory in the sandbox for authoring. Do not zip, publish, or list that source tree in the final answer.

# PPT production workflow

Follow these steps in order. Every step names a tool that exists in this sandbox. If a step's tool is missing or fails, use the skip rule — never invent a substitute pipeline (no python-pptx, no npx, no Chromium, no kimi.com).

Fixed paths (copy from the injection header; never `ls`/`find` `/workspace/skills`):

- `SKILL_DIR` = the injected `沙箱精确目录` (example `/workspace/skills/<slug>/`). Toolkit only: export scripts, recipes, references. Do not copy it into the project.
- `PROJECT` = `/workspace/tmp/ppt-project`. This is the editable PPTD working copy (session workspace, pulled each Run).
- Prefer `read_file` / `edit_file` / `write_file` on `PROJECT` files (`pages/*.page`, `DESIGN.md`, `deck.pptd`). `bash` still works. There is no `apply_patch`. Do not write to 「我的文件」.

```text
PROJECT/
  DESIGN.md
  deck.pptd
  pages/*.page
  media/*          # fetch_ppt_asset, workspace drawer, and chat-input uploads
  deck.pptx        # from run_export.py
```

### step0. Use the platform runtime

AXIOM has already validated and mounted the presentation runtime. Do not probe Node.js, npm, npx, Python, browser, dependency versions, PATH, sandbox, or package status before authoring. Use the mounted `scripts/run_export.py` wrapper directly. Runtime commands, paths, stdout/stderr, exit codes, intermediate files, and dependency details are internal observations; never copy them into public progress or the final answer. If export actually fails, describe only the user-visible impact and continue with every deliverable that can still be completed.

The sandbox does not provide an `apply_patch` command. Create and update `.pptd`, `.page`, and supporting project files with `write_file` / `edit_file` (or `bash`). PPTD is the required authoring backend: never bypass this workflow with python-pptx or another presentation generator.

Do not call `use_skill` again if this skill is already injected. Do not re-read this SKILL.md from disk.

### step1. Decide, then read only what this deck needs

Tool: `update_plan` if present (use only 3–4 steps: design+pages / export / publish). If `update_plan` is not in the tool list, skip it and keep working. Do not rewrite a valid plan merely to repeat more words from the user request.

Page count: user number wins; otherwise match an outline, or pick 6–10 pages for a topic deck. **Do not ask the user to confirm page count and wait.**

Read at most the references this deck needs (not the whole skill tree):

- Always, if you still need the DSL: read `reference/pptd.md` from `SKILL_DIR` (skip if you already know v2 fields).
- New deck or substantial redesign: `reference/visual-direction.md`.
- Person, athlete, founder, executive, biography, career, or memorial: **also** `recipes/person-profile.md` and `recipes/cinematic-keynote.md`. Use `examples/cinematic/` as composition references; do not rename one repeated template.
- Report / teaching / academic if the user asked for that: the matching recipe instead of cinematic dark pages.
- Self-directed design also uses `reference/slides_categories.md` and the scenario recipe that matches the query.

Ask the user only when files/URLs are inaccessible or intents truly contradict. Ambiguous taste is not a blocker — pick a direction, write it into `DESIGN.md`, and proceed.

### step2. Collect photos only when the content needs them

When a page needs a real person, product, place, building, event, or screenshot:

1. `search_web` for candidates.
2. `fetch_ppt_asset(url="图N", filename="semantic-name")` into `PROJECT/media/`.
3. Reference only `media/<filename>` in `.page` files. Never put `http://` or `https://` in `src`.

If search is down, blocked, or the plan cursor is `investigate`: **skip photos and write the pages anyway**. Do not loop on search probes. User-supplied photos from the workspace drawer **or the chat input** land in `PROJECT/media/` and win over search. Do not look for them under `/workspace/files/` or copy the skill tree into the project.

### step3. Author the PPTD project

Before writing any `.page` file, create `DESIGN.md` inside the project. Record:

1. a concise visual direction: purpose, audience, reference DNA, image treatment, typography (exact font families, weights, and sizes), palette, grid, and density;
2. a one-line storyboard for every page: page role, primary claim, visual evidence, composition skeleton, and intended audience effect.

Authoring rules:

- One claim per page. A photo is narrative evidence for that claim, not a mandatory wallpaper or a decorative right-hand panel.
- In decks of eight or more pages, use at least four composition skeletons, including one hero visual and one low-density breathing page. Adjacent pages must not repeat the same skeleton.
- Keep the deck in one coherent visual world while varying image scale, text density, alignment, and pacing. Do not achieve consistency by repeating the same split-screen, top bar, card grid, or photo-background layout.
- `bounds` is always `[x, y, width, height]`, never an object. Rectangles are `elementType: shape` + `shapeName: rect`. Images use `fit: {mode: cover}` (or `contain`). Horizontal/vertical rules must have at least 1 px width and height; a zero-height line fails publish lint.
- Theme text-style references belong **inside** `content` and must use the `$name` form, for example `content.style: "$title"`. Putting `style` on the element, or writing bare `style: title`, is ignored by the exporter and silently falls back to default black MiSans. Page `background` must be `{type: solid, color: "#..."}`; a lone `color` field becomes white.
- Manifest: `version: v2`, `pages` as relative path strings. Every element: `elementId`, `elementType`, `bounds`. Text `content` is an object with `text`, not another DSL.
- Escape literal `<` in rich text as `&lt;` (example `600 → &lt;100`). Single-line KPI/titles: `wrap: false` with enough box size. The platform image guarantees `Noto Sans CJK SC` and `Noto Serif CJK SC`; use those exact names by default and do not spend a tool call probing fonts. Declare at least two `theme.textStyles` roles with explicit `fontFamily`.
- Write several `.page` files with `write_file` (or bash) when possible.

### step4. Export PPTX (mandatory; do not wait for visual QA)

Tool: `bash`. Done when `PROJECT/deck.pptx` exists and is a non-empty ZIP.

```bash
python3 "$SKILL_DIR/scripts/run_export.py" "$PROJECT" \
  --output "$PROJECT/deck.pptx" --force
```

`$SKILL_DIR/scripts/run_export.py` forwards to the same package's `scripts/export_pptx.py` (local WASM). Pass the project directory or the `.pptd` manifest. Always pass `--force` so a retry can overwrite.

Do **not** pass `--browser`. Do **not** run `npx`, `npm`, Chromium, `agent-browser`, or `open-kimi-ppt-skill serve`. Those paths are for a local laptop, not this sandbox.

If this command fails, stop inventing exporters and report the user-visible blocker. Do not start a second deck from scratch. Do **not** `find /` or copy a random `.wasm`; the engine is `SKILL_DIR/scripts/local-export/pptd_wasm_bg.wasm` (platform-injected). Missing engine means this sandbox cannot export.

### step5. Optional self page-image QA (skip by default; must not block publish)

Tool: `bash`, then read images only if the model can see them.

```bash
python3 "$SKILL_DIR/scripts/export_images.py" "$PROJECT" \
  --output "$PROJECT/.qa-images" --force
```

This sandbox uses LibreOffice + pdftoppm. It does not need npm or a browser. If the command fails, skip QA and go to step6.

Use this only when the model can actually inspect the rendered pages and has a concrete visual uncertainty. Otherwise skip directly to publish. If images exist, check overflow, collisions, contrast, and text-on-faces. Fix issues in the corresponding `.page` file, then re-run `run_export.py --force`. Do **not** rebuild the deck because visual QA failed. The platform does not call a second visual model for PPT publish.

### step6. Publish to 我的文件 (the only user delivery)

Tool: `publish_ppt_artifact`. Done when it returns success (or success-with-warning). Until then, do not claim the file is delivered.

Call:

- `project_dir`: `/workspace/tmp/ppt-project`
- `pptx_path`: `/workspace/tmp/ppt-project/deck.pptx`
- `filename`: a user-readable `.pptx` name (no directories)

ZIP / page-count / layout lint failure: fix the `.page` files, re-export, publish again. The platform does not generate aesthetic or quality scores. Do not restart the deck for subjective polish.

`publish_ppt_artifact` is the only canonical verification call. Do not run manual `unzip`, python-pptx/XML coordinate dumps, duplicate font probes, or a second layout audit before it. The publisher already performs those deterministic checks and returns the exact repair if one fails.

Public reply: results only. No `/workspace` paths, no script names, no PPTD zip.

# Design method

Determine purpose first: create, edit, or replicate. Then design direction: self-directed, an explicit design system under `reference/design_system/`, a user template, or style transfer. Then input type: topic, full document, or outline. Expand a thin outline with search unless the user forbids it; if search fails, write from what you have.

#### Replicating a PPT

Estimate positions and type from the reference images and replicate as closely as possible. Crop user images with bash/python when needed. Approximate icons with Font Awesome; do not fake photos.

#### Editing a PPT

Convert the uploaded pptx into PPTD in `PROJECT`, review key pages, and edit only the requested scope.

#### Generating a PPT

Self-directed: write `DESIGN.md` before skeletons or media. Images may be full-bleed, edge-aligned, isolated, collaged, or absent when a typographic/data page is stronger.

Infographic / poster / single-page visual: read `reference/general-poster.md` and still author as PPTD. Do not load that file for ordinary decks.

Design system: only when the user named a preset; do not mix other styles; do not auto-pick a preset.

Template / style transfer: extract real fonts, sizes, density, and at least four skeletons into `DESIGN.md`. Preserve the source hierarchy; if a font is missing, record the substitute.

#### Images and visual materials

Use images to show a subject, explain, evidence, or set a scene. Logos and decorative textures do not count. Priority: user files, official sources, searched photos via `fetch_ppt_asset`, then generated atmosphere only if real photos cannot be obtained. Do not add irrelevant images to hit a quota. If the user asked for real/game/on-site photos, illustrations and bash-made JPEGs are not substitutes.

#### Content guidelines

Unless the user asks otherwise, avoid abstract slogans, “not X but Y”, “key takeaway”, and overly colloquial filler.

Element animations (`page.animations`): only when the user asks, or the deck is clearly for live playback. Default PPTX still gets a fade **page** transition from the exporter. Speaker `notes`: only when asked.
