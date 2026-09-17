# AXIOM 本地认证服务

提供原 `/sys/*` 协议的最小兼容实现：单管理员登录、图形验证码、Token 校验/注销以及只读菜单、角色视图。不是完整用户管理平台，也不提供知识检索实现。

## 配置

使用根目录 `docker-compose.local.yml`，在被 Git 忽略的 `deploy/local/.env` 设置 `AXIOM_ADMIN_PASSWORD`（至少 12 字符的独立密码；空值和 CHANGE_ME 占位符会阻止启动）。默认用户名为 admin。不要把此服务直接作为多用户生产认证系统。

本地演示可在该文件显式设置 `AXIOM_LOCAL_DEMO_LOGIN=true`，允许至少 7 字符的密码。通过 `AXIOM_LOCAL_DEMO_USERNAME` 和 `AXIOM_LOCAL_DEMO_PASSWORD` 配置登录框预填值，重新构建前端后生效；这些值会公开在浏览器资源中。默认关闭，实际演示凭据仅放在被忽略的本地配置中。

前后端公开 AES 协议值必须一致：默认 key `AxiomCampusKey16`、IV `AxiomCampusIv16!`，均为 16 字节。已有 `.env.development.local` 等覆盖文件也必须与后端一致。AES 参数会进入浏览器包，不是秘密，公网部署仍需 HTTPS。

认证数据库由 `AXIOM_AUTH_DB` 指定，Compose 挂载专用 `axiom_auth_data` 卷。密码使用带随机盐的 PBKDF2 哈希，Token 仅保存 SHA-256 摘要。相同配置重启保留未过期会话；修改管理员账号/密码会撤销所有旧会话。验证码只能消费一次，登录及验证码按连接来源限流，不信任客户端伪造的转发头。经 Vite 代理的用户共享代理来源限额；此默认配置仅面向本地开发。

Agent API 默认 `AUTH_TOKEN_CACHE_TTL_SECONDS=0`，每次请求重新校验 Token，避免退出登录后还可利用五分钟旧缓存发起新请求。显式启用缓存将引入相应撤销延迟。已经受理的后台任务不会因注销自动取消。

## 错误行为

- 未登录访问受保护接口返回 HTTP 401。
- 未实现的接口（包括新增用户、写入配置等）返回 HTTP 404 和 `success=false`。
- 知识库详情、文档、ACL、检索接口在认证后返回 HTTP 503，明确提示业务服务未接入。
- AES 解密失败返回 HTTP 400，不接受明文回退。
- 登录/验证码超过请求限制返回 HTTP 429。

保留 `success/code/message/result` 响应结构及前端既有认证头，未删除校园 ACL 或发布门禁。

## 验证

在仓库根目录运行，测试只使用进程内 HTTP 和临时 SQLite，不连接业务数据库或历史部署：

```powershell
agent-api/.venv/Scripts/python.exe -m pytest auth-api/tests -q
pnpm exec node --test scripts/local-auth-login.test.cjs
```

代码修改不会自动替换已运行的旧容器。配置准备完成后，按根目录启动说明构建并应用新版本；首次使用此版本需要重新登录。
