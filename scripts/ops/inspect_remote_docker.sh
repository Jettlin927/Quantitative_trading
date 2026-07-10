#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

env_value() {
  local key="$1"
  local fallback="$2"
  local value=""

  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -F= -v key="$key" '
      $0 !~ /^[[:space:]]*#/ && $1 == key {
        sub(/^[^=]*=/, "")
        gsub(/^"|"$/, "")
        gsub(/^'\''|'\''$/, "")
        print
        exit
      }
    ' "$ENV_FILE")"
  fi

  printf '%s' "${value:-$fallback}"
}

TARGET="${1:-run}"
REMOTE="${REMOTE:-$(env_value REMOTE ubuntu@182.254.180.169)}"
REMOTE_SSH_PORT="${REMOTE_SSH_PORT:-$(env_value REMOTE_SSH_PORT "$(env_value DEV_SERVER_SSH_PORT 22)")}"
REMOTE_SSH_KEY="${REMOTE_SSH_KEY:-$(env_value REMOTE_SSH_KEY "$(env_value DEV_SERVER_SSH_KEY "")")}"
PROJECT_DIR="${PROJECT_DIR:-$(env_value PROJECT_DIR /opt/quantitative-trading)}"
COMPOSE_SERVER_FILE="${COMPOSE_SERVER_FILE:-$(env_value COMPOSE_SERVER_FILE docker-compose.server.yml)}"
API_BASE="${API_BASE:-$(env_value API_BASE "")}"
FRONTEND_URL="${FRONTEND_URL:-$(env_value FRONTEND_URL "")}"
LOG_DIR="${LOG_DIR:-$(env_value INSPECTION_LOG_DIR "")}"
CRON_SCHEDULE="${CRON_SCHEDULE:-$(env_value INSPECTION_CRON_SCHEDULE "")}"

usage() {
  cat <<'EOF'
Usage: inspect_remote_docker.sh [run|install-cron|show-cron|remove-cron]

Environment:
  REMOTE=ubuntu@182.254.180.169
  REMOTE_SSH_PORT=22
  REMOTE_SSH_KEY=/Users/jettlin/.ssh/quantitative_trading_server_ed25519
  PROJECT_DIR=/opt/quantitative-trading
  COMPOSE_SERVER_FILE=docker-compose.server.yml
  API_BASE=http://127.0.0.1:18000
  FRONTEND_URL=http://127.0.0.1:15173
  INSPECTION_LOG_DIR=$HOME/quantitative-trading-logs
  INSPECTION_CRON_SCHEDULE="*/10 * * * *"
EOF
}

case "$TARGET" in
  -h|--help|help)
    usage
    exit 0
    ;;
  run|install-cron|show-cron|remove-cron)
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$REMOTE_SSH_PORT")
if [[ -n "$REMOTE_SSH_KEY" ]]; then
  ssh_opts+=(-i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes)
fi

remote_cmd=$(
  printf 'PROJECT_DIR=%q COMPOSE_SERVER_FILE=%q API_BASE=%q FRONTEND_URL=%q LOG_DIR=%q CRON_SCHEDULE=%q bash %q %q' \
    "$PROJECT_DIR" \
    "$COMPOSE_SERVER_FILE" \
    "$API_BASE" \
    "$FRONTEND_URL" \
    "$LOG_DIR" \
    "$CRON_SCHEDULE" \
    "${PROJECT_DIR}/scripts/ops/inspect_server_docker.sh" \
    "$TARGET"
)

ssh "${ssh_opts[@]}" "$REMOTE" "$remote_cmd"
