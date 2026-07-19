#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 运行本脚本" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNTIME_USER="${RUNTIME_USER:-${SUDO_USER:-ubuntu}}"
DATA_ROOT="${DATA_ROOT:-/srv/quantitative-trading}"
PROJECT_DIR="${PROJECT_DIR:-/opt/quantitative-trading}"
RELEASE_ROOT="${RELEASE_ROOT:-/opt/quantitative-trading-releases}"
VALIDATION_DIR="${VALIDATION_DIR:-/opt/quantitative-trading-bootstrap}"
BASE_COMPOSE_SOURCE="${BASE_COMPOSE_SOURCE:-$REPO_ROOT/docker-compose.yml}"
SERVER_COMPOSE_SOURCE="${SERVER_COMPOSE_SOURCE:-$REPO_ROOT/docker-compose.server.example.yml}"

if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
  echo "运行用户不存在：$RUNTIME_USER" >&2
  exit 1
fi
if [[ ! -f "$BASE_COMPOSE_SOURCE" || ! -f "$SERVER_COMPOSE_SOURCE" ]]; then
  echo "缺少 Compose 基础文件或服务器模板" >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "resolute" ]]; then
  echo "本脚本只验收 Ubuntu 26.04 resolute；当前为 ${ID:-unknown} ${VERSION_CODENAME:-unknown}" >&2
  exit 1
fi

TEMP_ROOT="$(mktemp -d /tmp/quant-docker-bootstrap.XXXXXX)"
cleanup() {
  find "$TEMP_ROOT" -depth -delete
}
trap cleanup EXIT

cat > "$TEMP_ROOT/daemon.json" <<'JSON'
{
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
JSON

if [[ -f /etc/docker/daemon.json ]] && ! cmp -s "$TEMP_ROOT/daemon.json" /etc/docker/daemon.json; then
  echo "已有 /etc/docker/daemon.json 与目标配置不同，拒绝覆盖" >&2
  exit 1
fi

conflicting_packages=()
for package_name in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
  if dpkg-query -W -f='${db:Status-Abbrev}\n' "$package_name" 2>/dev/null | grep -q '^ii '; then
    conflicting_packages+=("$package_name")
  fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
if (( ${#conflicting_packages[@]} > 0 )); then
  apt-get remove -y "${conflicting_packages[@]}"
fi
apt-get install -y ca-certificates curl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat > "$TEMP_ROOT/docker.sources" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
install -m 0644 "$TEMP_ROOT/docker.sources" /etc/apt/sources.list.d/docker.sources

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

install -m 0755 -d /etc/docker
install -m 0644 "$TEMP_ROOT/daemon.json" /etc/docker/daemon.json
systemctl enable --now containerd.service docker.service
systemctl restart docker.service

usermod -aG docker "$RUNTIME_USER"
runtime_group="$(id -gn "$RUNTIME_USER")"

install -d -m 0755 -o root -g root "$DATA_ROOT"
install -d -m 0700 -o root -g root "$DATA_ROOT/postgres"
install -d -m 0750 -o "$RUNTIME_USER" -g "$runtime_group" "$DATA_ROOT/research-artifacts"
install -d -m 0700 -o root -g root "$DATA_ROOT/backups"
install -d -m 0700 -o root -g root \
  "$DATA_ROOT/backups/daily" \
  "$DATA_ROOT/backups/weekly" \
  "$DATA_ROOT/backups/monthly"
install -d -m 0755 -o "$RUNTIME_USER" -g "$runtime_group" \
  "$PROJECT_DIR" \
  "$RELEASE_ROOT" \
  "$VALIDATION_DIR"

install -m 0644 -o "$RUNTIME_USER" -g "$runtime_group" \
  "$BASE_COMPOSE_SOURCE" "$VALIDATION_DIR/docker-compose.yml"
install -m 0644 -o "$RUNTIME_USER" -g "$runtime_group" \
  "$SERVER_COMPOSE_SOURCE" "$VALIDATION_DIR/docker-compose.server.yml"

POSTGRES_PASSWORD=compose-config-only docker compose \
  --env-file /dev/null \
  --file "$VALIDATION_DIR/docker-compose.yml" \
  --file "$VALIDATION_DIR/docker-compose.server.yml" \
  config --quiet

if ss -lntp | grep -Eq ':(2375|2376)\b'; then
  echo "检测到 Docker TCP socket，拒绝验收" >&2
  exit 1
fi

docker version --format 'Docker Server {{.Server.Version}}'
docker compose version
docker info --format 'Storage={{.Driver}} Cgroup={{.CgroupDriver}} Logging={{.LoggingDriver}}'
echo "运行环境初始化完成；未启动任何应用容器"
