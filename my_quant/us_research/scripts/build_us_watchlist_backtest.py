from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from my_quant.us_research.scripts.refresh_us_snapshot import (
    fetch_yfinance_histories,
    normalize_watchlist_row,
    parse_float,
    read_csv_rows,
)


US_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = US_ROOT / "config" / "watchlist_symbols.csv"
DEFAULT_JSON = US_ROOT / "reports" / "latest_us_watchlist_backtest.json"
DEFAULT_CSV = US_ROOT / "reports" / "latest_us_watchlist_backtest.csv"
DEFAULT_HTML = US_ROOT / "reports" / "latest_us_watchlist_backtest.html"
BACKTEST_FIELDS = [
    "ticker",
    "name",
    "strategy",
    "status",
    "evidence_label",
    "bar_count",
    "start_date",
    "end_date",
    "total_return",
    "annual_return",
    "buy_hold_return",
    "max_drawdown",
    "trade_count",
    "exposure_pct",
    "stale_reason",
]


def _clean_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for row in bars:
        close = parse_float(row.get("close"))
        if close is None or close <= 0:
            continue
        clean.append({"date": str(row.get("date", "")), "close": close})
    clean = [row for row in clean if row["date"]]
    clean.sort(key=lambda row: row["date"])
    return clean


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _max_drawdown(nav: list[float]) -> float:
    peak = nav[0]
    worst = 0.0
    for value in nav:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
    return worst


def _rule_signal(closes: list[float], index: int) -> bool:
    if index < 200:
        return False
    close = closes[index]
    ma20 = _average(closes[index - 19 : index + 1])
    ma50 = _average(closes[index - 49 : index + 1])
    ma200 = _average(closes[index - 199 : index + 1])
    ret20 = close / closes[index - 20] - 1.0 if closes[index - 20] else 0.0
    high_252 = max(closes[max(0, index - 251) : index + 1])
    from_high = close / high_252 - 1.0 if high_252 else 0.0
    trend_ok = close > ma50 > ma200
    pullback_not_chase = close <= ma20 * 1.08 and ret20 <= 0.25 and from_high <= -0.02
    return bool(trend_ok and pullback_not_chase)


def backtest_trend_pullback_no_chase(bars: list[dict[str, Any]], cost_rate: float = 0.001) -> dict[str, Any]:
    clean = _clean_bars(bars)
    if len(clean) < 220:
        return {
            "strategy": "trend_pullback_no_chase",
            "status": "stale",
            "evidence_label": "只等回调",
            "bar_count": len(clean),
            "stale_reason": "need at least 220 daily bars",
        }

    closes = [float(row["close"]) for row in clean]
    nav = [1.0]
    position = 0.0
    trade_count = 0
    exposure_days = 0
    for index in range(len(closes) - 1):
        target = 1.0 if _rule_signal(closes, index) else 0.0
        turnover = abs(target - position)
        current_nav = nav[-1]
        if turnover > 0:
            current_nav *= 1 - turnover * cost_rate
            trade_count += 1
        position = target
        if position > 0:
            exposure_days += 1
        next_return = closes[index + 1] / closes[index] - 1.0
        nav.append(current_nav * (1 + position * next_return))

    total_return = nav[-1] / nav[0] - 1.0
    years = max((len(nav) - 1) / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1
    return {
        "strategy": "trend_pullback_no_chase",
        "status": "ok",
        "evidence_label": "只等回调",
        "bar_count": len(clean),
        "start_date": clean[0]["date"],
        "end_date": clean[-1]["date"],
        "total_return": total_return,
        "annual_return": annual_return,
        "buy_hold_return": closes[-1] / closes[0] - 1.0,
        "max_drawdown": _max_drawdown(nav),
        "trade_count": trade_count,
        "exposure_pct": exposure_days / max(len(closes) - 1, 1),
        "stale_reason": "",
    }


def run_watchlist_backtest(watchlist: list[dict[str, Any]], history_by_ticker: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for raw_item in watchlist:
        item = normalize_watchlist_row(raw_item)
        ticker = item["ticker"]
        if not ticker:
            continue
        history = history_by_ticker.get(ticker)
        if isinstance(history, BaseException):
            metrics = {
                "strategy": "trend_pullback_no_chase",
                "status": "stale",
                "evidence_label": "只等回调",
                "bar_count": 0,
                "stale_reason": f"{type(history).__name__}: {history}",
            }
        else:
            metrics = backtest_trend_pullback_no_chase(history or [])
        row = {"ticker": ticker, "name": item.get("name", "")}
        row.update(metrics)
        rows.append(row)
    return rows


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_backtest_outputs(rows: list[dict[str, Any]], json_path: Path, csv_path: Path, html_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(_json_safe({"status": "ok", "rows": rows}), ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BACKTEST_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in BACKTEST_FIELDS})
    html_path.write_text(build_backtest_html(rows), encoding="utf-8")


def _pct(value: Any) -> str:
    number = parse_float(value)
    if number is None:
        return "N/A"
    return f"{number * 100:.2f}%"


def build_backtest_html(rows: list[dict[str, Any]]) -> str:
    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(row.get('ticker', '')))}</code></td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{_pct(row.get('annual_return'))}</td>"
            f"<td>{_pct(row.get('buy_hold_return'))}</td>"
            f"<td>{_pct(row.get('max_drawdown'))}</td>"
            f"<td>{html.escape(str(row.get('trade_count', '')))}</td>"
            f"<td>{html.escape(str(row.get('evidence_label', '')))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>美股关注池规则回测</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>美股关注池规则回测</h1>
  <p>策略：trend_pullback_no_chase。规则证据来自 A 股 `002-no-chase-after-extended-gap`，仅作研究辅助，不是交易指令。</p>
  <table>
    <thead><tr><th>Ticker</th><th>状态</th><th>策略年化</th><th>买入持有</th><th>最大回撤</th><th>交易数</th><th>规则证据</th></tr></thead>
    <tbody>{''.join(table_rows)}</tbody>
  </table>
</body>
</html>
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build US watchlist rule backtest from yfinance histories.")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--sleep", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    watchlist = [normalize_watchlist_row(row) for row in read_csv_rows(args.watchlist)]
    histories = fetch_yfinance_histories(watchlist, period=args.period, interval=args.interval, sleep_seconds=args.sleep)
    rows = run_watchlist_backtest(watchlist, histories)
    write_backtest_outputs(rows, json_path=args.json, csv_path=args.csv, html_path=args.html)
    print(f"wrote {args.json}")
    print(f"wrote {args.csv}")
    print(f"wrote {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
