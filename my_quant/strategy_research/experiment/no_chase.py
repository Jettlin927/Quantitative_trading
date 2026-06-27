from __future__ import annotations

import html
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class NoChaseConfig:
    breakout_window: int = 20
    min_gap_pct: float = 0.04
    min_mom5_pct: float = 0.12
    min_close_ma20_pct: float = 0.08
    pullback_pct: float = 0.05
    max_wait_days: int = 10
    hold_days: int = 20
    min_amount: float = 50000.0
    round_trip_cost_rate: float = 0.002


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    prepared = frame.copy()
    for column in columns:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    return prepared


def prepare_no_chase_frame(bars: pd.DataFrame, config: NoChaseConfig) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close", "pre_close", "amount"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"No-chase bars missing required columns: {sorted(missing)}")

    frame = _numeric(bars, ["open", "high", "low", "close", "pre_close", "amount"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    frame["ma20"] = frame["close"].rolling(window=20, min_periods=20).mean()
    frame["prior_high"] = frame["high"].rolling(window=config.breakout_window, min_periods=config.breakout_window).max().shift(1)
    frame["gap_pct"] = frame["open"] / frame["pre_close"] - 1.0
    frame["mom5_pct"] = frame["close"] / frame["close"].shift(5) - 1.0
    frame["close_ma20_pct"] = frame["close"] / frame["ma20"] - 1.0
    frame["trigger"] = (
        (frame["amount"] >= config.min_amount)
        & (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["pre_close"] > 0)
        & (frame["close"] >= frame["prior_high"])
        & (frame["gap_pct"] >= config.min_gap_pct)
        & (frame["mom5_pct"] >= config.min_mom5_pct)
        & (frame["close_ma20_pct"] >= config.min_close_ma20_pct)
    ).fillna(False)
    return frame


def _trade_row(
    *,
    symbol: str,
    group: str,
    signal: str,
    trigger_row: pd.Series,
    entry_row: pd.Series,
    exit_row: pd.Series,
    low_window: pd.Series,
    wait_days: int,
    config: NoChaseConfig,
) -> dict[str, Any]:
    entry_price = float(entry_row["open"])
    exit_price = float(exit_row["close"])
    gross_return = exit_price / entry_price - 1.0
    net_return = gross_return - config.round_trip_cost_rate
    mae = float(low_window.min()) / entry_price - 1.0
    return {
        "symbol": symbol,
        "group": group,
        "signal": signal,
        "trigger_date": trigger_row["trade_date"].date().isoformat(),
        "entry_date": entry_row["trade_date"].date().isoformat(),
        "exit_date": exit_row["trade_date"].date().isoformat(),
        "trigger_close": float(trigger_row["close"]),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return": net_return,
        "mae": mae,
        "wait_days": wait_days,
        "gap_pct": float(trigger_row["gap_pct"]),
        "mom5_pct": float(trigger_row["mom5_pct"]),
        "close_ma20_pct": float(trigger_row["close_ma20_pct"]),
    }


def _find_pullback_confirmation(frame: pd.DataFrame, trigger_index: int, config: NoChaseConfig) -> int | None:
    trigger_close = float(frame.loc[trigger_index, "close"])
    search_end = min(trigger_index + config.max_wait_days, len(frame) - config.hold_days - 2)
    for index in range(trigger_index + 1, search_end + 1):
        row = frame.loc[index]
        previous = frame.loc[index - 1]
        if pd.isna(row["ma20"]) or float(row["ma20"]) <= 0:
            continue
        pulled_back = float(row["close"]) <= trigger_close * (1.0 - config.pullback_pct)
        turned_up = float(row["close"]) > float(previous["close"])
        stayed_constructive = float(row["close"]) >= float(row["ma20"]) * 0.98
        if pulled_back and turned_up and stayed_constructive:
            return index
    return None


def evaluate_no_chase_for_symbol(symbol: str, bars: pd.DataFrame, config: NoChaseConfig) -> pd.DataFrame:
    frame = prepare_no_chase_frame(bars, config)
    trades: list[dict[str, Any]] = []
    next_allowed_index = 0

    trigger_indices = [int(index) for index in frame.index[frame["trigger"]]]
    for trigger_index in trigger_indices:
        if trigger_index < next_allowed_index:
            continue
        direct_entry_index = trigger_index + 1
        direct_exit_index = direct_entry_index + config.hold_days
        if direct_exit_index >= len(frame):
            continue

        trigger_row = frame.loc[trigger_index]
        direct_entry = frame.loc[direct_entry_index]
        direct_exit = frame.loc[direct_exit_index]
        direct_low_window = frame.loc[direct_entry_index:direct_exit_index, "low"]
        direct_row = _trade_row(
            symbol=symbol,
            group="direct_all",
            signal="direct_next_open",
            trigger_row=trigger_row,
            entry_row=direct_entry,
            exit_row=direct_exit,
            low_window=direct_low_window,
            wait_days=0,
            config=config,
        )
        trades.append(direct_row)

        confirm_index = _find_pullback_confirmation(frame, trigger_index, config)
        if confirm_index is not None:
            wait_entry_index = confirm_index + 1
            wait_exit_index = wait_entry_index + config.hold_days
            if wait_exit_index < len(frame):
                matched_row = dict(direct_row)
                matched_row["group"] = "direct_matched"
                trades.append(matched_row)
                trades.append(
                    _trade_row(
                        symbol=symbol,
                        group="wait_confirmed",
                        signal="wait_pullback_confirm",
                        trigger_row=trigger_row,
                        entry_row=frame.loc[wait_entry_index],
                        exit_row=frame.loc[wait_exit_index],
                        low_window=frame.loc[wait_entry_index:wait_exit_index, "low"],
                        wait_days=wait_entry_index - direct_entry_index,
                        config=config,
                    )
                )
                next_allowed_index = wait_exit_index + 1
            else:
                next_allowed_index = direct_exit_index + 1
        else:
            next_allowed_index = direct_exit_index + 1

    return pd.DataFrame(trades)


def evaluate_no_chase_market(bars: pd.DataFrame, config: NoChaseConfig) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()
    frames = []
    for symbol, group in bars.groupby("ts_code", sort=True):
        trades = evaluate_no_chase_for_symbol(str(symbol), group, config)
        if not trades.empty:
            frames.append(trades)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _profit_factor(returns: pd.Series) -> float | None:
    wins = returns[returns > 0].sum()
    losses = returns[returns < 0].sum()
    if losses == 0:
        return None if wins == 0 else math.inf
    return float(wins / abs(losses))


def _tail10(values: pd.Series) -> float | None:
    clean = values.dropna().sort_values()
    if clean.empty:
        return None
    count = max(1, math.ceil(len(clean) * 0.1))
    return float(clean.head(count).mean())


def _group_summary(group: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(group["return"], errors="coerce")
    mae = pd.to_numeric(group["mae"], errors="coerce")
    wait_days = pd.to_numeric(group["wait_days"], errors="coerce")
    return {
        "trade_count": int(len(group)),
        "avg_return": float(returns.mean()) if not returns.empty else None,
        "median_return": float(returns.median()) if not returns.empty else None,
        "win_rate": float((returns > 0).mean()) if not returns.empty else None,
        "profit_factor": _profit_factor(returns),
        "mean_mae": float(mae.mean()) if not mae.empty else None,
        "tail10_return": _tail10(returns),
        "tail10_mae": _tail10(mae),
        "avg_wait_days": float(wait_days.mean()) if not wait_days.empty else None,
    }


def summarize_no_chase_trades(trades: pd.DataFrame) -> dict[str, Any]:
    groups = {name: _group_summary(group) for name, group in trades.groupby("group", sort=True)}
    trigger_count = groups.get("direct_all", {}).get("trade_count", 0) or 0
    confirmed_count = groups.get("wait_confirmed", {}).get("trade_count", 0) or 0
    missed_count = max(0, int(trigger_count) - int(confirmed_count))

    direct_matched = groups.get("direct_matched", {})
    wait_confirmed = groups.get("wait_confirmed", {})
    direct_mae = direct_matched.get("mean_mae")
    wait_mae = wait_confirmed.get("mean_mae")
    direct_tail = direct_matched.get("tail10_return")
    wait_tail = wait_confirmed.get("tail10_return")
    direct_return = direct_matched.get("avg_return")
    wait_return = wait_confirmed.get("avg_return")

    mae_improvement = None if direct_mae is None or wait_mae is None else float(wait_mae - direct_mae)
    tail_loss_improvement = None if direct_tail is None or wait_tail is None else float(wait_tail - direct_tail)
    return_delta = None if direct_return is None or wait_return is None else float(wait_return - direct_return)

    enough_sample = confirmed_count >= 30
    improves_risk = (mae_improvement or 0.0) > 0 and (tail_loss_improvement or 0.0) > 0
    conclusion = "阶段通过" if enough_sample and improves_risk and (return_delta or 0.0) >= -0.02 else "观察"
    if trigger_count == 0 or confirmed_count == 0:
        conclusion = "观察"

    return {
        "label": "只等回调",
        "conclusion": conclusion,
        "trigger_count": int(trigger_count),
        "confirmed_count": int(confirmed_count),
        "missed_count": int(missed_count),
        "confirmation_rate": float(confirmed_count / trigger_count) if trigger_count else None,
        "mae_improvement": mae_improvement,
        "tail_loss_improvement": tail_loss_improvement,
        "return_delta": return_delta,
        "groups": groups,
    }


def _fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def build_no_chase_report_html(summary: dict[str, Any], generated_at: str) -> str:
    groups = summary.get("groups", {})
    config = summary.get("config", {})
    start_date = summary.get("start_date", "n/a")
    end_date = summary.get("end_date", "n/a")
    symbol_count = summary.get("symbol_count", "n/a")
    rows = []
    for group_name in ["direct_all", "direct_matched", "wait_confirmed"]:
        stats = groups.get(group_name)
        if not stats:
            continue
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(group_name)}</code></td>"
            f"<td>{stats.get('trade_count', 0)}</td>"
            f"<td>{_fmt_pct(stats.get('avg_return'))}</td>"
            f"<td>{_fmt_pct(stats.get('win_rate'))}</td>"
            f"<td>{_fmt_pct(stats.get('mean_mae'))}</td>"
            f"<td>{_fmt_pct(stats.get('tail10_return'))}</td>"
            f"<td>{stats.get('profit_factor') if stats.get('profit_factor') is not None else 'n/a'}</td>"
            "</tr>"
        )
    table_rows = "\n".join(rows)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>规则 002 不追高验证报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .badge {{ display: inline-block; padding: 4px 8px; border: 1px solid #85929e; border-radius: 4px; margin-right: 8px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #d5d8dc; padding: 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .note {{ background: #f8f9f9; border-left: 4px solid #566573; padding: 12px; }}
  </style>
</head>
<body>
  <h1>规则 002：不追高，等回调或止跌确认</h1>
  <p><span class="badge">结论：{html.escape(str(summary.get("conclusion", "观察")))}</span><span class="badge">美股标签：{html.escape(str(summary.get("label", "只等回调")))}</span></p>
  <p>生成时间：{html.escape(generated_at)}</p>
  <div class="note">本报告是研究辅助材料。A 股验证不能直接证明美股单票收益，只能为“当前不追高、等待回调/止跌确认”的纪律标签提供大样本证据。</div>
  <h2>样本概览</h2>
  <p>数据窗口：{html.escape(str(start_date))} 到 {html.escape(str(end_date))}；股票数：{html.escape(str(symbol_count))}；触发样本 {summary.get("trigger_count", 0)} 笔；等待确认成交 {summary.get("confirmed_count", 0)} 笔；未等到确认 {summary.get("missed_count", 0)} 笔；确认率 {_fmt_pct(summary.get("confirmation_rate"))}。</p>
  <p>等待确认相对直接追入的平均 MAE 改善：{_fmt_pct(summary.get("mae_improvement"))}；尾部收益改善：{_fmt_pct(summary.get("tail_loss_improvement"))}；平均收益差：{_fmt_pct(summary.get("return_delta"))}。</p>
  <h2>分组指标</h2>
  <table>
    <thead><tr><th>组别</th><th>交易数</th><th>平均收益</th><th>胜率</th><th>平均 MAE</th><th>最差 10% 收益</th><th>Profit Factor</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
  <h2>执行口径</h2>
  <p>触发日以收盘后可见信号判定，直接追入组使用下一交易日开盘价入场；等待确认组在触发后寻找回调且重新转强，再以下一交易日开盘价入场。两组均按固定持有窗口退出，并扣除双边成本。</p>
  <p>默认参数：突破窗口 {html.escape(str(config.get("breakout_window", "n/a")))} 日，最小高开 {html.escape(str(config.get("min_gap_pct", "n/a")))}，最小 5 日涨幅 {html.escape(str(config.get("min_mom5_pct", "n/a")))}，最小偏离 MA20 {html.escape(str(config.get("min_close_ma20_pct", "n/a")))}，等待窗口 {html.escape(str(config.get("max_wait_days", "n/a")))} 日，持有窗口 {html.escape(str(config.get("hold_days", "n/a")))} 日，双边成本 {html.escape(str(config.get("round_trip_cost_rate", "n/a")))}。</p>
</body>
</html>
"""


def make_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_no_chase_outputs(output_dir: Path, trades: pd.DataFrame, summary: dict[str, Any], generated_at: str) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_text = build_no_chase_report_html(summary, generated_at=generated_at)
    paths = {
        "trades_csv": str(output_dir / "trades.csv"),
        "summary_json": str(output_dir / "summary.json"),
        "summary_csv": str(output_dir / "summary.csv"),
        "report_html": str(output_dir / "index.html"),
    }
    trades.to_csv(paths["trades_csv"], index=False)
    pd.DataFrame([summary]).drop(columns=["groups"], errors="ignore").to_csv(paths["summary_csv"], index=False)
    Path(paths["summary_json"]).write_text(json.dumps(make_json_safe(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(paths["report_html"]).write_text(html_text, encoding="utf-8")
    return paths
