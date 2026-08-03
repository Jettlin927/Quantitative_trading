#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_COMPOSE_FILE="$REPO_ROOT/docker-compose.server.example.yml"
PERSONAL_COMPOSE_FILE="$REPO_ROOT/docker-compose.personal.yml"
FRONTEND_DOCKERFILE="$REPO_ROOT/frontend/Dockerfile"
FRONTEND_NGINX_CONFIG="$REPO_ROOT/frontend/nginx.conf"

if [[ ! -f "$SERVER_COMPOSE_FILE" ]]; then
  echo "缺少新服务器 Compose 模板：$SERVER_COMPOSE_FILE" >&2
  exit 1
fi

if [[ ! -f "$PERSONAL_COMPOSE_FILE" ]]; then
  echo "缺少个人工作台 Compose 覆盖：$PERSONAL_COMPOSE_FILE" >&2
  exit 1
fi

if grep -Fq 'npm run dev' "$FRONTEND_DOCKERFILE"; then
  echo "前端最终镜像仍运行 Vite 开发服务器" >&2
  exit 1
fi
grep -Fq 'FROM nginx:stable-alpine' "$FRONTEND_DOCKERFILE"
grep -Fq 'npm run build' "$FRONTEND_DOCKERFILE"
grep -Fq 'listen 5173;' "$FRONTEND_NGINX_CONFIG"
grep -Fq 'proxy_pass http://api:8000;' "$FRONTEND_NGINX_CONFIG"

if POSTGRES_PASSWORD= docker compose \
  --profile research-automation \
  --env-file /dev/null \
  --file "$REPO_ROOT/docker-compose.yml" \
  --file "$SERVER_COMPOSE_FILE" \
  config >/dev/null 2>&1; then
  echo "服务器 Compose 未拒绝空 PostgreSQL 密码" >&2
  exit 1
fi

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/quant-server-compose.XXXXXX")"
cleanup() {
  find "$TEST_ROOT" -depth -delete
}
trap cleanup EXIT

mkdir -p \
  "$TEST_ROOT/postgres" \
  "$TEST_ROOT/research-artifacts"
touch \
  "$TEST_ROOT/personal-gateway-token" \
  "$TEST_ROOT/personal-keyring.json" \
  "$TEST_ROOT/deepseek-credentials.json" \
  "$TEST_ROOT/alpaca-credentials.json" \
  "$TEST_ROOT/alpaca-authorization.json"

POSTGRES_PASSWORD=compose-config-only \
POSTGRES_DATA_DIR="$TEST_ROOT/postgres" \
RESEARCH_ARTIFACTS_DIR="$TEST_ROOT/research-artifacts" \
docker compose \
  --profile research-automation \
  --env-file /dev/null \
  --file "$REPO_ROOT/docker-compose.yml" \
  --file "$SERVER_COMPOSE_FILE" \
  config --format json > "$TEST_ROOT/config.json"

python3 - "$TEST_ROOT/config.json" "$TEST_ROOT" "$SERVER_COMPOSE_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


config_path = Path(sys.argv[1])
test_root = Path(sys.argv[2]).resolve()
server_compose_path = Path(sys.argv[3])
config = json.loads(config_path.read_text(encoding="utf-8"))
services = config["services"]
server_compose = server_compose_path.read_text(encoding="utf-8")
assert server_compose.count("create_host_path: false") == 3, server_compose_path

expected_ports = {
    "db": (5432, "5432"),
    "api": (8000, "18000"),
    "frontend": (5173, "15173"),
}
for service_name, (target, published) in expected_ports.items():
    ports = services[service_name].get("ports", [])
    assert len(ports) == 1, (service_name, ports)
    port = ports[0]
    assert port["host_ip"] == "127.0.0.1", (service_name, port)
    assert port["target"] == target, (service_name, port)
    assert str(port["published"]) == published, (service_name, port)

expected_mounts = {
    "db": (test_root / "postgres", "/var/lib/postgresql/data"),
    "api": (test_root / "research-artifacts", "/app/outputs/research-runs"),
    "research-worker": (test_root / "research-artifacts", "/app/outputs/research-runs"),
}
for service_name, (source, target) in expected_mounts.items():
    mounts = services[service_name].get("volumes", [])
    assert len(mounts) == 1, (service_name, mounts)
    mount = mounts[0]
    assert mount["type"] == "bind", (service_name, mount)
    assert Path(mount["source"]).resolve() == source, (service_name, mount)
    assert mount["target"] == target, (service_name, mount)
    assert mount.get("bind", {}).get("create_host_path") in (None, False), (service_name, mount)

assert services["worker"].get("volumes", []) == [], services["worker"].get("volumes")
assert services["frontend"].get("volumes", []) == [], services["frontend"].get("volumes")
assert not config.get("volumes"), config.get("volumes")
research_worker = services["research-worker"]
assert research_worker["profiles"] == ["research-automation"], research_worker["profiles"]
assert float(research_worker["cpus"]) == 0.75, research_worker["cpus"]
assert int(research_worker["mem_limit"]) == 768 * 1024**2, research_worker["mem_limit"]
assert research_worker["environment"]["RESEARCH_MAX_CPU_CORES"] == "0.75"
assert research_worker["environment"]["RESEARCH_MAX_MEMORY_MIB"] == "768"

memory_total = 0
cpu_total = 0.0
for service_name in ("db", "api", "worker", "frontend"):
    service = services[service_name]
    memory_total += int(service["mem_limit"])
    cpu_total += float(service["cpus"])
    assert int(service["pids_limit"]) > 0, (service_name, service["pids_limit"])
    assert service["logging"]["options"] == {"max-file": "3", "max-size": "10m"}

assert memory_total <= 3 * 1024**3, memory_total
assert cpu_total <= 2.0, cpu_total
PY

# #164 尚未配置 DeepSeek key 时，未启用 personal-ai profile 的日常 Compose
# 校验仍必须可执行；哨兵值只用于保持该 Worker 不可启动。
POSTGRES_PASSWORD=compose-config-only \
POSTGRES_DATA_DIR="$TEST_ROOT/postgres" \
RESEARCH_ARTIFACTS_DIR="$TEST_ROOT/research-artifacts" \
PRIVATE_DATABASE_URL='postgresql+psycopg://quant_personal_api:compose-only@db:5432/quant_trading' \
PERSONAL_GATEWAY_TOKEN_HOST_FILE="$TEST_ROOT/personal-gateway-token" \
PERSONAL_DATA_KEYRING_HOST_FILE="$TEST_ROOT/personal-keyring.json" \
PERSONAL_ALLOWED_ORIGINS='http://127.0.0.1:25173' \
ALPACA_CREDENTIALS_HOST_FILE="$TEST_ROOT/alpaca-credentials.json" \
ALPACA_AUTHORIZATION_HOST_FILE="$TEST_ROOT/alpaca-authorization.json" \
docker compose \
  --env-file /dev/null \
  --file "$REPO_ROOT/docker-compose.yml" \
  --file "$SERVER_COMPOSE_FILE" \
  --file "$PERSONAL_COMPOSE_FILE" \
  config >/dev/null

POSTGRES_PASSWORD=compose-config-only \
POSTGRES_DATA_DIR="$TEST_ROOT/postgres" \
RESEARCH_ARTIFACTS_DIR="$TEST_ROOT/research-artifacts" \
PRIVATE_DATABASE_URL='postgresql+psycopg://quant_personal_api:compose-only@db:5432/quant_trading' \
PERSONAL_ANALYSIS_DATABASE_URL='postgresql+psycopg://quant_personal_analysis:compose-only@db:5432/quant_trading' \
PERSONAL_GATEWAY_TOKEN_HOST_FILE="$TEST_ROOT/personal-gateway-token" \
PERSONAL_DATA_KEYRING_HOST_FILE="$TEST_ROOT/personal-keyring.json" \
DEEPSEEK_CREDENTIALS_HOST_FILE="$TEST_ROOT/deepseek-credentials.json" \
DEEPSEEK_MONTHLY_SOFT_BUDGET_USD='5' \
PERSONAL_ANALYSIS_PROVIDER='deepseek' \
PERSONAL_ALLOWED_ORIGINS='http://127.0.0.1:25173' \
ALPACA_CREDENTIALS_HOST_FILE="$TEST_ROOT/alpaca-credentials.json" \
ALPACA_AUTHORIZATION_HOST_FILE="$TEST_ROOT/alpaca-authorization.json" \
docker compose \
  --profile research-automation \
  --profile personal-ai \
  --env-file /dev/null \
  --file "$REPO_ROOT/docker-compose.yml" \
  --file "$SERVER_COMPOSE_FILE" \
  --file "$PERSONAL_COMPOSE_FILE" \
  config --format json > "$TEST_ROOT/personal-config.json"

python3 - "$TEST_ROOT/personal-config.json" "$TEST_ROOT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
test_root = Path(sys.argv[2]).resolve()
services = config["services"]
api = services["api"]

assert api["environment"]["PRIVATE_DATABASE_URL"] == (
    "postgresql+psycopg://quant_personal_api:compose-only@db:5432/quant_trading"
)
assert api["environment"]["PERSONAL_GATEWAY_TOKEN_FILE"] == "/run/secrets/personal-gateway-token"
assert api["environment"]["PERSONAL_DATA_KEYRING_FILE"] == "/run/secrets/personal-keyring.json"
assert api["environment"]["PERSONAL_ALLOWED_ORIGINS"] == "http://127.0.0.1:25173"
assert api["environment"]["ALPACA_CREDENTIALS_FILE"] == "/run/secrets/alpaca-credentials.json"
assert api["environment"]["ALPACA_AUTHORIZATION_FILE"] == "/run/config/alpaca-authorization.json"
assert api["environment"]["PERSONAL_ANALYSIS_PROVIDER"] == "deepseek"
assert api["environment"]["DEEPSEEK_MONTHLY_SOFT_BUDGET_USD"] == "5"

mounts = {mount["target"]: mount for mount in api.get("volumes", [])}
expected_secrets = {
    "/run/secrets/personal-gateway-token": test_root / "personal-gateway-token",
    "/run/secrets/personal-keyring.json": test_root / "personal-keyring.json",
    "/run/secrets/alpaca-credentials.json": test_root / "alpaca-credentials.json",
    "/run/config/alpaca-authorization.json": test_root / "alpaca-authorization.json",
}
for target, source in expected_secrets.items():
    mount = mounts[target]
    assert mount["type"] == "bind", mount
    assert Path(mount["source"]).resolve() == source, mount
    assert mount["read_only"] is True, mount
    assert mount.get("bind", {}).get("create_host_path") in (None, False), mount

alpaca_environment = {"ALPACA_CREDENTIALS_FILE", "ALPACA_AUTHORIZATION_FILE"}
alpaca_targets = {
    "/run/secrets/alpaca-credentials.json",
    "/run/config/alpaca-authorization.json",
}
for service_name, service in services.items():
    if service_name == "api":
        continue
    environment = service.get("environment", {})
    assert alpaca_environment.isdisjoint(environment), service_name
    targets = {mount["target"] for mount in service.get("volumes", [])}
    assert targets.isdisjoint(alpaca_targets), (service_name, targets)

for service_name in ("worker", "research-worker"):
    service = services[service_name]
    environment = service.get("environment", {})
    assert "PRIVATE_DATABASE_URL" not in environment, service_name
    assert "PERSONAL_GATEWAY_TOKEN_FILE" not in environment, service_name
    assert "PERSONAL_DATA_KEYRING_FILE" not in environment, service_name
    assert "PERSONAL_ALLOWED_ORIGINS" not in environment, service_name
    targets = {mount["target"] for mount in service.get("volumes", [])}
    assert targets.isdisjoint(expected_secrets), (service_name, targets)

frontend = services["frontend"]
assert frontend["environment"]["PERSONAL_GATEWAY_TOKEN_FILE"] == (
    "/run/secrets/personal-gateway-token"
)
for key in ("PRIVATE_DATABASE_URL", "PERSONAL_DATA_KEYRING_FILE", "PERSONAL_ALLOWED_ORIGINS"):
    assert key not in frontend.get("environment", {}), key
frontend_mounts = {mount["target"]: mount for mount in frontend.get("volumes", [])}
assert set(frontend_mounts) == {"/run/secrets/personal-gateway-token"}, frontend_mounts
frontend_gateway = frontend_mounts["/run/secrets/personal-gateway-token"]
assert frontend_gateway["type"] == "bind", frontend_gateway
assert Path(frontend_gateway["source"]).resolve() == test_root / "personal-gateway-token"
assert frontend_gateway["read_only"] is True, frontend_gateway
assert frontend_gateway.get("bind", {}).get("create_host_path") in (None, False), frontend_gateway

personal_worker = services["personal-analysis-worker"]
assert personal_worker["profiles"] == ["personal-ai"], personal_worker["profiles"]
assert personal_worker["environment"]["PERSONAL_ANALYSIS_DATABASE_URL"] == (
    "postgresql+psycopg://quant_personal_analysis:compose-only@db:5432/quant_trading"
)
assert personal_worker["environment"]["PERSONAL_DATA_KEYRING_FILE"] == (
    "/run/secrets/personal-keyring.json"
)
assert personal_worker["environment"]["DEEPSEEK_CREDENTIALS_FILE"] == (
    "/run/secrets/deepseek-credentials.json"
)
assert personal_worker["environment"]["DEEPSEEK_MONTHLY_SOFT_BUDGET_USD"] == "5"
assert float(personal_worker["cpus"]) == 0.5
assert int(personal_worker["mem_limit"]) == 512 * 1024**2
personal_mounts = {
    mount["target"]: mount for mount in personal_worker.get("volumes", [])
}
assert set(personal_mounts) == {
    "/run/secrets/personal-keyring.json",
    "/run/secrets/deepseek-credentials.json",
}, personal_mounts
for target, source in {
    "/run/secrets/personal-keyring.json": test_root / "personal-keyring.json",
    "/run/secrets/deepseek-credentials.json": test_root / "deepseek-credentials.json",
}.items():
    mount = personal_mounts[target]
    assert mount["type"] == "bind", mount
    assert Path(mount["source"]).resolve() == source, mount
    assert mount["read_only"] is True, mount
    assert mount.get("bind", {}).get("create_host_path") in (None, False), mount

deepseek_environment = {"DEEPSEEK_CREDENTIALS_FILE", "DEEPSEEK_TOKEN"}
for service_name, service in services.items():
    if service_name == "personal-analysis-worker":
        continue
    assert deepseek_environment.isdisjoint(service.get("environment", {})), service_name
    targets = {mount["target"] for mount in service.get("volumes", [])}
    assert "/run/secrets/deepseek-credentials.json" not in targets, service_name
PY

echo "新服务器 Compose 合同通过"
