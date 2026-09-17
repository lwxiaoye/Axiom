#!/usr/bin/env bash
# agent-api Docker 唯一入口：带互斥锁，合并多会话重启请求。
# 前端仍走 Vite :3200 HMR。本脚本不碰 web 容器、agent-browser、OpenSandbox、PG/Qdrant。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$ROOT/agent-api"
DEV_COMPOSE="$API_DIR/docker-compose.dev.yml"
LOCAL_SANDBOX_COMPOSE="$API_DIR/docker-compose.opensandbox-local.yml"
STATE_DIR="$ROOT/tmp/agent-api-docker"
INBOX="$STATE_DIR/inbox"
DONE="$STATE_DIR/done"
LOCK_DIR="$STATE_DIR/lock.d"
LAST_FILE="$STATE_DIR/last.txt"
SANDBOX_MODE_FILE="$STATE_DIR/opensandbox-mode"

REMOTE_SANDBOX_DOMAIN="http://127.0.0.1:8091"
LOCAL_SANDBOX_DOMAIN="http://host.docker.internal:8091"

STALE_SEC_DEFAULT=900
STALE_SEC_REBUILD=2700

usage() {
  cat <<'EOF'
用法: ./scripts/agent-api-docker.sh <command>

  status              容器 / :8000 / :3200 / 锁 / 待处理请求 / 源码是否新于进程
  request <action> [原因]   只写 inbox，不执行 Docker（改代码的会话用这个）
  restart             合并 inbox 后重启 agent-api + agent-worker（日常改 Python）
  recreate            合并 inbox 后 force-recreate 这两个服务（改了 compose/.env）
  rebuild             合并 inbox 后 --build --force-recreate（改了 requirements/Dockerfile）
  up                  容器已停时拉起，不 rebuild、不 recreate
  drain               有 inbox 则按最强请求执行；否则只 status
  sandbox [remote|local]
                      显示或切换 OpenSandbox；切换会 recreate API + Worker
                      默认 remote；local 仅限用户明确授权的本机联调

action: restart | recreate | rebuild
EOF
}

ensure_dirs() {
  mkdir -p "$INBOX" "$DONE"
}

sandbox_mode() {
  if [ -f "$SANDBOX_MODE_FILE" ] && [ "$(cat "$SANDBOX_MODE_FILE")" = "local" ]; then
    echo local
  else
    echo remote
  fi
}

sandbox_domain_for_mode() {
  if [ "$1" = "local" ]; then
    echo "$LOCAL_SANDBOX_DOMAIN"
  else
    echo "$REMOTE_SANDBOX_DOMAIN"
  fi
}

sandbox_health_url_for_mode() {
  if [ "$1" = "local" ]; then
    echo "http://127.0.0.1:8091/health"
  else
    echo "$REMOTE_SANDBOX_DOMAIN/health"
  fi
}

check_sandbox_endpoint() {
  local mode="$1"
  local health_url
  health_url="$(sandbox_health_url_for_mode "$mode")"
  echo "检查 OpenSandbox: $health_url"
  if ! curl -fsS -m 8 "$health_url" >/dev/null; then
    echo "✗ $mode OpenSandbox 不可用，未改动 agent-api / agent-worker"
    if [ "$mode" = "local" ]; then
      echo "  请先确认本机 OpenSandbox 已在 127.0.0.1:8091 运行"
    fi
    return 1
  fi
  echo "✓ OpenSandbox 可用"
}

compose_for_mode() {
  local mode="$1"
  shift
  if [ "$mode" = "local" ]; then
    docker compose -f "$DEV_COMPOSE" -f "$LOCAL_SANDBOX_COMPOSE" "$@"
  else
    docker compose -f "$DEV_COMPOSE" "$@"
  fi
}

active_sandbox_domain() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' agent-api 2>/dev/null \
    | awk -F= '$1 == "SKILL_SANDBOX_OPENSANDBOX_DOMAIN" {print substr($0, index($0, "=") + 1); exit}'
}

lock_stale_sec() {
  local action="${1:-restart}"
  if [ "$action" = "rebuild" ]; then
    echo "$STALE_SEC_REBUILD"
  else
    echo "$STALE_SEC_DEFAULT"
  fi
}

lock_owner_action() {
  if [ -f "$LOCK_DIR/owner" ]; then
    awk -F= '/^action=/{print $2; exit}' "$LOCK_DIR/owner" 2>/dev/null || echo restart
  else
    echo restart
  fi
}

lock_age_sec() {
  python3 - <<'PY' "$LOCK_DIR"
import os, sys, time
p = sys.argv[1]
if not os.path.isdir(p):
    print(0)
    raise SystemExit
print(int(time.time() - os.stat(p).st_mtime))
PY
}

lock_acquire() {
  local action="$1"
  ensure_dirs
  local waited=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    local owner_action age limit
    owner_action="$(lock_owner_action)"
    age="$(lock_age_sec)"
    limit="$(lock_stale_sec "$owner_action")"
    if [ "$age" -ge "$limit" ]; then
      echo "⚠ 锁已过期 ${age}s（上限 ${limit}s），接管"
      rm -rf "$LOCK_DIR"
      continue
    fi
    if [ "$waited" -ge 120 ]; then
      echo "✗ 等锁超时。另一个 Docker 操作仍在进行："
      cat "$LOCK_DIR/owner" 2>/dev/null || true
      exit 1
    fi
    echo "… Docker 锁占用中（${age}s / ${owner_action}），等待"
    sleep 2
    waited=$((waited + 2))
  done
  {
    echo "pid=$$"
    echo "action=$action"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$LOCK_DIR/owner"
  trap 'lock_release' EXIT INT TERM
}

lock_release() {
  rm -rf "$LOCK_DIR"
}

write_request() {
  local action="$1"
  local reason="${2:-unspecified}"
  ensure_dirs
  local f
  f="$INBOX/$(date +%Y%m%d-%H%M%S)-$$.request"
  {
    echo "action=$action"
    echo "reason=$reason"
    echo "at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
  } >"$f"
  echo "✓ 已排队 $f"
  echo "  请让 Docker 部署会话执行: ./scripts/agent-api-docker.sh drain"
}

inbox_strongest_action() {
  python3 - <<'PY' "$INBOX"
import os, sys
inbox = sys.argv[1]
rank = {"restart": 1, "recreate": 2, "rebuild": 3}
best = "restart"
n = 0
if os.path.isdir(inbox):
    for name in os.listdir(inbox):
        path = os.path.join(inbox, name)
        if not os.path.isfile(path):
            continue
        n += 1
        action = "restart"
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("action="):
                    action = line.split("=", 1)[1].strip().lower()
                    break
        if rank.get(action, 1) > rank.get(best, 1):
            best = action
print(f"{best}\t{n}")
PY
}

consume_inbox() {
  ensure_dirs
  shopt -s nullglob
  local files=("$INBOX"/*.request)
  if [ "${#files[@]}" -eq 0 ]; then
    echo "(inbox 空)"
    return 0
  fi
  local stamp dest
  stamp="$(date +%Y%m%d-%H%M%S)"
  dest="$DONE/$stamp"
  mkdir -p "$dest"
  mv "${files[@]}" "$dest/"
  echo "✓ 已归档 ${#files[@]} 条请求 → $dest"
}

started_at() {
  docker inspect -f '{{.State.StartedAt}}' agent-api 2>/dev/null || echo none
}

wait_healthy() {
  echo "等待 agent-api healthy..."
  local i st
  for i in $(seq 1 36); do
    st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' agent-api 2>/dev/null || echo none)
    if [ "$st" = "healthy" ]; then
      echo "✓ agent-api healthy  StartedAt=$(started_at)"
      curl -fsS -m 5 http://127.0.0.1:8000/health/ready
      echo
      return 0
    fi
    sleep 2
  done
  echo "⚠ 超时仍未 healthy（status=$st），最近日志："
  docker logs agent-api --tail 40 || true
  return 1
}

cmd_status() {
  local desired_mode desired_domain active_domain
  desired_mode="$(sandbox_mode)"
  desired_domain="$(sandbox_domain_for_mode "$desired_mode")"
  active_domain="$(active_sandbox_domain || true)"
  echo "== OpenSandbox =="
  echo "desired mode=$desired_mode  domain=$desired_domain"
  if [ -n "$active_domain" ]; then
    echo "active  domain=$active_domain"
    if [ "$active_domain" != "$desired_domain" ]; then
      echo "⚠ 期望与当前容器不一致，需要 recreate"
    fi
  else
    echo "active  domain=(agent-api 未运行)"
  fi
  echo
  echo "== 容器 =="
  docker ps -a --filter name=agent-api --filter name=agent-worker --filter name=agent-checkpoint-pg --filter name=agent-qdrant \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  echo
  echo "== 进程 / 健康 =="
  echo "agent-api StartedAt=$(started_at)"
  curl -sS -m 5 -o /dev/null -w 'GET /health/ready  %{http_code}\n' http://127.0.0.1:8000/health/ready || echo 'GET /health/ready  down'
  curl -sS -m 3 -o /dev/null -w 'GET :3200          %{http_code}\n' http://127.0.0.1:3200/ || echo 'GET :3200          down'
  echo
  echo "== 锁 / inbox =="
  if [ -d "$LOCK_DIR" ]; then
    echo "锁占用中："
    cat "$LOCK_DIR/owner" 2>/dev/null || true
  else
    echo "锁空闲"
  fi
  ensure_dirs
  local n
  n=$(find "$INBOX" -maxdepth 1 -type f -name '*.request' 2>/dev/null | wc -l | tr -d ' ')
  echo "待处理请求: $n"
  if [ "$n" != "0" ]; then
    ls -1 "$INBOX"
  fi
  if [ -f "$LAST_FILE" ]; then
    echo
    echo "== 上次操作 =="
    cat "$LAST_FILE"
  fi
  echo
  echo "== 源码是否新于进程 =="
  python3 - <<'PY' "$API_DIR"
import subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
root = Path(sys.argv[1])
try:
    raw = subprocess.check_output(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", "agent-api"],
        text=True,
    ).strip()
except subprocess.CalledProcessError:
    print("agent-api 不存在")
    raise SystemExit
started = datetime.fromisoformat(raw.replace("Z", "+00:00")[:26] + "+00:00")
watch = list(root.rglob("*.py"))
watch += list(root.glob("requirements*.txt"))
watch += list(root.glob("Dockerfile*"))
watch += [
    root / "docker-compose.dev.yml",
    root / "docker-compose.opensandbox-local.yml",
    root / ".env",
    root / ".env.dev",
]
newer = []
for p in watch:
    if not p.is_file():
        continue
    rel = str(p.relative_to(root))
    if rel.startswith((".venv", ".git")) or "/__pycache__/" in rel:
        continue
    m = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    if m > started:
        newer.append((m.isoformat(timespec="seconds"), rel))
newer.sort()
print(f"StartedAt {started.isoformat()}  newer={len(newer)}")
for t, rel in newer[-20:]:
    print(f"  {t}  {rel}")
if newer:
    print("→ 需要 Docker 部署会话执行 restart/recreate/rebuild")
else:
    print("→ 进程已加载当前源码，不必重启")
PY
}

forbid_side_targets() {
  echo "本脚本不会操作: Axiom / agent-browser / opensandbox / agent-checkpoint-pg / agent-qdrant"
}

record_last() {
  local action="$1"
  {
    echo "action=$action"
    echo "finished=$(date '+%Y-%m-%dT%H:%M:%S%z')"
    echo "started_at=$(started_at)"
    echo "opensandbox_mode=$(sandbox_mode)"
    echo "opensandbox_domain=$(active_sandbox_domain || true)"
  } >"$LAST_FILE"
}

run_restart() {
  forbid_side_targets
  consume_inbox
  docker restart agent-api agent-worker
  wait_healthy
  record_last restart
}

run_recreate() {
  local mode
  mode="$(sandbox_mode)"
  forbid_side_targets
  consume_inbox
  (cd "$API_DIR" && compose_for_mode "$mode" up -d --force-recreate --no-build agent-api agent-worker)
  wait_healthy
  record_last recreate
}

run_rebuild() {
  local mode
  mode="$(sandbox_mode)"
  forbid_side_targets
  consume_inbox
  (cd "$API_DIR" && compose_for_mode "$mode" up -d --build --force-recreate agent-api agent-worker)
  wait_healthy
  record_last rebuild
}

run_up() {
  local mode
  mode="$(sandbox_mode)"
  forbid_side_targets
  (cd "$API_DIR" && compose_for_mode "$mode" up -d --no-build agent-api agent-worker checkpoint-postgres qdrant)
  wait_healthy
  record_last up
}

run_sandbox_switch() {
  local mode="$1"
  forbid_side_targets
  check_sandbox_endpoint "$mode"
  echo "→ 切换 OpenSandbox 为 $mode ($(sandbox_domain_for_mode "$mode"))"
  (cd "$API_DIR" && compose_for_mode "$mode" up -d --force-recreate --no-build agent-api agent-worker)
  ensure_dirs
  echo "$mode" >"$SANDBOX_MODE_FILE"
  wait_healthy
  record_last "sandbox-$mode"
  cmd_status
}

with_lock_run() {
  local action="$1"
  lock_acquire "$action"
  case "$action" in
    restart) run_restart ;;
    recreate) run_recreate ;;
    rebuild) run_rebuild ;;
    up) run_up ;;
    sandbox-local) run_sandbox_switch local ;;
    sandbox-remote) run_sandbox_switch remote ;;
    *) echo "未知 action: $action"; exit 1 ;;
  esac
}

cmd_drain() {
  ensure_dirs
  local pair action n
  pair="$(inbox_strongest_action)"
  action="${pair%%$'\t'*}"
  n="${pair#*$'\t'}"
  if [ "$n" = "0" ]; then
    echo "inbox 空，不执行 Docker。"
    cmd_status
    return 0
  fi
  echo "inbox $n 条，最强动作=$action"
  with_lock_run "$action"
}

main() {
  local cmd="${1:-status}"
  case "$cmd" in
    status) cmd_status ;;
    request)
      local action="${2:-restart}"
      case "$action" in
        restart|recreate|rebuild) ;;
        *) echo "request 的 action 只能是 restart|recreate|rebuild"; exit 1 ;;
      esac
      shift 2 || true
      write_request "$action" "${*:-unspecified}"
      ;;
    restart|recreate|rebuild|up) with_lock_run "$cmd" ;;
    sandbox)
      local mode="${2:-}"
      if [ -z "$mode" ]; then
        cmd_status
      else
        case "$mode" in
          local|remote) with_lock_run "sandbox-$mode" ;;
          *) echo "sandbox 模式只能是 remote|local"; exit 1 ;;
        esac
      fi
      ;;
    drain) cmd_drain ;;
    help|-h|--help) usage ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
