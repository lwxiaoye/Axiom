#!/usr/bin/env bash
# agent-browser 网络边界自检 —— 两条门禁，都过才允许接进生产的主对话。
#
#   门禁 A（必须失败）：容器打不到私网 / 内网。浏览器会跟随重定向、执行页面 JS、发任意请求，
#                       一句藏在页面里的 prompt injection 就能让它去打内网业务后端，
#                       而且来源 IP 是内网可信地址。这比沙箱开网危险——沙箱里跑的代码是
#                       模型写的、能审；浏览器里跑的是别人网站的 JS。
#   门禁 B（必须成功）：容器能出公网。否则浏览器服务没有意义。
#
# 用法：
#   ./scripts/check-egress.sh
#   PRIVATE_TARGETS="http://127.0.0.1:9080/ http://10.0.0.1/" ./scripts/check-egress.sh
#
# 退出码：0 = 两条都过；1 = 有门禁未过。
# 没配出口代理时，门禁 A 预期是**不过**的 —— 这不是脚本坏了，是墙还没砌，见 README。
set -uo pipefail

CONTAINER="${CONTAINER:-agent-browser}"

# ⚠️ 关于门禁 A 的目标怎么选（这条决定了绿灯有没有意义）：
#   「连不上」有两种截然不同的原因——被防火墙 DROP，或者那个地址上根本没东西。
#   两者在客户端看起来一模一样（都是超时）。所以拿一个不存在的地址去测，
#   得到的绿灯是**空的**，什么也没证明。
#   门禁 A 的目标必须是「不加防护时确实连得通」的真实内网地址，比如你们的业务后端：
#     PRIVATE_TARGETS="http://127.0.0.1:9080/" ./scripts/check-egress.sh
#   正确的验法是前后对比：加防护前 REACHABLE，加防护后 超时/拒绝。
read -r -a PRIVATE <<< "${PRIVATE_TARGETS:-http://10.0.0.1/ http://192.168.0.1/ http://172.16.0.1/ http://169.254.169.254/latest/meta-data/}"
read -r -a PUBLIC  <<< "${PUBLIC_TARGETS:-https://example.com/}"
CUSTOM_PRIVATE="${PRIVATE_TARGETS:+yes}"
TIMEOUT_MS="${PROBE_TIMEOUT_MS:-8000}"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "✗ 容器 $CONTAINER 不存在，先 ./deploy.sh"
  exit 1
fi

# 用容器内的 node fetch 探测（镜像必有 node；不依赖 curl 是否安装）。
# 分三种结果，**不要把超时和明确拒绝混成一个「BLOCKED」**：
#   REACHABLE  连上了
#   REFUSED    明确拒绝（ECONNREFUSED / EHOSTUNREACH / DNS 失败）—— 这是真拦住了
#   TIMEOUT    超时 —— 可能被 DROP，也可能那个地址根本不存在，**不构成拦截的证据**
# 超时的目标重试一次：8s 一次偶发抖动就误判成「拦住了」，那正是最危险的假绿灯。
probe_once() {
  docker exec "$CONTAINER" node -e "
    const url = process.argv[1], ms = Number(process.argv[2]);
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), ms);
    fetch(url, { signal: ac.signal, redirect: 'manual' })
      .then(r => { clearTimeout(t); console.log('REACHABLE ' + r.status); })
      .catch(e => {
        clearTimeout(t);
        const n = e.name || '', m = String(e.cause?.code || e.message || '');
        if (n === 'AbortError' || /TIMEOUT|ETIMEDOUT/i.test(m)) console.log('TIMEOUT');
        else console.log('REFUSED ' + (m || n));
      });
  " "$1" "$TIMEOUT_MS" 2>&1 | tail -1
}

probe() {
  local out
  out="$(probe_once "$1")"
  # 只有超时才重试：明确拒绝和已连通都是确定结论
  if [[ "$out" == TIMEOUT* ]]; then
    out="$(probe_once "$1")"
  fi
  echo "$out"
}

fail=0
inconclusive=0

echo "门禁 A —— 私网 / 内网必须打不通（超时 ${TIMEOUT_MS}ms，超时会重试一次）："
for url in "${PRIVATE[@]}"; do
  out="$(probe "$url")"
  case "$out" in
    REACHABLE*) echo "  ✗ $url  →  $out   ← 通了，不合格"; fail=1 ;;
    REFUSED*)   echo "  ✓ $url  →  $out   ← 明确拒绝，确实拦住了" ;;
    TIMEOUT*)   echo "  ⚠ $url  →  超时   ← 不算证据：可能被 DROP，也可能这地址上本来就没东西"
                inconclusive=1 ;;
    *)          echo "  ? $url  →  $out"; inconclusive=1 ;;
  esac
done

echo "门禁 B —— 公网必须打得通："
for url in "${PUBLIC[@]}"; do
  out="$(probe "$url")"
  if [[ "$out" == REACHABLE* ]]; then
    echo "  ✓ $url  →  $out"
  else
    echo "  ✗ $url  →  $out   ← 出不去，浏览器服务不可用"
    fail=1
  fi
done

echo
if [[ $fail -ne 0 ]]; then
  echo "✗ 有门禁未过 —— 见 README「网络边界」。出口代理 + 私网段拒绝没落地前，"
  echo "  本服务可以本地验证，但不要接进生产的主对话。"
  exit 1
fi
if [[ $inconclusive -ne 0 ]]; then
  echo "⚠ 没有目标是「通的」，但有目标只是超时 —— 这个结论是弱的。"
  if [[ -z "$CUSTOM_PRIVATE" ]]; then
    echo "  你用的是默认目标，那些地址在你们网段可能根本不存在，绿灯是空的。"
    echo "  换成真实存在的内网地址再测，例如："
    echo "    PRIVATE_TARGETS=\"http://127.0.0.1:9080/\" ./scripts/check-egress.sh"
  else
    echo "  确认这些目标在**不加防护时确实连得通**，否则证明不了防护起了作用。"
  fi
  exit 2
fi
echo "✓ 两条门禁全过（私网目标均为明确拒绝，不是超时）。"
exit 0
