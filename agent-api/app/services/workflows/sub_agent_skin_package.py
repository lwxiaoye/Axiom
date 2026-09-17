"""Portable declarative package wrappers for `/agent/run/:appId` skins."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.services.campus_assistant.main_chat_skin_package import (
    ParsedMainChatSkinPackage,
    build_main_chat_skin_package,
    build_main_chat_skin_package_from_directory,
    parse_main_chat_skin_package,
)


PACKAGE_KIND = "axiom-sub-agent-skin"
PACKAGE_SCOPE = "sub_agent"
RENDERER_KEY = "decorated-agent-run-v1"


def parse_sub_agent_skin_package(package_bytes: bytes) -> ParsedMainChatSkinPackage:
    return parse_main_chat_skin_package(
        package_bytes,
        package_kind=PACKAGE_KIND,
        package_scope=PACKAGE_SCOPE,
        renderer_key=RENDERER_KEY,
    )


def build_sub_agent_skin_package(manifest: Mapping[str, Any], assets: Mapping[str, bytes]) -> bytes:
    return build_main_chat_skin_package(
        manifest,
        assets,
        package_kind=PACKAGE_KIND,
        package_scope=PACKAGE_SCOPE,
        renderer_key=RENDERER_KEY,
    )


def build_sub_agent_skin_package_from_directory(source_dir: Path) -> bytes:
    return build_main_chat_skin_package_from_directory(
        source_dir,
        package_kind=PACKAGE_KIND,
        package_scope=PACKAGE_SCOPE,
        renderer_key=RENDERER_KEY,
    )
