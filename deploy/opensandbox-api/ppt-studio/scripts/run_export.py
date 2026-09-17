#!/usr/bin/env python3
"""Thin wrapper: resolve skill dir and forward to scripts/export_pptx.py.

Usage (in sandbox):
  python3 /workspace/skills/<slug>/scripts/run_export.py \\
    /workspace/tmp/ppt-xxx/deck.pptd \\
    --output /workspace/files/out.pptx --force
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    skill_dir = Path(__file__).resolve().parent.parent
    export_py = skill_dir / "scripts" / "export_pptx.py"
    wasm_candidates = (
        skill_dir / "scripts" / "local-export" / "pptd_wasm_bg.wasm",
        Path("/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm"),
    )
    if not export_py.is_file():
        print(f"[ppt-studio] missing {export_py}", file=sys.stderr)
        return 2
    if not any(path.is_file() for path in wasm_candidates):
        print(
            "[ppt-studio] missing wasm in skill package and /opt/open-kimi-ppt. "
            "Bake the engine into the sandbox image.",
            file=sys.stderr,
        )
        return 3
    # Node preflight
    try:
        out = subprocess.check_output(["node", "--version"], text=True, stderr=subprocess.STDOUT)
        major = int(out.strip().lstrip("v").split(".")[0])
        if major < 18:
            print(f"[ppt-studio] Node 18+ required, got {out.strip()}", file=sys.stderr)
            return 4
    except FileNotFoundError:
        print("[ppt-studio] node not found in PATH (need Node 18+ in sandbox image)", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"[ppt-studio] node check failed: {exc}", file=sys.stderr)
        return 4

    env = os.environ.copy()
    cmd = [sys.executable, str(export_py), *sys.argv[1:]]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
