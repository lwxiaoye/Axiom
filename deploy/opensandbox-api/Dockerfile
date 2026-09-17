FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG OPENSANDBOX_SERVER_VERSION=0.2.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" \
      "opensandbox-server==$OPENSANDBOX_SERVER_VERSION"

EXPOSE 8080

CMD ["opensandbox-server", "--config", "/etc/opensandbox/sandbox.toml"]
