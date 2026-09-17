"""Safe, portable package format for main-chat skins.

The package is deliberately declarative.  It may contain bounded layout values and raster images,
but never HTML, CSS, JavaScript, Vue components, SVG or remote URLs.  This lets a customer import a
skin without turning the import endpoint into a code-deployment endpoint.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError


PACKAGE_KIND = "axiom-main-chat-skin"
PACKAGE_SCOPE = "main_chat"
SCHEMA_VERSION = 1
RENDERER_KEY = "decorated-chat-v1"
DEVICE_KEYS = ("desktop", "tablet", "mobile")

MAX_PACKAGE_BYTES = 12 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_FILES = 24
MAX_ASSETS = 16
MAX_ASSET_BYTES = 6 * 1024 * 1024
MAX_TOTAL_PIXELS = 32_000_000
MAX_IMAGE_EDGE = 8192
MAX_COMPRESSION_RATIO = 100

KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$")
VERSION_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")

ALLOWED_MIME_TYPES = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}
ALLOWED_TOP_LEVEL = {
    "kind", "scope", "schemaVersion", "key", "version", "name", "description",
    "renderer", "assets", "theme", "layouts",
}
ALLOWED_THEME_KEYS = {"colors", "composer"}
ALLOWED_COLOR_KEYS = {
    "page", "title", "body", "accent", "composer", "composerBorder",
    "userBubble", "userBubbleText",
}
ALLOWED_COMPOSER_KEYS = {"radius", "shadow"}
ALLOWED_LAYOUT_KEYS = {"background", "content", "decorations"}
ALLOWED_BACKGROUND_KEYS = {"asset", "fit", "position", "opacity"}
ALLOWED_CONTENT_KEYS = {"maxWidth", "topGap"}
ALLOWED_DECORATION_KEYS = {"asset", "anchor", "width", "x", "y", "opacity", "visible"}
ALLOWED_ANCHORS = {
    "page-top-left", "page-top-right", "page-bottom-left", "page-bottom-right",
    "intro-top", "composer-top-left", "composer-top-right",
}


class MainChatSkinPackageError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedMainChatSkinPackage:
    manifest: dict[str, Any]
    assets: dict[str, bytes]
    content_hash: str


def _fail(message: str) -> None:
    raise MainChatSkinPackageError(message)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} 必须是对象")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _fail(f"{label} 包含不支持的字段：{', '.join(unknown)}")


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} 必须是数字")
    number = float(value)
    if number < minimum or number > maximum:
        _fail(f"{label} 必须在 {minimum:g}–{maximum:g} 之间")
    return number


def _safe_zip_path(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        _fail("皮肤包包含非法文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        _fail("皮肤包包含绝对路径或路径穿越")
    if any(part.startswith(".") for part in path.parts):
        _fail("皮肤包不能包含隐藏文件")
    return path.as_posix()


def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name, "r") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        _fail(f"皮肤包文件解压后过大：{name}")
    return data


def _validate_asset_image(data: bytes, mime: str, path: str) -> None:
    if not data:
        _fail(f"素材为空：{path}")
    if len(data) > MAX_ASSET_BYTES:
        _fail(f"素材超过 {MAX_ASSET_BYTES // (1024 * 1024)} MiB：{path}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != ALLOWED_MIME_TYPES[mime]:
                _fail(f"素材声明类型与实际格式不一致：{path}")
            width, height = image.size
            if width <= 0 or height <= 0 or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE:
                _fail(f"素材尺寸超出限制：{path}")
            if width * height > MAX_TOTAL_PIXELS:
                _fail(f"素材像素总量超出限制：{path}")
            image.verify()
    except MainChatSkinPackageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MainChatSkinPackageError(f"素材不是有效图片：{path}") from exc


def _validate_theme(value: Any) -> dict[str, Any]:
    theme = _require_object(value or {}, "theme")
    _reject_unknown(theme, ALLOWED_THEME_KEYS, "theme")
    colors = _require_object(theme.get("colors") or {}, "theme.colors")
    _reject_unknown(colors, ALLOWED_COLOR_KEYS, "theme.colors")
    normalized_colors: dict[str, str] = {}
    for key, color in colors.items():
        if not isinstance(color, str) or not HEX_COLOR_RE.fullmatch(color):
            _fail(f"theme.colors.{key} 必须是 6 或 8 位十六进制颜色")
        normalized_colors[key] = color.lower()

    composer = _require_object(theme.get("composer") or {}, "theme.composer")
    _reject_unknown(composer, ALLOWED_COMPOSER_KEYS, "theme.composer")
    normalized_composer: dict[str, Any] = {}
    if "radius" in composer:
        normalized_composer["radius"] = int(_bounded_number(
            composer["radius"], "theme.composer.radius", 0, 48,
        ))
    if "shadow" in composer:
        shadow = str(composer["shadow"] or "")
        if shadow not in {"none", "soft", "elevated"}:
            _fail("theme.composer.shadow 只能是 none、soft 或 elevated")
        normalized_composer["shadow"] = shadow
    return {"colors": normalized_colors, "composer": normalized_composer}


def _validate_layout(value: Any, device: str, asset_keys: set[str]) -> dict[str, Any]:
    layout = _require_object(value, f"layouts.{device}")
    _reject_unknown(layout, ALLOWED_LAYOUT_KEYS, f"layouts.{device}")

    background = _require_object(layout.get("background") or {}, f"layouts.{device}.background")
    _reject_unknown(background, ALLOWED_BACKGROUND_KEYS, f"layouts.{device}.background")
    normalized_background: dict[str, Any] = {}
    if "asset" in background:
        asset = str(background["asset"] or "")
        if asset not in asset_keys:
            _fail(f"layouts.{device}.background 引用了不存在的素材：{asset}")
        normalized_background["asset"] = asset
    fit = str(background.get("fit") or "cover")
    if fit not in {"cover", "contain"}:
        _fail(f"layouts.{device}.background.fit 只能是 cover 或 contain")
    normalized_background["fit"] = fit
    position = str(background.get("position") or "center-bottom")
    if position not in {"center", "center-top", "center-bottom", "left-bottom", "right-bottom"}:
        _fail(f"layouts.{device}.background.position 不受支持")
    normalized_background["position"] = position
    normalized_background["opacity"] = _bounded_number(
        background.get("opacity", 1), f"layouts.{device}.background.opacity", 0, 1,
    )

    content = _require_object(layout.get("content") or {}, f"layouts.{device}.content")
    _reject_unknown(content, ALLOWED_CONTENT_KEYS, f"layouts.{device}.content")
    normalized_content = {
        "maxWidth": int(_bounded_number(
            content.get("maxWidth", 820), f"layouts.{device}.content.maxWidth", 280, 1200,
        )),
        "topGap": int(_bounded_number(
            content.get("topGap", 96), f"layouts.{device}.content.topGap", 0, 320,
        )),
    }

    decorations = layout.get("decorations") or []
    if not isinstance(decorations, list):
        _fail(f"layouts.{device}.decorations 必须是数组")
    if len(decorations) > 8:
        _fail(f"layouts.{device}.decorations 最多 8 个")
    normalized_decorations: list[dict[str, Any]] = []
    for index, raw in enumerate(decorations):
        item = _require_object(raw, f"layouts.{device}.decorations[{index}]")
        _reject_unknown(item, ALLOWED_DECORATION_KEYS, f"layouts.{device}.decorations[{index}]")
        asset = str(item.get("asset") or "")
        if asset not in asset_keys:
            _fail(f"layouts.{device}.decorations[{index}] 引用了不存在的素材：{asset}")
        anchor = str(item.get("anchor") or "")
        if anchor not in ALLOWED_ANCHORS:
            _fail(f"layouts.{device}.decorations[{index}].anchor 不受支持")
        normalized_decorations.append({
            "asset": asset,
            "anchor": anchor,
            "width": int(_bounded_number(item.get("width", 64), "装饰宽度", 16, 480)),
            "x": int(_bounded_number(item.get("x", 0), "装饰横向偏移", -240, 240)),
            "y": int(_bounded_number(item.get("y", 0), "装饰纵向偏移", -240, 240)),
            "opacity": _bounded_number(item.get("opacity", 1), "装饰透明度", 0, 1),
            "visible": bool(item.get("visible", True)),
        })

    return {
        "background": normalized_background,
        "content": normalized_content,
        "decorations": normalized_decorations,
    }


def _normalize_manifest(
    raw: Any,
    assets_by_path: Mapping[str, bytes],
    *,
    package_kind: str,
    package_scope: str,
    renderer_key: str,
) -> dict[str, Any]:
    manifest = _require_object(raw, "manifest")
    _reject_unknown(manifest, ALLOWED_TOP_LEVEL, "manifest")
    if manifest.get("kind") != package_kind or manifest.get("scope") != package_scope:
        _fail("皮肤包类型或 scope 不匹配；主对话与子智能体皮肤不能混用")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        _fail(f"仅支持 schemaVersion={SCHEMA_VERSION}")
    if manifest.get("renderer") != renderer_key:
        _fail(f"当前系统不支持 renderer={manifest.get('renderer')!r}")

    key = str(manifest.get("key") or "")
    version = str(manifest.get("version") or "")
    name = str(manifest.get("name") or "").strip()
    description = str(manifest.get("description") or "").strip()
    if not KEY_RE.fullmatch(key):
        _fail("skin key 只能使用 3–64 位小写字母、数字和短横线")
    if not VERSION_RE.fullmatch(version):
        _fail("version 必须使用 major.minor.patch")
    if not name or len(name) > 128:
        _fail("皮肤名称长度必须为 1–128")
    if len(description) > 512:
        _fail("皮肤说明不能超过 512 字")

    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not 1 <= len(raw_assets) <= MAX_ASSETS:
        _fail(f"assets 数量必须为 1–{MAX_ASSETS}")
    normalized_assets: list[dict[str, Any]] = []
    asset_keys: set[str] = set()
    asset_paths: set[str] = set()
    for index, raw_asset in enumerate(raw_assets):
        item = _require_object(raw_asset, f"assets[{index}]")
        _reject_unknown(item, {"key", "path", "mime", "sha256"}, f"assets[{index}]")
        asset_key = str(item.get("key") or "")
        path = _safe_zip_path(str(item.get("path") or ""))
        mime = str(item.get("mime") or "").lower()
        sha256 = str(item.get("sha256") or "").lower()
        if not KEY_RE.fullmatch(asset_key):
            _fail(f"assets[{index}].key 不合法")
        if asset_key in asset_keys:
            _fail(f"素材 key 重复：{asset_key}")
        if path in asset_paths or not path.startswith("assets/"):
            _fail(f"素材路径重复或不在 assets/：{path}")
        if mime not in ALLOWED_MIME_TYPES:
            _fail(f"素材类型不支持：{mime}")
        if not SHA256_RE.fullmatch(sha256):
            _fail(f"素材哈希不合法：{path}")
        data = assets_by_path.get(path)
        if data is None:
            _fail(f"皮肤包缺少素材：{path}")
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != sha256:
            _fail(f"素材哈希不匹配：{path}")
        _validate_asset_image(data, mime, path)
        asset_keys.add(asset_key)
        asset_paths.add(path)
        normalized_assets.append({"key": asset_key, "path": path, "mime": mime, "sha256": actual_sha})

    layouts = _require_object(manifest.get("layouts"), "layouts")
    if set(layouts) != set(DEVICE_KEYS):
        _fail("皮肤包必须同时包含 desktop、tablet、mobile 三端布局")
    normalized_layouts = {
        device: _validate_layout(layouts[device], device, asset_keys)
        for device in DEVICE_KEYS
    }

    return {
        "kind": package_kind,
        "scope": package_scope,
        "schemaVersion": SCHEMA_VERSION,
        "key": key,
        "version": version,
        "name": name,
        "description": description,
        "renderer": renderer_key,
        "assets": normalized_assets,
        "theme": _validate_theme(manifest.get("theme")),
        "layouts": normalized_layouts,
    }


def parse_main_chat_skin_package(
    package_bytes: bytes,
    *,
    package_kind: str = PACKAGE_KIND,
    package_scope: str = PACKAGE_SCOPE,
    renderer_key: str = RENDERER_KEY,
) -> ParsedMainChatSkinPackage:
    if not package_bytes or len(package_bytes) > MAX_PACKAGE_BYTES:
        _fail(f"皮肤包不能为空且不能超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes))
    except (zipfile.BadZipFile, OSError) as exc:
        raise MainChatSkinPackageError("皮肤包不是有效 ZIP") from exc

    with archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        if not 1 <= len(files) <= MAX_FILES:
            _fail(f"皮肤包文件数必须为 1–{MAX_FILES}")
        names: list[str] = []
        folded: set[str] = set()
        total = 0
        for info in files:
            name = _safe_zip_path(info.filename)
            lowered = name.casefold()
            if name in names or lowered in folded:
                _fail(f"皮肤包文件路径重复或大小写碰撞：{name}")
            if info.flag_bits & 0x1:
                _fail("皮肤包不能包含加密文件")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                _fail(f"皮肤包不能包含符号链接：{name}")
            member_limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_ASSET_BYTES
            if info.file_size > member_limit:
                _fail(f"皮肤包文件过大：{name}")
            if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                _fail(f"皮肤包压缩比异常：{name}")
            total += info.file_size
            if total > MAX_PACKAGE_BYTES:
                _fail("皮肤包解压后总大小超限")
            names.append(name)
            folded.add(lowered)

        if "manifest.json" not in names:
            _fail("皮肤包根目录缺少 manifest.json")
        manifest_bytes = _read_member(archive, "manifest.json", MAX_MANIFEST_BYTES)
        try:
            raw_manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MainChatSkinPackageError("manifest.json 不是有效 UTF-8 JSON") from exc

        assets_by_path = {
            name: _read_member(archive, name, MAX_ASSET_BYTES)
            for name in names
            if name != "manifest.json"
        }
        if sum(len(value) for value in assets_by_path.values()) + len(manifest_bytes) > MAX_PACKAGE_BYTES:
            _fail("皮肤包解压后总大小超限")
        manifest = _normalize_manifest(
            raw_manifest,
            assets_by_path,
            package_kind=package_kind,
            package_scope=package_scope,
            renderer_key=renderer_key,
        )
        referenced = {item["path"] for item in manifest["assets"]}
        extra = sorted(set(assets_by_path) - referenced)
        if extra:
            _fail(f"皮肤包包含未声明文件：{', '.join(extra)}")
        assets_by_key = {
            item["key"]: assets_by_path[item["path"]]
            for item in manifest["assets"]
        }
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256()
        digest.update(canonical)
        for item in sorted(manifest["assets"], key=lambda row: row["key"]):
            digest.update(item["key"].encode())
            digest.update(bytes.fromhex(item["sha256"]))
        return ParsedMainChatSkinPackage(
            manifest=manifest,
            assets=assets_by_key,
            content_hash=digest.hexdigest(),
        )


def build_main_chat_skin_package(
    manifest: Mapping[str, Any],
    assets: Mapping[str, bytes],
    *,
    package_kind: str = PACKAGE_KIND,
    package_scope: str = PACKAGE_SCOPE,
    renderer_key: str = RENDERER_KEY,
) -> bytes:
    """Build a deterministic package and re-run the importer validation before returning it."""
    raw_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
    raw_assets = raw_manifest.get("assets") if isinstance(raw_manifest, dict) else None
    if not isinstance(raw_assets, list):
        _fail("manifest 缺少 assets")
    by_path: dict[str, bytes] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            _fail("manifest.assets 项不合法")
        key = str(item.get("key") or "")
        path = str(item.get("path") or "")
        data = assets.get(key)
        if data is None:
            _fail(f"导出缺少素材字节：{key}")
        item["sha256"] = hashlib.sha256(data).hexdigest()
        by_path[path] = data

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        def write_member(path: str, content: bytes) -> None:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info,
                content,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )

        write_member(
            "manifest.json",
            json.dumps(raw_manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        )
        for path in sorted(by_path):
            write_member(path, by_path[path])
    package_bytes = stream.getvalue()
    parse_main_chat_skin_package(
        package_bytes,
        package_kind=package_kind,
        package_scope=package_scope,
        renderer_key=renderer_key,
    )
    return package_bytes


def build_main_chat_skin_package_from_directory(
    source_dir: Path,
    *,
    package_kind: str = PACKAGE_KIND,
    package_scope: str = PACKAGE_SCOPE,
    renderer_key: str = RENDERER_KEY,
) -> bytes:
    manifest_path = source_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MainChatSkinPackageError(f"无法读取内置皮肤 manifest：{manifest_path}") from exc
    raw_assets = manifest.get("assets") if isinstance(manifest, dict) else None
    if not isinstance(raw_assets, list):
        _fail("内置皮肤 manifest 缺少 assets")
    assets: dict[str, bytes] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            _fail("内置皮肤素材清单不合法")
        key = str(item.get("key") or "")
        path = _safe_zip_path(str(item.get("path") or ""))
        try:
            assets[key] = (source_dir / Path(path)).read_bytes()
        except OSError as exc:
            raise MainChatSkinPackageError(f"无法读取内置皮肤素材：{path}") from exc
    return build_main_chat_skin_package(
        manifest,
        assets,
        package_kind=package_kind,
        package_scope=package_scope,
        renderer_key=renderer_key,
    )
