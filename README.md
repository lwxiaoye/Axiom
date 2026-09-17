# AXIOM Campus Agents

AXIOM 校园智能体项目。仓库：https://github.com/lwxiaoye/Axiom

这是从提供的完整工作区复刻并脱敏的 Vue 前端 + Python Agent API 底座，保留原有功能实现和未提交源码改动。
现有校园助手是基于已审核知识库与官方网页的只读问答产品；十智能体协作、校园事务状态和学校业务接入仍需开发。

## 先读这些文档

- [项目拆解与校园复用路线](docs/AXIOM-项目拆解与复用路线.md)
- [本地复刻、依赖与配置](docs/AXIOM-本地复刻与配置.md)
- [脱敏范围与验证结果](docs/AXIOM-脱敏与验收.md)
- [来源与第三方声明](NOTICE.md)

## 启动前提

前端可启动开发服务器，但登录和真实问答依赖独立的 Java 业务后端、MySQL、PostgreSQL、模型网关，以及知识检索服务。
此仓库不包含 Java 后端源码、原学校数据库、知识资料或任何可用服务凭据。
环境模板中的 CHANGE_ME 不能直接用于运行。详情及接口清单见上述文档。

```powershell
pnpm install --frozen-lockfile
Copy-Item .env.local.example .env.development.local
# 配置自己的 Java 地址与登录协议参数
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

只启动本仓库能提供的栈：MySQL、PostgreSQL、Qdrant、Agent API、Worker。Java 业务后端、模型网关和学校知识不在镜像里。

```powershell
Copy-Item deploy/local/.env.example deploy/local/.env
# 把 CHANGE_ME 换成自己的本地密码
docker compose --env-file deploy/local/.env -f docker-compose.local.yml up -d --build
curl.exe http://127.0.0.1:8000/health/ready
```

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
