from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .kronos_forecast_slope import evaluate_kronos_slope_signal


@dataclass(frozen=True)
class KronosPredictionArchive:
    path: Path
    symbol: str
    generated_at: str
    source_file: str
    last_close: float
    predicted: pd.DataFrame
    actual: pd.DataFrame


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _symbol_from_file_path(file_path: str) -> str:
    name = Path(file_path).name
    for suffix in ("_USDT-5m-futures.feather", "-5m-futures.feather", ".feather", ".csv"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = name.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return name


def _frame_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    clean = []
    for row in rows:
        close = _as_float(row.get("close"))
        timestamp = str(row.get("timestamp", "") or row.get("date", ""))
        if close is None or close <= 0 or not timestamp:
            continue
        clean.append({"timestamp": timestamp, "close": close})
    return pd.DataFrame(clean)


def load_prediction_archive(path: Path) -> KronosPredictionArchive:
    payload = json.loads(path.read_text(encoding="utf-8"))
    last_close = _as_float(payload.get("input_data_summary", {}).get("last_values", {}).get("close"))
    if last_close is None or last_close <= 0:
        raise ValueError(f"{path} missing positive input last close")
    predicted = _frame_from_rows(list(payload.get("prediction_results", [])))
    actual = _frame_from_rows(list(payload.get("actual_data", [])))
    if len(predicted) < 2 or len(actual) < 2:
        raise ValueError(f"{path} does not contain enough prediction/actual rows")
    return KronosPredictionArchive(
        path=path,
        symbol=_symbol_from_file_path(str(payload.get("file_path", ""))),
        generated_at=str(payload.get("timestamp", "")),
        source_file=str(payload.get("file_path", "")),
        last_close=last_close,
        predicted=predicted,
        actual=actual,
    )


def run_archive_backtest(archive: KronosPredictionArchive, cost_rate: float = 0.001) -> dict[str, Any]:
    stats = pd.DataFrame({"median": archive.predicted["close"]})
    signal = evaluate_kronos_slope_signal(stats, last_close=archive.last_close)
    horizon_pos = min(signal.horizon_days, len(archive.actual)) - 1
    entry_close = float(archive.actual["close"].iloc[0])
    exit_close = float(archive.actual["close"].iloc[horizon_pos])
    actual_return = exit_close / entry_close - 1.0
    if signal.action == "buy":
        direction_hit = actual_return > 0
        long_only_return = actual_return - cost_rate
    elif signal.action == "sell":
        direction_hit = actual_return < 0
        long_only_return = 0.0
    else:
        direction_hit = abs(actual_return) < 0.005
        long_only_return = 0.0
    return {
        "file": str(archive.path),
        "symbol": archive.symbol,
        "generated_at": archive.generated_at,
        "source_file": archive.source_file,
        "action": signal.action,
        "reason": signal.reason,
        "daily_log_slope": signal.daily_log_slope,
        "forecast_horizon_return": signal.horizon_return,
        "actual_horizon_return": actual_return,
        "direction_hit": bool(direction_hit),
        "long_only_return": long_only_return,
        "horizon_rows": horizon_pos + 1,
        "prediction_rows": int(len(archive.predicted)),
        "actual_rows": int(len(archive.actual)),
    }


def summarize_archive_backtest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prediction_count = len(rows)
    if prediction_count == 0:
        return {
            "prediction_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "hold_count": 0,
            "direction_hit_rate": None,
            "long_only_total_return": 0.0,
            "mean_actual_horizon_return": None,
        }
    long_only_total = 1.0
    for row in rows:
        long_only_total *= 1.0 + float(row.get("long_only_return") or 0.0)
    return {
        "prediction_count": prediction_count,
        "buy_count": sum(1 for row in rows if row.get("action") == "buy"),
        "sell_count": sum(1 for row in rows if row.get("action") == "sell"),
        "hold_count": sum(1 for row in rows if row.get("action") == "hold"),
        "direction_hit_rate": sum(1 for row in rows if row.get("direction_hit")) / prediction_count,
        "long_only_total_return": long_only_total - 1.0,
        "mean_actual_horizon_return": sum(float(row.get("actual_horizon_return") or 0.0) for row in rows) / prediction_count,
    }


def run_prediction_archive_dir(prediction_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for path in sorted(prediction_dir.glob("*.json")):
        try:
            archive = load_prediction_archive(path)
            rows.append(run_archive_backtest(archive))
        except Exception as error:  # noqa: BLE001 - preserve bad archive as evidence instead of aborting the run.
            rows.append({"file": str(path), "symbol": "", "action": "error", "error": f"{type(error).__name__}: {error}"})
    valid_rows = [row for row in rows if row.get("action") in {"buy", "sell", "hold"}]
    summary = summarize_archive_backtest(valid_rows)
    summary["archive_count"] = len(rows)
    summary["error_count"] = len(rows) - len(valid_rows)
    return rows, summary


def _pct(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "N/A"
    return f"{number * 100:.2f}%"


def build_archive_report_html(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(row.get('action', '')))}</td>"
            f"<td>{_pct(row.get('forecast_horizon_return'))}</td>"
            f"<td>{_pct(row.get('actual_horizon_return'))}</td>"
            f"<td>{html.escape(str(row.get('direction_hit', '')))}</td>"
            f"<td>{_pct(row.get('long_only_return'))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Kronos 归档预测斜率回测</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #d5d8dc; padding: 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .note {{ background: #f8f9f9; border-left: 4px solid #566573; padding: 12px; }}
  </style>
</head>
<body>
  <h1>Kronos 归档预测斜率回测</h1>
  <div class="note">本报告使用外部 Kronos webui 已落盘预测 JSON，与对应 actual_data 对比验证 `12_kronos_forecast_slope` 信号。样本是加密期货 5 分钟数据，不等价于 HK 股票或 A 股策略验证。</div>
  <p>预测归档数：{summary.get('archive_count', 0)}；有效预测数：{summary.get('prediction_count', 0)}；方向命中率：{_pct(summary.get('direction_hit_rate'))}；long-only 复利收益：{_pct(summary.get('long_only_total_return'))}。</p>
  <table>
    <thead><tr><th>Symbol</th><th>Action</th><th>预测收益</th><th>实际收益</th><th>方向命中</th><th>long-only 收益</th></tr></thead>
    <tbody>{''.join(table_rows)}</tbody>
  </table>
</body>
</html>
"""


def write_archive_outputs(output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_csv = output_dir / "rows.csv"
    summary_json = output_dir / "summary.json"
    report_html = output_dir / "index.html"
    pd.DataFrame(rows).to_csv(rows_csv, index=False)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_html.write_text(build_archive_report_html(rows, summary), encoding="utf-8")
    return {"rows_csv": str(rows_csv), "summary_json": str(summary_json), "report_html": str(report_html)}
