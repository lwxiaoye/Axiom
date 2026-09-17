FROM python:3.11-slim

WORKDIR /app

# Install system dependencies（nodejs：code 节点 JS 沙箱运行时，蓝本 sandbox 双语言对齐）
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    nodejs \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies（本机网络不可达 pypi.org，走清华镜像）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 测试依赖：默认**不装**（生产镜像不该带 pytest）。开发/本机传 --build-arg INSTALL_DEV_DEPS=1
# 或在 compose 的 build.args 里给，容器重建后就不用再手工 pip install 一遍。
# 这条存在的理由：这些包此前只手工装进**运行中的容器**，不在任何清单里，于是每次
# `docker compose up -d`（会 recreate 容器）之后 pytest 就整个消失，表现为「昨天全绿的
# 947 个测试今天一个都跑不起来」——2026-07-27 一天内踩了两次。
ARG INSTALL_DEV_DEPS=0
COPY requirements-dev.txt .
RUN if [ "$INSTALL_DEV_DEPS" = "1" ]; then \
      pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt; \
    fi

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
