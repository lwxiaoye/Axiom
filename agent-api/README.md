# Agent API

AXIOM Agent 平台的 FastAPI Runtime。服务对外提供 `/agent-api` 前缀的 API：主对话 Run 由 HTTP 层受理、后台 `agent-worker` 执行；工作流、文件、Skill、知识库、连接器和平台配置使用同一 API 前缀，但与主对话 Harness 保持独立业务边界。

## 运行时与数据

- **FastAPI + LangGraph**：主对话 Harness、Run/事件回放、Plan、工具和 Worker。
- **MySQL（业务域）**：复用 Java 的 `ai_boot`，保存应用、聊天、知识库及相关业务数据。
- **PostgreSQL（Runtime 域）**：Run/Job、事件、计划、记忆、工具结果和 Provider 调用审计；LangGraph checkpoint 使用独立数据库/连接池，可共用 PG 实例。
- **Qdrant**：向量检索。项目不使用 pgvector。

日常在本机跑：`python -u ./run.py` 同时起 FastAPI `:8000` 和 worker。环境文件先读 `.env`，若存在 `.env.han` 再覆盖（本机 Java / 沙箱等个人项，不要提交）。Compose 的 `.env.dev` 只给 Docker 用，`run.py` 不读它。

`load_local_env()` 读取 `.env` 时保留进程中已设置的变量，`.env.han` 则覆盖同名值。`RUNTIME_DATABASE_URL` 为空时从 checkpoint 地址派生同实例的 `agent_runtime` 库。默认本机凭据密钥文件为 `agent-api/data/.connector_key`；启动器先初始化密钥，再让 API 与子 Worker 使用同一配置。Worker 异常退出后由启动器退避重启，退出启动器时回收它所拥有的 Worker。

## 开发入口

前端通过 `http://localhost:3200` 把 `/agent-api` 代理到本机 `:8000`。macOS Homebrew Python 受 PEP 668 保护，必须用项目 venv，不要 `pip install` 进系统，也不要 `--break-system-packages`。

```bash
python3.11 -m venv .venv                 # 仅首次
source .venv/bin/activate
pip install -r requirements.txt          # 仅首次或依赖变更
python -u ./run.py                       # 依赖已装好时只跑这一条
```

改 Python 后请跑服务的会话重启 `run.py`（默认不 `--reload`）。健康检查：`curl -fsS http://127.0.0.1:8000/health/ready`。该接口检查 Runtime schema 就绪和本地受管 Worker 存活；直接启动 API 的外置 Worker 部署不以本机子进程为条件。返回 ready 不证明最新 Python 已加载或模型任务已完成。API 文档：`http://localhost:8000/docs`。

更完整的前后端分工见仓库根目录 [`AGENTS.md`](../AGENTS.md) 与 [`docs/本地热更新开发工作流.md`](../docs/本地热更新开发工作流.md)。

## 主要接口

所有下列路径均带 `/agent-api` 前缀。

| 范围 | 主要接口 |
| --- | --- |
| 主对话 Run | `POST /chat/runs`、`GET /chat/runs/{run_id}`、`GET /chat/runs/{run_id}/events`、`POST /chat/runs/{run_id}/inputs`、`POST /chat/runs/{run_id}/cancel` |
| 会话与队列 | `/chat/threads`、`/chat/threads/{thread_id}/messages`、`/chat/threads/{thread_id}/queue` |
| 主对话附件 | `POST /chat/upload` |
| 面试状态 | `GET /chat/threads/{thread_id}/interview`；写操作仍通过共享 Run 的 `interview_input` 受理 |
| 用户文件与工作文件夹 | `/files`、`/files/folders`、`/files/upload`、`/files/{file_id}/download`、`/files/{file_id}/versions`；Thread 的 `workspace_folder_id` 绑定访问范围 |
| 内置智能体广场 | `GET /chat/builtin-apps`、`GET /chat/builtin-apps/{preset}`、`GET /marketplace/model/options`；管理端 `/campus-assistant/*` |
| 敏感工具审批 | `POST /gateway/approve`、`POST /gateway/reject`、`GET /gateway/calls`（`approval.required` 审批卡出口） |
| Skill 与知识库 | `/skill`、`/embedding-config` |
| 管理 | `/models`、`/master-config`、`/platform-config`、`/audit` |

接口的请求/事件契约，尤其是主对话 Run，不以本页的摘要替代：请以 [`docs/主对话-Agent-Harness-架构与开发规范.md`](../docs/主对话-Agent-Harness-架构与开发规范.md) 为准。

主对话与 presentation/campus_services/interview 共用 Harness。所有智能体都是代码内置、预置在广场的三个（校园百事通 / 演示文稿助手 / 面试助手）；用户不自建智能体，工作流编排、子智能体与对外 Agent API 已整体删除。

## 鉴权

客户端应携带 `X-Access-Token`，并兼容 `Authorization: Bearer <token>`。服务优先验证已配置密钥的签名网关身份头；否则回源 Java 的 `getUserInfo` 校验 token，并从可信响应构造用户、角色和租户上下文。不要把未签名的身份头视作可直接信任的用户身份。

## 迁移与验证

生产使用 MySQL 与 Runtime PostgreSQL 两条独立的 Alembic 迁移链。部署前、迁移命令及 `MIGRATE_ON_STARTUP` 的约束见 [`migrations/README.md`](migrations/README.md)。

按变更范围运行现有定向测试、`pytest` 或 `py_compile`；不要依赖已删除的 `phase7_smoke.py`，也不要将某一个历史 smoke 脚本宣传为全量验收。

本地迁移在 venv 里跑（不要 `docker exec`）：

```bash
source .venv/bin/activate
alembic -c migrations/alembic.ini -n mysql   upgrade head
alembic -c migrations/alembic.ini -n runtime upgrade head
```

改完 Python 不要自己重启进程；请跑服务的会话重启 `python -u ./run.py`。服务器部署见仓库 [`docs/生产部署手册.md`](../docs/生产部署手册.md)。
