#!/usr/bin/env bash
# 离线搬运 —— 给「服务器拉不动 mcr.microsoft.com / 连不上 cdn.playwright.dev」用。
#
# 在一台能正常出网的机器上跑本脚本，把镜像导成一个 .tar.gz，scp 到服务器 docker load，
# 服务器上就一个字节都不用下。这是国内受限网络下最可靠的路子。
#
# ⚠️ 架构必须对齐：服务器基本都是 linux/amd64，而 Apple Silicon 的 Mac 默认 build 出
#    arm64 镜像，load 上去起不来。在 Mac 上导给服务器用，务必带 --platform：
#      PLATFORM=linux/amd64 ./scripts/save-image.sh
#    （走 QEMU 模拟，慢；有条件就直接在一台能出网的 amd64 Linux 上跑本脚本。）
#
# 用法：
#   ./scripts/save-image.sh                      # 用当前架构
#   PLATFORM=linux/amd64 ./scripts/save-image.sh # 交叉导出给 amd64 服务器
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] && { set -a; source .env; set +a; }
VER="${PLAYWRIGHT_VERSION:-1.61.0}"
MCPV="${MCP_VERSION:-0.0.76}"
IMAGE="agent-browser:${VER}-mcp${MCPV}"
OUT="agent-browser-${VER}-mcp${MCPV}${PLATFORM:+-$(echo "$PLATFORM" | tr '/' '-')}.tar.gz"

BUILD_ARGS=(
  --build-arg "PLAYWRIGHT_VERSION=${VER}"
  --build-arg "MCP_VERSION=${MCPV}"
)
[[ -n "${NPM_REGISTRY:-}" ]] && BUILD_ARGS+=(--build-arg "NPM_REGISTRY=${NPM_REGISTRY}")
[[ -n "${PLAYWRIGHT_DOWNLOAD_HOST:-}" ]] && BUILD_ARGS+=(--build-arg "PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST}")

echo "═══ 构建 $IMAGE ${PLATFORM:+（平台 $PLATFORM）}"
# 注意用 docker build 而不是 compose：要 --platform，且要确保镜像以本地单架构形式落地
# （buildx 默认可能只把结果留在 build cache 里，--load 强制落到本地镜像库）。
docker build ${PLATFORM:+--platform "$PLATFORM"} "${BUILD_ARGS[@]}" --load -t "$IMAGE" .

echo "═══ 导出 $OUT"
docker save "$IMAGE" | gzip -1 > "$OUT"
ls -lh "$OUT"

cat <<EOF

─────────────────────────────────────────────────────
搬到服务器上：

  scp $OUT <用户>@<服务器>:/tmp/
  ssh <用户>@<服务器>
  gunzip -c /tmp/$OUT | docker load

然后把本目录（不含大文件）也拷过去，在目录里：

  ./deploy.sh --no-build     # 直接用已 load 的镜像起，不再 build

确认镜像已在：docker images | grep agent-browser
─────────────────────────────────────────────────────
EOF
