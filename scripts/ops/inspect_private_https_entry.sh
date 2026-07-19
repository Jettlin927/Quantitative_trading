#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 运行本脚本，以只读验收凭据文件和 Docker 状态" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_COMPOSE_FILE="${BASE_COMPOSE_FILE:-$REPO_ROOT/docker-compose.yml}"
SERVER_COMPOSE_FILE="${SERVER_COMPOSE_FILE:-$REPO_ROOT/docker-compose.server.yml}"
HTTPS_COMPOSE_FILE="${HTTPS_COMPOSE_FILE:-$REPO_ROOT/docker-compose.private-https.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$REPO_ROOT/.env}"
PRIVATE_HTTPS_URL="${PRIVATE_HTTPS_URL:-}"
HTTPS_TEST_SCENARIO="${HTTPS_TEST_SCENARIO:-空闲}"

case "$PRIVATE_HTTPS_URL" in
  https://*) ;;
  *)
    echo "必须通过 PRIVATE_HTTPS_URL 提供已批准的 https:// 入口" >&2
    exit 1
    ;;
esac
python3 - "$PRIVATE_HTTPS_URL" <<'PY'
import sys
from urllib.parse import urlsplit

url = urlsplit(sys.argv[1])
if (
    url.scheme != "https"
    or not url.hostname
    or url.username is not None
    or url.password is not None
    or url.port not in (None, 443)
    or url.path not in ("", "/")
    or url.query
    or url.fragment
):
    raise SystemExit("PRIVATE_HTTPS_URL 必须是无 userinfo、查询或片段的精确 HTTPS 根入口")
PY
for required_file in \
  "$BASE_COMPOSE_FILE" \
  "$SERVER_COMPOSE_FILE" \
  "$HTTPS_COMPOSE_FILE" \
  "$COMPOSE_ENV_FILE"; do
  if [[ ! -f "$required_file" ]]; then
    echo "缺少验收文件：$required_file" >&2
    exit 1
  fi
done

umask 077
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/quant-private-https-inspect.XXXXXX")"
cleanup() {
  find "$TEST_ROOT" -depth -delete
}
trap cleanup EXIT

docker compose \
  --env-file "$COMPOSE_ENV_FILE" \
  --file "$BASE_COMPOSE_FILE" \
  --file "$SERVER_COMPOSE_FILE" \
  --file "$HTTPS_COMPOSE_FILE" \
  config --quiet

TOKEN_FILE="/srv/quantitative-trading/secrets/cloudflared-tunnel-token"
TOKEN_DIRECTORY="/srv/quantitative-trading/secrets"
POSTGRES_PASSWORD=compose-inspection-only \
docker compose \
  --env-file /dev/null \
  --file "$BASE_COMPOSE_FILE" \
  --file "$SERVER_COMPOSE_FILE" \
  --file "$HTTPS_COMPOSE_FILE" \
  config --format json > "$TEST_ROOT/config.json"

python3 - "$TEST_ROOT/config.json" "$TOKEN_FILE" <<'PY'
import json
import sys
from pathlib import Path

config = json.load(open(sys.argv[1], encoding="utf-8"))
mounts = config["services"]["cloudflared"].get("volumes", [])
matches = [
    mount
    for mount in mounts
    if mount.get("target") == "/run/secrets/cloudflared-tunnel-token"
]
if len(matches) != 1 or matches[0].get("type") != "bind" or not matches[0].get("read_only"):
    raise SystemExit("cloudflared token 必须是唯一的只读 bind mount")
if Path(matches[0]["source"]).resolve() != Path(sys.argv[2]).resolve():
    raise SystemExit("cloudflared token mount 与待验收文件路径不一致")
PY

if (
  [[ ! -d "$TOKEN_DIRECTORY" || -L "$TOKEN_DIRECTORY" ]] ||
  [[ "$(realpath -m "$TOKEN_DIRECTORY")" != "$TOKEN_DIRECTORY" ]]
); then
  echo "Tunnel token 目录必须是不含符号链接的固定目录" >&2
  exit 1
fi
read -r directory_mode directory_uid directory_gid < <(stat -c '%a %u %g' "$TOKEN_DIRECTORY")
if [[ "$directory_mode" != "700" || "$directory_uid" != "0" || "$directory_gid" != "0" ]]; then
  echo "Tunnel token 目录必须为 0700 且归属 root:root" >&2
  exit 1
fi
if [[ ! -f "$TOKEN_FILE" || -L "$TOKEN_FILE" ]]; then
  echo "Tunnel token 必须是存在且非符号链接的普通文件" >&2
  exit 1
fi
read -r token_mode token_uid token_gid < <(stat -c '%a %u %g' "$TOKEN_FILE")
if [[ "$token_mode" != "600" || "$token_uid" != "65532" || "$token_gid" != "65532" ]]; then
  echo "Tunnel token 必须为 0600 且归属 65532:65532" >&2
  exit 1
fi

ss -H -ltn > "$TEST_ROOT/listeners.txt"
python3 - "$TEST_ROOT/listeners.txt" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path


expected = {"5432", "18000", "15173"}
seen: set[str] = set()
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    parts = line.split()
    if len(parts) < 4:
        continue
    endpoint = parts[3]
    port = endpoint.rsplit(":", 1)[-1]
    if port not in expected:
        continue
    host = endpoint[: -(len(port) + 1)].strip("[]")
    if host not in {"127.0.0.1", "::1"}:
        raise SystemExit(f"端口 {port} 存在非 loopback 监听：{endpoint}")
    seen.add(port)
missing = expected - seen
if missing:
    raise SystemExit(f"缺少预期 loopback 监听：{sorted(missing)}")
PY

docker inspect --format '{{json .State}}' \
  quant_trading_cloudflared > "$TEST_ROOT/cloudflared-state.json"
CLOUDFLARED_PID="$(docker inspect --format '{{.State.Pid}}' quant_trading_cloudflared)"
if [[ ! "$CLOUDFLARED_PID" =~ ^[1-9][0-9]*$ ]]; then
  echo "cloudflared 容器没有可验收的运行 PID" >&2
  exit 1
fi
CONTAINER_TOKEN_FILE="/proc/$CLOUDFLARED_PID/root/run/secrets/cloudflared-tunnel-token"
if [[ ! -f "$CONTAINER_TOKEN_FILE" ]]; then
  echo "cloudflared 容器内没有可读的 token file mount" >&2
  exit 1
fi
if [[ "$(stat -Lc '%d:%i' "$TOKEN_FILE")" != "$(stat -Lc '%d:%i' "$CONTAINER_TOKEN_FILE")" ]]; then
  echo "cloudflared 仍绑定旧 token inode；必须 force-recreate 后再验收" >&2
  exit 1
fi
docker inspect --format '{{json .Config}}' quant_trading_cloudflared |
  python3 -c '
import json
import sys

config = json.load(sys.stdin)
expected_image = (
    "cloudflare/cloudflared:2026.7.2"
    "@sha256:4f6655284ab3d252b7f28fedb19fe6c8fc82ee5b1295c20ac74d475e5398a52d"
)
expected_command = [
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
]
expected_environment = {
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
}
if config.get("Image") != expected_image:
    raise SystemExit("cloudflared 实际镜像不是仓库锁定 digest")
if config.get("User") != "65532:65532":
    raise SystemExit("cloudflared 实际用户不是 65532:65532")
if config.get("Entrypoint") != ["cloudflared", "--no-autoupdate"]:
    raise SystemExit("cloudflared 实际 entrypoint 与锁定镜像合同不一致")
if config.get("Cmd") != expected_command:
    raise SystemExit("cloudflared 实际命令不符合 token-file 合同")
if set(config.get("Env") or []) != expected_environment:
    raise SystemExit("cloudflared 实际环境变量存在额外或缺失项")
if config.get("StopTimeout") != 30:
    raise SystemExit("cloudflared 实际停止等待时间与仓库合同不一致")
healthcheck = config.get("Healthcheck") or {}
if healthcheck.get("Test") != [
    "CMD",
    "/usr/local/bin/cloudflared",
    "tunnel",
    "--metrics",
    "127.0.0.1:2000",
    "ready",
]:
    raise SystemExit("cloudflared 实际健康检查命令与仓库合同不一致")
if (
    healthcheck.get("Interval") != 15_000_000_000
    or healthcheck.get("Timeout") != 5_000_000_000
    or healthcheck.get("Retries") != 4
    or healthcheck.get("StartPeriod") != 20_000_000_000
):
    raise SystemExit("cloudflared 实际健康检查时序与仓库合同不一致")
'
docker inspect --format '{{json .HostConfig}}' \
  quant_trading_cloudflared > "$TEST_ROOT/cloudflared-host-config.json"
docker inspect --format '{{json .NetworkSettings.Networks}}' \
  quant_trading_cloudflared > "$TEST_ROOT/cloudflared-networks.json"
read -r ORIGIN_NETWORK EGRESS_NETWORK < <(
  python3 - "$TEST_ROOT/cloudflared-networks.json" <<'PY'
import json
import sys

networks = set(json.load(open(sys.argv[1], encoding="utf-8")))
origin = [name for name in networks if name.endswith("_private_https_origin")]
egress = [name for name in networks if name.endswith("_private_https_egress")]
if len(origin) != 1 or len(egress) != 1 or len(networks) != 2:
    raise SystemExit("cloudflared 实际网络不是唯一 origin + egress")
print(origin[0], egress[0])
PY
)
docker network inspect "$ORIGIN_NETWORK" "$EGRESS_NETWORK" > "$TEST_ROOT/docker-networks.json"
docker inspect --format '{{json .Mounts}}' \
  quant_trading_cloudflared > "$TEST_ROOT/cloudflared-mounts.json"
for container_name in quant_trading_frontend quant_trading_api quant_trading_db quant_trading_worker; do
  docker inspect --format '{{json .NetworkSettings.Networks}}' \
    "$container_name" > "$TEST_ROOT/$container_name-networks.json"
done
for port_contract in \
  'quant_trading_db 5432/tcp 5432' \
  'quant_trading_api 8000/tcp 18000' \
  'quant_trading_frontend 5173/tcp 15173'; do
  read -r container_name container_port host_port <<< "$port_contract"
  docker inspect --format '{{json .HostConfig.PortBindings}}' "$container_name" |
    python3 -c '
import json
import sys

bindings = json.load(sys.stdin) or {}
container_port = sys.argv[1]
host_port = sys.argv[2]
expected = {container_port: [{"HostIp": "127.0.0.1", "HostPort": host_port}]}
if bindings != expected:
    raise SystemExit(f"{sys.argv[3]} 实际端口发布不是唯一 loopback 合同")
' "$container_port" "$host_port" "$container_name"
done
docker inspect --format '{{json .HostConfig.PortBindings}}' quant_trading_worker |
  python3 -c '
import json
import sys

if json.load(sys.stdin):
    raise SystemExit("quant_trading_worker 不得发布宿主端口")
'
python3 - "$TEST_ROOT" "$TOKEN_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


root = Path(sys.argv[1])


def load(name: str) -> object:
    return json.loads((root / name).read_text(encoding="utf-8"))


state = load("cloudflared-state.json")
if not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
    raise SystemExit("cloudflared 容器必须为 running + healthy")
host_config = load("cloudflared-host-config.json")
if host_config.get("PortBindings"):
    raise SystemExit("cloudflared 不得发布任何宿主端口")
if not host_config.get("ReadonlyRootfs"):
    raise SystemExit("cloudflared 根文件系统必须只读")
if host_config.get("Privileged") or host_config.get("CapAdd"):
    raise SystemExit("cloudflared 不得使用 privileged 或额外 capability")
if host_config.get("CapDrop") != ["ALL"]:
    raise SystemExit("cloudflared 必须丢弃全部 capability")
if host_config.get("SecurityOpt") not in (
    ["no-new-privileges:true"],
    ["no-new-privileges=true"],
    ["no-new-privileges"],
):
    raise SystemExit("cloudflared 必须启用 no-new-privileges")
if host_config.get("Memory") != 192 * 1024**2 or host_config.get("NanoCpus") != 150_000_000:
    raise SystemExit("cloudflared 资源上限与仓库合同不一致")
if host_config.get("MemoryReservation") != 64 * 1024**2:
    raise SystemExit("cloudflared 内存保留值与仓库合同不一致")
if host_config.get("PidsLimit") != 64:
    raise SystemExit("cloudflared PID 上限与仓库合同不一致")
tmpfs = host_config.get("Tmpfs") or {}
tmpfs_options = tmpfs.get("/tmp", "")
if set(tmpfs) != {"/tmp"} or "mode=1777" not in tmpfs_options or not any(
    size in tmpfs_options for size in ("size=16m", "size=16777216")
):
    raise SystemExit("cloudflared /tmp tmpfs 与仓库合同不一致")
if host_config.get("RestartPolicy", {}).get("Name") != "unless-stopped":
    raise SystemExit("cloudflared 重启策略与仓库合同不一致")
log_config = host_config.get("LogConfig", {})
if log_config.get("Type") != "json-file" or log_config.get("Config") != {
    "max-file": "3",
    "max-size": "10m",
}:
    raise SystemExit("cloudflared 日志轮转与仓库合同不一致")
network_names = set(load("cloudflared-networks.json"))
if len(network_names) != 2 or not any(name.endswith("_private_https_origin") for name in network_names):
    raise SystemExit("cloudflared 必须只加入隔离的 origin/egress 网络")
if not any(name.endswith("_private_https_egress") for name in network_names):
    raise SystemExit("cloudflared 缺少独立出站网络")

network_details = {
    network["Name"]: network for network in load("docker-networks.json")
}
origin_name = next(name for name in network_names if name.endswith("_private_https_origin"))
egress_name = next(name for name in network_names if name.endswith("_private_https_egress"))
origin = network_details[origin_name]
egress = network_details[egress_name]
if origin.get("Driver") != "bridge" or origin.get("Internal") is not True:
    raise SystemExit("Tunnel origin 必须是 internal bridge 网络")
if egress.get("Driver") != "bridge" or egress.get("Internal") is not False:
    raise SystemExit("Tunnel egress 必须是独立的非 internal bridge 网络")
origin_containers = {item["Name"] for item in origin.get("Containers", {}).values()}
egress_containers = {item["Name"] for item in egress.get("Containers", {}).values()}
if origin_containers != {"quant_trading_cloudflared", "quant_trading_frontend"}:
    raise SystemExit("Tunnel origin 网络成员不是唯一 cloudflared + frontend")
if egress_containers != {"quant_trading_cloudflared"}:
    raise SystemExit("Tunnel egress 网络只允许 cloudflared")
for container_name in ("quant_trading_api", "quant_trading_db", "quant_trading_worker"):
    names = set(load(f"{container_name}-networks.json"))
    if any(name.endswith(("_private_https_origin", "_private_https_egress")) for name in names):
        raise SystemExit(f"{container_name} 不得加入 Tunnel 网络")
frontend_networks = set(load("quant_trading_frontend-networks.json"))
if not any(name.endswith("_private_https_origin") for name in frontend_networks):
    raise SystemExit("前端缺少 Tunnel origin 隔离网络")

token_path = Path(sys.argv[2]).resolve()
mounts = load("cloudflared-mounts.json")
matches = [mount for mount in mounts if mount.get("Destination") == "/run/secrets/cloudflared-tunnel-token"]
if (
    len(mounts) != 1
    or len(matches) != 1
    or matches[0].get("Type") != "bind"
    or matches[0].get("RW")
    or matches[0].get("Propagation") != "rprivate"
    or Path(matches[0]["Source"]).resolve() != token_path
):
    raise SystemExit("cloudflared 实际 token mount 不符合只读文件合同")
PY

docker logs quant_trading_cloudflared > "$TEST_ROOT/cloudflared.log" 2>&1
python3 - "$TOKEN_FILE" "$TEST_ROOT/cloudflared.log" <<'PY'
import re
import sys
from pathlib import Path

token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
logs = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
if token and token in logs:
    raise SystemExit("cloudflared 日志中出现 Tunnel token 内容")
if re.search(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9._-]{80,}", logs):
    raise SystemExit("cloudflared 保留日志中出现疑似历史 Tunnel token")
PY

curl --disable --silent --show-error --fail \
  --max-time 10 \
  --output /dev/null \
  http://127.0.0.1:15173/

curl --disable --silent --show-error \
  --proto '=https' \
  --tlsv1.2 \
  --max-time 20 \
  --output /dev/null \
  --dump-header "$TEST_ROOT/https-headers.txt" \
  --write-out '%{http_code}' \
  "$PRIVATE_HTTPS_URL" > "$TEST_ROOT/https-status.txt"
python3 - \
  "$TEST_ROOT/https-status.txt" \
  "$TEST_ROOT/https-headers.txt" \
  "$PRIVATE_HTTPS_URL" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit


status = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
headers = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
entry_host = urlsplit(sys.argv[3]).hostname
locations = [
    line.split(":", 1)[1].strip()
    for line in headers.splitlines()
    if line.lower().startswith("location:")
]
if status not in {"301", "302", "303", "307", "308"}:
    raise SystemExit(f"未认证请求应转入 Access 登录，实际 HTTP {status}")


def is_access_login(location: str) -> bool:
    parsed = urlsplit(location)
    login_path = parsed.path == "/cdn-cgi/access/login" or parsed.path.startswith(
        "/cdn-cgi/access/login/"
    )
    if not login_path or parsed.username is not None or parsed.password is not None:
        return False
    if not parsed.scheme and not parsed.netloc:
        return location.startswith("/cdn-cgi/access/login")
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        hostname == entry_host
        or hostname == "cloudflareaccess.com"
        or hostname.endswith(".cloudflareaccess.com")
    )


if not locations or not any(is_access_login(location) for location in locations):
    raise SystemExit("未认证请求未转入 Cloudflare Access 登录")
PY

echo "私有 HTTPS 只读基础验收通过：TLS 可验证、未认证请求进入 Access、源端口仅 loopback、Tunnel 容器健康且网络隔离"
echo "资源采样场景：$HTTPS_TEST_SCENARIO"
docker stats --no-stream \
  --format '{{.Name}} CPU={{.CPUPerc}} MEM={{.MemUsage}} PIDS={{.PIDs}}' \
  quant_trading_cloudflared
