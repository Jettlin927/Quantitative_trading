#!/usr/bin/env bash
set -euo pipefail

export TZ="${TZ:-Asia/Shanghai}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
API_BASE="${API_BASE:-http://127.0.0.1:${API_PORT:-18000}}"
SYNC_DATE="${SYNC_DATE:-$(date +%F)}"
MIN_EXISTING_ROWS="${MIN_EXISTING_ROWS:-5000}"
JOB_POLL_SECONDS="${JOB_POLL_SECONDS:-5}"
JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-7200}"
FUNDAMENTALS_START_DATE="${FUNDAMENTALS_START_DATE:-$SYNC_DATE}"
FUNDAMENTALS_END_DATE="${FUNDAMENTALS_END_DATE:-$SYNC_DATE}"
FUNDAMENTALS_MAX_STOCKS="${FUNDAMENTALS_MAX_STOCKS:-0}"
FUNDAMENTALS_RATE_PER_MINUTE="${FUNDAMENTALS_RATE_PER_MINUTE:-150}"
QUALITY_UNIVERSE_LIMIT="${QUALITY_UNIVERSE_LIMIT:-20}"
QUALITY_BENCHMARK="${QUALITY_BENCHMARK:-000300.SH}"
QUALITY_UNIVERSE_DIR="${QUALITY_UNIVERSE_DIR:-${PROJECT_ROOT}/outputs/quality-universes}"
QUALITY_UNIVERSE_CONTAINER_ROOT="${QUALITY_UNIVERSE_CONTAINER_ROOT:-/app/outputs/quality-universes}"
LOCK_FILE="${LOCK_FILE:-/tmp/quantitative-trading-daily-sync.lock}"
FLOCK_BIN="${FLOCK_BIN:-flock}"

if [[ ! "$SYNC_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SYNC_DATE must be YYYY-MM-DD, got: $SYNC_DATE" >&2
  exit 2
fi
if [[ ! "$JOB_POLL_SECONDS" =~ ^[1-9][0-9]*$ || ! "$JOB_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "JOB_POLL_SECONDS and JOB_TIMEOUT_SECONDS must be positive integers." >&2
  exit 2
fi
if [[ ! "$QUALITY_UNIVERSE_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
  echo "QUALITY_UNIVERSE_LIMIT must be a positive integer." >&2
  exit 2
fi
if [[ ! "$QUALITY_BENCHMARK" =~ ^[0-9A-Z]+\.(SH|SZ)$ ]]; then
  echo "QUALITY_BENCHMARK must be a Tushare index code such as 000300.SH." >&2
  exit 2
fi
if [[ ! "$FUNDAMENTALS_START_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ || ! "$FUNDAMENTALS_END_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "FUNDAMENTALS_START_DATE and FUNDAMENTALS_END_DATE must be YYYY-MM-DD." >&2
  exit 2
fi
if [[ "$FUNDAMENTALS_START_DATE" > "$FUNDAMENTALS_END_DATE" || ! "$FUNDAMENTALS_MAX_STOCKS" =~ ^[0-9]+$ ]]; then
  echo "Invalid fundamentals date range or FUNDAMENTALS_MAX_STOCKS." >&2
  exit 2
fi
if [[ ! "$FUNDAMENTALS_RATE_PER_MINUTE" =~ ^[1-9][0-9]*$ ]] || (( FUNDAMENTALS_RATE_PER_MINUTE > 150 )); then
  echo "FUNDAMENTALS_RATE_PER_MINUTE must be between 1 and 150." >&2
  exit 2
fi
if ! command -v "$FLOCK_BIN" >/dev/null 2>&1; then
  echo "flock command not found: $FLOCK_BIN" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] skipped reason=already_running lock=${LOCK_FILE}"
  exit 0
fi

json_field() {
  local field="$1"
  python3 -c 'import json,sys; value=json.load(sys.stdin); result=value.get(sys.argv[1]); print("" if result is None else result)' "$field"
}

LAST_JOB_ID=""
submit_and_wait() {
  local action="$1"
  local payload="$2"
  local job_response
  local job_status
  local deadline

  echo "POST /api/sync-jobs action=${action}"
  job_response="$(curl --fail --silent --show-error --max-time 30 \
    -H "Content-Type: application/json" \
    -X POST \
    --data "$payload" \
    "${API_BASE}/api/sync-jobs")"
  LAST_JOB_ID="$(printf '%s' "$job_response" | json_field id)"
  if [[ -z "$LAST_JOB_ID" ]]; then
    echo "sync job response did not contain id: ${job_response}" >&2
    exit 1
  fi

  deadline=$(( $(date +%s) + JOB_TIMEOUT_SECONDS ))
  while true; do
    job_response="$(curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/sync-jobs/${LAST_JOB_ID}")"
    job_status="$(printf '%s' "$job_response" | json_field status)"
    echo "SYNC action=${action} job_id=${LAST_JOB_ID} status=${job_status}"
    case "$job_status" in
      ok)
        break
        ;;
      partial|failed)
        echo "${action} sync job finished unsuccessfully: ${job_response}" >&2
        exit 1
        ;;
      queued|running)
        ;;
      *)
        echo "${action} sync job returned unknown status: ${job_status}" >&2
        exit 1
        ;;
    esac
    if (( $(date +%s) >= deadline )); then
      echo "${action} sync job timed out after ${JOB_TIMEOUT_SECONDS}s: ${LAST_JOB_ID}" >&2
      exit 1
    fi
    sleep "$JOB_POLL_SECONDS"
  done
}

calendar_payload=$(printf '{"action":"trade_calendar","payload":{"start_date":"%s","end_date":"%s","exchange":"SSE"}}' "$SYNC_DATE" "$SYNC_DATE")
submit_and_wait "trade_calendar" "$calendar_payload"
calendar_job_id="$LAST_JOB_ID"

calendar_response="$(curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/trade-calendars/${SYNC_DATE}")"
is_open="$(printf '%s' "$calendar_response" | json_field isOpen)"
if [[ "$is_open" != "True" && "$is_open" != "true" && "$is_open" != "1" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] skipped reason=non_trading_day date=${SYNC_DATE} calendar_job=${calendar_job_id}"
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start daily market sync date=${SYNC_DATE} api=${API_BASE}"

daily_payload=$(printf '{"action":"daily_market","payload":{"start_date":"%s","end_date":"%s","skip_existing":false,"min_existing_rows":%s,"benchmark":"%s"}}' "$SYNC_DATE" "$SYNC_DATE" "$MIN_EXISTING_ROWS" "$QUALITY_BENCHMARK")
submit_and_wait "daily_market" "$daily_payload"
daily_job_id="$LAST_JOB_ID"

fundamentals_payload=$(printf '{"action":"market_fundamentals","payload":{"start_date":"%s","end_date":"%s","max_stocks":%s,"rate_per_minute":%s,"skip_existing":false}}' "$FUNDAMENTALS_START_DATE" "$FUNDAMENTALS_END_DATE" "$FUNDAMENTALS_MAX_STOCKS" "$FUNDAMENTALS_RATE_PER_MINUTE")
submit_and_wait "market_fundamentals" "$fundamentals_payload"
fundamentals_job_id="$LAST_JOB_ID"

echo "REFRESH database overview after jobs=${daily_job_id},${fundamentals_job_id}"
curl --fail --silent --show-error --max-time 180 "${API_BASE}/api/db/overview?refresh=true"
echo

stocks_response="$(curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/stocks?limit=${QUALITY_UNIVERSE_LIMIT}")"
mkdir -p "$QUALITY_UNIVERSE_DIR"
safe_job_id="$(printf '%s' "$daily_job_id" | tr -c 'A-Za-z0-9_-' '_')"
universe_file="${QUALITY_UNIVERSE_DIR}/${SYNC_DATE}-${safe_job_id}.txt"
universe_temp="$(mktemp "${universe_file}.tmp.XXXXXX")"
trap 'rm -f "$universe_temp"' EXIT
printf '%s' "$stocks_response" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
limit=int(sys.argv[1])
universe=sorted({str(row.get("ts_code") or "").strip().upper() for row in rows if row.get("ts_code")})[:limit]
if not universe:
    raise SystemExit("latest-day quality universe is empty")
sys.stdout.write("".join(f"{code}\n" for code in universe))
' "$QUALITY_UNIVERSE_LIMIT" > "$universe_temp"
mv -f "$universe_temp" "$universe_file"
trap - EXIT

universe_container_path="${QUALITY_UNIVERSE_CONTAINER_ROOT}/$(basename "$universe_file")"
quality_payload="$(python3 -c '
import json,sys
host_path=sys.argv[1]
source_path=sys.argv[2]
sync_date=sys.argv[3]
benchmark=sys.argv[4]
with open(host_path, encoding="utf-8") as handle:
    universe=[line.strip().upper() for line in handle if line.strip()]
if not universe or universe != sorted(set(universe)):
    raise SystemExit("quality universe artifact must be non-empty, unique and sorted")
print(json.dumps({
    "scope":"a_share_cross_section",
    "start_date":sync_date,
    "end_date":sync_date,
    "universe":universe,
    "universe_type":"explicit_snapshot",
    "universe_source":source_path,
    "universe_as_of_date":sync_date,
    "benchmark":benchmark,
    "statement_timeout_ms":30000,
}, separators=(",", ":")))
' "$universe_file" "$universe_container_path" "$SYNC_DATE" "$QUALITY_BENCHMARK")"
echo "POST /api/data-quality/runs date=${SYNC_DATE} universe_limit=${QUALITY_UNIVERSE_LIMIT}"
quality_response="$(curl --fail --silent --show-error --max-time 180 \
  -H "Content-Type: application/json" \
  -X POST \
  --data "$quality_payload" \
  "${API_BASE}/api/data-quality/runs")"
quality_status="$(printf '%s' "$quality_response" | json_field status)"
echo "QUALITY status=${quality_status} response=${quality_response}"
case "$quality_status" in
  ready|ready_with_warnings)
    ;;
  blocked|failed)
    echo "latest-day quality check did not pass: ${quality_response}" >&2
    exit 1
    ;;
  *)
    echo "latest-day quality check returned unknown status: ${quality_status}" >&2
    exit 1
    ;;
esac

curl --fail --silent --show-error --max-time 30 "${API_BASE}/api/health?include_counts=false"
echo

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] finish daily market sync date=${SYNC_DATE} jobs=${calendar_job_id},${daily_job_id},${fundamentals_job_id} quality_universe=${universe_container_path}"
