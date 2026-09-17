FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 ccache \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
# 预热：初始化并跑一次，把 PP-OCRv4 的检测/方向/识别模型下载进镜像（之后运行时不联网、首个请求不卡）
RUN python -c "import numpy as np; from paddleocr import PaddleOCR; e=PaddleOCR(use_angle_cls=True, lang='ch', show_log=False); e.ocr(np.zeros((64,64,3), dtype=np.uint8), cls=True)"
COPY app.py .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
