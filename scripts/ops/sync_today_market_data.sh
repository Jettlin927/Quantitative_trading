#!/usr/bin/env bash
set -euo pipefail

export TZ="${TZ:-Asia/Shanghai}"

API_BASE="${API_BASE:-http://127.0.0.1:${API_PORT:-18000}}"
SYNC_DATE="${SYNC_DATE:-$(date +%F)}"
MIN_EXISTING_ROWS="${MIN_EXISTING_ROWS:-5000}"
CURL_MAX_TIME="${CURL_MAX_TIME:-7200}"
FUNDAMENTALS_START_DATE="${FUNDAMENTALS_START_DATE:-$SYNC_DATE}"
FUNDAMENTALS_END_DATE="${FUNDAMENTALS_END_DATE:-$SYNC_DATE}"
FUNDAMENTALS_MAX_STOCKS="${FUNDAMENTALS_MAX_STOCKS:-0}"

if [[ ! "$SYNC_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SYNC_DATE must be YYYY-MM-DD, got: $SYNC_DATE" >&2
  exit 2
fi

post_json() {
  local path="$1"
  local payload="$2"

  echo "POST ${path} ${payload}"
  curl --fail --silent --show-error --max-time "$CURL_MAX_TIME" \
    -H "Content-Type: application/json" \
    -X POST \
    --data "$payload" \
    "${API_BASE}${path}"
  echo
}

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start daily market sync date=${SYNC_DATE} api=${API_BASE}"

curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/health"
echo

market_payload=$(printf '{"start_date":"%s","end_date":"%s","skip_existing":false,"min_existing_rows":%s}' "$SYNC_DATE" "$SYNC_DATE" "$MIN_EXISTING_ROWS")
fundamentals_payload=$(printf '{"start_date":"%s","end_date":"%s","skip_existing":false,"max_stocks":%s}' "$FUNDAMENTALS_START_DATE" "$FUNDAMENTALS_END_DATE" "$FUNDAMENTALS_MAX_STOCKS")

post_json "/api/tushare/sync-stock-basic" "{}"
post_json "/api/tushare/sync-market-daily" "$market_payload"
post_json "/api/tushare/sync-market-daily-basic" "$market_payload"
post_json "/api/tushare/sync-market-fundamentals" "$fundamentals_payload"

curl --fail --silent --show-error --max-time 60 "${API_BASE}/api/db/overview"
echo

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] finish daily market sync date=${SYNC_DATE}"
