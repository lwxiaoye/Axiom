"""自托管 OCR 服务（官方 PaddleOCR 2.x / PP-OCRv4）。

接口刻意对齐平台「自建 OCR 端点」策略（document_parse_service._ocr_image）：
  POST /ocr  multipart 表单字段名 file  →  {"text": "识别出的文字"}
平台后台「系统设置 → OCR」选「自建 OCR 端点」，地址填 http://<本机IP>:8088/ocr 即可。

纯 CPU、中文强；PP-OCRv4 模型在构建阶段已下载并打进镜像，运行时不联网、首个请求不卡。
"""
import io
import logging
import os

import numpy as np
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from PIL import Image
from paddleocr import PaddleOCR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr")

app = FastAPI(title="Self-hosted OCR (PaddleOCR)")
# 进程内常驻：检测 + 方向分类 + 识别；中文模型。show_log 关掉每张图的刷屏日志。
_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
_TOKEN = os.getenv("OCR_TOKEN", "")     # 设了则要求 Authorization: Bearer <token>


def _extract_text(res) -> str:
    """PaddleOCR 2.x .ocr() 返回 [[[box,(text,score)], ...]]（外层按图，内层按行）；
    只取文字、按行拼接。空结果/异常行都安全跳过。"""
    lines = []
    for page in res or []:
        for ln in page or []:
            try:
                if isinstance(ln, (list, tuple)) and len(ln) >= 2:
                    t = ln[1]
                    lines.append(str(t[0]) if isinstance(t, (list, tuple)) else str(t))
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(lines)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...), authorization: str = Header("")):
    if _TOKEN and authorization != f"Bearer {_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法解析图片: {e}")
    arr = np.array(img)
    res = _engine.ocr(arr, cls=True)
    text = _extract_text(res)
    logger.info("OCR %s -> %d 字", file.filename, len(text))
    return {"text": text}
