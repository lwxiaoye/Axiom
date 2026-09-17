#!/usr/bin/env bash
# 可选 ws 端点（--profile ws）的冒烟。宿主不需要装 node / playwright ——用同一个镜像起一次性
# 容器来跑，从「另一个容器」经 ws 连过来，与沙箱将来 chromium.connect() 的路径一致。
#
# 用法：./scripts/smoke_ws.sh [目标URL]
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] && { set -a; source .env; set +a; }

VER="${PLAYWRIGHT_VERSION:-1.61.0}"
MCPV="${MCP_VERSION:-0.0.76}"
IMAGE="agent-browser:${VER}-mcp${MCPV}"
WS_PATH="${BROWSER_WS_PATH:-CHANGE-ME-A-LONG-RANDOM-STRING}"
TARGET="${1:-${TARGET_URL:-https://example.com/}}"

if ! docker inspect agent-browser-ws >/dev/null 2>&1; then
  echo "✗ agent-browser-ws 容器不存在。ws 端点是可选的，起它："
  echo "    docker compose --profile ws up -d"
  exit 1
fi

# 跟着容器实际所在的网络走，不猜网络名（compose 默认名 = <项目目录名>_agent-network）
NET="$(docker inspect agent-browser-ws \
        --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"

mkdir -p tmp
docker run --rm \
  --network "$NET" \
  -v "$PWD/scripts:/smoke:ro" \
  -v "$PWD/tmp:/out" \
  -e WS_ENDPOINT="ws://agent-browser-ws:3000/${WS_PATH}" \
  -e TARGET_URL="$TARGET" \
  -e NODE_PATH=/usr/lib/node_modules \
  --entrypoint node \
  "$IMAGE" \
  /smoke/smoke_ws.mjs
