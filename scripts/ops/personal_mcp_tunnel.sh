#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SSH_TARGET="quant-trading-prod"
LOCAL_PORT=26174
REMOTE_PORT=16174
CONTROL_SOCKET="${PERSONAL_MCP_CONTROL_SOCKET:-${TMPDIR:-/tmp}/quant-personal-mcp-${UID}.sock}"
ENDPOINT="http://127.0.0.1:${LOCAL_PORT}/mcp"

usage() {
  echo "用法：personal_mcp_tunnel.sh [start|status|stop]" >&2
}

control_is_running() {
  ssh -S "$CONTROL_SOCKET" -O check "$SSH_TARGET" >/dev/null 2>&1
}

local_port_is_available() {
  python3 - "$LOCAL_PORT" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.bind(("127.0.0.1", int(sys.argv[1])))
PY
}

endpoint_is_protected() {
  local status
  status="$(
    curl --silent --show-error --output /dev/null \
      --write-out '%{http_code}' --max-time 3 "$ENDPOINT" 2>/dev/null || true
  )"
  [[ "$status" == "401" ]]
}

show_status() {
  if ! control_is_running; then
    echo "隧道未运行或已断开" >&2
    return 1
  fi
  if ! endpoint_is_protected; then
    echo "隧道存在，但远端 MCP 不可达或未返回认证门禁" >&2
    return 1
  fi
  echo "远端 MCP 隧道正常：${ENDPOINT}（未携带 token 的探针返回 401）"
}

start_tunnel() {
  if control_is_running; then
    show_status
    return
  fi
  if ! local_port_is_available; then
    echo "本机端口已占用：127.0.0.1:${LOCAL_PORT}" >&2
    return 1
  fi
  ssh \
    -M \
    -S "$CONTROL_SOCKET" \
    -fNT \
    -o BatchMode=yes \
    -o ControlMaster=yes \
    -o ControlPersist=no \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$SSH_TARGET"
  if ! control_is_running; then
    echo "SSH ControlMaster 未建立或已立即断开" >&2
    return 1
  fi
  if ! show_status; then
    ssh -S "$CONTROL_SOCKET" -O exit "$SSH_TARGET" >/dev/null 2>&1 || true
    return 1
  fi
}

stop_tunnel() {
  if ! control_is_running; then
    echo "远端 MCP 隧道已停止"
    return
  fi
  ssh -S "$CONTROL_SOCKET" -O exit "$SSH_TARGET" >/dev/null
  echo "远端 MCP 隧道已停止"
}

case "$ACTION" in
  start)
    start_tunnel
    ;;
  status)
    show_status
    ;;
  stop)
    stop_tunnel
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 2
    ;;
esac
