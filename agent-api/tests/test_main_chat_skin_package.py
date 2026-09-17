import hashlib
import io
import json
import stat
import zipfile

import pytest
from pathlib import Path

from PIL import Image

from app.services.campus_assistant.main_chat_skin_package import (
    MainChatSkinPackageError,
    build_main_chat_skin_package,
    build_main_chat_skin_package_from_directory,
    parse_main_chat_skin_package,
)
from app.services.workflows.sub_agent_skin_package import (
    build_sub_agent_skin_package,
    build_sub_agent_skin_package_from_directory,
    parse_sub_agent_skin_package,
)


def _png(color=(40, 120, 210, 255)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", (24, 18), color).save(stream, format="PNG")
    return stream.getvalue()


def _manifest(image: bytes) -> dict:
    layout = {
        "background": {
            "asset": "background",
            "fit": "cover",
            "position": "center-bottom",
            "opacity": 1,
        },
        "content": {"maxWidth": 820, "topGap": 96},
        "decorations": [
            {
                "asset": "background",
                "anchor": "composer-top-left",
                "width": 48,
                "x": 12,
                "y": -3,
                "opacity": 1,
                "visible": True,
            }
        ],
    }
    return {
        "kind": "axiom-main-chat-skin",
        "scope": "main_chat",
        "schemaVersion": 1,
        "key": "test-campus-skin",
        "version": "1.2.3",
        "name": "测试校园皮肤",
        "description": "可移植三端皮肤",
        "renderer": "decorated-chat-v1",
        "assets": [
            {
                "key": "background",
                "path": "assets/background.png",
                "mime": "image/png",
                "sha256": hashlib.sha256(image).hexdigest(),
            }
        ],
        "theme": {
            "colors": {
                "page": "#eef6ff",
                "title": "#16324f",
                "accent": "#2878d2",
            },
            "composer": {"radius": 22, "shadow": "soft"},
        },
        "layouts": {
            "desktop": layout,
            "tablet": layout,
            "mobile": layout,
        },
    }


def _rewrite_package(package: bytes, mutate) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package)) as source:
        files = {info.filename: source.read(info.filename) for info in source.infolist()}
    mutate(files)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_portable_package_round_trip_is_deterministic_and_keeps_three_layouts():
    image = _png()
    manifest = _manifest(image)

    first = build_main_chat_skin_package(manifest, {"background": image})
    second = build_main_chat_skin_package(manifest, {"background": image})
    parsed = parse_main_chat_skin_package(first)

    assert first == second
    assert parsed.manifest["scope"] == "main_chat"
    assert set(parsed.manifest["layouts"]) == {"desktop", "tablet", "mobile"}
    assert parsed.assets == {"background": image}
    assert len(parsed.content_hash) == 64


def test_sub_agent_package_cannot_be_imported_as_main_chat_skin():
    image = _png()
    manifest = _manifest(image)
    manifest["kind"] = "axiom-sub-agent-skin"
    manifest["scope"] = "sub_agent"

    with pytest.raises(MainChatSkinPackageError, match="不能混用"):
        build_main_chat_skin_package(manifest, {"background": image})


def test_sub_agent_package_round_trip_and_scope_isolated_from_main_chat():
    image = _png()
    manifest = _manifest(image)
    manifest.update({
        "kind": "axiom-sub-agent-skin",
        "scope": "sub_agent",
        "renderer": "decorated-agent-run-v1",
    })

    package = build_sub_agent_skin_package(manifest, {"background": image})
    parsed = parse_sub_agent_skin_package(package)

    assert parsed.manifest["scope"] == "sub_agent"
    assert set(parsed.manifest["layouts"]) == {"desktop", "tablet", "mobile"}
    with pytest.raises(MainChatSkinPackageError, match="不能混用"):
        parse_main_chat_skin_package(package)


def test_package_without_mobile_layout_is_rejected():
    image = _png()
    manifest = _manifest(image)
    manifest["layouts"].pop("mobile")

    with pytest.raises(MainChatSkinPackageError, match="mobile"):
        build_main_chat_skin_package(manifest, {"background": image})


def test_unlisted_executable_file_is_rejected():
    image = _png()
    package = build_main_chat_skin_package(_manifest(image), {"background": image})
    tampered = _rewrite_package(package, lambda files: files.__setitem__("assets/install.js", b"alert(1)"))

    with pytest.raises(MainChatSkinPackageError, match="未声明文件"):
        parse_main_chat_skin_package(tampered)


def test_zip_path_traversal_and_symlinks_are_rejected():
    image = _png()
    package = build_main_chat_skin_package(_manifest(image), {"background": image})
    traversal = _rewrite_package(package, lambda files: files.__setitem__("../escape.png", image))
    with pytest.raises(MainChatSkinPackageError, match="路径穿越"):
        parse_main_chat_skin_package(traversal)

    with zipfile.ZipFile(io.BytesIO(package)) as source:
        files = {info.filename: source.read(info.filename) for info in source.infolist()}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        link = zipfile.ZipInfo("assets/link.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"background.png")
    with pytest.raises(MainChatSkinPackageError, match="符号链接"):
        parse_main_chat_skin_package(stream.getvalue())


def test_manifest_cannot_inject_css_or_external_url_fields():
    image = _png()
    package = build_main_chat_skin_package(_manifest(image), {"background": image})

    def inject(files):
        manifest = json.loads(files["manifest.json"])
        manifest["css"] = "body{display:none}"
        files["manifest.json"] = json.dumps(manifest).encode()

    tampered = _rewrite_package(package, inject)
    with pytest.raises(MainChatSkinPackageError, match="不支持的字段"):
        parse_main_chat_skin_package(tampered)


def test_theme_accepts_user_bubble_colors_and_rejects_unknown_color_keys():
    image = _png()
    manifest = _manifest(image)
    manifest["theme"]["colors"]["userBubble"] = "#FFFFFF"
    manifest["theme"]["colors"]["userBubbleText"] = "#16324F"
    parsed = parse_main_chat_skin_package(
        build_main_chat_skin_package(manifest, {"background": image}),
    )
    assert parsed.manifest["theme"]["colors"]["userBubble"] == "#ffffff"
    assert parsed.manifest["theme"]["colors"]["userBubbleText"] == "#16324f"

    def inject(files):
        payload = json.loads(files["manifest.json"])
        payload["theme"]["colors"]["bubbleCss"] = "#ff0000"
        files["manifest.json"] = json.dumps(payload).encode()

    tampered = _rewrite_package(
        build_main_chat_skin_package(manifest, {"background": image}),
        inject,
    )
    with pytest.raises(MainChatSkinPackageError, match="不支持的字段"):
        parse_main_chat_skin_package(tampered)


def test_builtin_campus_skins_declare_white_user_bubble():
    repo = Path(__file__).resolve().parents[1]
    main = parse_main_chat_skin_package(
        build_main_chat_skin_package_from_directory(
            repo / "skin_packages" / "main_chat" / "campus-blueprint-main",
        ),
    )
    sub = parse_sub_agent_skin_package(
        build_sub_agent_skin_package_from_directory(
            repo / "skin_packages" / "sub_agent" / "campus-welcome-portable",
        ),
    )
    assert main.manifest["version"] == "1.1.1"
    assert sub.manifest["version"] == "1.1.1"
    assert main.manifest["theme"]["colors"]["userBubble"] == "#ffffff"
    assert sub.manifest["theme"]["colors"]["userBubble"] == "#ffffff"
