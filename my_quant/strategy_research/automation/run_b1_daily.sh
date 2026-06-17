#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${B1_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
ENV_FILE="${REPO_ROOT}/.env.local"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Python not found or not executable: ${PYTHON_BIN}" >&2
  exit 66
fi

if [[ -z "${TUSHARE_TOKEN:-}" ]]; then
  echo "[ERROR] TUSHARE_TOKEN is not set. Add it to ${ENV_FILE} or export it before running." >&2
  exit 64
fi

RUN_DATE="${B1_RUN_DATE:-$(date +%Y%m%d)}"
END_DATE="${B1_END_DATE:-$(date +%F)}"
LOG_DIR="${REPO_ROOT}/my_quant/strategy_research/logs"
REPORT_PREMARKET_DIR="${REPO_ROOT}/my_quant/strategy_research/web_report/premarket"
LATEST_HTML="${REPO_ROOT}/my_quant/strategy_research/web_report/b1_premarket_plan_latest.html"
OUTPUT_PREFIX="b1_premarket_plan_${RUN_DATE}"
HTML_PATH="${REPORT_PREMARKET_DIR}/b1_premarket_plan_${RUN_DATE}.html"

mkdir -p "${LOG_DIR}" "${REPORT_PREMARKET_DIR}"

echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') B1 premarket plan run start"
echo "[INFO] repo=${REPO_ROOT}"
echo "[INFO] end_date=${END_DATE}"
echo "[INFO] output_prefix=${OUTPUT_PREFIX}"

cd "${REPO_ROOT}"
"${PYTHON_BIN}" -m my_quant.strategy_research.web_report.build_b1_premarket_plan \
  --end "${END_DATE}" \
  --output-prefix "${OUTPUT_PREFIX}" \
  --html-path "${HTML_PATH}"

cp "${HTML_PATH}" "${LATEST_HTML}"
echo "$(date '+%Y-%m-%d %H:%M:%S') ${HTML_PATH}" >> "${LOG_DIR}/b1_premarket_runs.log"
echo "[INFO] latest_html=${LATEST_HTML}"
echo "[INFO] dated_html=${HTML_PATH}"
echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') B1 premarket plan run complete"
