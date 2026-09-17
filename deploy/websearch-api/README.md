# AXIOM 校园智能体 · 联网搜索自托管栈（独立部署分支）

> 本分支**只包含联网搜索三段服务的 docker 配置**，用于在一台（内存充足的）服务器上独立部署。
> 与主应用（agent-api / 前端）解耦——主应用不动，只靠填 `IP:端口` 连过来。

三段：**SearXNG（搜索）+ Firecrawl 全栈（抓取）+ TEI reranker（重排，bge-reranker-v2-m3）**。

## 端口 & 资源

| 段 | 服务 | 对外端口 | CPU 内存(空闲/负载) |
|---|---|---|---|
| 搜索 | SearXNG | `8085` | ~0.2G / ~0.5G |
| 抓取 | Firecrawl(api+worker+playwright+redis+rabbitmq+postgres) | `8086` | ~2G / 5~10G |
| 重排 | TEI + `BAAI/bge-reranker-v2-m3`（墙内由 init 容器预下载） | `8087` | ~2~2.5G(常驻) |

- 合计舒服跑给 **12~16G 内存**；**不需要 GPU**（纯 CPU）。
- 只有这 3 个端口对外，服务间走内部网络 `websearch`。

## 前置

- Docker + Docker Compose(v2)。
- 服务器能访问外网（SearXNG 查真实引擎、Firecrawl 抓公网页、reranker 下模型）。
- 国内服务器：镜像拉取建议配 registry 加速器；reranker 模型由 `ws-reranker-model-init` 一次性容器走 `hf-mirror.com` 预下载（~2.3G），TEI 只读本地加载——无需能直连 huggingface.co。

## 部署

```bash
git clone -b 联网搜索 https://github.com/ai/Axiom.git websearch-stack
cd websearch-stack

cp .env.example .env                 # 改 FIRECRAWL_PG_PASSWORD；大内存机器把并发调高
# 把 searxng/settings.yml 的 secret_key 换成：openssl rand -hex 32 的结果

docker compose up -d
docker compose ps                              # 看六七个容器是否 Up
docker compose logs -f ws-reranker-model-init  # 首次看模型下载进度（下完该容器 Exited(0)）
docker compose logs -f ws-reranker             # 再看 TEI 从本地路径加载，出现 Ready 即好
```

## 验证（把 SERVER 换成本服务器 IP）

```bash
# ① 搜索
curl "http://SERVER:8085/search?q=fastgpt&format=json" | head -c 300

# ② 抓取（返回 markdown 正文）
curl -X POST "http://SERVER:8086/v1/scrape" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown"]}' | head -c 300

# ③ 重排（TEI /rerank）
curl -X POST "http://SERVER:8087/rerank" \
  -H "Content-Type: application/json" \
  -d '{"query":"什么是机器学习","texts":["机器学习是人工智能的分支","今天天气不错"]}'
```

## 主应用（agent-api）如何对接

在主应用后台「联网搜索配置」页（或 `PUT /agent-api/platform-config/web-search`）填：

| 字段 | 值 |
|---|---|
| enabled | `true` |
| searchProvider | `searxng` |
| searxngUrl | `http://SERVER:8085` |
| scraperProvider | `firecrawl` |
| firecrawlUrl | `http://SERVER:8086` |
| rerankerProvider | `local` |
| localRerankerUrl | `http://SERVER:8087` ← **只填 base，不带 /rerank**（代码会自动补） |

> 说明：`rerankerProvider=local` 的对接代码在**主应用**（`agent-api/app/services/web_search_service.py` 的 `_stage_rerank`），不在本分支——本分支只负责把三段服务跑起来。

## 安全（重要）

三个服务**默认都没有鉴权**。只在**内网 / 防火墙 / 安全组内**开放 8085/8086/8087，只放行 agent-api 所在机器，**绝不要暴露公网**。

## 调优 / GPU / 兜底

- **大内存服务器**：`.env` 里调高 `NUM_WORKERS_PER_QUEUE` / `MAX_CONCURRENT_JOBS` / `CRAWL_CONCURRENT_REQUESTS`，并上调 compose 里 `firecrawl-*` 的 `mem_limit`。
- **上 GPU（仅重排受益，2~3G 显存）**：把 `reranker` 换成 `ghcr.io/huggingface/text-embeddings-inference:latest` 并加 `deploy.resources.reservations.devices` 挂 GPU；模型仍由 init 容器预下载，`--model-id /models/bge-reranker-v2-m3` 不变。
- **reranker 一直下不动 / 报 `Header content-range is missing`**：这是 TEI 自带 Rust 下载器与 hf-mirror 不兼容，不是模型问题。本栈已改由 `ws-reranker-model-init` 用 python `huggingface-cli` 预下载、TEI 只读本地路径绕开。init 失败多为 pip 装不上或 hf-mirror 抽风——重跑 `docker compose up -d ws-reranker-model-init`（幂等，已下好会跳过）。
- **想换别的 reranker 模型**：改 `reranker-model-init` 的 `huggingface-cli download <repo>` 与 `reranker` 的 `--model-id /models/<dir>`，删卷重下：`docker compose down && docker volume rm ai-zhongtai-websearch_reranker-models && docker compose up -d`。只选 **TEI 原生支持**的（bge/bce 系）；jina-reranker-v2 这类自定义架构 TEI 加载不了（`missing field model_type`）。
- **SearXNG 返回 0 结果**：**境内服务器**多为默认境外引擎（google/ddg/brave…）被墙全 timeout——`searxng/settings.yml` 已内置国内引擎配置（关境外、启用 bing），实测 `curl "http://SERVER:8085/search?q=测试&format=json"` 有结果即通；bing 不行就取消 `baidu` 注释。境外服务器则多为引擎限流（同 IP 频繁 suspend 180s），低频会恢复。
