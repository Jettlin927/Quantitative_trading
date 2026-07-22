#!/usr/bin/env bash
set -euo pipefail

export TZ="${TZ:-Asia/Shanghai}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
API_BASE="${API_BASE:-http://127.0.0.1:${API_PORT:-18000}}"
SYNC_END_DATE="${SYNC_END_DATE:-$(date +%F)}"
SYNC_START_DATE="${SYNC_START_DATE:-$(python3 -c 'from datetime import date,timedelta; print(date.today()-timedelta(days=10))')}"
BATCH_SIZE="${BATCH_SIZE:-20}"
BATCH_DELAY_SECONDS="${BATCH_DELAY_SECONDS:-5}"
RETRY_BASE_DELAY_SECONDS="${RETRY_BASE_DELAY_SECONDS:-15}"
SOURCE_CODES_FILE="${SOURCE_CODES_FILE:-}"
if [[ -n "$SOURCE_CODES_FILE" ]]; then
  VALIDATION_SAMPLE_SIZE="${VALIDATION_SAMPLE_SIZE:-0}"
  TARGET_UNIVERSE_ARGS=(--source-codes-file "$SOURCE_CODES_FILE")
else
  VALIDATION_SAMPLE_SIZE="${VALIDATION_SAMPLE_SIZE:-30}"
  TARGET_UNIVERSE_ARGS=()
fi
JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-7200}"
LOCK_FILE="${LOCK_FILE:-/tmp/quantitative-trading-us-experiment-sync.lock}"
CHECKPOINT="${CHECKPOINT:-${PROJECT_ROOT}/outputs/us-experiment-checkpoints/daily-${SYNC_END_DATE}.json}"
FLOCK_BIN="${FLOCK_BIN:-flock}"

if ! command -v "$FLOCK_BIN" >/dev/null 2>&1; then
  echo "flock command not found: $FLOCK_BIN" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] skipped reason=already_running lock=${LOCK_FILE}"
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start US experimental daily sync range=${SYNC_START_DATE}:${SYNC_END_DATE}"
if python3 "${SCRIPT_DIR}/backfill_us_experiment.py" \
  --api-base "$API_BASE" \
  --start-date "$SYNC_START_DATE" \
  --end-date "$SYNC_END_DATE" \
  --batch-size "$BATCH_SIZE" \
  --batch-delay-seconds "$BATCH_DELAY_SECONDS" \
  --retry-base-delay-seconds "$RETRY_BASE_DELAY_SECONDS" \
  --validation-sample-size "$VALIDATION_SAMPLE_SIZE" \
  --job-timeout-seconds "$JOB_TIMEOUT_SECONDS" \
  "${TARGET_UNIVERSE_ARGS[@]}" \
  --checkpoint "$CHECKPOINT"; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] finish status=ok US experimental daily sync"
else
  exit_code=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] finish status=failed exit_code=${exit_code} US experimental daily sync" >&2
  exit "$exit_code"
fi
