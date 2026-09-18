#!/usr/bin/env bash
# 在服务器上以开发模式提供前端（Vite dev server，端口 3200，支持热更新）。
# 后端仍由 docker compose 提供：auth-api 127.0.0.1:9090、agent-api 127.0.0.1:8000
#   ./deploy/dev-server.sh            前台运行，Ctrl-C 停止
#   ./deploy/dev-server.sh --daemon   后台运行，日志见 /root/axiom/dev-server.log
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/node_modules/.bin:$PATH"
export NODE_OPTIONS="--max-old-space-size=1536"

# dev server 只监听回环地址：公网访问一律走 nginx + HTTPS。
# vite.config.ts 里的 host:true 是给本地局域网开发用的，这里用 --host 覆盖。
BIND="${AXIOM_DEV_BIND:-127.0.0.1}"
# 页面的公网域名，供 HMR websocket 回连（见 vite.config.ts 的 hmr 配置）
export VITE_DEV_PUBLIC_HOST="${VITE_DEV_PUBLIC_HOST:-clinirag.top}"
if [ "${1:-}" = "--daemon" ]; then
  nohup vite --host "$BIND" > dev-server.log 2>&1 &
  echo "dev server 已后台启动 (pid $!)，日志: $PWD/dev-server.log"
else
  exec vite --host "$BIND"
fi
