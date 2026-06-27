from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    build_b1_panels,
    run_b1_backtest,
    write_b1_artifacts,
)
from my_quant.strategy_research.experiment.config import COST_RATE, DATA_DIR, RESULTS_DIR
from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame
from my_quant.strategy_research.run_b1_walk_forward import load_symbols_from_csv_file


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "web_report"
DEFAULT_SYMBOLS_FILE = RESULTS_DIR / "b1_tushare_active_20241231_top300_universe.csv"
DEFAULT_OUTPUT_PREFIX = "b1_tushare_quality_gate_top300"
DEFAULT_HTML_PATH = REPORT_DIR / "b1_quality_strategy.html"
DISPLAY_CAPITAL = 1_000_000.0


@dataclass
class ReportData:
    nav: pd.DataFrame
    trades: pd.DataFrame
    round_trips: pd.DataFrame
    summary: dict[str, float | bool | str]
    symbol_names: dict[str, str]
    symbol_count: int
    artifacts: dict[str, str]


def build_report_manifest_payload(
    strategy: str,
    html_path: Path,
    display_capital: float,
    artifacts: dict[str, str],
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "html": str(html_path),
        "display_capital": display_capital,
        "artifacts": {key: Path(path).name for key, path in artifacts.items()},
    }


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _format_date(value: object) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _escape(value: object) -> str:
    if pd.isna(value):
        return ""
    return html.escape(str(value))


def _pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _money(value: float | int | None, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):,.{digits}f}"


def _axis_money(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if abs_value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _read_symbol_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = pd.read_csv(path, dtype=str)
    if "symbol" not in raw.columns or "name" not in raw.columns:
        return {}
    names: dict[str, str] = {}
    for _, row in raw.iterrows():
        symbol = str(row["symbol"]).split(".")[0].zfill(6)
        names[symbol] = str(row["name"])
    return names


def _final_prices_from_panels(panels: dict[str, pd.DataFrame], final_date: pd.Timestamp) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, frame in panels.items():
        if final_date in frame.index and pd.notna(frame.loc[final_date, "close"]):
            prices[symbol] = float(frame.loc[final_date, "close"])
    return prices


def _round_trip_row(lot: dict[str, object], status: str, display_capital: float) -> dict[str, object]:
    buy_value = float(lot["buy_value"])
    sell_value = float(lot["sell_value"])
    buy_cost = float(lot["buy_cost"])
    sell_cost = float(lot["sell_cost"])
    net_pnl = sell_value - buy_value - buy_cost - sell_cost
    sell_shares = float(lot["sell_shares"])
    buy_shares = float(lot["buy_shares"])
    sell_date = pd.Timestamp(lot["sell_date"])
    buy_date = pd.Timestamp(lot["buy_date"])
    return {
        "trade_id": int(lot["trade_id"]),
        "status": status,
        "symbol": str(lot["symbol"]),
        "buy_date": buy_date,
        "sell_date": sell_date,
        "holding_days": int((sell_date - buy_date).days),
        "buy_shares": buy_shares,
        "sell_shares": sell_shares,
        "sell_count": int(lot["sell_count"]),
        "buy_price": float(lot["buy_price"]),
        "average_sell_price": sell_value / sell_shares if sell_shares else 0.0,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "gross_pnl": sell_value - buy_value,
        "cost": buy_cost + sell_cost,
        "net_pnl": net_pnl,
        "net_pnl_display": net_pnl * display_capital,
        "net_return_pct": net_pnl / (buy_value + buy_cost) if buy_value + buy_cost else 0.0,
        "exit_reasons": " / ".join(str(reason) for reason in lot["exit_reasons"]),
    }


def build_round_trips(
    trades: pd.DataFrame,
    cost_rate: float = COST_RATE,
    display_capital: float = DISPLAY_CAPITAL,
    end_prices: dict[str, float] | None = None,
    end_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Pair B1 buy/sell rows into per-entry trade outcomes.

    The B1 engine records raw buy and sell executions. This helper groups every
    sell that belongs to the same entry lot, so partial exits become one
    economic trade for P&L distribution analysis.
    """

    columns = [
        "trade_id",
        "status",
        "symbol",
        "buy_date",
        "sell_date",
        "holding_days",
        "buy_shares",
        "sell_shares",
        "sell_count",
        "buy_price",
        "average_sell_price",
        "buy_value",
        "sell_value",
        "gross_pnl",
        "cost",
        "net_pnl",
        "net_pnl_display",
        "net_return_pct",
        "exit_reasons",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    open_lots: dict[str, list[dict[str, object]]] = {}
    rows: list[dict[str, object]] = []
    trade_id = 0
    ordered = trades.copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    ordered = ordered.sort_values(["date", "side"]).reset_index(drop=True)
    for _, trade in ordered.iterrows():
        symbol = str(trade["symbol"]).split(".")[0].zfill(6)
        side = str(trade["side"])
        shares = float(trade["shares"])
        price = float(trade["price"])
        date = pd.Timestamp(trade["date"])
        if shares <= 0 or price <= 0:
            continue
        if side == "buy":
            trade_id += 1
            value = shares * price
            open_lots.setdefault(symbol, []).append(
                {
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "buy_date": date,
                    "buy_price": price,
                    "buy_shares": shares,
                    "remaining_shares": shares,
                    "buy_value": value,
                    "buy_cost": value * cost_rate,
                    "sell_value": 0.0,
                    "sell_cost": 0.0,
                    "sell_shares": 0.0,
                    "sell_count": 0,
                    "sell_date": date,
                    "exit_reasons": [],
                }
            )
            continue
        if side != "sell":
            continue

        shares_to_match = shares
        lots = open_lots.get(symbol, [])
        while shares_to_match > 1e-12 and lots:
            lot = lots[0]
            remaining = float(lot["remaining_shares"])
            matched = min(remaining, shares_to_match)
            sell_value = matched * price
            lot["remaining_shares"] = remaining - matched
            lot["sell_value"] = float(lot["sell_value"]) + sell_value
            lot["sell_cost"] = float(lot["sell_cost"]) + sell_value * cost_rate
            lot["sell_shares"] = float(lot["sell_shares"]) + matched
            lot["sell_count"] = int(lot["sell_count"]) + 1
            lot["sell_date"] = date
            reasons = lot["exit_reasons"]
            if isinstance(reasons, list):
                reason = str(trade.get("reason", "")).strip()
                if reason and reason not in reasons:
                    reasons.append(reason)
            shares_to_match -= matched
            if float(lot["remaining_shares"]) <= 1e-10:
                rows.append(_round_trip_row(lot, "closed", display_capital))
                lots.pop(0)
        if not lots and symbol in open_lots:
            open_lots.pop(symbol, None)

    if end_prices and end_date is not None:
        for symbol, lots in open_lots.items():
            if symbol not in end_prices:
                continue
            end_price = float(end_prices[symbol])
            for lot in lots:
                remaining = float(lot["remaining_shares"])
                if remaining <= 1e-10:
                    continue
                lot = lot.copy()
                sell_value = remaining * end_price
                lot["sell_value"] = float(lot["sell_value"]) + sell_value
                lot["sell_cost"] = float(lot["sell_cost"]) + sell_value * cost_rate
                lot["sell_shares"] = float(lot["sell_shares"]) + remaining
                lot["sell_count"] = int(lot["sell_count"]) + 1
                lot["sell_date"] = end_date
                reasons = lot["exit_reasons"]
                if isinstance(reasons, list) and "open_mark_to_market" not in reasons:
                    reasons.append("open_mark_to_market")
                rows.append(_round_trip_row(lot, "open_marked", display_capital))

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["buy_date", "trade_id"]).reset_index(drop=True)


def _line_points(values: Iterable[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in values)


def svg_nav_curve(nav: pd.DataFrame, width: int = 1120, height: int = 390) -> str:
    if nav.empty:
        return "<svg viewBox='0 0 1120 390' role='img'><text x='20' y='40'>No NAV data</text></svg>"
    frame = nav.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    values = frame["nav"].astype(float).tolist()
    dates = frame["date"].tolist()
    margin = {"left": 68, "right": 24, "top": 26, "bottom": 48}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    y_min = min(min(values), 1.0) * 0.98
    y_max = max(max(values), 1.0) * 1.02
    if abs(y_max - y_min) < 1e-9:
        y_max += 0.1
        y_min -= 0.1

    def x_at(index: int) -> float:
        if len(values) == 1:
            return margin["left"] + plot_w / 2
        return margin["left"] + plot_w * index / (len(values) - 1)

    def y_at(value: float) -> float:
        return margin["top"] + (y_max - value) / (y_max - y_min) * plot_h

    line = _line_points((x_at(i), y_at(value)) for i, value in enumerate(values))
    base_y = y_at(1.0)
    area = f"{margin['left']},{base_y:.1f} {line} {margin['left'] + plot_w},{base_y:.1f}"
    y_ticks = [y_min + (y_max - y_min) * i / 4 for i in range(5)]
    date_tick_indices = sorted(set(round((len(values) - 1) * i / 5) for i in range(6)))
    grid = []
    for tick in y_ticks:
        y = y_at(tick)
        grid.append(
            f"<line x1='{margin['left']}' y1='{y:.1f}' x2='{margin['left'] + plot_w}' y2='{y:.1f}' stroke='#e4e9f0'/>"
            f"<text x='{margin['left'] - 10}' y='{y + 4:.1f}' text-anchor='end' fill='#66727f' font-size='12'>{tick:.2f}</text>"
        )
    for index in date_tick_indices:
        x = x_at(index)
        label = dates[index].strftime("%Y-%m")
        grid.append(
            f"<line x1='{x:.1f}' y1='{margin['top']}' x2='{x:.1f}' y2='{margin['top'] + plot_h}' stroke='#eef2f6'/>"
            f"<text x='{x:.1f}' y='{height - 18}' text-anchor='middle' fill='#66727f' font-size='12'>{label}</text>"
        )
    final_label = f"{values[-1]:.2f}x"
    final_x = x_at(len(values) - 1)
    final_y = y_at(values[-1])
    return f"""
<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" aria-label="B1 策略净值曲线">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>
  {''.join(grid)}
  <line x1="{margin['left']}" y1="{base_y:.1f}" x2="{margin['left'] + plot_w}" y2="{base_y:.1f}" stroke="#99a6b2" stroke-dasharray="4 5"/>
  <polygon points="{area}" fill="#dcefea" opacity="0.75"/>
  <polyline points="{line}" fill="none" stroke="#c0392b" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{final_x:.1f}" cy="{final_y:.1f}" r="4.5" fill="#c0392b"/>
  <text x="{final_x - 10:.1f}" y="{final_y - 12:.1f}" text-anchor="end" fill="#9f2f24" font-size="13" font-weight="700">{final_label}</text>
</svg>"""


def svg_sorted_pnl_bars(values: list[float], width: int = 1120, height: int = 330) -> str:
    if not values:
        return "<svg viewBox='0 0 1120 330' role='img'><text x='20' y='40'>No trade P&L data</text></svg>"
    ordered = sorted(float(value) for value in values)
    margin = {"left": 72, "right": 24, "top": 24, "bottom": 44}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    max_abs = max(abs(min(ordered)), abs(max(ordered)), 1.0)
    y_min = -max_abs * 1.08
    y_max = max_abs * 1.08

    def x_at(index: int) -> float:
        return margin["left"] + plot_w * index / max(1, len(ordered))

    def y_at(value: float) -> float:
        return margin["top"] + (y_max - value) / (y_max - y_min) * plot_h

    zero_y = y_at(0.0)
    bar_w = max(1.8, plot_w / len(ordered) * 0.86)
    bars = []
    for index, value in enumerate(ordered):
        x = x_at(index)
        y = y_at(max(value, 0.0))
        height_value = abs(zero_y - y_at(value))
        fill = "#c0392b" if value >= 0 else "#16865f"
        label = _money(value)
        bars.append(
            f"<rect x='{x:.1f}' y='{min(y, zero_y):.1f}' width='{bar_w:.1f}' height='{height_value:.1f}' fill='{fill}' opacity='0.82'>"
            f"<title>{label} 元</title></rect>"
        )
    y_ticks = [-max_abs, -max_abs / 2, 0.0, max_abs / 2, max_abs]
    grid = []
    for tick in y_ticks:
        y = y_at(tick)
        grid.append(
            f"<line x1='{margin['left']}' y1='{y:.1f}' x2='{margin['left'] + plot_w}' y2='{y:.1f}' stroke='#e4e9f0'/>"
            f"<text x='{margin['left'] - 10}' y='{y + 4:.1f}' text-anchor='end' fill='#66727f' font-size='12'>{_axis_money(tick)}</text>"
        )
    return f"""
<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" aria-label="每笔交易盈亏金额分布">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>
  {''.join(grid)}
  {''.join(bars)}
  <line x1="{margin['left']}" y1="{zero_y:.1f}" x2="{margin['left'] + plot_w}" y2="{zero_y:.1f}" stroke="#34495e" stroke-width="1.2"/>
  <text x="{margin['left']}" y="{height - 15}" fill="#66727f" font-size="12">按闭环交易净盈亏从低到高排序；红色为盈利，绿色为亏损</text>
</svg>"""


def svg_pct_histogram(values: list[float], bins: int = 24, width: int = 1120, height: int = 330) -> str:
    if not values:
        return "<svg viewBox='0 0 1120 330' role='img'><text x='20' y='40'>No return data</text></svg>"
    clean = [float(value) for value in values]
    max_abs = max(abs(min(clean)), abs(max(clean)), 0.01)
    lower = -max_abs * 1.05
    upper = max_abs * 1.05
    step = (upper - lower) / bins
    counts = [0 for _ in range(bins)]
    for value in clean:
        index = int((value - lower) / step)
        index = min(max(index, 0), bins - 1)
        counts[index] += 1
    margin = {"left": 54, "right": 24, "top": 24, "bottom": 54}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    max_count = max(max(counts), 1)

    def y_at(count: int | float) -> float:
        return margin["top"] + (max_count - count) / max_count * plot_h

    zero_index = (0 - lower) / (upper - lower)
    zero_x = margin["left"] + plot_w * zero_index
    bar_w = plot_w / bins * 0.84
    bars = []
    for index, count in enumerate(counts):
        x = margin["left"] + plot_w * index / bins + (plot_w / bins - bar_w) / 2
        y = y_at(count)
        h = margin["top"] + plot_h - y
        left = lower + index * step
        right = left + step
        fill = "#c0392b" if (left + right) / 2 >= 0 else "#16865f"
        bars.append(
            f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{h:.1f}' fill='{fill}' opacity='0.82'>"
            f"<title>{_pct(left, 1)} 到 {_pct(right, 1)}: {count} 笔</title></rect>"
        )
    y_ticks = [0, round(max_count / 2), max_count]
    grid = []
    for tick in sorted(set(y_ticks)):
        y = y_at(tick)
        grid.append(
            f"<line x1='{margin['left']}' y1='{y:.1f}' x2='{margin['left'] + plot_w}' y2='{y:.1f}' stroke='#e4e9f0'/>"
            f"<text x='{margin['left'] - 9}' y='{y + 4:.1f}' text-anchor='end' fill='#66727f' font-size='12'>{tick}</text>"
        )
    x_labels = []
    for tick in [-max_abs, -max_abs / 2, 0.0, max_abs / 2, max_abs]:
        x = margin["left"] + plot_w * (tick - lower) / (upper - lower)
        x_labels.append(
            f"<line x1='{x:.1f}' y1='{margin['top'] + plot_h}' x2='{x:.1f}' y2='{margin['top'] + plot_h + 5}' stroke='#99a6b2'/>"
            f"<text x='{x:.1f}' y='{height - 20}' text-anchor='middle' fill='#66727f' font-size='12'>{_pct(tick, 0)}</text>"
        )
    return f"""
<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" aria-label="每笔交易盈亏比例分布">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>
  {''.join(grid)}
  {''.join(bars)}
  <line x1="{zero_x:.1f}" y1="{margin['top']}" x2="{zero_x:.1f}" y2="{margin['top'] + plot_h}" stroke="#34495e" stroke-width="1.2"/>
  {''.join(x_labels)}
  <text x="{margin['left']}" y="{height - 8}" fill="#66727f" font-size="12">按闭环交易净收益率分桶；红色为盈利，绿色为亏损</text>
</svg>"""


def _metrics_grid(summary: dict[str, float | bool | str], round_trips: pd.DataFrame, nav: pd.DataFrame) -> str:
    closed = round_trips[round_trips["status"] == "closed"] if not round_trips.empty else pd.DataFrame()
    wins = int((closed["net_pnl"] > 0).sum()) if not closed.empty else 0
    win_rate = wins / len(closed) if len(closed) else 0.0
    final_nav = float(nav["nav"].iloc[-1]) if not nav.empty else 1.0
    total_return = final_nav - 1.0
    cells = [
        ("最终净值", f"{final_nav:.2f}x"),
        ("总收益率", _pct(total_return)),
        ("年化收益率", _pct(float(summary["annual_return"]))),
        ("最大回撤", _pct(float(summary["max_drawdown"]))),
        ("Calmar", f"{float(summary['calmar']):.2f}"),
        ("闭环交易胜率", _pct(win_rate)),
        ("原始买卖笔数", f"{int(float(summary['trade_count']))}"),
        ("闭环交易笔数", f"{len(closed)}"),
    ]
    return "\n".join(f"<div class='metric'><span>{label}</span><strong>{value}</strong></div>" for label, value in cells)


def _window_table(details_path: Path, strategy: str) -> str:
    if not details_path.exists():
        return "<p>未找到 walk-forward 明细文件。</p>"
    details = pd.read_csv(details_path)
    rows = details[details["strategy"] == strategy].copy()
    if rows.empty:
        return "<p>未找到候选策略窗口明细。</p>"
    order = ["full", "train_2025", "oos_2026", "wf_2025_h1", "wf_2025_h2"]
    rows["window_order"] = rows["window"].map({name: index for index, name in enumerate(order)}).fillna(99)
    rows = rows.sort_values("window_order")
    body = []
    for _, row in rows.iterrows():
        body.append(
            "<tr>"
            f"<td>{_escape(row['window'])}</td>"
            f"<td>{_escape(row['start'])}</td>"
            f"<td>{_escape(row['end'])}</td>"
            f"<td>{_pct(float(row['annual_return']))}</td>"
            f"<td>{_pct(float(row['max_drawdown']))}</td>"
            f"<td>{float(row['calmar']):.2f}</td>"
            f"<td>{_escape(row['passes_return_gate'])}</td>"
            f"<td>{_escape(row['passes_drawdown_gate'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>窗口</th><th>开始</th><th>结束</th><th>年化</th><th>最大回撤</th>"
        "<th>Calmar</th><th>过 50% 年化</th><th>过 -30% 回撤</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _round_trip_table(round_trips: pd.DataFrame, names: dict[str, str]) -> str:
    if round_trips.empty:
        return "<p>没有闭环交易。</p>"
    body = []
    for _, row in round_trips.iterrows():
        symbol = str(row["symbol"])
        body.append(
            "<tr>"
            f"<td>{int(row['trade_id'])}</td>"
            f"<td><code>{_escape(symbol)}</code><br><span>{_escape(names.get(symbol, ''))}</span></td>"
            f"<td>{_escape(row['status'])}</td>"
            f"<td>{_format_date(row['buy_date'])}</td>"
            f"<td>{_format_date(row['sell_date'])}</td>"
            f"<td>{int(row['holding_days'])}</td>"
            f"<td>{float(row['buy_price']):.3f}</td>"
            f"<td>{float(row['average_sell_price']):.3f}</td>"
            f"<td class='{ 'pos' if float(row['net_pnl_display']) >= 0 else 'neg' }'>{_money(row['net_pnl_display'])}</td>"
            f"<td class='{ 'pos' if float(row['net_return_pct']) >= 0 else 'neg' }'>{_pct(float(row['net_return_pct']))}</td>"
            f"<td>{_escape(row['exit_reasons'])}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'><table><thead><tr><th>#</th><th>标的</th><th>状态</th><th>买入日</th><th>卖出/标记日</th>"
        "<th>持有天数</th><th>买入价</th><th>均卖价</th><th>净盈亏(元)</th><th>净盈亏率</th><th>退出原因</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _raw_trade_table(trades: pd.DataFrame, names: dict[str, str]) -> str:
    if trades.empty:
        return "<p>没有买卖流水。</p>"
    rows = trades.copy()
    rows["date"] = pd.to_datetime(rows["date"])
    body = []
    for index, row in rows.iterrows():
        symbol = str(row["symbol"]).split(".")[0].zfill(6)
        side = str(row["side"])
        side_label = "买入" if side == "buy" else "卖出"
        body.append(
            "<tr>"
            f"<td>{index + 1}</td>"
            f"<td>{_format_date(row['date'])}</td>"
            f"<td><code>{_escape(symbol)}</code><br><span>{_escape(names.get(symbol, ''))}</span></td>"
            f"<td class='{ 'buy' if side == 'buy' else 'sell' }'>{side_label}</td>"
            f"<td>{float(row['shares']):.4f}</td>"
            f"<td>{float(row['price']):.3f}</td>"
            f"<td>{_money(float(row['value']) * DISPLAY_CAPITAL)}</td>"
            f"<td>{_escape(row['reason'])}</td>"
            f"<td>{_money(float(row['cash_after']) * DISPLAY_CAPITAL)}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'><table><thead><tr><th>#</th><th>日期</th><th>标的</th><th>方向</th><th>股数(账户单位)</th>"
        "<th>价格</th><th>成交金额(元)</th><th>原因</th><th>成交后现金(元)</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def build_html(data: ReportData, strategy_name: str, start: str, end: str, generated_at: str | None = None) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    closed_round_trips = data.round_trips[data.round_trips["status"] == "closed"] if not data.round_trips.empty else pd.DataFrame()
    amount_values = closed_round_trips["net_pnl_display"].astype(float).tolist() if not closed_round_trips.empty else []
    pct_values = closed_round_trips["net_return_pct"].astype(float).tolist() if not closed_round_trips.empty else []
    open_count = int((data.round_trips["status"] != "closed").sum()) if not data.round_trips.empty else 0
    details_path = RESULTS_DIR / f"{DEFAULT_OUTPUT_PREFIX}_details.csv"
    artifacts = "\n".join(
        f"<li><code>{_escape(Path(path).name)}</code></li>" for path in data.artifacts.values()
    )
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>B1 趋势回调策略执行报告</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #62707d;
      --line: #d9e1e8;
      --paper: #f5f7fa;
      --surface: #ffffff;
      --red: #c0392b;
      --green: #16865f;
      --blue: #2f6fa3;
      --amber: #b7791f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.58;
    }}
    .shell {{ width: min(1280px, calc(100% - 40px)); margin: 0 auto; }}
    header {{ background: #fff; border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 10; }}
    nav {{ min-height: 64px; display: flex; align-items: center; justify-content: space-between; gap: 22px; }}
    nav strong {{ font-size: 17px; }}
    nav a {{ color: var(--muted); text-decoration: none; margin-left: 16px; font-size: 14px; }}
    .hero {{ background: #fff; border-bottom: 1px solid var(--line); padding: 34px 0 26px; }}
    h1 {{ font-size: clamp(30px, 4vw, 48px); line-height: 1.1; margin: 0 0 14px; letter-spacing: 0; }}
    .lead {{ color: var(--muted); max-width: 920px; margin: 0; font-size: 17px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    .pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 999px; background: #fff; color: var(--muted); font-size: 13px; }}
    section {{ padding: 34px 0; }}
    h2 {{ font-size: 28px; margin: 0 0 14px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 12px; font-size: 18px; }}
    .section-copy {{ color: var(--muted); margin: 0 0 18px; max-width: 920px; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-height: 86px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 24px; line-height: 1.12; overflow-wrap: anywhere; }}
    .panel {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 18px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .grid-2 > * {{ min-width: 0; }}
    .flow {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .flow-step {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .flow-step b {{ display: block; margin-bottom: 8px; color: var(--blue); }}
    .chart-card {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; margin-top: 16px; }}
    .chart-svg {{ width: 100%; height: auto; display: block; }}
    .note {{ border-left: 5px solid var(--amber); background: #fffaf0; color: #4a3513; padding: 14px 16px; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #f6f8fb; z-index: 1; color: var(--muted); font-weight: 700; }}
    td span {{ color: var(--muted); }}
    code {{ font-family: "SFMono-Regular", Menlo, Consolas, monospace; background: #eef2f7; padding: 2px 5px; border-radius: 4px; }}
    .table-wrap {{ max-height: 520px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .pos, .sell {{ color: var(--red); font-weight: 700; }}
    .neg, .buy {{ color: var(--green); font-weight: 700; }}
    .artifact-list {{ columns: 2; margin: 0; padding-left: 22px; }}
    footer {{ color: var(--muted); padding: 28px 0 44px; font-size: 13px; }}
    @media (max-width: 980px) {{
      .metric-grid, .grid-2, .flow {{ grid-template-columns: 1fr; }}
      nav {{ align-items: flex-start; flex-direction: column; padding: 14px 0; }}
      nav a {{ margin: 0 12px 0 0; }}
      .shell {{ width: min(100% - 28px, 1280px); }}
      .artifact-list {{ columns: 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <nav class="shell">
      <strong>B1 趋势回调策略执行报告</strong>
      <div>
        <a href="#summary">摘要</a>
        <a href="#logic">策略逻辑</a>
        <a href="#charts">图表</a>
        <a href="#details">买卖明细</a>
        <a href="#risk">执行边界</a>
      </div>
    </nav>
  </header>

  <main>
    <div class="hero">
      <div class="shell">
        <h1>{_escape(strategy_name)}</h1>
        <p class="lead">这份 HTML 用本地 Tushare 缓存重建 B1 趋势回调候选策略，重点解释执行流程，并持久化净值曲线、每笔闭环交易盈亏金额分布、盈亏比例分布和完整买卖流水。报告金额按初始资金 1,000,000 元缩放展示。</p>
        <div class="meta">
          <span class="pill">区间：{_escape(start)} 至 {_escape(end)}</span>
          <span class="pill">股票池：活跃度 Top300，实际加载 {data.symbol_count} 只</span>
          <span class="pill">数据源：Tushare 前复权日线 + 沪深300指数</span>
          <span class="pill">交易成本：单边 {COST_RATE * 100:.2f}%</span>
        </div>
      </div>
    </div>

    <section id="summary">
      <div class="shell">
        <h2>核心结果</h2>
        <p class="section-copy">候选策略通过 50% 年化收益和 -30% 最大回撤两个约束；但它仍是历史回测与本地复刻，不等同于实盘承诺。</p>
        <div class="metric-grid">
          {_metrics_grid(data.summary, data.round_trips, data.nav)}
        </div>
      </div>
    </section>

    <section id="logic">
      <div class="shell">
        <h2>策略逻辑与执行方案</h2>
        <div class="grid-2">
          <div class="panel">
            <h3>选股公式</h3>
            <p>收盘价 &gt; BBI(14,28,57,114)，且 EMA(EMA(10)) &gt; BBI，且 KDJ.J &lt; 13。为了降低追高，最终候选额外要求 close/BBI &lt;= 27.5%，20 日动量在 2% 到 75% 之间。</p>
          </div>
          <div class="panel">
            <h3>市场门与仓位</h3>
            <p>沪深300收盘价必须在 BBI 上方，并且 MA20 &gt; MA60 才允许新开仓。每日按 B1 分数排序取 Top2，单票最高 50%，空仓资金不强行补满。</p>
          </div>
        </div>
        <div class="flow" style="margin-top:16px;">
          <div class="flow-step"><b>1. 收盘后筛选</b>计算 BBI、双 EMA、KDJ.J、20 日动量和市场状态，只在市场门打开时生成候选。</div>
          <div class="flow-step"><b>2. 次日执行买入</b>候选信号延后一日成交，按目标仓位和可用现金下单，计入单边成本。</div>
          <div class="flow-step"><b>3. 持仓退出</b>价格达到 8%/16%/24% 止盈阶梯时卖出；本候选分段比例为 100%/100%/100%，等价于首次达到 8% 即全仓止盈。</div>
          <div class="flow-step"><b>4. 风险切断</b>若个股收盘价跌破 BBI，则清仓止损；市场门关闭时不新增仓，但既有仓位仍按个股退出规则处理。</div>
        </div>
      </div>
    </section>

    <section id="charts">
      <div class="shell">
        <h2>净值曲线与交易分布</h2>
        <div class="chart-card">
          <h3>净值曲线</h3>
          {svg_nav_curve(data.nav)}
        </div>
        <div class="grid-2">
          <div class="chart-card">
            <h3>每笔交易盈亏金额分布</h3>
            {svg_sorted_pnl_bars(amount_values)}
          </div>
          <div class="chart-card">
            <h3>每笔交易盈亏比例分布</h3>
            {svg_pct_histogram(pct_values)}
          </div>
        </div>
        <p class="section-copy" style="margin-top:14px;">分布图只统计已闭环交易；未平仓标记到期末的交易数：{open_count}。</p>
      </div>
    </section>

    <section id="validation">
      <div class="shell">
        <h2>分窗口验证</h2>
        <p class="section-copy">最终候选必须在完整区间、2025 训练段、2026 样本外段、2025 上半年和 2025 下半年都通过收益与回撤门槛。</p>
        {_window_table(details_path, strategy_name)}
      </div>
    </section>

    <section id="details">
      <div class="shell">
        <h2>闭环交易盈亏明细</h2>
        <p class="section-copy">这里把一次买入后的所有分段卖出归并成一笔经济交易，用于计算每笔盈亏金额和盈亏率。</p>
        {_round_trip_table(data.round_trips, data.symbol_names)}

        <h2 style="margin-top:34px;">原始买卖明细</h2>
        <p class="section-copy">这里保留回测引擎原始买入和卖出流水，可与平台“买卖明细”逐行对比。</p>
        {_raw_trade_table(data.trades, data.symbol_names)}
      </div>
    </section>

    <section id="risk">
      <div class="shell">
        <h2>执行边界与下一步</h2>
        <div class="grid-2">
          <div class="panel">
            <h3>当前可执行版本</h3>
            <p>适合作为小仓卫星策略候选：每日收盘后筛选，次日开盘或可成交价执行，单票最高 50%，组合层面继续用 -30% 回撤作为硬约束。</p>
          </div>
          <div class="panel">
            <h3>不能忽略的误差</h3>
            <p>本地复刻未完全模拟涨跌停、停牌、成交容量、真实滑点，也未拿到平台完整卖出规则；截图中的 68% 年化不能直接外推到未来实盘。</p>
          </div>
        </div>
        <p class="note" style="margin-top:16px;">纪律提醒：低位不是入场信号，止跌才是入场信号。本报告只用于研究和执行方案设计，不构成投资建议或收益保证。</p>
      </div>
    </section>

    <section id="artifacts">
      <div class="shell">
        <h2>持久化产物</h2>
        <ul class="artifact-list">
          {artifacts}
        </ul>
      </div>
    </section>
  </main>

  <footer>
    <div class="shell">生成时间：{_escape(generated_at)}。本报告为本地回测研究产物，金额按 1,000,000 元初始资金缩放。</div>
  </footer>
</body>
</html>
"""
    return html_text


def build_report_data(
    symbols_file: Path = DEFAULT_SYMBOLS_FILE,
    start: str = "2025-01-01",
    end: str = "2026-05-15",
    history_start: str = "2024-06-01",
    data_dir: Path = DATA_DIR / "b1_a_share",
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    html_path: Path = DEFAULT_HTML_PATH,
) -> ReportData:
    config = B1BacktestConfig(
        take_profit_levels=(0.08, 0.16, 0.24),
        take_profit_fractions=(1.0, 1.0, 1.0),
        max_entry_close_bbi=0.275,
        min_entry_mom20=0.02,
        max_entry_mom20=0.75,
    )
    symbols = load_symbols_from_csv_file(symbols_file)
    panels = build_b1_panels(
        symbols=symbols,
        start=_compact_date(history_start),
        end=_compact_date(end),
        data_dir=data_dir,
        config=config,
        refresh=False,
        data_provider="tushare",
    )
    market = build_market_frame(
        history_start,
        end,
        start,
        data_provider="tushare",
        data_dir=data_dir,
        require_ma20_gt_ma60=True,
    )
    result = run_b1_backtest(panels, market, config)
    artifacts = write_b1_artifacts(result, start, end, len(panels), RESULTS_DIR, artifact_prefix=output_prefix)

    nav = result.nav.rename("nav").reset_index().rename(columns={"index": "date"})
    nav["date"] = pd.to_datetime(nav["date"])
    nav["drawdown"] = nav["nav"] / nav["nav"].cummax() - 1.0
    trades = result.trades.copy()
    if not trades.empty:
        trades["date"] = pd.to_datetime(trades["date"])
    final_date = pd.Timestamp(nav["date"].iloc[-1])
    end_prices = _final_prices_from_panels(panels, final_date)
    round_trips = build_round_trips(
        trades,
        cost_rate=config.cost_rate,
        display_capital=DISPLAY_CAPITAL,
        end_prices=end_prices,
        end_date=final_date,
    )
    round_trips_path = RESULTS_DIR / f"{output_prefix}_round_trips.csv"
    round_trips.to_csv(round_trips_path, index=False)
    nav.to_csv(RESULTS_DIR / f"{output_prefix}_nav_with_drawdown.csv", index=False)

    manifest_path = RESULTS_DIR / f"{output_prefix}_report_manifest.json"
    report_artifacts = {
        **artifacts,
        "round_trips": str(round_trips_path),
        "nav_with_drawdown": str(RESULTS_DIR / f"{output_prefix}_nav_with_drawdown.csv"),
    }
    manifest_path.write_text(
        json.dumps(
            build_report_manifest_payload(output_prefix, html_path, DISPLAY_CAPITAL, report_artifacts),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_artifacts["report_manifest"] = str(manifest_path)

    return ReportData(
        nav=nav,
        trades=trades,
        round_trips=round_trips,
        summary=result.summary,
        symbol_names=_read_symbol_names(symbols_file),
        symbol_count=len(panels),
        artifacts=report_artifacts,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the B1 quality-gate static HTML report.")
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-05-15")
    parser.add_argument("--history-start", default="2024-06-01")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share")
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--html-path", type=Path, default=DEFAULT_HTML_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data = build_report_data(
        symbols_file=args.symbols_file,
        start=args.start,
        end=args.end,
        history_start=args.history_start,
        data_dir=args.data_dir,
        output_prefix=args.output_prefix,
        html_path=args.html_path,
    )
    args.html_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = build_html(data, "tp8_16_24_f100_100_100", args.start, args.end)
    args.html_path.write_text(html_text, encoding="utf-8")
    print(f"html={args.html_path}")
    print(f"nav_rows={len(data.nav)}")
    print(f"raw_trades={len(data.trades)}")
    print(f"round_trips={len(data.round_trips)}")
    print(f"annual_return={float(data.summary['annual_return']) * 100:.2f}%")
    print(f"max_drawdown={float(data.summary['max_drawdown']) * 100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
