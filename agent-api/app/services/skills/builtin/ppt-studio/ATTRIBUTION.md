# Upstream attribution

- Upstream repository: https://github.com/jinwyp/open-ppt-skill
- Upstream skill: `skills/open-kimi-ppt`
- Pinned commit: `07eeaadcb04c32c9adb107eb5c8608e6be4e1008`
- License: MIT, included in `LICENSE`

The `SKILL.md` authoring body, `reference/`, `scripts/export_images.py`, and the
local PPTD export implementation are vendored from that revision. The
frontmatter uses the platform routing alias `ppt-studio`; the authoring body is
unchanged. Runtime compatibility patches are limited to the server boundary:
resolve the preinstalled exporter/WASM under `/opt/open-kimi-ppt/`, normalize
the concise `type/x/y/w/h` element dialect into canonical PPTD v2 before WASM
export, and render QA images offline with LibreOffice/Poppler when npm/browser
automation is unavailable. The upstream design method remains unchanged.
