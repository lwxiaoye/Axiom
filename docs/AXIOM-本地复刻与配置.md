# 本地复刻与配置

首版 2026-09-17；2026-09-19 按 Java 下线、知识库 / Skill 目录由 agent-api 自持、工作流编排与皮肤系统删除后的源码核对。服务器部署看 [`生产部署手册.md`](生产部署手册.md)，本文只讲本机复刻。

## 仓库位置

新 Git 根目录为 `D:\lwxiaoye\Axiom`，origin 指向 `https://github.com/lwxiaoye/Axiom.git`。
本地原始备份目录与 `__MACOSX` 已被忽略；复制或压缩整个目录仍会包含它们，不要将其当成发布包。
本地 Docker 使用仓库根目录 `docker-compose.local.yml` 与 `deploy/local/.env`（后者不入库）。

## 前端

登录、自助注册与个人资料由 `auth-api`（FastAPI，SQLite）提供，没有 Java。知识库、Skill 目录、文件与平台配置都在 agent-api 里。认证配置、持久会话和测试见 [auth-api/README.md](../auth-api/README.md)。

```powershell
cd D:\lwxiaoye\Axiom
pnpm install --frozen-lockfile
Copy-Item .env.local.example .env.development.local
pnpm dev --host 127.0.0.1 --port 3200
```

`.env` 为可公开的产品默认配置；`.env.development` 仅代理到本机服务；`.env.production` 使用同源 API。
自己的服务地址写到被忽略的 `.env.development.local`。不要将模型密钥、数据库密码写入任何 `VITE_*` 变量。

需要与 auth-api 对齐的公开协议参数：

| 参数 | 用途 | 未配置时 |
|---|---|---|
| `VITE_LOGIN_AES_KEY` | 登录协议 AES key，16 字节，须与 auth-api 的 `AXIOM_LOGIN_AES_KEY` 一致 | 登录明确报配置错误 |
| `VITE_LOGIN_AES_IV` | 登录协议 AES IV，16 字节，须与 `AXIOM_LOGIN_AES_IV` 一致 | 登录明确报配置错误 |
| `VITE_REQUEST_SIGNATURE_SALT` | 请求签名附加值（`src/utils/encryption/signMd5Utils.js`） | 需签名请求明确报错 |

`.env.local.example` 里的 `VITE_BPMN_*` 两项已无代码消费，留空即可。上述值会进入浏览器包，不提供秘密保护；部署仍需 HTTPS 和服务端身份/权限校验。

## Python 服务

```powershell
cd agent-api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# 填写 .env，准备数据库与模型后再运行
.venv\Scripts\python.exe -u run.py
```

当前 `run.py` 读取自身目录的 `.env`，存在 `.env.dev` 时覆盖（`LOCAL_ENV_FILE`）。
API 和 Worker 必须使用同一套数据库和连接器加密密钥（`CONNECTOR_SECRET_KEY`）；不要接回原工作区的服务。

## 必要外部服务

| 服务 | 是否随本仓库提供 | 说明 |
|---|---|---|
| Vue 前端 | 源码已提供 | Vite 默认 3200 |
| auth-api | 源码已提供 | 9090；单管理员 + 自助注册，SQLite 文件由 `AXIOM_AUTH_DB` 指定 |
| agent-api + Worker | 源码已提供 | API 默认 8000，依赖下列服务 |
| MySQL（`ai_boot`） | 有迁移 | 会话、知识库切片正本、Skill、文件、平台配置；空库由 `MIGRATE_ON_STARTUP` 或 `alembic -n mysql upgrade head` 建表 |
| Runtime PostgreSQL | 有迁移 | Run、事件、计划、工具结果、Worker 队列；`alembic -n runtime upgrade head` |
| 对话模型 | 无 | 管理员在 `/admin` 名册里填 OpenAI 兼容 / Anthropic 地址与密钥；`AXIOM_MODEL_BASE_URL` 只是旧网关缺省地址 |
| Qdrant + 向量模型 | 有适配代码 | 向量模型在 `/admin` 配置；需重建本校知识索引 |
| SearXNG、OpenSandbox、Playwright 浏览器 | 有 compose 材料（`docker-compose.server.yml`） | 按启用能力配置，不能把模板当已经运行的服务 |
| MinIO | 有适配 | `FILE_STORAGE_PROVIDER=local` 时用本地磁盘；不复制旧文件 |

本地 Compose 的 `AXIOM_MODEL_BASE_URL` 只是模型 API 地址，可以连接本机网关或云端模型。栈内没有部署模型权重或推理服务，也不会自动生成模型凭据。
阅读 `agent-api/migrations/README.md`，分别执行 MySQL 与 Runtime PG 迁移；旧库升级必须走正式迁移，不能只靠启动期建表。

## 校园知识依赖

校园百事通读取知识库基础信息 / 文档列表 / ACL 走 `campus_services/knowledge_access.py`，在进程内直接调用 `services/knowledge/knowledge_base_service.py`，不经 HTTP。发布前至少要有一个可用（已启用、有已入库文档）的知识库，否则草稿校验不通过。

## 部署材料的状态

- Compose 中数据库密码、沙箱与搜索密钥都来自被 Git 忽略的 `deploy/local/.env`（模板 `deploy/local/.env.example`）。
- 原 TLS 私钥及证书未复制；需为自己的域名重新配置。
- 首次上线前检查监听地址、端口暴露、权限、存储和数据库迁移；服务器现状以 [`生产部署手册.md`](生产部署手册.md) 为准。

## 上线前验收

1. 正常登录、无权限用户拒绝、不同用户数据隔离。
2. 后台校园配置能保存、校验、发布与回滚。
3. 校园页面基于已绑定知识库回答，引用真实来源；依据不足时明确说明。
4. 修改客户端知识库 / Skill / 模式参数不能越过校园策略。
5. 断线重连、历史记录与运行状态一致。
6. 桌面和手机页面可用。
7. 比赛空间/智能体数量按赛事平台规则验收。
