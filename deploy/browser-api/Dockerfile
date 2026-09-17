# agent-browser 镜像 —— 官方 Playwright 基础镜像 + 两个 CLI，全部烤进镜像层。
#
# 装两个东西：
#   ① @playwright/mcp   —— 主用。微软官方开源(Apache-2.0)的 MCP 服务器，自带
#                          browser_navigate / browser_click / browser_type /
#                          browser_snapshot(可访问性树带 ref) / browser_take_screenshot
#                          等一整套工具，我们不用自己写工具层。
#   ② playwright        —— 备用。提供 `playwright run-server`(ws 端点)，给「沙箱里写脚本
#                          批量抓取」那种场景用：沙箱只装 pip 客户端(~50MB、不下浏览器)
#                          chromium.connect() 连过来，浏览器仍然跑在本服务里。
#
# 为什么必须自己 build 一层（官方镜像不够用）：
#   实测 mcr.microsoft.com/playwright:v1.61.0-noble 的全局 node_modules 只有
#   corepack / npm / yarn —— **不带 playwright 这个 npm 包**，`which playwright` 是空的。
#   它带的是浏览器二进制(/ms-playwright: chromium/firefox/webkit/ffmpeg)和 node v24。
#
# 为什么装在 build 期而不是启动时 npx：
#   ① 启动时 `npx --yes xxx` 是**运行期外部依赖**，每次冷启动都要连 npm registry。
#      服务器出网不一定稳(agent-api 的 Dockerfile 就是因为 pypi.org 不可达才改走清华镜像)，
#      把浏览器服务的可启动性押在 registry 上不合适。
#   ② 版本会随 registry 漂。钉进镜像层才叫可复现。
#
# 构建：docker compose build      （或 docker build -t agent-browser:local .）
ARG PLAYWRIGHT_VERSION=1.61.0
# 基础镜像仓库可换：国内服务器拉不动 mcr.microsoft.com 时改成内网 registry 的镜像地址，
#   docker build --build-arg BASE_IMAGE_REPO=<你的registry>/playwright ...
ARG BASE_IMAGE_REPO=mcr.microsoft.com/playwright
FROM ${BASE_IMAGE_REPO}:v${PLAYWRIGHT_VERSION}-noble

# FROM 之后 ARG 要重新声明才可见（Dockerfile 作用域规则）
ARG PLAYWRIGHT_VERSION
# @playwright/mcp 的版本和 playwright 版本是绑死的（它 dependencies 里钉了精确的 alpha 版）。
# 0.0.76 -> playwright 1.61.0-alpha-*，与基础镜像 v1.61.0-noble 同一个 minor，浏览器
# 版本大概率复用镜像里已有的 chromium-1228。挑版本前先核对：
#   curl -s https://registry.npmjs.org/@playwright/mcp | jq '.versions|to_entries|map(...)'
# 注：latest 0.0.78 绑的是 1.62.0-alpha，而官方镜像还没发 v1.62.0 tag，会多下一份 chromium。
ARG MCP_VERSION=0.0.76

# ───── 国内环境的两个旁路（默认值就是为国内服务器选的）─────
# npm 源：registry.npmmirror.com 是全量镜像，国内快且稳（实测 200）。想用官方：
#   --build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com
# 浏览器二进制下载源。**实测 npmmirror 没有镜像这些 revision**
# （npmmirror.com/mirrors/playwright/... 302 到 cdn.npmmirror.com 后 404），
# 所以默认留空 = 走上游 cdn.playwright.dev（Azure CDN，国内通常能连，就是慢）。
# 有内网镜像就传进来：--build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://<内网>/playwright
ARG PLAYWRIGHT_DOWNLOAD_HOST=

# playwright 包的 postinstall 默认会去下浏览器；基础镜像的 /ms-playwright 已经有了
# （PLAYWRIGHT_BROWSERS_PATH 也已指向它），先跳过，下面再按各 core 绑定的版本精确补。
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

RUN npm config set registry "${NPM_REGISTRY}" \
    && echo "npm registry = $(npm config get registry)" \
    && npm install -g "playwright@${PLAYWRIGHT_VERSION}" "@playwright/mcp@${MCP_VERSION}" \
    && npm cache clean --force

# 关键一步：给**每一个** playwright-core 补它自己要的 chromium。
#
# 这里有个实测坑，别简化成 `find ... | head -1`：装完之后镜像里有两份 playwright-core，
# 要的 chromium revision **不一样**——
#   /usr/lib/node_modules/playwright/node_modules/playwright-core          1.61.0        -> chromium-1228
#   /usr/lib/node_modules/@playwright/mcp/node_modules/playwright-core     1.61.0-alpha-*-> chromium-1226
# 基础镜像只带 1228。只对第一个跑 install 的话是 4s 的 no-op，build 绿灯，
# 而 MCP 一启动就 "browser is not installed"——缺失被 no-op 掩盖了。
# 所以遍历所有 core 逐个装（已有的自然 no-op），装完再断言 MCP 那份要的 revision
# 真的落到了 /ms-playwright 上。这条断言才是能抓住此类错的东西。
#
# ⚠️ 国内环境注意：这一步要从 cdn.playwright.dev 下 ~270MB（chromium + headless shell）。
# 实测没有任何 @playwright/mcp 版本的 chromium revision 能对上官方基础镜像自带的那个
# （1.61线要1226 / 镜像有1228；1.62线要1229或1232；1.60线要1217~1223），所以这次下载
# 躲不掉。慢或不通时两个办法：传 PLAYWRIGHT_DOWNLOAD_HOST 指向内网镜像，
# 或在能出网的机器上 build 完用 ./scripts/save-image.sh 导出，服务器 docker load（见 README）。
RUN set -eu; \
    if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST}" ]; then \
      export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}"; \
      echo "==> 浏览器下载源: ${PLAYWRIGHT_DOWNLOAD_HOST}"; \
    else \
      echo "==> 浏览器下载源: 上游 cdn.playwright.dev（未指定内网镜像）"; \
    fi; \
    found=0; \
    for d in $(find /usr/lib/node_modules -maxdepth 4 -type d -name playwright-core); do \
      found=1; \
      echo "==> install chromium for $d ($(node -e "console.log(require('$d/package.json').version)"))"; \
      PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=0 node "$d/cli.js" install chromium; \
    done; \
    test "$found" = 1 || { echo "找不到任何 playwright-core，npm 安装失败了"; exit 1; }; \
    MCP_CORE=/usr/lib/node_modules/@playwright/mcp/node_modules/playwright-core; \
    test -d "$MCP_CORE" || MCP_CORE="$(find /usr/lib/node_modules/@playwright -maxdepth 4 -type d -name playwright-core | head -1)"; \
    REV="$(node -e "const b=require('$MCP_CORE/browsers.json');console.log(b.browsers.find(x=>x.name==='chromium').revision)")"; \
    echo "==> MCP 要的 chromium revision: $REV"; \
    test -d "/ms-playwright/chromium-$REV" \
      || { echo "断言失败：/ms-playwright/chromium-$REV 不存在，MCP 起来会说 browser is not installed"; ls /ms-playwright; exit 1; }; \
    chmod -R a+rX /ms-playwright; \
    ls /ms-playwright

# 构建期自检：两个 CLI 都在、版本对得上、子命令存在。
# 装挂了要在 build 期炸，而不是等运行时 healthcheck 慢慢红。
RUN playwright --version \
    && playwright run-server --help > /dev/null \
    && playwright-mcp --version

# 运行期无需 root：浏览器不写宿主任何东西，/ms-playwright 全局可读
USER pwuser

# MCP 的输出目录（截图/trace/网络日志落盘用）。放容器内临时路径，不挂宿主卷：
# 这些是过程产物，不该长期留存，容器重建即弃。
ENV PLAYWRIGHT_MCP_OUTPUT_DIR=/tmp/pw-output
