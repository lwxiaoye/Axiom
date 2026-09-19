# -*- coding: utf-8 -*-
"""Publish future HTML artifacts as self-contained files.

The My Files preview renders HTML with ``iframe.srcdoc``.  A relative URL such as
``<img src="photo.jpg">`` therefore resolves against the application page, not a
sibling object in file storage; sandbox-only paths are even less meaningful to a
browser.  New HTML artifacts pass through this module before persistence so local
image references become data URLs.  Historical files are intentionally untouched.
"""
from __future__ import annotations

import base64
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote, urlsplit


_REMOTE_OR_INLINE_SCHEMES = frozenset({"data", "http", "https"})
_IMAGE_EXTENSIONS = frozenset({
    ".apng", ".avif", ".bmp", ".gif", ".ico", ".jfif", ".jpeg", ".jpg",
    ".png", ".svg", ".tif", ".tiff", ".webp",
})
_TAG_RE = re.compile(
    r"<(?P<tag>img|source|video|image|link)\b(?P<attrs>[^<>]*?)>",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?P<name>src|srcset|poster|href)\s*=\s*)"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_UNQUOTED_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?P<name>src|poster|href)\s*=\s*)"
    r"(?P<value>[^\s'\"=<>`]+)",
    re.IGNORECASE,
)
_REL_ATTR_RE = re.compile(r"\brel\s*=\s*(['\"])(?P<value>.*?)\1", re.IGNORECASE | re.DOTALL)
_CSS_URL_RE = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<value>[^)'\"]+)(?P=quote)\s*\)",
    re.IGNORECASE,
)
_STYLE_BLOCK_RE = re.compile(
    r"(?P<open><style\b[^>]*>)(?P<value>[\s\S]*?)(?P<close></style\s*>)",
    re.IGNORECASE,
)
_STYLE_ATTR_RE = re.compile(
    r"(?P<prefix>\bstyle\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class HtmlBundleResult:
    data: bytes
    embedded: tuple[str, ...]
    missing: tuple[str, ...]


def _normalise_asset_path(raw: str) -> str:
    value = unquote(str(raw or "").strip()).replace("\\", "/")
    if not value:
        return ""
    # Browser-only suffixes are not part of the stored filename.
    value = urlsplit(value).path
    for prefix in (
        "/workspace/files/",
        "/workspace/tmp/ppt-project/",
        "workspace/files/",
        "workspace/tmp/ppt-project/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.lstrip("/")
    normal = posixpath.normpath(value)
    if normal in ("", ".") or normal == ".." or normal.startswith("../"):
        return ""
    return normal


def _asset_aliases(assets: Mapping[str, bytes]) -> dict[str, tuple[str, bytes]]:
    exact: dict[str, tuple[str, bytes]] = {}
    basename_rows: dict[str, list[tuple[str, bytes]]] = {}
    for raw_path, raw_data in (assets or {}).items():
        if not isinstance(raw_data, (bytes, bytearray)) or not raw_data:
            continue
        path = _normalise_asset_path(str(raw_path or ""))
        if not path:
            continue
        row = (path, bytes(raw_data))
        exact[path] = row
        basename_rows.setdefault(posixpath.basename(path), []).append(row)
    # A basename is safe only when it identifies exactly one asset.  Guessing through
    # collisions would silently embed the wrong user photo.
    for basename, rows in basename_rows.items():
        unique = {path: data for path, data in rows}
        if len(unique) == 1:
            path, data = next(iter(unique.items()))
            exact.setdefault(basename, (path, data))
    return exact


def _reference_candidates(reference: str, *, html_path: str) -> list[str]:
    raw = unquote(str(reference or "").strip()).replace("\\", "/")
    path = urlsplit(raw).path
    candidates: list[str] = []

    direct = _normalise_asset_path(path)
    if direct:
        candidates.append(direct)

    if path and not path.startswith("/"):
        html_dir = posixpath.dirname(_normalise_asset_path(html_path))
        joined = _normalise_asset_path(posixpath.join(html_dir, path))
        if joined:
            candidates.insert(0, joined)

    basename = posixpath.basename(direct or path)
    if basename:
        candidates.append(basename)
    return list(dict.fromkeys(candidates))


def _is_external_or_inline(reference: str) -> bool:
    value = str(reference or "").strip()
    if not value or value.startswith("#"):
        return True
    scheme = urlsplit(value).scheme.lower()
    return scheme in _REMOTE_OR_INLINE_SCHEMES


def _looks_like_local_image(reference: str, *, image_context: bool) -> bool:
    if _is_external_or_inline(reference):
        return False
    if image_context:
        return True
    path = urlsplit(str(reference or "").strip()).path.lower()
    return posixpath.splitext(path)[1] in _IMAGE_EXTENSIONS


# 标准库 mimetypes 在 3.11 且容器没有 /etc/mime.types 时不认识这几种（.webp 到 3.13 才内置），
# 会把它们内嵌成 data:application/octet-stream——<source type>/srcset 与部分 CSS 引擎不会
# 对 octet-stream 做图片嗅探，网页里图就是空的。凡在 _IMAGE_EXTENSIONS 白名单里的都自己兜底。
_IMAGE_MIME_FALLBACK = {
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jfif": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def _data_url(path: str, data: bytes) -> str:
    mime = mimetypes.guess_type(path)[0]
    if not mime or not mime.startswith("image/"):
        ext = posixpath.splitext(urlsplit(str(path or "")).path.lower())[1]
        mime = _IMAGE_MIME_FALLBACK.get(ext) or mime or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def bundle_html_images(
    html: str | bytes,
    assets: Mapping[str, bytes],
    *,
    html_path: str = "index.html",
) -> HtmlBundleResult:
    """Inline local image references and report any that cannot be resolved.

    HTTP(S) and pre-existing data URLs are left alone.  Local ``img``/``source``/
    ``poster``/SVG-image/icon references and image-looking CSS ``url(...)`` values
    must resolve to an explicitly supplied asset; unresolved references are returned
    to the caller so persistence can be blocked instead of publishing a broken file.
    """
    if isinstance(html, bytes):
        text = html.decode("utf-8-sig")
    else:
        text = str(html or "")
    aliases = _asset_aliases(assets)
    embedded: list[str] = []
    missing: list[str] = []

    def resolve(reference: str, *, image_context: bool) -> str:
        if not _looks_like_local_image(reference, image_context=image_context):
            return reference
        for candidate in _reference_candidates(reference, html_path=html_path):
            row = aliases.get(candidate)
            if row is None:
                continue
            path, data = row
            embedded.append(path)
            return _data_url(path, data)
        missing.append(str(reference or "").strip())
        return reference

    def replace_srcset(value: str) -> str:
        # Data URLs are skipped before this point.  Local srcset candidates are the
        # conventional comma-separated ``URL [descriptor]`` form.
        out: list[str] = []
        for raw_candidate in value.split(","):
            candidate = raw_candidate.strip()
            if not candidate:
                continue
            bits = candidate.split()
            url = bits[0]
            replacement = resolve(url, image_context=True)
            out.append(" ".join([replacement, *bits[1:]]))
        return ", ".join(out)

    def replace_css_text(value: str) -> str:
        def replace_css(match: re.Match[str]) -> str:
            reference = match.group("value")
            replacement = resolve(reference, image_context=False)
            quote = match.group("quote")
            return f"url({quote}{replacement}{quote})"

        return _CSS_URL_RE.sub(replace_css, value)

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group("tag")
        tag_lower = tag.lower()
        attrs = match.group("attrs")
        rel_match = _REL_ATTR_RE.search(attrs)
        link_is_icon = bool(
            rel_match
            and "icon" in {token.lower() for token in rel_match.group("value").split()}
        )

        def replace_attr(attr: re.Match[str]) -> str:
            name = attr.group("name").lower()
            value = attr.group("value")
            allowed = (
                name in {"src", "srcset"} and tag_lower in {"img", "source"}
            ) or (name == "poster" and tag_lower == "video") or (
                name == "href" and (tag_lower == "image" or link_is_icon)
            )
            if not allowed:
                return attr.group(0)
            if name == "srcset" and not value.strip().lower().startswith("data:"):
                replacement = replace_srcset(value)
            else:
                replacement = resolve(
                    value,
                    image_context=(tag_lower != "source" or name == "srcset"),
                )
            return f"{attr.group('prefix')}{attr.group('quote')}{replacement}{attr.group('quote')}"

        def replace_unquoted_attr(attr: re.Match[str]) -> str:
            name = attr.group("name").lower()
            value = attr.group("value")
            allowed = (name == "src" and tag_lower in {"img", "source"}) or (
                name == "poster" and tag_lower == "video"
            ) or (name == "href" and (tag_lower == "image" or link_is_icon))
            if not allowed:
                return attr.group(0)
            replacement = resolve(value, image_context=(tag_lower != "source"))
            return f"{attr.group('prefix')}{replacement}"

        attrs = _QUOTED_ATTR_RE.sub(replace_attr, attrs)
        attrs = _UNQUOTED_ATTR_RE.sub(replace_unquoted_attr, attrs)
        attrs = _STYLE_ATTR_RE.sub(
            lambda item: (
                f"{item.group('prefix')}{item.group('quote')}"
                f"{replace_css_text(item.group('value'))}{item.group('quote')}"
            ),
            attrs,
        )
        return f"<{tag}{attrs}>"

    text = _TAG_RE.sub(replace_tag, text)
    text = _STYLE_BLOCK_RE.sub(
        lambda item: (
            f"{item.group('open')}{replace_css_text(item.group('value'))}{item.group('close')}"
        ),
        text,
    )
    text = _STYLE_ATTR_RE.sub(
        lambda item: (
            f"{item.group('prefix')}{item.group('quote')}"
            f"{replace_css_text(item.group('value'))}{item.group('quote')}"
        ),
        text,
    )
    return HtmlBundleResult(
        data=text.encode("utf-8"),
        embedded=tuple(dict.fromkeys(embedded)),
        missing=tuple(dict.fromkeys(item for item in missing if item)),
    )


async def bundle_html_for_thread(
    *,
    user_id: str,
    thread_id: str,
    html: str | bytes,
    html_path: str,
    extra_assets: Mapping[str, bytes] | None = None,
) -> HtmlBundleResult:
    """Bundle from this turn's explicit assets plus the conversation workspace."""
    assets: dict[str, bytes] = {}
    if user_id and thread_id:
        from app.services.agent_harness import workspace_service

        assets.update(await workspace_service.asset_bytes_for_publish(thread_id, user_id))
    # Current-run bytes are more authoritative than an older same-name workspace asset.
    assets.update(extra_assets or {})
    return bundle_html_images(html, assets, html_path=html_path)
