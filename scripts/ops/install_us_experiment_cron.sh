#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/quantitative-trading}"
API_BASE="${API_BASE:-http://127.0.0.1:18000}"
LOG_DIR="${LOG_DIR:-$HOME/quantitative-trading-logs}"
SOURCE_CODES_FILE="${SOURCE_CODES_FILE:-}"
MARKER_BEGIN="# BEGIN quant-us-experiment-daily-sync"
MARKER_END="# END quant-us-experiment-daily-sync"

if [[ "$PROJECT_DIR" == *" "* || "$LOG_DIR" == *" "* || "$API_BASE" == *" "* || "$SOURCE_CODES_FILE" == *" "* ]]; then
  echo "PROJECT_DIR, LOG_DIR, API_BASE and SOURCE_CODES_FILE must not contain spaces." >&2
  exit 2
fi

existing_cron="$(crontab -l 2>/dev/null || true)"
kept_cron="$(printf '%s\n' "$existing_cron" | sed "/${MARKER_BEGIN}/,/${MARKER_END}/d")"
source_codes_env=""
if [[ -n "$SOURCE_CODES_FILE" ]]; then
  source_codes_env=" SOURCE_CODES_FILE=${SOURCE_CODES_FILE}"
fi
job_line="0 10 * * * cd ${PROJECT_DIR} && mkdir -p ${LOG_DIR} && TZ=Asia/Shanghai API_BASE=${API_BASE}${source_codes_env} ./scripts/ops/sync_us_experiment_daily.sh >> ${LOG_DIR}/us_experiment_daily_sync.log 2>&1"

{
  printf '%s\n' "$kept_cron" | sed '/^[[:space:]]*$/d'
  printf '%s\n' "$MARKER_BEGIN"
  printf '%s\n' "CRON_TZ=Asia/Shanghai"
  printf '%s\n' "$job_line"
  printf '%s\n' "$MARKER_END"
} | crontab -

crontab -l | sed -n "/${MARKER_BEGIN}/,/${MARKER_END}/p"
