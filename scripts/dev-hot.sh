#!/usr/bin/env bash
# 本地热更新入口（对齐 docs/本地热更新开发工作流.md）
# 日常：依赖 Docker 常驻 + 前端 Vite HMR + 后端 restart，不烤镜像。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
用法: ./scripts/dev-hot.sh <command>

  up            拉起 PG/Qdrant + agent-api + agent-worker（不 rebuild）
  restart-api   仅重启 agent-api + agent-worker（改 Python 后默认这招）
  frontend      启动 Vite（mode=local80，:3200，HMR）
  status        看容器与本机 8000/3200
  help          本帮助

日常循环:
  1) ./scripts/dev-hot.sh up
  2) ./scripts/dev-hot.sh frontend   # 浏览器开 http://localhost:3200/center/chat
  3) 改前端 → 自动 HMR
  4) 改后端 → ./scripts/dev-hot.sh restart-api
  5) 只有改依赖 / 要演示 :80 烤镜像时才 docker build
EOF
}

cmd_up() {
  exec "$ROOT/scripts/agent-api-docker.sh" up
}

cmd_restart_api() {
  exec "$ROOT/scripts/agent-api-docker.sh" restart
}

cmd_frontend() {
  cd "$ROOT"
  # local80：Java 学校 + agent-api 本机 8000（见 .env.local80）
  if [ -f .env.local80 ]; then
    echo "→ Vite mode=local80  port=3200  (HMR)"
    echo "  打开 http://localhost:3200/center/chat"
    exec pnpm exec vite --mode local80 --port 3200 --host
  fi
  echo "→ 无 .env.local80，回退 pnpm dev"
  exec pnpm dev
}

cmd_status() {
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
    | grep -E 'NAMES|agent-|base-platform' || true
  echo "---"
  curl -sS -o /dev/null -w 'agent-api :8000/docs  %{http_code}\n' http://127.0.0.1:8000/docs || true
  curl -sS -o /dev/null -w 'vite :3200          %{http_code}\n' http://127.0.0.1:3200/ || true
}

main() {
  case "${1:-help}" in
    up) cmd_up ;;
    restart-api) cmd_restart_api ;;
    frontend) cmd_frontend ;;
    status) cmd_status ;;
    help|-h|--help) usage ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
