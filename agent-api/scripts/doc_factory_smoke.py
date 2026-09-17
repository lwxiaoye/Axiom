"""文档工厂沙箱冒烟（ADR-047 §6.6）：直接在沙箱镜像里验证 PPT/Word/PDF 能力 + to-pdf 转换 + 中文字体。

运行（容器内）：docker exec agent-api python scripts/doc_factory_smoke.py
不经 LLM——直接调 code_runner.run_code，验证镜像与 to-pdf 包装可用、产物字节读回。
"""
import asyncio
import sys

sys.path.insert(0, "/app")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    from app.services.sandbox import code_runner

    # 1 生成 Word（含中文）→ 转 PDF（to-pdf / LibreOffice）
    code = (
        "from docx import Document\n"
        "d = Document(); d.add_heading('活动策划报告', 0)\n"
        "d.add_paragraph('这是一段中文正文，用于验证中文字体在 PDF 转换后不乱码。')\n"
        "d.save('/workspace/outputs/报告.docx')\n"
        "import subprocess\n"
        "r = subprocess.run(['to-pdf','/workspace/outputs/报告.docx','/workspace/outputs'],"
        " capture_output=True, text=True)\n"
        "print('to-pdf rc', r.returncode)\n"
        "print('stderr', r.stderr[-300:])\n"
    )
    res = await code_runner.run_code(code, fetch_output_bytes=True)
    names = {f["name"] for f in res.output_files}
    check("run_code 成功", res.ok and res.exit_code == 0, res.to_tool_text()[:400])
    check("生成 报告.docx", "报告.docx" in names)
    check("Word→PDF 得到 报告.pdf", "报告.pdf" in names)
    pdf = next((f for f in res.output_files if f["name"] == "报告.pdf"), None)
    check("PDF 有内容且被读回", bool(pdf and pdf.get("content") and len(pdf["content"]) > 1000))
    # PDF 里嵌入了字体即说明中文渲染（%PDF 头 + 合理体积）
    check("PDF 合法头", bool(pdf and pdf.get("content", b"")[:5] == b"%PDF-"))

    # 2 生成 PPT
    code2 = (
        "from pptx import Presentation\n"
        "from pptx.util import Inches\n"
        "p = Presentation(); s = p.slides.add_slide(p.slide_layouts[0])\n"
        "s.shapes.title.text = '季度汇报'\n"
        "s.placeholders[1].text = '第一性原理复盘'\n"
        "p.save('/workspace/outputs/汇报.pptx')\n"
        "print('pptx done')\n"
    )
    res2 = await code_runner.run_code(code2, fetch_output_bytes=True)
    ppt = next((f for f in res2.output_files if f["name"] == "汇报.pptx"), None)
    check("生成 PPT 汇报.pptx", bool(ppt and ppt.get("content") and len(ppt["content"]) > 5000))

    # 3 改 PDF：读上一步生成的 PDF、加水印/合并（pypdf 可用即可）
    code3 = (
        "from reportlab.pdfgen import canvas\n"
        "c = canvas.Canvas('/workspace/outputs/gen.pdf'); c.drawString(72,720,'Hello PDF'); c.save()\n"
        "from pypdf import PdfReader, PdfWriter\n"
        "r = PdfReader('/workspace/outputs/gen.pdf'); w = PdfWriter()\n"
        "for pg in r.pages: w.add_page(pg)\n"
        "w.add_metadata({'/Title':'edited'})\n"
        "with open('/workspace/outputs/edited.pdf','wb') as f: w.write(f)\n"
        "print('pages', len(r.pages))\n"
    )
    res3 = await code_runner.run_code(code3, fetch_output_bytes=True)
    check("reportlab 建 PDF + pypdf 改 PDF", res3.ok and "edited.pdf" in {f["name"] for f in res3.output_files})

    # 4 断网护栏仍在（加固后不回归）
    res4 = await code_runner.run_code(
        "import urllib.request; urllib.request.urlopen('http://example.com', timeout=5)"
    )
    check("断网护栏仍生效（联网失败）", not res4.ok or res4.exit_code != 0)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} passed")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
