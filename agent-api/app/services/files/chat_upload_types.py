"""主对话 /chat/upload 附件白名单（与前端 chatUploadTypes.ts 保持一致）。"""

from __future__ import annotations

ALLOWED_EXTENSIONS = frozenset({
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "xlsm",
    "txt",
    "md",
    "markdown",
    "html",
    "htm",
    "csv",
    "json",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "bmp",
})

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})

LEGACY_OFFICE = {
    "doc": "docx",
    "ppt": "pptx",
    "xls": "xlsx",
}

FORMAT_HINT = "PDF、Word、PPT、Excel、TXT、Markdown、HTML 或常见图片"


def file_extension(filename: str) -> str:
    name = str(filename or "").strip()
    head, sep, suffix = name.rpartition(".")
    if not sep or not head or not suffix:
        return ""
    return suffix.lower()


def chat_upload_reject_reason(filename: str, content_type: str = "") -> str:
    name = str(filename or "").strip() or "该文件"
    ext = file_extension(filename)
    modern = LEGACY_OFFICE.get(ext)
    if modern:
        return f"「{name}」是旧版 Office 格式，请另存为 .{modern} 后再上传"
    if ext in ALLOWED_EXTENSIONS:
        return ""
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if (not ext) and mime.startswith("image/"):
        subtype = mime.split("/", 1)[-1]
        if subtype == "jpeg":
            subtype = "jpg"
        if subtype in IMAGE_EXTENSIONS:
            return ""
    return f"不支持「{name}」，请上传 {FORMAT_HINT}"
