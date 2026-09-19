# AXIOM Campus Agents

AXIOM 校园智能体项目。仓库：https://github.com/lwxiaoye/Axiom

Vue 3 前端 + FastAPI 智能体后端（agent-api）+ 轻量认证服务（auth-api）。面向一所学校部署：一个管理员、学生自助注册，
所有智能体由平台预置在「智能体广场」，用户不自建智能体。

现有能力（2026-09）：

- **主对话**：平台默认对话模型对所有登录用户生效（个人 Key 可选）；图片附件直接进多模态模型；文档附件解析；
  联网搜索（SearXNG + 平台重排模型）；@Skill；深度研究与计划模式。
- **预置智能体**：校园百事通（已审核知识库 + 学校官网白名单，管理员草稿 → 校验 → 发布）、演示文稿助手、面试助手。
- **我的知识库**：上传解析、切片正本落 MySQL + 向量入 Qdrant，向量 / 关键词 / 混合检索与重排，分段编辑与重建，检索日志与运营统计。
- **Skill 广场**：按用户隔离的个人技能（直接编写或 zip 导入、版本化），管理员可把自己的技能分发给全体用户或撤回。
- **隐私边界**：管理员看不到用户的会话、文件、知识库与技能（不存在即 404）；同一浏览器换人登录时本机状态按用户作用域清理。
- **管理页 /admin**：对话模型、向量/重排模型、联网搜索、校园百事通发布、用户列表——只有这五块。

## 先读这些文档

- [项目拆解与校园复用路线](docs/AXIOM-项目拆解与复用路线.md)
- [本地复刻、依赖与配置](docs/AXIOM-本地复刻与配置.md)
- [脱敏范围与验证结果](docs/AXIOM-脱敏与验收.md)
- [2026-09-17 全项目检查报告](docs/AXIOM-全项目检查报告-2026-09-17.md)
- [AI 修复指导书](docs/AXIOM-AI修复指导书.md)
- [来源与第三方声明](NOTICE.md)

## 启动前提

前端使用 Vue 3，智能体后端使用 FastAPI。本地 `auth-api` 可兼容基本登录接口，无需 Java；真实问答仍需模型服务、知识检索及相应业务配置。该认证服务不是完整 Java 业务后端的替代实现。
此仓库不包含 Java 后端源码、原学校数据库、知识资料或任何可用服务凭据。
环境模板中的 CHANGE_ME 不能直接用于运行。详情及接口清单见上述文档。

```powershell
pnpm install --frozen-lockfile
Copy-Item .env.local.example .env.development.local
# 模板默认匹配本地 auth-api；接其他业务后端时同步调整地址与登录协议参数
pnpm dev --host 127.0.0.1 --port 3200
```

校园入口：`/center/chat/campus`。保留登录与权限检查。

```powershell
cd agent-api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# 填写自己的服务地址/凭据，准备业务库并按 migrations/README.md 执行数据库迁移
.venv\Scripts\python.exe -u run.py
```

## 本地 Docker

启动本仓库能提供的栈：Vue 前端（Nginx）、MySQL、PostgreSQL、Qdrant、Agent API、Worker、FastAPI 认证服务。完整知识库业务后端、模型网关、模型权重和学校知识不在镜像里。

```powershell
if (!(Test-Path deploy/local/.env)) { Copy-Item deploy/local/.env.example deploy/local/.env }
# 填写数据库密码、连接器密钥和至少 12 位的独立管理员密码
docker compose --env-file deploy/local/.env -f docker-compose.local.yml up -d --build
curl.exe http://127.0.0.1:8000/health/ready
```

浏览器打开 **http://127.0.0.1:3200/**；Docker Desktop 的 `axiom-frontend` 显示 `3200:80`，点击宿主端口即可打开页面。前端通过同源 Nginx 转发认证和 Agent API。已有后台时仅添加前端，见 [前端 Docker 使用说明](deploy/frontend/README.md)。

Compose 项目名为 `axiom`。本机若已有其他 MySQL，宿主端口映射为 `3307`。容器内部仍使用 `mysql:3306`。
登录走 FastAPI 认证服务 `axiom-auth-api`（`127.0.0.1:9090`），默认用户名 `admin`，密码必须通过 `AXIOM_ADMIN_PASSWORD` 设置，没有内置密码，仍需输入图形验证码。Compose 配置中的本地端口仅绑定 `127.0.0.1`；旧容器需重新创建才会应用新绑定，以 `docker ps` 为准。
认证状态保存于 `axiom_auth_data` 卷；重启保留会话，修改配置中的账号或密码会撤销旧会话。当前仅支持一个管理员，不支持创建用户、编辑角色或知识库管理；未实现接口明确报错。详见 [本地认证服务](auth-api/README.md)。

`AXIOM_MODEL_BASE_URL` 配置模型 API 地址：可以是本机网关，也可以是兼容的云端服务。默认 `host.docker.internal:3001/v1` 只是地址，不表示模型已部署。仍需配置聊天模型及用户模型凭据；不能把密钥放入 `VITE_*`。
本地镜像 `agent-api/Dockerfile.local` 不含 LibreOffice；Office 文档转换需要生产 `Dockerfile`。
健康检查通过只说明 Runtime 和数据库已起来，不表示可以登录或回答校园问题。

`run.py` 实际加载 `.env`，存在 `.env.dev` 时再覆盖；新项目建议先只使用 `.env`。
本次已按 requirements.txt 与 requirements-dev.txt 安装本地虚拟环境；外部业务服务仍须单独准备。

## 发布前检查

```powershell
python scripts/check_release.py
git status --short
```

不要直接压缩整个本地目录：目录内可能保留被 Git 忽略的原始备份和依赖。
需要交付源码时按 Git 可见文件清单打包；不要包含旧历史、私有环境、证书或用户数据。
