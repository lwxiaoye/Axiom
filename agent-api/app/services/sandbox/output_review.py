"""沙箱产物审查 v1——硬校验（实施说明 Phase C §4.2）。

在**同一个沙箱内**、用户脚本执行完之后、容器销毁之前跑结构化检查（能否打开/是否为空/
页数是否正常），零新增依赖（解析库用沙箱镜像预装的 python-docx/pptx/openpyxl/pypdf），
不起第二个沙箱（§4.3 规则 5）。

判定策略（防误杀）：只有确定性缺陷才 failed（空文件、Office/PDF 打不开、零页/零 slide、
.json 非法）；解析库缺失、文本编码不确定等一律 warning——审查不可用时允许交付但如实标注，
不伪装通过也不误伤正常产物（§4.2 v1.5 精神）。

草稿版→审查通过再转正式版（§4.3 规则 4）依赖 Phase B 版本表，v1 先保存+如实标注审查状态。
"""
from __future__ import annotations

import json
import logging
import shlex
from typing import Optional

from .base import ExecuteOptions, FileWriteEntry

logger = logging.getLogger(__name__)

_REVIEW_ENTRY = "/workspace/__review__.py"
_REVIEW_TIMEOUT_MS = 20000
_REVIEW_MAX_FILES = 20  # 单次审查文件数上限（防产物海量拖时）

# 这些区段覆盖 PPT 中最常见的 Emoji、彩色符号、Dingbats 和技术符号图标。
# 普通中文标点、项目符号（•）、数学运算符和货币符号不在其中，避免误伤正文。
_PPT_FORBIDDEN_GLYPH_RANGES = (
    (0x2300, 0x23FF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F000, 0x1FAFF),
)
_PPT_FORBIDDEN_GLYPH_POINTS = frozenset({0x20E3, 0xFE0E, 0xFE0F})


def _is_forbidden_ppt_glyph(char: str) -> bool:
    codepoint = ord(char)
    return codepoint in _PPT_FORBIDDEN_GLYPH_POINTS or any(
        start <= codepoint <= end for start, end in _PPT_FORBIDDEN_GLYPH_RANGES
    )

# 沙箱内校验脚本：python -I 执行（isolated 模式，sys.path 不含 /workspace，
# 防用户产物投毒 shadow 标准库）。逐文件 try/except，单个坏文件不毁整批。
_REVIEW_SCRIPT = r'''
import json, os, sys

MAX_FILES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUTPUTS = "/workspace/outputs"
# 目录可覆盖（2026-07-27）：统一文件系统后产物写在 /workspace/files。
# ⚠️ 上面那行字面量必须原样保留 —— tests/test_ppt_output_review_policy.py 与
# tests/test_output_review_empty_artifacts.py 靠字符串替换它来把目录指向 tmp_path
# （改成三元表达式会让 replace 静默失配，测试直接 KeyError；2026-07-27 实测踩到）。
if len(sys.argv) > 2:
    OUTPUTS = sys.argv[2]
PPT_FORBIDDEN_GLYPH_RANGES = __PPT_FORBIDDEN_GLYPH_RANGES__
PPT_FORBIDDEN_GLYPH_POINTS = __PPT_FORBIDDEN_GLYPH_POINTS__
PPT_ICON_FONTS = {"wingdings", "wingdings 2", "wingdings 3", "webdings", "symbol"}

EXPECTED_USER_IMAGES = []
if len(sys.argv) > 4:
    try:
        EXPECTED_USER_IMAGES = [str(x) for x in json.loads(sys.argv[4]) if str(x)]
    except Exception:
        EXPECTED_USER_IMAGES = []


def check(name, status, message=""):
    return {"name": name, "status": status, "message": str(message)[:200]}


def is_forbidden_ppt_glyph(char):
    codepoint = ord(char)
    return codepoint in PPT_FORBIDDEN_GLYPH_POINTS or any(
        start <= codepoint <= end for start, end in PPT_FORBIDDEN_GLYPH_RANGES
    )


def iter_shapes(shapes):
    for shape in shapes:
        yield shape
        children = getattr(shape, "shapes", None)
        if children is not None:
            yield from iter_shapes(children)


def review_file(path, name):
    checks = []
    size = os.path.getsize(path)
    checks.append(check("non_empty", "passed" if size > 0 else "failed", f"{size} bytes"))
    if size == 0:
        return checks
    ext = os.path.splitext(name.lower())[1]
    try:
        if ext == ".docx":
            try:
                import docx
            except ImportError:
                checks.append(check("openable", "warning", "镜像缺 python-docx，跳过深度校验"))
                return checks
            d = docx.Document(path)
            paras = [p for p in d.paragraphs if p.text.strip()]
            table_cells = [
                cell for table in d.tables for row in table.rows for cell in row.cells
                if cell.text.strip()
            ]
            checks.append(check("openable", "passed", f"{len(d.paragraphs)} 段落 / {len(d.tables)} 表格"))
            checks.append(check(
                "has_content", "passed" if (paras or table_cells) else "failed",
                "" if (paras or table_cells) else "文档打开正常但没有任何文字或表格内容",
            ))
        elif ext == ".pptx":
            try:
                from pptx import Presentation
            except ImportError:
                checks.append(check("openable", "warning", "镜像缺 python-pptx，跳过深度校验"))
                return checks
            pres = Presentation(path)
            n = len(pres.slides)
            checks.append(check("openable", "passed", f"{n} 页"))
            checks.append(check("has_pages", "passed" if n > 0 else "failed",
                                "" if n > 0 else "PPT 打开正常但页数为 0"))
            forbidden, icon_fonts = [], []
            has_meaningful_content = False
            for page_no, slide in enumerate(pres.slides, 1):
                for shape in iter_shapes(slide.shapes):
                    text = str(getattr(shape, "text", "") or "")
                    if text.strip():
                        has_meaningful_content = True
                    # 图片、图表、表格、SVG/形状等视觉内容也算有效页面。仅有空文本框或
                    # 占位符的幻灯片仍应被识别为空白，不能作为交付物保存。
                    if getattr(shape, "shape_type", None) not in (14, 17):
                        has_meaningful_content = True
                    for char in text:
                        if is_forbidden_ppt_glyph(char):
                            item = f"第{page_no}页 {char} U+{ord(char):04X}"
                            if item not in forbidden:
                                forbidden.append(item)
                    text_frame = getattr(shape, "text_frame", None)
                    if text_frame is None:
                        continue
                    for paragraph in text_frame.paragraphs:
                        for run in paragraph.runs:
                            font_name = str(run.font.name or "").strip()
                            if font_name.lower() in PPT_ICON_FONTS:
                                item = f"第{page_no}页 {font_name}"
                                if item not in icon_fonts:
                                    icon_fonts.append(item)
            checks.append(check(
                "web_safe_icons", "failed" if forbidden else "passed",
                ("检测到文本 Emoji/Unicode 图标：" + "、".join(forbidden[:12]))
                if forbidden else "未检测到文本 Emoji/Unicode 图标",
            ))
            checks.append(check(
                "no_icon_fonts", "failed" if icon_fonts else "passed",
                ("检测到跨平台不稳定的图标字体：" + "、".join(icon_fonts[:8]))
                if icon_fonts else "未检测到图标字体",
            ))
            checks.append(check(
                "has_content", "passed" if has_meaningful_content else "failed",
                "" if has_meaningful_content else "PPT 有页面但所有页面均为空白",
            ))
            # 真图嵌入闸：用户本轮选中图片时，要求 pptx 内嵌像素与 /workspace/files
            # 下用户原图 sha256 对齐。只数「有图」不够——模型/技能常用纯色占位图交差。
            # EXPECTED_USER_IMAGES 由宿主经 argv[4] 注入（相对 OUTPUTS 的文件名列表）。
            expected_names = [str(x) for x in EXPECTED_USER_IMAGES if str(x).strip()]
            expected_paths = []
            for rel in expected_names:
                # 只认 basename：files/ 镜像键是扁平文件名；防路径穿越。
                base = os.path.basename(rel.replace(chr(92), "/"))
                if not base or base in (".", ".."):
                    continue
                candidate = os.path.join(OUTPUTS, base)
                if os.path.isfile(candidate):
                    expected_paths.append((base, candidate))
            if expected_paths:
                import hashlib, zipfile
                expected_hashes = {}
                for base, candidate in expected_paths:
                    try:
                        with open(candidate, "rb") as fh:
                            expected_hashes[base] = hashlib.sha256(fh.read()).hexdigest()
                    except Exception:
                        continue
                media_hashes = set()
                media_count = 0
                try:
                    with zipfile.ZipFile(path) as zf:
                        for info in zf.infolist():
                            name_in = str(info.filename or "")
                            if not name_in.startswith("ppt/media/") or info.is_dir():
                                continue
                            media_count += 1
                            try:
                                media_hashes.add(hashlib.sha256(zf.read(name_in)).hexdigest())
                            except Exception:
                                continue
                except Exception as e:
                    checks.append(check(
                        "user_images_embedded", "warning",
                        f"无法检查用户图是否嵌入：{type(e).__name__}",
                    ))
                else:
                    matched = [base for base, digest in expected_hashes.items() if digest in media_hashes]
                    if media_count == 0:
                        checks.append(check(
                            "user_images_embedded", "failed",
                            f"用户选中了 {len(expected_paths)} 张图，但 PPT 内没有任何嵌入图片；"
                            "必须把 /workspace/files 下的真实文件嵌入，禁止占位图交差",
                        ))
                    elif not matched:
                        names = "、".join(base for base, _ in expected_paths[:5])
                        checks.append(check(
                            "user_images_embedded", "failed",
                            f"用户选中图片未以原始像素嵌入 PPT（期望：{names}）。"
                            "请对 /workspace/files/<名> 直接 add_picture / 引用，"
                            "禁止换成占位图、假 URL 或另生成图冒充",
                        ))
                    else:
                        checks.append(check(
                            "user_images_embedded", "passed",
                            f"已嵌入用户原图 {len(matched)}/{len(expected_hashes)}",
                        ))
        elif ext in (".xlsx", ".xlsm"):
            try:
                import openpyxl
            except ImportError:
                checks.append(check("openable", "warning", "镜像缺 openpyxl，跳过深度校验"))
                return checks
            wb = openpyxl.load_workbook(path, read_only=True)
            names = wb.sheetnames
            non_empty_cells = sum(
                1 for ws in wb.worksheets for row in ws.iter_rows()
                for cell in row if cell.value not in (None, "")
            )
            checks.append(check("openable", "passed", f"{len(names)} 个工作表"))
            checks.append(check("has_sheets", "passed" if names else "failed",
                                "" if names else "工作簿没有任何工作表"))
            checks.append(check("has_content", "passed" if non_empty_cells else "failed",
                                "" if non_empty_cells else "工作簿存在工作表但没有任何有效单元格"))
            wb.close()
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                checks.append(check("openable", "warning", "镜像缺 pypdf，跳过深度校验"))
                return checks
            r = PdfReader(path)
            n = len(r.pages)
            has_content = False
            for page in r.pages:
                text = (page.extract_text() or "").strip()
                contents = page.get_contents()
                stream = contents.get_data().strip() if contents is not None else b""
                resources = page.get("/Resources") or {}
                if text or stream or resources.get("/XObject"):
                    has_content = True
                    break
            checks.append(check("openable", "passed", f"{n} 页"))
            checks.append(check("has_pages", "passed" if n > 0 else "failed",
                                "" if n > 0 else "PDF 打开正常但页数为 0"))
            checks.append(check("has_content", "passed" if has_content else "failed",
                                "" if has_content else "PDF 有页面但页面没有有效内容"))
        elif ext == ".json":
            with open(path, "rb") as fh:
                json.loads(fh.read().decode("utf-8"))
            checks.append(check("valid_json", "passed"))
        elif ext in (".txt", ".md", ".markdown", ".csv", ".log", ".html", ".xml"):
            with open(path, "rb") as fh:
                blob = fh.read(1 << 20)
            try:
                text = blob.decode("utf-8")
                checks.append(check("utf8_text", "passed"))
                checks.append(check("has_content", "passed" if text.strip() else "failed",
                                    "" if text.strip() else "文本文件只有空白字符"))
            except UnicodeDecodeError:
                checks.append(check("utf8_text", "warning", "非 UTF-8 文本（可能是其他编码，无法确认内容）"))
        elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            try:
                from PIL import Image
            except ImportError:
                checks.append(check("openable", "warning", "镜像缺 Pillow，跳过图片解码校验"))
                return checks
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
            checks.append(check("openable", "passed", f"{width}x{height}"))
            checks.append(check("has_pixels", "passed" if width > 0 and height > 0 else "failed",
                                "" if width > 0 and height > 0 else "图片尺寸为 0"))
        # 其他扩展名：只做 non_empty
    except Exception as e:  # 打不开=确定性缺陷
        checks.append(check("openable", "failed", f"{type(e).__name__}: {e}"))
    return checks


results = []
# 待审名单优先取 argv[3]（调用方明确给出的本次产物相对路径，JSON 数组）。
#
# 为什么不能只 listdir：统一文件系统把**用户整个文件区**镜像到了 files/，而这里原先是
# 「listdir 该目录、非递归、只审前 MAX_FILES 个」——真产物按字母序排在 20 名之后就只拿到
# skipped/warning，写在子目录里的产物根本不在名单上（by_name 未命中 → unknown），
# 两种情况下门禁都退化成"通过"。顺带还会去解析用户所有的 xlsx/docx/pdf，挤爆 20s 预算。
_explicit = []
if len(sys.argv) > 3:
    try:
        _explicit = [str(x) for x in json.loads(sys.argv[3]) if str(x)]
    except Exception:
        _explicit = []
if _explicit:
    files = [n for n in _explicit if os.path.isfile(os.path.join(OUTPUTS, n))]
else:
    names = sorted(os.listdir(OUTPUTS)) if os.path.isdir(OUTPUTS) else []
    files = [n for n in names if os.path.isfile(os.path.join(OUTPUTS, n))]
for name in files[:MAX_FILES]:
    try:
        checks = review_file(os.path.join(OUTPUTS, name), name)
    except Exception as e:
        checks = [check("review_error", "warning", str(e))]
    statuses = {c["status"] for c in checks}
    status = "failed" if "failed" in statuses else ("warning" if "warning" in statuses else "passed")
    results.append({"name": name, "status": status, "checks": checks})
if len(files) > MAX_FILES:
    for name in files[MAX_FILES:]:
        results.append({"name": name, "status": "warning",
                        "checks": [check("skipped", "warning", "超出单次审查文件数上限，未审查")]})
print(json.dumps({"files": results}, ensure_ascii=False))
'''.replace("__PPT_FORBIDDEN_GLYPH_RANGES__", repr(_PPT_FORBIDDEN_GLYPH_RANGES)).replace(
    "__PPT_FORBIDDEN_GLYPH_POINTS__", repr(tuple(_PPT_FORBIDDEN_GLYPH_POINTS))
)


def _summary_of(entry: dict) -> str:
    """一个文件的审查结论压成一句话（给模型回执与前端 tooltip）。"""
    bad = [c for c in entry.get("checks", []) if c.get("status") in ("failed", "warning")]
    if not bad:
        ok = [c.get("message") for c in entry.get("checks", []) if c.get("message")]
        return ok[-1] if ok else ""
    return "；".join(f"{c['name']}: {c.get('message') or c['status']}" for c in bad)[:300]


async def review_outputs(sandbox, outputs: list, *, directory: str = "/workspace/outputs", expected_user_images: Optional[list] = None) -> Optional[dict]:  # noqa: ANN001
    """在沙箱内对 /workspace/outputs 产物做硬校验，就地给 outputs[i] 附 "review"。

    返回总体 {"status": passed/warning/failed/unknown}；审查自身失败（脚本超时/JSON 坏）
    返回 unknown 并给每个产物标 unknown——审查不可用不阻塞交付，但如实标注（§4.3 规则 3）。
    """
    try:
        await sandbox.write_files(
            [FileWriteEntry(path=_REVIEW_ENTRY, data=_REVIEW_SCRIPT.encode("utf-8"))]
        )
        # 显式传本次要审的相对路径：不传就退化成"扫整个目录"，而 files/ 是用户整个文件区
        targets = json.dumps(
            [str(o.get("name") or "") for o in outputs if o.get("name")], ensure_ascii=False)
        expected = json.dumps(
            [str(x) for x in (expected_user_images or []) if str(x).strip()],
            ensure_ascii=False,
        )
        res = await sandbox.execute(
            f"python -I {_REVIEW_ENTRY} {_REVIEW_MAX_FILES} {shlex.quote(directory)} "
            f"{shlex.quote(targets)} {shlex.quote(expected)}",
            ExecuteOptions(timeout_ms=_REVIEW_TIMEOUT_MS, max_output_bytes=131072),
        )
        data = json.loads((res.stdout or "").strip())
        by_name = {f["name"]: f for f in data.get("files", [])}
    except Exception as e:  # noqa: BLE001 审查失败≠产物失败
        logger.warning("产物审查执行失败（标 unknown 不阻塞交付）: %s", e)
        for o in outputs:
            o["review"] = {"status": "unknown", "summary": "审查未能完成（不代表文件有问题）"}
        return {"status": "unknown"}

    worst = "passed"
    for o in outputs:
        entry = by_name.get(o["name"])
        if entry is None:
            o["review"] = {"status": "unknown", "summary": "审查未能完成（不代表文件有问题）"}
            worst = worst if worst == "failed" else "warning"
            continue
        o["review"] = {"status": entry["status"], "summary": _summary_of(entry)}
        if entry["status"] == "failed":
            worst = "failed"
        elif entry["status"] == "warning" and worst != "failed":
            worst = "warning"
    return {"status": worst}
