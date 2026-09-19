# 处理校园或办公网络的方法

| 项 | 内容 |
| --- | --- |
| 用途 | 本机开发时连接 auth-api、agent-api 与数据库的网络排障（2026-09-19 按 Java 下线后的代理目标核对） |
| 关系 | 日常起停以 [`本地热更新开发工作流.md`](本地热更新开发工作流.md) 为准。本文只补网络排障 |
| auth-api | 开发模式 `/api`、`/upload` 代理到 `127.0.0.1:9090`（`.env.development` 的 `VITE_PROXY`）。不要把 Vite 代理改到旧 Java 地址 |

---

## 1. 两条链路，不要混修

```text
浏览器 → http://127.0.0.1:3200
           ├─ /api、/upload     → 127.0.0.1:9090     auth-api（本机进程或 docker-compose.dev.yml 发布的端口）
           └─ /agent-api        → 127.0.0.1:8000     本机 Agent API 或 Docker 映射端口
                                      ├─ MySQL / PostgreSQL / Qdrant
                                      └─ 模型网关（可选）
```

页面 HTML 200 只说明 Vite 能返回页面。登录失败应先检查 auth-api 代理与 `/sys/login` 响应；对话 503 可能来自 Runtime schema、Worker、鉴权回源或其他依赖。先读 `/health/ready` 的具体检查结果。

不要用 ping 判断内网服务是否可用，只认 TCP 与 HTTP 响应。

## 2. 本机 auth-api（`127.0.0.1:9090`）

系统代理或 TUN 模式可能劫持 `127.0.0.1`，表现为 TCP 能通但 HTTP 空响应。

处理：

1. 把本机回环地址和局域网服务设为直连，或临时关闭 TUN 后再测。
2. 确认当前网络能访问自己的数据库 / 模型服务，而不是误连到其他环境。
3. 需要学校或办公 VPN 时，只把它当作到达自己服务的通道；不要把历史环境地址写进源码或提交到 Git。

开发前端：

```powershell
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:http_proxy,Env:https_proxy,Env:ALL_PROXY,Env:all_proxy -ErrorAction SilentlyContinue
pnpm exec vite --mode development --port 3200 --host 127.0.0.1
```

## 3. 一分钟自检

```powershell
Test-NetConnection 127.0.0.1 -Port 9090
Test-NetConnection 127.0.0.1 -Port 5432
curl.exe -sS -m 5 -o NUL -w "vite %{http_code}`n" http://127.0.0.1:3200/
curl.exe -sS -m 5 -w "`nhealth %{http_code}`n" http://127.0.0.1:8000/health/ready
```

入口：`http://127.0.0.1:3200/center/chat/campus`。TCP 成功只证明目标端口可连接，健康检查也不等于真实对话已成功。
