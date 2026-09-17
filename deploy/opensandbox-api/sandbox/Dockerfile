# Shared runtime for the local Docker provider and OpenSandbox.
FROM python:3.11-slim

RUN sed -i \
      -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
      -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
      /etc/apt/sources.list.d/debian.sources

RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
      bash nodejs \
      libreoffice-writer libreoffice-impress libreoffice-calc \
      fonts-dejavu fontconfig poppler-utils \
      unzip zip make git jq rsync file \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Keep the large CJK font archive in its own layer so a transient mirror failure
# does not invalidate the complete LibreOffice/Node dependency download.
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends fonts-noto-cjk \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      PyYAML pandas openpyxl python-docx python-pptx matplotlib pillow tabulate \
      pypdf pdfplumber reportlab websocket-client

COPY sandbox/to-pdf /usr/local/bin/to-pdf
COPY sandbox/fonts-zh-aliases.conf /etc/fonts/conf.d/64-zh-aliases.conf
COPY ppt-studio/scripts/ /opt/open-kimi-ppt/scripts/
COPY sandbox/pptd_wasm_bg.wasm \
     /opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm

RUN chmod 0755 /usr/local/bin/to-pdf \
    && fc-cache -f \
    && useradd --create-home sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
