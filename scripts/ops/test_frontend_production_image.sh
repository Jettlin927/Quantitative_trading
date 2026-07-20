#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SMOKE_COMPOSE_FILE="$SCRIPT_DIR/fixtures/frontend-production-smoke.yml"
FRONTEND_IMAGE="${1:-quant-frontend-production-smoke}"
PROJECT_NAME="quant_frontend_smoke_$$"

cleanup() {
  FRONTEND_IMAGE="$FRONTEND_IMAGE" docker compose \
    --project-name "$PROJECT_NAME" \
    --file "$SMOKE_COMPOSE_FILE" \
    down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build \
  --file "$REPO_ROOT/frontend/Dockerfile" \
  --tag "$FRONTEND_IMAGE" \
  "$REPO_ROOT"

FRONTEND_IMAGE="$FRONTEND_IMAGE" docker compose \
  --project-name "$PROJECT_NAME" \
  --file "$SMOKE_COMPOSE_FILE" \
  up --detach

frontend_id="$(
  FRONTEND_IMAGE="$FRONTEND_IMAGE" docker compose \
    --project-name "$PROJECT_NAME" \
    --file "$SMOKE_COMPOSE_FILE" \
    ps --quiet frontend
)"

for _ in $(seq 1 30); do
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$frontend_id")"
  if [[ "$health" == "healthy" ]]; then
    break
  fi
  if [[ "$health" == "unhealthy" ]]; then
    docker logs "$frontend_id" >&2
    echo "前端生产容器健康检查失败" >&2
    exit 1
  fi
  sleep 1
done

if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$frontend_id")" != "healthy" ]]; then
  docker logs "$frontend_id" >&2
  echo "等待前端生产容器健康检查超时" >&2
  exit 1
fi

docker exec "$frontend_id" nginx -t
docker exec "$frontend_id" sh -ec '
  test "$(cat /proc/1/comm)" = "nginx"
  ! command -v node >/dev/null 2>&1
  ! command -v npm >/dev/null 2>&1
  wget -qO- http://127.0.0.1:5173/ | grep -F "<div id=\"root\"></div>"
  wget -qO- http://127.0.0.1:5173/research/deep/link | grep -F "<div id=\"root\"></div>"
  wget -qO- http://127.0.0.1:5173/api/health | grep -F "{\"status\":\"ok\"}"
'

echo "前端生产静态镜像、SPA 回退与 API 反代合同通过"
