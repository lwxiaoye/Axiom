# 本地复刻与配置

## 仓库位置

新 Git 根目录为 `D:\lwxiaoye\Axiom`，origin 指向 `https://github.com/lwxiaoye/Axiom.git`。
本地原始备份目录与 `__MACOSX` 已被忽略；复制或压缩整个目录仍会包含它们，不要将其当成发布包。
本地 Docker 使用仓库根目录 `docker-compose.local.yml` 与 `deploy/local/.env`（后者不入库）。

## 前端

当前本地栈新增 `auth-api`（FastAPI 单管理员兼容认证），可以不使用 Java 完成基本登录。它不实现用户写入管理或知识检索；这些缺失能力返回明确错误，不再以空数据伪装成功。下面有关 Java 的描述是上游完整业务接口依赖，不能理解为当前已提供完整 Java 或 Python 业务平台。认证配置、持久会话和测试见 [auth-api/README.md](../auth-api/README.md)。

```powershell
cd D:\lwxiaoye\Axiom
pnpm install --frozen-lockfile
Copy-Item .env.local.example .env.development.local
pnpm dev --host 127.0.0.1 --port 3200
```

`.env` 为可公开的产品默认配置；`.env.development` 仅代理到本机服务；`.env.production` 使用同源 API。
自己的服务地址写到被忽略的 `.env.development.local`。不要将模型密钥、数据库密码写入任何 `VITE_*` 变量。

需要与 Java 对齐的公开兼容参数：

| 参数 | 用途 | 未配置时 |
|---|---|---|
| `VITE_LOGIN_AES_KEY` | 原登录协议 AES key，16/24/32 字节 | 登录明确报配置错误 |
| `VITE_LOGIN_AES_IV` | 原登录协议 AES IV，16 字节 | 登录明确报配置错误 |
| `VITE_REQUEST_SIGNATURE_SALT` | 原请求签名协议附加值 | 需签名请求明确报错 |
| `VITE_BPMN_TASK_LISTENER_CLASS` | 可选旧 BPMN 用户任务监听 Java 类名 | 旧 BPMN 相关功能未配置 |
| `VITE_BPMN_SEND_DELEGATE_CLASS` | 可选旧 BPMN 委托 Java 类名 | 旧 BPMN 相关功能未配置 |

上述值会进入浏览器包，不提供秘密保护；部署仍需 HTTPS 和服务端身份/权限校验。
没有假造新 Java 包名；实际类名应由所接入的 Java 后端提供。

## Python 服务

```powershell
cd agent-api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# 填写 .env，准备数据库与模型后再运行
.venv\Scripts\python.exe -u run.py
```

当前 `run.py` 读取自身目录的 `.env`，存在 `.env.dev` 时覆盖。历史文档提到的 `.env.han` 与当前源码不一致，以源码为准。
API 和 Worker 必须使用同一套数据库和连接器加密密钥；不要接回原工作区的服务。

## 必要外部服务

| 服务 | 是否随本仓库提供 | 说明 |
|---|---|---|
| Vue 前端 | 源码已提供 | Vite 默认 3200 |
| Python Agent API + Worker | 源码已提供 | API 默认 8000，依赖下列业务服务 |
| Java 认证/业务后端 | **未提供源码** | 登录、用户权限、应用目录、知识库管理和相关查询 |
| MySQL 业务库 | 未提供数据 | 需要 Java 基线建库及 Python 所属表迁移 |
| Runtime PostgreSQL | 有配置/迁移材料 | 持久 Run、事件、工具结果、Worker 队列 |
| 模型网关/模型账号 | 有部署参考 | 新建自己的渠道/模型与访问配置 |
| Qdrant/向量模型 | 有适配代码 | 需重建本校知识索引 |
| 搜索、沙箱、浏览器、OCR | 有部分部署材料 | 按启用能力配置，不能把模板当已经运行的服务 |
| MinIO | 有适配 | 文件可按配置使用本地存储；不复制旧文件 |

新空库不能只运行 Python migrations 就假定整个 Java 业务系统已建好。

本地 Compose 的 `AXIOM_MODEL_BASE_URL` 只是模型 API 地址，可以连接本机网关或云端模型。栈内没有部署模型权重或推理服务，也不会自动生成模型凭据。
阅读 `agent-api/migrations/README.md`，按所接入 Java 版本准备业务基线后，分别执行 MySQL 与 Runtime PG 迁移。

## 校园知识依赖接口

`campus_services/java_knowledge.py` 明确调用：

- `GET /ai/knowledge/base/queryById`
- `GET /ai/knowledge/document/list`
- `GET /ai/knowledge/acl/list`

检索链还依赖 Java 知识检索接口和身份校验接口。需要保留相同的认证头、租户隔离、响应结构与权限语义。
这份清单是校园配置链的关键入口，不是整个 Java 平台全部接口的替代规格。

## 部署材料的状态

- Compose 中 PostgreSQL、模型网关相关密码改为环境变量要求或示例占位；需为实际部署填入新值。
- APISIX 的配置改为 `config.yaml.example`；先复制到同目录 `config.yaml` 并替换管理凭据，后者被 Git 忽略。
- 原 TLS 私钥及证书未复制；需为自己的域名重新配置。
- 历史部署文档与脚本保留作结构参考，地址已泛化；它们不构成一键可用的整套校园后端。
- 首次上线前检查监听地址、端口暴露、权限、存储和数据库迁移。后续本地验证已启动 AXIOM Docker 栈，前端入口为 http://127.0.0.1:3200/；未连接旧服务器。当前状态以 [2026-09-17 检查报告](AXIOM-全项目检查报告-2026-09-17.md) 为准。

## 上线前验收

1. 正常登录、无权限用户拒绝、不同用户数据隔离。
2. 后台校园配置能保存、校验、发布与回滚。
3. 校园页面基于已绑定知识库回答，引用真实来源；依据不足时明确说明。
4. 修改客户端知识库/Skill/子智能体参数不能越过校园策略。
5. 断线重连、历史记录与运行状态一致。
6. 桌面和手机页面可用。
7. 比赛空间/智能体数量按赛事平台规则验收。
