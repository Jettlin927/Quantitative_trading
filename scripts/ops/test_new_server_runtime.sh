#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_COMPOSE_FILE="$REPO_ROOT/docker-compose.server.example.yml"

if [[ ! -f "$SERVER_COMPOSE_FILE" ]]; then
  echo "缺少新服务器 Compose 模板：$SERVER_COMPOSE_FILE" >&2
  exit 1
fi

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

echo "新服务器 Compose 合同通过"
