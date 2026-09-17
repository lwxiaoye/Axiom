# Vue 前端 Docker 使用说明

在仓库根目录执行。前端为 Vue 3 / TypeScript / Vite，构建阶段使用 Node 22、pnpm 10.15.1，运行阶段为 Nginx 静态服务，无需 Java。

## 已有后台，只添加或更新前端

```powershell
docker compose --env-file deploy/local/.env -f docker-compose.local.yml build frontend
docker compose --env-file deploy/local/.env -f docker-compose.local.yml up -d --no-deps --no-build frontend
docker exec axiom-frontend nginx -t
curl.exe --noproxy "*" http://127.0.0.1:3200/healthz
```

浏览器打开 http://127.0.0.1:3200/，或点 Docker Desktop 中 `axiom-frontend` 的 `3200:80`。`agent-worker` 是后台任务进程，不需要浏览器端口；8000 是接口端口，不是 Vue 页面。

新环境按根 README 准备私有 `deploy/local/.env` 后启动完整 Compose。不要覆盖已存在的配置或执行 `down -v`。默认账号 `admin`；密码为私有文件里的 `AXIOM_ADMIN_PASSWORD`，登录仍需图形验证码。不要上传该文件、将密码写入源码或放入 `VITE_*`。

## 路由和构建

| 路径 | 处理 |
| --- | --- |
| `/`、Vue 深层路由 | SPA 回退到 index.html |
| `/api/*` | 去掉 `/api` 后转发 auth-api:9090 |
| `/agent-api/*` | 保留前缀转发 agent-api:8000，关闭缓冲以支持 SSE |
| `/upload/*` | 转发认证/业务兼容服务；当前服务不代表实现了完整上传业务 |
| `/healthz` | 仅检查 Nginx 可用 |
| `.mjs` | application/javascript，兼容 PDF 模块 Worker |
| 不存在的静态文件、隐藏文件 | 404、403，不返回伪成功页面 |

Agent 健康检查在后端根路径 `/health/ready`，直接访问 http://127.0.0.1:8000/health/ready。`/agent-api/health/ready` 并不存在；不能用这个 404 判断代理故障。

`AXIOM_LOGIN_AES_KEY`、`AXIOM_LOGIN_AES_IV`、`AXIOM_REQUEST_SIGNATURE_SALT` 是兼容旧登录协议的公开前端参数，必须与 auth-api 对齐，改变后需要重建前端。它们不是模型密钥，也不能代替 HTTPS。镜像构建的专用 ignore 文件仅允许必要源码、已审核的前端默认环境文件，不包含私有 deploy/local/.env、历史归档、证书和 node_modules。

页面启动不代表模型、用户模型凭据、知识索引和完整业务接口已经配置。详细缺项及测试结果见 [检查报告](../../docs/AXIOM-全项目检查报告-2026-09-17.md)。
