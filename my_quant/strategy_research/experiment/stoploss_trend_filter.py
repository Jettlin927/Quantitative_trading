from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .backtest import interval_rebalance_dates, run_config
from .config import COST_RATE, DEFENSE_ASSET, EVAL_END, EVAL_START, RESULTS_DIR, StrategyConfig
from .data import load_prices
from .metrics import calculate_metrics
from .strategies import make_ram_topn, normalize_weights


@dataclass(frozen=True)
class StoplossReportPaths:
    output_dir: Path
    summary_csv: Path
    nav_csv: Path
    report_html: Path


def run_stoploss_overlay_nav(
    prices: pd.DataFrame,
    eval_start: str,
    eval_end: str,
    rebalance_dates: set[pd.Timestamp],
    make_weights,
    cost_rate: float,
    stop_loss_pct: float,
    defense_asset: str = DEFENSE_ASSET,
) -> tuple[pd.Series, pd.DataFrame, dict[str, float]]:
    eval_prices = prices.loc[eval_start:eval_end]
    if eval_prices.empty:
        raise ValueError(f"No prices in evaluation range {eval_start} to {eval_end}")

    returns = prices.pct_change().fillna(0.0)
    columns = list(prices.columns)
    nav = pd.Series(index=eval_prices.index, dtype=float)
    weights_history = pd.DataFrame(index=eval_prices.index, columns=columns, dtype=float)
    nav.iloc[0] = 1.0
    current_weights = pd.Series(0.0, index=columns)
    entry_prices: dict[str, float] = {}
    total_turnover = 0.0
    rebalance_count = 0
    stop_count = 0

    for i, date in enumerate(eval_prices.index):
        if date in rebalance_dates or i == 0:
            target_weights = normalize_weights(make_weights(date), columns)
            turnover = float((target_weights - current_weights).abs().sum())
            if turnover > 1e-12:
                nav.iloc[i] = nav.iloc[i] * (1 - turnover * cost_rate)
                total_turnover += turnover
                rebalance_count += 1
            current_weights = target_weights
            entry_prices = {
                symbol: float(prices.loc[date, symbol])
                for symbol, weight in current_weights.items()
                if weight > 0 and symbol != defense_asset
            }
        else:
            stopped_symbols = []
            for symbol, weight in current_weights.items():
                if symbol == defense_asset or weight <= 0:
                    continue
                entry_price = entry_prices.get(symbol)
                current_price = float(prices.loc[date, symbol])
                if entry_price and current_price <= entry_price * (1 - stop_loss_pct):
                    stopped_symbols.append(symbol)
            if stopped_symbols:
                target_weights = current_weights.copy()
                stopped_weight = float(target_weights.loc[stopped_symbols].sum())
                target_weights.loc[stopped_symbols] = 0.0
                target_weights.loc[defense_asset] = float(target_weights.get(defense_asset, 0.0)) + stopped_weight
                turnover = float((target_weights - current_weights).abs().sum())
                if turnover > 1e-12:
                    nav.iloc[i] = nav.iloc[i] * (1 - turnover * cost_rate)
                    total_turnover += turnover
                current_weights = target_weights
                for symbol in stopped_symbols:
                    entry_prices.pop(symbol, None)
                stop_count += len(stopped_symbols)

        weights_history.loc[date] = current_weights
        if i + 1 < len(eval_prices.index):
            next_date = eval_prices.index[i + 1]
            daily_return = float((current_weights * returns.loc[next_date, columns]).sum())
            nav.iloc[i + 1] = nav.iloc[i] * (1 + daily_return)

    stats = {
        "total_turnover": total_turnover,
        "rebalance_count": float(rebalance_count),
        "estimated_cost": total_turnover * cost_rate,
        "stop_count": float(stop_count),
    }
    return nav, weights_history, stats


def run_stoploss_config(
    prices: pd.DataFrame,
    stop_loss_pct: float,
    eval_start: str = EVAL_START,
    eval_end: str = EVAL_END,
) -> tuple[dict[str, Any], pd.Series]:
    eval_index = prices.loc[eval_start:eval_end].index
    nav, _weights, run_stats = run_stoploss_overlay_nav(
        prices=prices,
        eval_start=eval_start,
        eval_end=eval_end,
        rebalance_dates=interval_rebalance_dates(eval_index, 21),
        make_weights=make_ram_topn(prices, top_n=2, momentum_window=60, volatility_window=60),
        cost_rate=COST_RATE,
        stop_loss_pct=stop_loss_pct,
    )
    name = f"ram_top2_m60_v60_monthly_stop{int(stop_loss_pct * 100):02d}_cost"
    row: dict[str, Any] = {"strategy": name, "stop_loss_pct": stop_loss_pct, "kind": "ram_topn_stoploss"}
    row.update(calculate_metrics(nav))
    row.update(run_stats)
    return row, nav.rename(name)


def build_stoploss_trend_filter_results(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = StrategyConfig(
        "ram_top2_m60_v60_monthly_cost",
        "ram_topn",
        top_n=2,
        momentum_window=60,
        volatility_window=60,
        interval_days=21,
        cost_rate=COST_RATE,
    )
    trend = StrategyConfig(
        "ram_top2_m60_v60_monthly_trend120_cost",
        "ram_topn_trend_filter",
        top_n=2,
        momentum_window=60,
        volatility_window=60,
        interval_days=21,
        cost_rate=COST_RATE,
    )
    rows: list[dict[str, Any]] = [run_config(prices, baseline, EVAL_START, EVAL_END), run_config(prices, trend, EVAL_START, EVAL_END)]
    nav_columns = []
    for stop_loss_pct in (0.05, 0.10, 0.15):
        row, nav = run_stoploss_config(prices, stop_loss_pct)
        rows.append(row)
        nav_columns.append(nav)
    summary = pd.DataFrame(rows)
    nav_frame = pd.concat(nav_columns, axis=1)
    return summary, nav_frame


def build_stoploss_report_html(summary: pd.DataFrame) -> str:
    rows = []
    for row in summary.to_dict(orient="records"):
        stop_count = row.get("stop_count")
        stop_count_text = "n/a" if pd.isna(stop_count) else f"{float(stop_count):.0f}"
        rows.append(
            "<tr>"
            f"<td><code>{row['strategy']}</code></td>"
            f"<td>{float(row['annual_return']) * 100:.2f}%</td>"
            f"<td>{float(row['max_drawdown']) * 100:.2f}%</td>"
            f"<td>{float(row['calmar']):.2f}</td>"
            f"<td>{float(row.get('estimated_cost', 0.0)) * 100:.2f}%</td>"
            f"<td>{stop_count_text}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>05 止损与趋势过滤独立验证</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d5d8dc; padding: 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .note {{ background: #f8f9f9; border-left: 4px solid #566573; padding: 12px; }}
  </style>
</head>
<body>
  <h1>05 止损与趋势过滤独立验证</h1>
  <div class="note">本报告补齐原策略目录 `05_stoploss_trend_filter` 的止损三档证据。它是 ETF 研究回测，不构成真实交易建议。</div>
  <p>数据来源：AkShare ETF 缓存；窗口：{EVAL_START} 到 {EVAL_END}；策略基准：RAM Top2 月度切换，单边成本 {COST_RATE:.3%}。</p>
  <table>
    <thead><tr><th>策略</th><th>年化收益</th><th>最大回撤</th><th>Calmar</th><th>估算成本</th><th>止损次数</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""


def write_stoploss_report(output_dir: Path = RESULTS_DIR / "stoploss_trend_filter_20260626") -> StoplossReportPaths:
    prices = load_prices(repair_splits=True)
    summary, nav = build_stoploss_trend_filter_results(prices)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = output_dir / "summary.csv"
    nav_csv = output_dir / "nav.csv"
    report_html = output_dir / "index.html"
    summary.to_csv(summary_csv, index=False)
    nav.to_csv(nav_csv, index_label="date")
    report_html.write_text(build_stoploss_report_html(summary), encoding="utf-8")
    return StoplossReportPaths(output_dir=output_dir, summary_csv=summary_csv, nav_csv=nav_csv, report_html=report_html)
