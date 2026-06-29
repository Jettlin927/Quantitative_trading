#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

env_value() {
  local key="$1"
  local fallback="$2"
  local value=""

  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -F= -v key="$key" '
      $0 !~ /^[[:space:]]*#/ && $1 == key {
        sub(/^[^=]*=/, "")
        gsub(/^"|"$/, "")
        gsub(/^'\''|'\''$/, "")
        print
        exit
      }
    ' "$ENV_FILE")"
  fi

  printf '%s' "${value:-$fallback}"
}

REMOTE="${REMOTE:-$(env_value REMOTE ubuntu@182.254.180.169)}"
REMOTE_SSH_PORT="${REMOTE_SSH_PORT:-$(env_value REMOTE_SSH_PORT "$(env_value DEV_SERVER_SSH_PORT 22)")}"
REMOTE_SSH_KEY="${REMOTE_SSH_KEY:-$(env_value REMOTE_SSH_KEY "$(env_value DEV_SERVER_SSH_KEY "")")}"
REPO_URL="${REPO_URL:-$(env_value REPO_URL ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git)}"
REMOTE_KEY="${REMOTE_KEY:-$(env_value REMOTE_KEY .ssh/quantitative_trading_github)}"
LOCAL_KEY="${LOCAL_KEY:-$(env_value LOCAL_KEY "")}"

usage() {
  cat <<'EOF'
Usage: bootstrap_remote_github_ssh.sh

Environment:
  REMOTE=ubuntu@182.254.180.169
  REMOTE_SSH_PORT=22
  REMOTE_SSH_KEY=/Users/jettlin/.ssh/quantitative_trading_server_ed25519
  REPO_URL=ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git
  LOCAL_KEY=$HOME/.ssh/id_ed25519
  REMOTE_KEY=.ssh/quantitative_trading_github

This copies only the selected SSH key file to the server. It never writes the
private key into the repository.
EOF
}

choose_local_key() {
  local candidate

  if [[ -n "$LOCAL_KEY" ]]; then
    printf '%s' "$LOCAL_KEY"
    return
  fi

  for candidate in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return
    fi
  done

  return 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

LOCAL_KEY="$(choose_local_key)" || {
  echo "No local SSH key found. Set LOCAL_KEY=/path/to/key." >&2
  exit 2
}

if [[ ! -f "$LOCAL_KEY" ]]; then
  echo "LOCAL_KEY does not exist: $LOCAL_KEY" >&2
  exit 2
fi

tmp_pub="$(mktemp)"
cleanup() {
  rm -f "$tmp_pub"
}
trap cleanup EXIT

if [[ -f "${LOCAL_KEY}.pub" ]]; then
  cp "${LOCAL_KEY}.pub" "$tmp_pub"
else
  ssh-keygen -y -f "$LOCAL_KEY" > "$tmp_pub"
fi

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$REMOTE_SSH_PORT")
scp_opts=(-q -o BatchMode=yes -o StrictHostKeyChecking=accept-new -P "$REMOTE_SSH_PORT")
if [[ -n "$REMOTE_SSH_KEY" ]]; then
  ssh_opts+=(-i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes)
  scp_opts+=(-i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes)
fi

ssh "${ssh_opts[@]}" "$REMOTE" \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh'

scp "${scp_opts[@]}" "$LOCAL_KEY" "$REMOTE:${REMOTE_KEY}.tmp"
scp "${scp_opts[@]}" "$tmp_pub" "$REMOTE:${REMOTE_KEY}.pub.tmp"

ssh "${ssh_opts[@]}" "$REMOTE" \
  "mv '${REMOTE_KEY}.tmp' '${REMOTE_KEY}' &&
   mv '${REMOTE_KEY}.pub.tmp' '${REMOTE_KEY}.pub' &&
   chmod 600 '${REMOTE_KEY}' &&
   chmod 644 '${REMOTE_KEY}.pub' &&
   (ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null || true) &&
   (ssh-keyscan -p 443 -H ssh.github.com >> ~/.ssh/known_hosts 2>/dev/null || true)"

ssh "${ssh_opts[@]}" "$REMOTE" \
  "GIT_SSH_COMMAND='ssh -i ~/${REMOTE_KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new' git ls-remote '${REPO_URL}' HEAD >/dev/null"

echo "Remote GitHub SSH access OK: ${REMOTE} -> ${REPO_URL}"
