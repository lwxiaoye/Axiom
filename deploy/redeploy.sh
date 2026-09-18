#!/usr/bin/env bash
# Axiom 服务器重新发布脚本（3GB 内存机器专用）
#   ./deploy/redeploy.sh frontend   改了前端代码后重新构建 dist + 换镜像
#   ./deploy/redeploy.sh backend    改了 agent-api / auth-api 后重建后端镜像
#   ./deploy/redeploy.sh all        两者都做
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose --env-file deploy/local/.env -f docker-compose.local.yml -f docker-compose.server.yml"
TARGET="${1:-frontend}"

# 前端构建的内存全部靠 zram 兜底：溢出到磁盘 swap 会导致数十 GB 的换页
# 抖动，构建永远跑不完。zstd 压缩比约 3:1，解压是 GB/s 级。
ensure_zram() {
  local want=$((6144*1024*1024))
  if [ "$(cat /sys/block/zram0/disksize 2>/dev/null)" != "$want" ] \
     || ! grep -q 'zstd\]' /sys/block/zram0/comp_algorithm 2>/dev/null; then
    echo "==> 配置 zram (6G / zstd / 最高优先级)"
    swapoff /dev/zram0 2>/dev/null || true
    echo 1 > /sys/block/zram0/reset
    echo zstd > /sys/block/zram0/comp_algorithm
    echo "$want" > /sys/block/zram0/disksize
    mkswap /dev/zram0 >/dev/null 2>&1
    swapon -p 100 /dev/zram0
  fi
  sysctl -w vm.page-cluster=0 vm.swappiness=100 >/dev/null
}

build_frontend() {
  echo "==> 释放内存（前端构建需要几乎全部内存）"
  $COMPOSE stop >/dev/null 2>&1 || true
  ensure_zram
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

sysctl -w vm.swappiness=60 >/dev/null 2>&1 || true
# 对话模型经 grok2api 提供，但它属于另一套 compose，只发布在宿主机回环端口上。
# agent-api 要按容器名访问它，必须与之同网；docker network connect 是运行时操作，
# grok2api 重建后会丢失，因此每次发布都幂等地重连一次。
ensure_grok_network() {
  docker inspect grok2api >/dev/null 2>&1 || return 0
  local net
  net="$(docker inspect axiom-agent-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | awk '{print $1}')"
  [ -n "$net" ] || net=axiom_default
  if ! docker inspect grok2api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | grep -q "$net"; then
    docker network connect "$net" grok2api 2>/dev/null && echo "==> 已将 grok2api 接入 $net"
  fi
}

echo "==> 拉起全部服务"
$COMPOSE up -d
ensure_grok_network
echo "==> 等待健康检查"
for i in $(seq 1 30); do
  sleep 5
  if curl -fsS -o /dev/null http://127.0.0.1:3200/healthz 2>/dev/null; then echo "前端 OK"; break; fi
done
$COMPOSE ps
echo "==> 完成：https://clinirag.top"
