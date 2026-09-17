#!/usr/bin/env python3
"""Build and validate a portable `.axiomskin` file from a skin source directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


AGENT_API_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_API_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_API_ROOT))

from app.services.campus_assistant.main_chat_skin_package import (  # noqa: E402
    MainChatSkinPackageError,
    build_main_chat_skin_package_from_directory,
    parse_main_chat_skin_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 manifest.json + assets/ 构建成可跨系统导入的主对话皮肤包",
    )
    parser.add_argument("source", type=Path, help="皮肤源码目录")
    parser.add_argument("output", type=Path, help="输出 .axiomskin 文件")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if output.suffix.lower() != ".axiomskin":
        parser.error("输出文件必须使用 .axiomskin 扩展名")
    try:
        package = build_main_chat_skin_package_from_directory(source)
        parsed = parse_main_chat_skin_package(package)
    except MainChatSkinPackageError as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(package)
    print(
        f"已生成 {output} | {parsed.manifest['key']} {parsed.manifest['version']} | "
        f"{len(package)} bytes | sha256-content={parsed.content_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
