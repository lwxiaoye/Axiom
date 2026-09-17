#!/usr/bin/env python3
"""Build a deterministic portable `.axiomskin` for `/agent/run/:appId`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


AGENT_API_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_API_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_API_ROOT))

from app.services.workflows.sub_agent_skin_package import (  # noqa: E402
    build_sub_agent_skin_package_from_directory,
    parse_sub_agent_skin_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a safe portable sub-agent run-page skin package",
    )
    parser.add_argument("source", type=Path, help="Directory containing manifest.json and assets/")
    parser.add_argument("output", type=Path, help="Output .axiomskin path")
    args = parser.parse_args()

    package = build_sub_agent_skin_package_from_directory(args.source.resolve())
    parsed = parse_sub_agent_skin_package(package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(package)
    print(
        f"built {args.output} key={parsed.manifest['key']} "
        f"version={parsed.manifest['version']} sha256={parsed.content_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
