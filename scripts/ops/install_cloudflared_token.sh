#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 运行本脚本" >&2
  exit 1
fi
if [[ ! -t 0 ]]; then
  echo "必须在交互式终端中输入 Tunnel token，拒绝命令行、环境变量或管道传入" >&2
  exit 1
fi

TOKEN_DIRECTORY="/srv/quantitative-trading/secrets"
TOKEN_DESTINATION="$TOKEN_DIRECTORY/cloudflared-tunnel-token"
TOKEN_UID=65532
TOKEN_GID=65532

if [[ "$(realpath -m "$TOKEN_DIRECTORY")" != "$TOKEN_DIRECTORY" ]]; then
  echo "Tunnel token 目录的父路径不得包含符号链接" >&2
  exit 1
fi
if [[ -L "$TOKEN_DIRECTORY" || -L "$TOKEN_DESTINATION" ]]; then
  echo "Tunnel token 目录与文件不得是符号链接" >&2
  exit 1
fi
if [[ -e "$TOKEN_DIRECTORY" && ! -d "$TOKEN_DIRECTORY" ]]; then
  echo "Tunnel token 上层路径必须是目录" >&2
  exit 1
fi
if [[ -e "$TOKEN_DESTINATION" && ! -f "$TOKEN_DESTINATION" ]]; then
  echo "Tunnel token 目标必须是普通文件" >&2
  exit 1
fi
TEMP_TOKEN_FILE=""
cleanup() {
  if [[ -n "$TEMP_TOKEN_FILE" && -f "$TEMP_TOKEN_FILE" ]]; then
    find "$TEMP_TOKEN_FILE" -maxdepth 0 -delete
  fi
}
trap cleanup EXIT

read -r -s -p "请粘贴 Cloudflare Tunnel token（输入不回显）：" TUNNEL_TOKEN_VALUE
printf '\n' >&2
if [[ -z "$TUNNEL_TOKEN_VALUE" || "$TUNNEL_TOKEN_VALUE" == *[[:space:]]* ]]; then
  unset TUNNEL_TOKEN_VALUE
  echo "Tunnel token 不能为空或包含空白字符" >&2
  exit 1
fi

install -d -m 0700 -o root -g root "$TOKEN_DIRECTORY"
TEMP_TOKEN_FILE="$(mktemp "$TOKEN_DIRECTORY/.cloudflared-tunnel-token.XXXXXX")"
printf '%s' "$TUNNEL_TOKEN_VALUE" > "$TEMP_TOKEN_FILE"
unset TUNNEL_TOKEN_VALUE
chown "$TOKEN_UID:$TOKEN_GID" "$TEMP_TOKEN_FILE"
chmod 0600 "$TEMP_TOKEN_FILE"
mv -fT -- "$TEMP_TOKEN_FILE" "$TOKEN_DESTINATION"
TEMP_TOKEN_FILE=""

read -r actual_mode actual_uid actual_gid < <(stat -c '%a %u %g' "$TOKEN_DESTINATION")
if [[ "$actual_mode" != "600" || "$actual_uid" != "$TOKEN_UID" || "$actual_gid" != "$TOKEN_GID" ]]; then
  echo "Tunnel token 权限读回不符合 0600 / 65532:65532" >&2
  exit 1
fi

echo "Tunnel token 已安全写入 $TOKEN_DESTINATION；未输出凭据内容"
