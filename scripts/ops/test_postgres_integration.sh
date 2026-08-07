#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yml"
if [[ -z "${PYTHON_BIN:-}" && -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
PROJECT_SUFFIX="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
PROJECT_NAME="quant-trading-test-$(printf '%s' "$PROJECT_SUFFIX" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9_-' '-')"
COMPOSE=(docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE")

cleanup() {
  local status=$?
  trap - EXIT
  "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || {
  echo "错误：需要 Docker 才能运行 PostgreSQL 16 集成测试。" >&2
  exit 1
}
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "错误：找不到 Python 命令：$PYTHON_BIN" >&2
  exit 1
}
docker info >/dev/null

"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" up --detach --wait --wait-timeout 90 test-db

server_version="$("${COMPOSE[@]}" exec -T test-db psql -U quant_test -d quant_migration_test -Atqc 'show server_version')"
if [[ "$server_version" != 16.* ]]; then
  echo "错误：期望 PostgreSQL 16，实际为 $server_version。" >&2
  exit 1
fi

published_endpoint="$("${COMPOSE[@]}" port test-db 5432)"
test_port="${published_endpoint##*:}"
if [[ ! "$test_port" =~ ^[0-9]+$ ]]; then
  echo "错误：无法从 $published_endpoint 解析测试数据库端口。" >&2
  exit 1
fi

export TEST_POSTGRES_URL="postgresql+psycopg://quant_test:quant_test_password@127.0.0.1:${test_port}/quant_migration_test"
export DATABASE_URL="$TEST_POSTGRES_URL"
export APP_GIT_COMMIT="${APP_GIT_COMMIT:-$(git rev-parse --verify HEAD)}"

echo "PostgreSQL $server_version 已在隔离 tmpfs 测试容器中就绪。"
echo "运行 backend/tests 自动发现矩阵（migration、quality、snapshot、个人工作台）。"
"$PYTHON_BIN" -m unittest discover -s backend/tests -p 'test_*.py' -v
