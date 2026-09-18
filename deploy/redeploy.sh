#!/usr/bin/env bash
# Axiom 服务器重新发布脚本（3GB 内存机器专用）
#   ./deploy/redeploy.sh frontend   改了前端代码后重新构建 dist + 换镜像
#   ./deploy/redeploy.sh backend    改了 agent-api / auth-api 后重建后端镜像
#   ./deploy/redeploy.sh all        两者都做
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose --env-file deploy/local/.env -f docker-compose.local.yml -f docker-compose.server.yml"
TARGET="${1:-frontend}"

build_frontend() {
  echo "==> 释放内存（前端构建需要几乎全部内存）"
  $COMPOSE stop >/dev/null 2>&1 || true
  sync; echo 3 > /proc/sys/vm/drop_caches || true

  echo "==> 构建 dist（heap 4096，zram 兜底）"
  export PATH="$PWD/node_modules/.bin:$PATH"
  set -a; . deploy/local/.env; set +a
  export VITE_LOGIN_AES_KEY="${AXIOM_LOGIN_AES_KEY}"
  export VITE_LOGIN_AES_IV="${AXIOM_LOGIN_AES_IV}"
  export VITE_REQUEST_SIGNATURE_SALT="${AXIOM_REQUEST_SIGNATURE_SALT:-AxiomSignSalt}"
  export VITE_LOCAL_DEMO_USERNAME="${AXIOM_LOCAL_DEMO_USERNAME:-}"
  export VITE_LOCAL_DEMO_PASSWORD="${AXIOM_LOCAL_DEMO_PASSWORD:-}"
  rm -rf dist
  cross-env NODE_ENV=production NODE_OPTIONS=--max-old-space-size=4096 vite build
  esno ./build/script/postBuild.ts
  node scripts/bust-theme-css.mjs

  echo "==> 打包 frontend 镜像"
  $COMPOSE build frontend
}

build_backend() {
  echo "==> 重建后端镜像（逐个来，避免并行吃爆内存）"
  $COMPOSE build auth-api
  $COMPOSE build agent-api
}

case "$TARGET" in
  frontend) build_frontend ;;
  backend)  build_backend ;;
  all)      build_frontend; build_backend ;;
  *) echo "用法: $0 [frontend|backend|all]"; exit 1 ;;
esac

echo "==> 拉起全部服务"
$COMPOSE up -d
echo "==> 等待健康检查"
for i in $(seq 1 30); do
  sleep 5
  if curl -fsS -o /dev/null http://127.0.0.1:3200/healthz 2>/dev/null; then echo "前端 OK"; break; fi
done
$COMPOSE ps
echo "==> 完成：https://clinirag.top"
