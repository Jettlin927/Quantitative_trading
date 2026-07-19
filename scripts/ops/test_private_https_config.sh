#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_COMPOSE_FILE="$REPO_ROOT/docker-compose.server.example.yml"
HTTPS_COMPOSE_FILE="$REPO_ROOT/docker-compose.private-https.example.yml"

if [[ ! -f "$HTTPS_COMPOSE_FILE" ]]; then
  echo "缺少私有 HTTPS Compose 模板：$HTTPS_COMPOSE_FILE" >&2
  exit 1
fi

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/quant-private-https.XXXXXX")"
cleanup() {
  find "$TEST_ROOT" -depth -delete
}
trap cleanup EXIT

mkdir -p \
  "$TEST_ROOT/postgres" \
  "$TEST_ROOT/research-artifacts"
umask 077

POSTGRES_PASSWORD=compose-config-only \
POSTGRES_DATA_DIR="$TEST_ROOT/postgres" \
RESEARCH_ARTIFACTS_DIR="$TEST_ROOT/research-artifacts" \
docker compose \
  --env-file /dev/null \
  --file "$REPO_ROOT/docker-compose.yml" \
  --file "$SERVER_COMPOSE_FILE" \
  --file "$HTTPS_COMPOSE_FILE" \
  config --format json > "$TEST_ROOT/config.json"

python3 - \
  "$TEST_ROOT/config.json" \
  "$HTTPS_COMPOSE_FILE" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


config_path = Path(sys.argv[1])
token_file = Path("/srv/quantitative-trading/secrets/cloudflared-tunnel-token")
https_compose_path = Path(sys.argv[2])
rendered = config_path.read_text(encoding="utf-8")
config = json.loads(rendered)
services = config["services"]

assert "cloudflared" in services, services.keys()
cloudflared = services["cloudflared"]

assert cloudflared["image"] == (
    "cloudflare/cloudflared:2026.7.2"
    "@sha256:4f6655284ab3d252b7f28fedb19fe6c8fc82ee5b1295c20ac74d475e5398a52d"
), cloudflared["image"]
assert cloudflared.get("ports", []) == [], cloudflared.get("ports")
assert cloudflared.get("environment", {}) == {}, cloudflared.get("environment")
assert cloudflared.get("read_only") is True, cloudflared.get("read_only")
assert cloudflared.get("user") == "65532:65532", cloudflared.get("user")
assert cloudflared.get("privileged", False) is False, cloudflared.get("privileged")
assert cloudflared.get("cap_drop") == ["ALL"], cloudflared.get("cap_drop")
assert "no-new-privileges:true" in cloudflared.get("security_opt", []), cloudflared.get("security_opt")
assert cloudflared.get("restart") == "unless-stopped", cloudflared.get("restart")
assert int(cloudflared["pids_limit"]) == 64, cloudflared["pids_limit"]
assert int(cloudflared["mem_limit"]) == 192 * 1024**2, cloudflared["mem_limit"]
assert float(cloudflared["cpus"]) == 0.15, cloudflared["cpus"]
assert cloudflared["logging"]["options"] == {"max-file": "3", "max-size": "10m"}

command = cloudflared["command"]
assert command == [
    "tunnel",
    "--metrics",
    "127.0.0.1:2000",
    "--loglevel",
    "info",
    "--transport-loglevel",
    "warn",
    "run",
    "--token-file",
    "/run/secrets/cloudflared-tunnel-token",
], command
assert "--token" not in command, command

mounts = cloudflared.get("volumes", [])
assert len(mounts) == 1, mounts
mount = mounts[0]
assert mount["type"] == "bind", mount
assert Path(mount["source"]).resolve() == token_file, mount
assert mount["target"] == "/run/secrets/cloudflared-tunnel-token", mount
assert mount.get("read_only") is True, mount
assert mount.get("bind", {}).get("create_host_path") in (None, False), mount

healthcheck = cloudflared["healthcheck"]
assert healthcheck["test"] == [
    "CMD",
    "/usr/local/bin/cloudflared",
    "tunnel",
    "--metrics",
    "127.0.0.1:2000",
    "ready",
], healthcheck

def network_names(service: dict[str, object]) -> set[str]:
    networks = service.get("networks", {})
    if isinstance(networks, list):
        return set(networks)
    return set(networks)


assert network_names(cloudflared) == {"private_https_egress", "private_https_origin"}
assert cloudflared["networks"]["private_https_egress"]["gw_priority"] == 1
assert cloudflared["networks"]["private_https_origin"].get("gw_priority", 0) == 0
assert network_names(services["frontend"]) == {"default", "private_https_origin"}
for service_name in ("db", "api", "worker"):
    assert network_names(services[service_name]) == {"default"}, (
        service_name,
        network_names(services[service_name]),
    )
assert config["networks"]["private_https_origin"]["internal"] is True
assert config["networks"]["private_https_egress"].get("internal", False) is False

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

memory_total = 0
cpu_total = 0.0
for service_name in ("db", "api", "worker", "frontend", "cloudflared"):
    service = services[service_name]
    memory_total += int(service["mem_limit"])
    cpu_total += float(service["cpus"])
assert memory_total <= 3 * 1024**3, memory_total
assert cpu_total <= 2.0, cpu_total

source = https_compose_path.read_text(encoding="utf-8")
assert source.count("create_host_path: false") == 1, https_compose_path
assert not re.search(r"(?:^|\s)TUNNEL_TOKEN\s*:", source), https_compose_path
assert not re.search(r"(?:^|\s)--token(?:\s|$)", source), https_compose_path

repo_root = https_compose_path.parent
installer_path = repo_root / "scripts" / "ops" / "install_cloudflared_token.sh"
inspector_path = repo_root / "scripts" / "ops" / "inspect_private_https_entry.sh"
runbook_path = repo_root / "docs" / "operations" / "private-https-entry.md"
for required_path in (installer_path, inspector_path, runbook_path):
    assert required_path.is_file(), required_path

installer = installer_path.read_text(encoding="utf-8")
for required_fragment in (
    '[[ ! -t 0 ]]',
    'read -r -s -p',
    'TOKEN_DIRECTORY="/srv/quantitative-trading/secrets"',
    'TOKEN_DESTINATION="$TOKEN_DIRECTORY/cloudflared-tunnel-token"',
    'TOKEN_UID=65532',
    'TOKEN_GID=65532',
    'chmod 0600',
    'mv -fT --',
    'realpath -m "$TOKEN_DIRECTORY"',
    '[[ -L "$TOKEN_DIRECTORY" || -L "$TOKEN_DESTINATION" ]]',
    '[[ -e "$TOKEN_DESTINATION" && ! -f "$TOKEN_DESTINATION" ]]',
):
    assert required_fragment in installer, (required_fragment, installer_path)
assert "export TUNNEL_TOKEN" not in installer, installer_path
assert "${CLOUDFLARED_TOKEN_FILE" not in installer, installer_path

inspector = inspector_path.read_text(encoding="utf-8")
assert "docker logs --since" not in inspector, inspector_path
for required_fragment in (
    "curl --disable",
    "from urllib.parse import urlsplit",
    'hostname.endswith(".cloudflareaccess.com")',
    'TOKEN_DIRECTORY="/srv/quantitative-trading/secrets"',
    'directory_mode" != "700"',
    'config.get("Healthcheck")',
    'config.get("StopTimeout") != 30',
    '/proc/$CLOUDFLARED_PID/root/run/secrets/cloudflared-tunnel-token',
    "stat -Lc '%d:%i'",
):
    assert required_fragment in inspector, (required_fragment, inspector_path)
for forbidden_command in ("up", "down", "start", "stop", "restart", "rm"):
    assert not re.search(
        rf"^\s*docker(?:\s+compose)?\b[^\n]*\b{forbidden_command}\b",
        inspector,
        flags=re.MULTILINE,
    ), (forbidden_command, inspector_path)

runbook = runbook_path.read_text(encoding="utf-8")
for required_fragment in (
    "上线前人工批准单",
    "HTTP Host Header",
    "http_status:404",
    "控制面全量读回",
    "登录后应用",
    "显式退出",
    "会话超时",
    "重复猜测",
    "SSH 恢复",
    "失败关闭",
):
    assert required_fragment in runbook, (required_fragment, runbook_path)

env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
assert "CLOUDFLARED_TOKEN_FILE=" not in env_example
assert "CLOUDFLARED_TUNNEL_TOKEN=" not in env_example
gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
assert "docker-compose.private-https.yml" in gitignore
PY

echo "私有 HTTPS Compose 合同通过"
