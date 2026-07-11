#!/usr/bin/env bash
set -euo pipefail

export TZ="${TZ:-Asia/Shanghai}"

API_BASE="${API_BASE:-http://127.0.0.1:${API_PORT:-18000}}"
SYNC_DATE="${SYNC_DATE:-$(date +%F)}"
MIN_EXISTING_ROWS="${MIN_EXISTING_ROWS:-5000}"
JOB_POLL_SECONDS="${JOB_POLL_SECONDS:-5}"
JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-7200}"
FUNDAMENTALS_START_DATE="${FUNDAMENTALS_START_DATE:-$SYNC_DATE}"
FUNDAMENTALS_END_DATE="${FUNDAMENTALS_END_DATE:-$SYNC_DATE}"
FUNDAMENTALS_MAX_STOCKS="${FUNDAMENTALS_MAX_STOCKS:-0}"
FUNDAMENTALS_RATE_PER_MINUTE="${FUNDAMENTALS_RATE_PER_MINUTE:-150}"

if [[ ! "$SYNC_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SYNC_DATE must be YYYY-MM-DD, got: $SYNC_DATE" >&2
  exit 2
fi
if [[ ! "$JOB_POLL_SECONDS" =~ ^[1-9][0-9]*$ || ! "$JOB_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "JOB_POLL_SECONDS and JOB_TIMEOUT_SECONDS must be positive integers." >&2
  exit 2
fi

json_field() {
  local field="$1"
  python3 -c 'import json,sys; value=json.load(sys.stdin); result=value.get(sys.argv[1]); print("" if result is None else result)' "$field"
}

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start daily market sync date=${SYNC_DATE} api=${API_BASE}"

curl --fail --silent --show-error --max-time 180 "${API_BASE}/api/db/overview?refresh=true"
echo

job_payload=$(printf '{"action":"daily_market","payload":{"start_date":"%s","end_date":"%s","skip_existing":false,"min_existing_rows":%s}}' "$SYNC_DATE" "$SYNC_DATE" "$MIN_EXISTING_ROWS")
echo "POST /api/sync-jobs action=daily_market date=${SYNC_DATE}"
job_response="$(curl --fail --silent --show-error --max-time 30 \
  -H "Content-Type: application/json" \
  -X POST \
  --data "$job_payload" \
  "${API_BASE}/api/sync-jobs")"
job_id="$(printf '%s' "$job_response" | json_field id)"
if [[ -z "$job_id" ]]; then
  echo "sync job response did not contain id: ${job_response}" >&2
  exit 1
fi

deadline=$(( $(date +%s) + JOB_TIMEOUT_SECONDS ))
while true; do
  job_response="$(curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/sync-jobs/${job_id}")"
  job_status="$(printf '%s' "$job_response" | json_field status)"
  echo "SYNC job_id=${job_id} status=${job_status}"
  case "$job_status" in
    ok)
      break
      ;;
    partial|failed)
      echo "daily_market sync job finished unsuccessfully: ${job_response}" >&2
      exit 1
      ;;
    queued|running)
      ;;
    *)
      echo "daily_market sync job returned unknown status: ${job_status}" >&2
      exit 1
      ;;
  esac
  if (( $(date +%s) >= deadline )); then
    echo "daily_market sync job timed out after ${JOB_TIMEOUT_SECONDS}s: ${job_id}" >&2
    exit 1
  fi
  sleep "$JOB_POLL_SECONDS"
done

echo "SYNC fina_indicator start_date=${FUNDAMENTALS_START_DATE} end_date=${FUNDAMENTALS_END_DATE} rate_per_minute=${FUNDAMENTALS_RATE_PER_MINUTE} max_stocks=${FUNDAMENTALS_MAX_STOCKS}"
docker exec -i quant_trading_api python scripts/ops/sync_fina_indicator_throttled.py \
  --start-date "$FUNDAMENTALS_START_DATE" \
  --end-date "$FUNDAMENTALS_END_DATE" \
  --rate-per-minute "$FUNDAMENTALS_RATE_PER_MINUTE" \
  --max-stocks "$FUNDAMENTALS_MAX_STOCKS"

curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/health?include_counts=false"
echo

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] finish daily market sync date=${SYNC_DATE}"
