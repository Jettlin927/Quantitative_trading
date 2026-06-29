#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-all}"
PROJECT_DIR="${PROJECT_DIR:-/opt/quantitative-trading}"
REPO_URL="${REPO_URL:-ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git}"
BRANCH="${BRANCH:-main}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/quantitative_trading_github}"
COMPOSE_SERVER_FILE="${COMPOSE_SERVER_FILE:-docker-compose.server.yml}"
SKIP_GIT_PULL="${SKIP_GIT_PULL:-0}"

usage() {
  cat <<'EOF'
Usage: deploy_server.sh [all|frontend|backend|api|pg|db|status|verify]

Environment:
  PROJECT_DIR=/opt/quantitative-trading
  REPO_URL=ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git
  BRANCH=main
  SSH_KEY=$HOME/.ssh/quantitative_trading_github
  COMPOSE_SERVER_FILE=docker-compose.server.yml
  SKIP_GIT_PULL=1
EOF
}

env_value() {
  local key="$1"
  local fallback="$2"
  local value=""

  if [[ -f .env ]]; then
    value="$(awk -F= -v key="$key" '
      $0 !~ /^[[:space:]]*#/ && $1 == key {
        sub(/^[^=]*=/, "")
        gsub(/^"|"$/, "")
        gsub(/^'\''|'\''$/, "")
        print
        exit
      }
    ' .env)"
  fi

  printf '%s' "${value:-$fallback}"
}

git_ssh_command() {
  if [[ -f "$SSH_KEY" ]]; then
    printf 'ssh -i %q -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new' "$SSH_KEY"
  else
    printf 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new'
  fi
}

sync_code() {
  local backup_dir

  if [[ "$SKIP_GIT_PULL" == "1" ]]; then
    cd "$PROJECT_DIR"
    return
  fi

  mkdir -p "$(dirname "$PROJECT_DIR")"

  if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    if [[ -d "$PROJECT_DIR" ]] && [[ -n "$(find "$PROJECT_DIR" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; then
      backup_dir="${PROJECT_DIR}.pre-git.$(date +%Y%m%d%H%M%S)"
      mv "$PROJECT_DIR" "$backup_dir"
      echo "Backed up non-git project dir to: $backup_dir"
    fi

    GIT_SSH_COMMAND="$(git_ssh_command)" git clone --branch "$BRANCH" "$REPO_URL" "$PROJECT_DIR"

    if [[ -n "${backup_dir:-}" ]]; then
      for preserved in .env "$COMPOSE_SERVER_FILE" logs; do
        if [[ -e "${backup_dir}/${preserved}" && ! -e "${PROJECT_DIR}/${preserved}" ]]; then
          cp -a "${backup_dir}/${preserved}" "${PROJECT_DIR}/${preserved}"
        fi
      done
    fi
  fi

  cd "$PROJECT_DIR"
  GIT_SSH_COMMAND="$(git_ssh_command)" git fetch --prune origin "$BRANCH"
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
}

compose_cmd() {
  local cmd=(docker compose)

  if [[ -f "$COMPOSE_SERVER_FILE" ]]; then
    cmd+=(-f docker-compose.yml -f "$COMPOSE_SERVER_FILE")
  fi

  "${cmd[@]}" "$@"
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local attempt

  for attempt in {1..30}; do
    if curl -fsS --max-time 5 "$url" >/dev/null; then
      echo "${name} OK: ${url}"
      return 0
    fi
    sleep 2
  done

  echo "${name} failed: ${url}" >&2
  return 1
}

verify_pg() {
  compose_cmd exec -T db sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

verify_backend() {
  local api_port
  api_port="$(env_value API_PORT 18000)"
  wait_for_http "http://127.0.0.1:${api_port}/api/health" "backend"
}

verify_frontend() {
  local frontend_port
  frontend_port="$(env_value FRONTEND_PORT 15173)"
  wait_for_http "http://127.0.0.1:${frontend_port}" "frontend"
}

deploy_pg() {
  compose_cmd up -d db
  verify_pg
}

deploy_backend() {
  deploy_pg
  compose_cmd build api
  compose_cmd up -d --no-deps api
  verify_backend
}

deploy_frontend() {
  compose_cmd build frontend
  compose_cmd up -d --no-deps frontend
  verify_frontend
}

show_status() {
  compose_cmd ps
}

run_verify() {
  verify_pg
  verify_backend
  verify_frontend
  show_status
}

case "$TARGET" in
  -h|--help|help)
    usage
    exit 0
    ;;
  all|frontend|backend|api|pg|db)
    sync_code
    compose_cmd config >/dev/null
    ;;
  status|verify)
    cd "$PROJECT_DIR"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

case "$TARGET" in
  all)
    deploy_pg
    deploy_backend
    deploy_frontend
    show_status
    ;;
  frontend)
    deploy_frontend
    show_status
    ;;
  backend|api)
    deploy_backend
    show_status
    ;;
  pg|db)
    deploy_pg
    show_status
    ;;
  status)
    show_status
    ;;
  verify)
    run_verify
    ;;
esac
