FROM python:3.11-slim

WORKDIR /app

RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY app.py store.py ./

EXPOSE 9090

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9090", "--no-proxy-headers"]
