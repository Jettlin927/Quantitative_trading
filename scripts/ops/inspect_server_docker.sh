#!/usr/bin/env bash
set -uo pipefail

TARGET="${1:-run}"
PROJECT_DIR="${PROJECT_DIR:-/opt/quantitative-trading}"
COMPOSE_SERVER_FILE="${COMPOSE_SERVER_FILE:-docker-compose.server.yml}"
EXPECTED_SERVICES="${EXPECTED_SERVICES:-db api frontend}"
LOG_DIR="${LOG_DIR:-$HOME/quantitative-trading-logs}"
CRON_SCHEDULE="${CRON_SCHEDULE:-*/10 * * * *}"
MAX_HTTP_SECONDS="${MAX_HTTP_SECONDS:-10}"
MARKER_BEGIN="# BEGIN quant-docker-inspection"
MARKER_END="# END quant-docker-inspection"

failures=0
warnings=0
service_cids=()

usage() {
  cat <<'EOF'
Usage: inspect_server_docker.sh [run|install-cron|show-cron|remove-cron]

Environment:
  PROJECT_DIR=/opt/quantitative-trading
  COMPOSE_SERVER_FILE=docker-compose.server.yml
  EXPECTED_SERVICES="db api frontend"
  LOG_DIR=$HOME/quantitative-trading-logs
  CRON_SCHEDULE="*/10 * * * *"
  API_BASE=http://127.0.0.1:18000
  FRONTEND_URL=http://127.0.0.1:15173
EOF
}

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %z'
}

log() {
  printf '%s %s\n' "$(timestamp)" "$*"
}

ok() {
  log "OK $*"
}

warn() {
  warnings=$((warnings + 1))
  log "WARN $*"
}

fail() {
  failures=$((failures + 1))
  log "FAIL $*"
}

finish() {
  if (( failures > 0 )); then
    log "RESULT=fail failures=${failures} warnings=${warnings}"
    exit 1
  fi

  if (( warnings > 0 )); then
    log "RESULT=warn failures=0 warnings=${warnings}"
    exit 0
  fi

  log "RESULT=ok failures=0 warnings=0"
  exit 0
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

compose_cmd() {
  local cmd=(docker compose)

  if [[ -f "$COMPOSE_SERVER_FILE" ]]; then
    cmd+=(-f docker-compose.yml -f "$COMPOSE_SERVER_FILE")
  fi

  "${cmd[@]}" "$@"
}

check_http() {
  local name="$1"
  local url="$2"

  if curl -fsS --max-time "$MAX_HTTP_SECONDS" "$url" >/dev/null; then
    ok "${name} HTTP check passed url=${url}"
  else
    fail "${name} HTTP check failed url=${url}"
  fi
}

check_service() {
  local service="$1"
  local cid
  local inspect_output
  local name
  local status
  local running
  local health
  local restart_count
  local started_at
  local image

  cid="$(compose_cmd ps -q "$service" 2>/dev/null | head -n 1 || true)"
  if [[ -z "$cid" ]]; then
    fail "service=${service} container not found"
    return
  fi

  service_cids+=("$cid")
  inspect_output="$(docker inspect --format '{{.Name}}|{{.State.Status}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}|{{.Config.Image}}' "$cid" 2>/dev/null || true)"
  if [[ -z "$inspect_output" ]]; then
    fail "service=${service} docker inspect failed cid=${cid}"
    return
  fi

  IFS='|' read -r name status running health restart_count started_at image <<<"$inspect_output"
  name="${name#/}"

  if [[ "$running" == "true" ]]; then
    ok "service=${service} container=${name} status=${status} health=${health} restarts=${restart_count} image=${image}"
  else
    fail "service=${service} container=${name} status=${status} running=${running} health=${health}"
  fi

  if [[ "$health" != "none" && "$health" != "healthy" ]]; then
    fail "service=${service} container=${name} health=${health}"
  fi

  if [[ "$restart_count" =~ ^[0-9]+$ && "$restart_count" -gt 0 ]]; then
    warn "service=${service} container=${name} restart_count=${restart_count} started_at=${started_at}"
  fi
}

run_inspection() {
  local api_port
  local frontend_port
  local api_base
  local frontend_url

  log "START remote Docker inspection host=$(hostname) project=${PROJECT_DIR}"

  if ! command -v docker >/dev/null 2>&1; then
    fail "docker command not found"
    finish
  fi

  if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon is not available"
    finish
  fi
  ok "Docker daemon is available"

  if [[ ! -d "$PROJECT_DIR" ]]; then
    fail "PROJECT_DIR does not exist: ${PROJECT_DIR}"
    finish
  fi

  cd "$PROJECT_DIR" || {
    fail "cannot cd PROJECT_DIR: ${PROJECT_DIR}"
    finish
  }

  if [[ ! -f docker-compose.yml ]]; then
    fail "docker-compose.yml not found in ${PROJECT_DIR}"
    finish
  fi

  if compose_cmd config --quiet >/dev/null 2>&1; then
    ok "docker compose config passed"
  else
    fail "docker compose config failed"
  fi

  if compose_cmd ps; then
    ok "docker compose ps completed"
  else
    fail "docker compose ps failed"
  fi

  for service in $EXPECTED_SERVICES; do
    check_service "$service"
  done

  if compose_cmd exec -T db sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    ok "PostgreSQL pg_isready passed"
  else
    fail "PostgreSQL pg_isready failed"
  fi

  api_port="$(env_value API_PORT 18000)"
  frontend_port="$(env_value FRONTEND_PORT 15173)"
  api_base="${API_BASE:-http://127.0.0.1:${api_port}}"
  frontend_url="${FRONTEND_URL:-http://127.0.0.1:${frontend_port}}"

  check_http "backend" "${api_base}/api/health"
  check_http "frontend" "$frontend_url"

  if (( ${#service_cids[@]} > 0 )); then
    if docker stats --no-stream --format 'STAT name={{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} net={{.NetIO}} block={{.BlockIO}}' "${service_cids[@]}"; then
      ok "docker stats completed"
    else
      warn "docker stats failed"
    fi
  fi

  finish
}

install_cron() {
  if [[ "$PROJECT_DIR" == *" "* || "$LOG_DIR" == *" "* ]]; then
    echo "PROJECT_DIR and LOG_DIR must not contain spaces." >&2
    exit 2
  fi

  local existing_cron
  local kept_cron
  local job_line
  local env_prefix="TZ=Asia/Shanghai"

  [[ -n "${API_BASE:-}" ]] && env_prefix="${env_prefix} API_BASE=${API_BASE}"
  [[ -n "${FRONTEND_URL:-}" ]] && env_prefix="${env_prefix} FRONTEND_URL=${FRONTEND_URL}"

  existing_cron="$(crontab -l 2>/dev/null || true)"
  kept_cron="$(printf '%s\n' "$existing_cron" | sed "/${MARKER_BEGIN}/,/${MARKER_END}/d")"
  job_line="${CRON_SCHEDULE} cd ${PROJECT_DIR} && mkdir -p ${LOG_DIR} && ${env_prefix} ./scripts/ops/inspect_server_docker.sh run >> ${LOG_DIR}/docker_container_inspection.log 2>&1"

  {
    printf '%s\n' "$kept_cron" | sed '/^[[:space:]]*$/d'
    printf '%s\n' "$MARKER_BEGIN"
    printf '%s\n' "CRON_TZ=Asia/Shanghai"
    printf '%s\n' "$job_line"
    printf '%s\n' "$MARKER_END"
  } | crontab -

  crontab -l | sed -n "/${MARKER_BEGIN}/,/${MARKER_END}/p"
}

show_cron() {
  (crontab -l 2>/dev/null || true) | sed -n "/${MARKER_BEGIN}/,/${MARKER_END}/p"
}

remove_cron() {
  local existing_cron
  local kept_cron

  existing_cron="$(crontab -l 2>/dev/null || true)"
  kept_cron="$(printf '%s\n' "$existing_cron" | sed "/${MARKER_BEGIN}/,/${MARKER_END}/d")"
  printf '%s\n' "$kept_cron" | sed '/^[[:space:]]*$/d' | crontab -
  show_cron
}

case "$TARGET" in
  -h|--help|help)
    usage
    ;;
  run)
    run_inspection
    ;;
  install-cron)
    install_cron
    ;;
  show-cron)
    show_cron
    ;;
  remove-cron)
    remove_cron
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
