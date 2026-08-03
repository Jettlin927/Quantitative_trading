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

TARGET="${1:-all}"
REMOTE="${REMOTE:-$(env_value REMOTE ubuntu@182.254.180.169)}"
REMOTE_SSH_PORT="${REMOTE_SSH_PORT:-$(env_value REMOTE_SSH_PORT "$(env_value DEV_SERVER_SSH_PORT 22)")}"
REMOTE_SSH_KEY="${REMOTE_SSH_KEY:-$(env_value REMOTE_SSH_KEY "$(env_value DEV_SERVER_SSH_KEY "")")}"
PROJECT_DIR="${PROJECT_DIR:-$(env_value PROJECT_DIR /opt/quantitative-trading)}"
REPO_URL="${REPO_URL:-$(env_value REPO_URL ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git)}"
BRANCH="${BRANCH:-$(env_value BRANCH main)}"
COMPOSE_PERSONAL_FILE="${COMPOSE_PERSONAL_FILE:-$(env_value COMPOSE_PERSONAL_FILE "")}"

usage() {
  cat <<'EOF'
Usage: deploy_remote.sh [all|frontend|backend|api|pg|db|status|verify]

Environment:
  REMOTE=ubuntu@182.254.180.169
  REMOTE_SSH_PORT=22
  REMOTE_SSH_KEY=/Users/jettlin/.ssh/quantitative_trading_server_ed25519
  PROJECT_DIR=/opt/quantitative-trading
  REPO_URL=ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git
  BRANCH=main
  COMPOSE_PERSONAL_FILE=docker-compose.personal.yml  # optional
EOF
}

case "$TARGET" in
  -h|--help|help)
    usage
    exit 0
    ;;
  all|frontend|backend|api|pg|db|status|verify)
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

remote_env=$(
  printf 'PROJECT_DIR=%q REPO_URL=%q BRANCH=%q COMPOSE_PERSONAL_FILE=%q bash -s -- %q' \
    "$PROJECT_DIR" "$REPO_URL" "$BRANCH" "$COMPOSE_PERSONAL_FILE" "$TARGET"
)

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$REMOTE_SSH_PORT")
if [[ -n "$REMOTE_SSH_KEY" ]]; then
  ssh_opts+=(-i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes)
fi

ssh "${ssh_opts[@]}" "$REMOTE" "$remote_env" < "$SCRIPT_DIR/deploy_server.sh"
