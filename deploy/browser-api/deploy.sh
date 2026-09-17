#!/usr/bin/env bash
# agent-browser 一键部署 —— 解压后在本目录执行：./deploy.sh
#
# 做四件事：build → up → 等 healthy → 跑冒烟（真抓一个页面 + 截图）。
# 冒烟不过会非零退出并打出诊断，不会假装部署成功。
#
#   ./deploy.sh              正常部署
#   ./deploy.sh --with-ws    额外起可选的 ws 端点（8090，给沙箱 chromium.connect 用）
#   ./deploy.sh --no-smoke   跳过冒烟（服务器完全不能出网时用）
#   ./deploy.sh --no-build   跳过构建，直接用已有镜像（配合 scripts/save-image.sh 离线搬运）
set -euo pipefail
cd "$(dirname "$0")"

WITH_WS=0
RUN_SMOKE=1
DO_BUILD=1
for arg in "$@"; do
  case "$arg" in
    --with-ws) WITH_WS=1 ;;
    --no-smoke) RUN_SMOKE=0 ;;
    --no-build) DO_BUILD=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg（--with-ws / --no-smoke / --no-build）"; exit 2 ;;
  esac
done

[[ -f .env ]] || { echo "✗ 缺 .env（本包自带，别删）"; exit 1; }
set -a; source .env; set +a
PORT="${BROWSER_HOST_PORT:-8089}"

# compose 命令：优先 docker compose（v2），退回 docker-compose（v1）
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "✗ 找不到 docker compose，也找不到 docker-compose"; exit 1
fi

PROFILE_ARGS=()
[[ $WITH_WS -eq 1 ]] && PROFILE_ARGS=(--profile ws)

# 包一层再调用。别直接写 "${DC[@]}" "${PROFILE_ARGS[@]}" ——bash 3.2（macOS 自带）在
# set -u 下展开**空数组**会报 "unbound variable" 直接退出（bash 4.4+ 才修）。
# ${ARR[@]+"${ARR[@]}"} 是兼容两代的写法：数组为空就整个消失。
dc() { "${DC[@]}" ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} "$@"; }

if [[ $DO_BUILD -eq 0 ]]; then
  IMG="agent-browser:${PLAYWRIGHT_VERSION:-1.61.0}-mcp${MCP_VERSION:-0.0.76}"
  echo "═══ 1/4 跳过构建（--no-build），检查镜像 $IMG 是否已在本机"
  docker image inspect "$IMG" >/dev/null 2>&1 || {
    echo "✗ 镜像 $IMG 不存在。先在能出网的机器上 ./scripts/save-image.sh 导出，"
    echo "  再在本机 gunzip -c <文件>.tar.gz | docker load"
    exit 1
  }
  echo "    ✓ 镜像已在"
else
  echo "═══ 1/4 构建镜像（首次要拉 ~2.5GB 基础镜像 + 下 ~270MB chromium，慢是正常的）"
  echo "    国内网络慢/不通 → 见 README「国内环境」，可走内网镜像或离线搬运"
  dc build
fi

echo "═══ 2/4 启动"
dc up -d

echo "═══ 3/4 等 healthy（最多 3 分钟）"
for i in $(seq 1 36); do
  st="$(docker inspect agent-browser --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)"
  printf '\r    [%02d/36] %s        ' "$i" "$st"
  case "$st" in
    healthy) echo; break ;;
    unhealthy)
      echo; echo "✗ 容器 unhealthy，最后 40 行日志："
      docker logs --tail 40 agent-browser; exit 1 ;;
  esac
  sleep 5
done
if [[ "$(docker inspect agent-browser --format '{{.State.Health.Status}}' 2>/dev/null)" != "healthy" ]]; then
  echo; echo "✗ 3 分钟内没到 healthy，最后 40 行日志："
  docker logs --tail 40 agent-browser; exit 1
fi

if [[ $RUN_SMOKE -eq 0 ]]; then
  echo "═══ 4/4 冒烟已跳过（--no-smoke）"
else
  echo "═══ 4/4 冒烟：MCP 握手 → 工具清单 → 真抓页面 → 截图"
  PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
  if ! "$PY" scripts/smoke_mcp.py "http://127.0.0.1:${PORT}"; then
    echo
    echo "✗ 冒烟没过。服务起来了但不能用，先别接 agent-api。最后 40 行日志："
    docker logs --tail 40 agent-browser
    exit 1
  fi
fi

echo
echo "─────────────────────────────────────────────────────"
echo "部署完成。MCP 端点：http://<本机IP>:${PORT}/mcp"
if [[ $WITH_WS -eq 1 ]]; then
  echo "ws  端点：ws://<本机IP>:${BROWSER_WS_HOST_PORT:-8090}/${BROWSER_WS_PATH}"
fi
echo
echo "⚠️  ${PORT} 没有认证。现在就把来源限制到 agent-api 那台机器，例如："
echo "      sudo ufw allow from <agent-api的IP> to any port ${PORT} proto tcp"
echo "      sudo ufw deny ${PORT}/tcp"
echo "    然后跑 ./scripts/check-egress.sh 看网络边界两条门禁。"
echo "─────────────────────────────────────────────────────"
