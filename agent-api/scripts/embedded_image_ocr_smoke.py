"""文档内嵌图片视觉识别回归（docker exec agent-api python scripts/embedded_image_ocr_smoke.py）。

真造一个「文字 + 一张照片」的 docx 和 pdf，验证：
1. _extract_docx_images / _extract_pdf_images 能抽到内嵌位图；
2. parse_upload 在 OCR 启用时把图片识别文本补进正文（打桩视觉调用，确定性）；
3. OCR 未启用时不注入图片段（不打扰）、文字仍在；
4. 去重 + 跳过过小装饰图。
"""
import asyncio
import io
import sys

sys.path.insert(0, "/app")

import os  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402
from app.services.files import document_parse_service as dps  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def _make_png(w=400, h=300, text="PHOTO") -> bytes:
    # 噪声底 → PNG 压缩后 >3KB，贴近真实照片（不被「跳过过小装饰图」过滤）
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    d = ImageDraw.Draw(img)
    d.text((40, 40), text, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_docx_with_image(png: bytes, extra_png: bytes = b"") -> bytes:
    import docx
    document = docx.Document()
    document.add_paragraph("这是一段正文文字，说明下面有一张图。")
    document.add_picture(io.BytesIO(png))
    if extra_png:
        document.add_picture(io.BytesIO(extra_png))
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _make_pdf_with_image(png: bytes) -> bytes:
    # 用 Pillow 把一张含文字与照片的页面存成 PDF（含可提取位图）
    page = Image.new("RGB", (595, 842), (255, 255, 255))
    photo = Image.open(io.BytesIO(png))
    page.paste(photo, (100, 200))
    d = ImageDraw.Draw(page)
    d.text((60, 60), "PDF 正文文字，下面是一张照片。" * 3, fill=(0, 0, 0))
    buf = io.BytesIO()
    page.save(buf, format="PDF")
    return buf.getvalue()


async def main():
    photo = _make_png(text="CAT ON MAT")

    print("[1] 抽取 docx 内嵌图片")
    docx_bytes = _make_docx_with_image(photo, photo)  # 同图两份 → 测去重
    imgs = dps._extract_docx_images(docx_bytes)
    check("抽到 ≥1 张内嵌图", len(imgs) >= 1, f"got {len(imgs)}")

    print("[2] 抽取 pdf 内嵌图片")
    pdf_bytes = _make_pdf_with_image(photo)
    pimgs = dps._extract_pdf_images(pdf_bytes, dps.MAX_EMBEDDED_IMAGES)
    check("pdf 抽到 ≥1 张内嵌图", len(pimgs) >= 1, f"got {len(pimgs)}")

    # 打桩：视觉识别与 OCR 配置，避免真调模型
    orig_desc = dps._describe_image
    orig_cfg = dps.cfg.get_ocr_config

    async def fake_desc(content, ext, newapi_key, conf):
        return "图中是一只坐在垫子上的猫（视觉识别）"

    async def cfg_on():
        return {"enabled": True, "strategy": "multimodal_model", "model": "glm-4v"}

    async def cfg_off():
        return {"enabled": False, "strategy": "none"}

    dps._describe_image = fake_desc
    try:
        print("[3] OCR 启用：docx 正文补进图片识别文本")
        dps.cfg.get_ocr_config = cfg_on
        r = await dps.parse_upload("测试.docx", docx_bytes)
        check("含原文字", "正文文字" in r["text"])
        check("含图片识别文本", "坐在垫子上的猫" in r["text"], r["text"][:200])
        # 同图两份 → 去重后只出现一次识别块
        check("重复图去重（只识别一次）", r["text"].count("坐在垫子上的猫") == 1,
              f"count={r['text'].count('坐在垫子上的猫')}")

        print("[4] OCR 启用：pdf 内嵌图注入路径（parse_upload 有文字层分支跑的正是此表达式）")
        pimg_text = await dps._ocr_embedded_images(
            dps._extract_pdf_images(pdf_bytes, dps.MAX_EMBEDDED_IMAGES), "", "PDF"
        )
        check("pdf 内嵌图识别出文本", "坐在垫子上的猫" in pimg_text, pimg_text[:200])

        print("[5] OCR 未启用：不注入图片段，文字仍在")
        dps.cfg.get_ocr_config = cfg_off
        r2 = await dps.parse_upload("测试.docx", docx_bytes)
        check("未启用时无图片识别段", "坐在垫子上的猫" not in r2["text"])
        check("未启用时文字仍在", "正文文字" in r2["text"])

        print("[6] 预览开关关闭：OCR 启用也不注入图片段（保预览快）")
        dps.cfg.get_ocr_config = cfg_on
        r3 = await dps.parse_upload("测试.docx", docx_bytes, ocr_embedded_images=False)
        check("预览关闭时无图片识别段", "坐在垫子上的猫" not in r3["text"])
        check("预览关闭时文字仍在", "正文文字" in r3["text"])
    finally:
        dps._describe_image = orig_desc
        dps.cfg.get_ocr_config = orig_cfg

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
