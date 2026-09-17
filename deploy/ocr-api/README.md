# AXIOM 校园智能体 · 自托管 OCR 服务（独立部署分支 · 官方 PaddleOCR）

> 本分支**只包含自托管 OCR 服务的 docker 配置**，独立部署在一台服务器上。
> 与主应用解耦——主应用不动，后台「系统设置 → OCR」填 `IP:端口` 连过来。

一个薄 FastAPI 服务包住**官方 PaddleOCR（2.x / PP-OCRv4）**，接口对齐平台「自建 OCR 端点」
策略——**上传图片 → 返回识别出的文字**。纯 CPU、无需 GPU，中文识别强；PP-OCRv4 模型
在**构建阶段已下载并打进镜像**，运行时不联网、首个请求不卡。

> 定位：**文档 / 截图 / 拍照的「提取文字」（OCR）**，只认字、不「描述图片内容」。
> 需要「看懂图、理解场景」用视觉大模型（qwen-vl 等），走后台「多模态模型识别」策略，不是这套。

## 资源

| 项 | 值 |
|---|---|
| 对外端口 | `8088` |
| 内存 | 空闲 ~0.3G，单张识别峰值 1~2G（compose 限 3G） |
| 硬件 | 纯 CPU，无需 GPU |

## 部署

```bash
git clone -b ocr https://github.com/ai/Axiom.git ocr-stack
cd ocr-stack

docker compose up -d --build      # 首次构建装 paddlepaddle + 下模型，约几~十几分钟
docker compose ps                 # 看 ai-ocr 是否 Up (healthy)
docker compose logs -f ocr
```

## 验证（把 SERVER 换成本机 IP）

```bash
curl http://SERVER:8088/health                       # {"status":"ok"}
curl -X POST http://SERVER:8088/ocr -F "file=@图片.png"   # {"text":"..."}（字段名必须是 file）
```

## 接入平台

后台 **系统设置 → OCR**：
| 字段 | 值 |
|---|---|
| 启用 OCR | 开 |
| 识别策略 | **自建 OCR 端点** |
| OCR 端点地址 | `http://<本机IP>:8088/ocr` |
| 端点 API Key | 留空（内网）；若设了 `OCR_TOKEN` 则填相同值 |

保存后上传图片，图片里的文字会被识别、随附件交给主对话模型作答。

## 可选：访问令牌

内网默认无鉴权。要加一层：clone 后写一份 `.env` 加 `OCR_TOKEN=xxx` 再 `up -d`，
调用方须带 `Authorization: Bearer xxx`（后台「端点 API Key」填同一个值）。

## 说明 / 边界

- 版本钉死 `paddlepaddle==2.6.2` + `paddleocr==2.7.3`（PP-OCRv4，经典架构、无 paddlex/langchain
  依赖，容器内干净可靠）。想上 PP-OCRv5（3.x）需引入 paddlex 全家桶，依赖较重、另议。
- **只处理图片**（png/jpg/jpeg/webp/bmp/gif）——正是平台后端发给「自建 OCR 端点」的东西；
  PDF 在平台侧走「抽取文字层」（pypdf）不经本服务，**扫描件/图片型 PDF 暂不识别**
  （需要则在平台后端加「PDF 逐页转图 → OCR」）。
- 返回体 `{"text": ...}`；平台后端认 `text`/`result`/`content` 任一字段。
