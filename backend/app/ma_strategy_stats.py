from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd


RULE_TREND_FOLLOWING = "ma5_gt_ma20_5pct_3d"
RULE_TREND_REVERSAL = "ma5_cross_up_ma20_above_ma60"
RULE_FILTERED_TREND_ENTRY = "ma5_gt_ma20_3d_filtered"
RULE_FILTERED_TREND_ENTRY_TOP10 = "ma5_gt_ma20_3d_filtered_top10"
DEFAULT_HORIZONS = (5, 10, 20, 60)


@dataclass(frozen=True)
class TradeCost:
    buy_cost_rate: float = 0.00035
    sell_cost_rate: float = 0.00085
    slippage_rate: float = 0.001


def prepare_price_panel(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return frame

    frame = normalize_price_frame(frame)
    frame = frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("ts_code", sort=False)
    frame["ma5"] = grouped["close"].transform(lambda series: series.rolling(5, min_periods=5).mean())
    frame["ma20"] = grouped["close"].transform(lambda series: series.rolling(20, min_periods=20).mean())
    frame["ma60"] = grouped["close"].transform(lambda series: series.rolling(60, min_periods=60).mean())
    frame["amount_ma20"] = grouped["amount"].transform(lambda series: series.rolling(20, min_periods=20).mean()) if "amount" in frame.columns else None
    frame["bar_return"] = grouped["close"].pct_change()
    if "pct_chg" in frame.columns:
        frame["bar_return"] = frame["pct_chg"].fillna(frame["bar_return"] * 100) / 100
    market_filter = build_market_filter(frame)
    return frame.merge(market_filter[["trade_date", "market_filter_pass", "market_regime"]], on="trade_date", how="left")


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"])
    for column in ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount", "ma5", "ma20", "ma60"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized


def build_market_filter(frame: pd.DataFrame) -> pd.DataFrame:
    daily_return = frame.groupby("trade_date", sort=True)["bar_return"].mean().dropna()
    market = daily_return.to_frame("market_return")
    market["market_nav"] = (1 + market["market_return"]).cumprod()
    market["market_ma20"] = market["market_nav"].rolling(20, min_periods=20).mean()
    market["market_ma60"] = market["market_nav"].rolling(60, min_periods=60).mean()
    market["market_filter_pass"] = (market["market_nav"] > market["market_ma60"]) & (market["market_ma20"] > market["market_ma60"])
    market["market_regime"] = "bear_or_warmup"
    market.loc[market["market_filter_pass"], "market_regime"] = "risk_on"
    weak = (market["market_nav"] > market["market_ma60"]) & (market["market_ma20"] <= market["market_ma60"])
    market.loc[weak, "market_regime"] = "transition"
    return market.reset_index()


def detect_ma_signals(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return empty_signals()

    frame = normalize_price_frame(panel)
    if "market_filter_pass" not in frame.columns:
        frame["market_filter_pass"] = True
    frame = frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("ts_code", sort=False)
    frame["prev_ma5"] = grouped["ma5"].shift(1)
    frame["prev_ma20"] = grouped["ma20"].shift(1)
    frame["ma_spread"] = frame["ma5"] / frame["ma20"] - 1

    above_threshold = (frame["ma20"] > 0) & (frame["ma_spread"] >= 0.05)
    streak = (
        above_threshold.astype(int)
        .groupby(frame["ts_code"], sort=False)
        .rolling(3, min_periods=3)
        .sum()
        .reset_index(level=0, drop=True)
    )
    previous_streak = streak.groupby(frame["ts_code"], sort=False).shift(1).fillna(0)
    market_ok = frame["market_filter_pass"].fillna(False).astype(bool)

    trend_following = market_ok & (streak >= 3) & (previous_streak < 3)
    trend_reversal = (
        market_ok
        & (frame["prev_ma5"] <= frame["prev_ma20"])
        & (frame["ma5"] > frame["ma20"])
        & (frame["ma5"] > frame["prev_ma5"])
        & (frame[["ma5", "ma20"]].min(axis=1) > frame["ma60"])
    )

    rows = []
    rows.extend(build_signal_rows(frame[trend_following], RULE_TREND_FOLLOWING))
    rows.extend(build_signal_rows(frame[trend_reversal], RULE_TREND_REVERSAL))
    if not rows:
        return empty_signals()
    return pd.DataFrame(rows).sort_values(["signal_date", "ts_code", "rule"]).reset_index(drop=True)


def detect_filtered_trend_entry_signals(
    panel: pd.DataFrame,
    max_close_ma20_distance: float = 0.08,
    min_amount_20d: float = 50_000,
    cooldown_days: int = 20,
) -> pd.DataFrame:
    if panel.empty:
        return empty_signals()

    frame = normalize_price_frame(panel)
    if "market_filter_pass" not in frame.columns:
        frame["market_filter_pass"] = True
    frame = frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("ts_code", sort=False)
    if "amount_ma20" not in frame.columns and "amount" in frame.columns:
        frame["amount_ma20"] = grouped["amount"].transform(lambda series: series.rolling(20, min_periods=20).mean())

    frame["ma_spread"] = frame["ma5"] / frame["ma20"] - 1
    frame["close_ma20_distance"] = frame["close"] / frame["ma20"] - 1
    frame["ma20_slope_5d"] = frame["ma20"] / grouped["ma20"].shift(5) - 1
    frame["ma60_slope_10d"] = frame["ma60"] / grouped["ma60"].shift(10) - 1
    above_ma20 = frame["ma5"] > frame["ma20"]
    streak = (
        above_ma20.astype(int)
        .groupby(frame["ts_code"], sort=False)
        .rolling(3, min_periods=3)
        .sum()
        .reset_index(level=0, drop=True)
    )

    market_ok = frame["market_filter_pass"].fillna(False).astype(bool)
    not_chasing = (frame["close_ma20_distance"] >= 0) & (frame["close_ma20_distance"] <= max_close_ma20_distance)
    trend_ok = (streak >= 3) & (frame["ma20_slope_5d"] > 0) & ((frame["ma20"] > frame["ma60"]) | (frame["ma60_slope_10d"] > 0))
    liquidity_ok = frame.get("amount_ma20", pd.Series(index=frame.index, dtype=float)) >= min_amount_20d
    candidate = market_ok & trend_ok & not_chasing & liquidity_ok

    rows: list[dict[str, Any]] = []
    for _, group in frame[candidate].groupby("ts_code", sort=False):
        last_signal_position = -cooldown_days - 1
        for original_position, row in group.iterrows():
            if original_position - last_signal_position <= cooldown_days:
                continue
            signal = build_signal_rows(pd.DataFrame([row.to_dict()]), RULE_FILTERED_TREND_ENTRY)[0]
            signal["close"] = row["close"]
            signal["close_ma20_distance"] = row["close_ma20_distance"]
            signal["ma20_slope_5d"] = row["ma20_slope_5d"]
            signal["ma60_slope_10d"] = row["ma60_slope_10d"]
            signal["amount_ma20"] = row["amount_ma20"]
            rows.append(signal)
            last_signal_position = original_position

    if not rows:
        return empty_signals()
    return pd.DataFrame(rows).sort_values(["signal_date", "ts_code", "rule"]).reset_index(drop=True)


def select_daily_top_signals(signals: pd.DataFrame, max_per_day: int = 10) -> pd.DataFrame:
    if signals.empty:
        return empty_signals()
    frame = signals.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"])
    for column in ("ma_spread", "close_ma20_distance", "ma20_slope_5d", "ma60_slope_10d", "amount_ma20"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["strength_score"] = (
        frame.get("ma20_slope_5d", 0) * 3
        + frame.get("ma60_slope_10d", 0) * 2
        + frame.get("ma_spread", 0)
        - frame.get("close_ma20_distance", 0)
        + (frame.get("amount_ma20", 0).clip(lower=0) / 100_000).clip(upper=2) * 0.01
    )
    selected = (
        frame.sort_values(["signal_date", "strength_score", "ts_code"], ascending=[True, False, True])
        .groupby("signal_date", sort=False)
        .head(max_per_day)
        .copy()
    )
    selected["rule"] = RULE_FILTERED_TREND_ENTRY_TOP10
    return selected.sort_values(["signal_date", "strength_score", "ts_code"], ascending=[True, False, True]).reset_index(drop=True)


def build_signal_rows(rows: pd.DataFrame, rule: str) -> list[dict[str, Any]]:
    signal_rows = []
    for row in rows.itertuples(index=False):
        signal_rows.append(
            {
                "ts_code": row.ts_code,
                "signal_date": row.trade_date,
                "rule": rule,
                "ma5": row.ma5,
                "ma20": row.ma20,
                "ma60": row.ma60,
                "ma_spread": row.ma_spread,
                "market_filter_pass": bool(row.market_filter_pass),
                "market_regime": getattr(row, "market_regime", None),
            }
        )
    return signal_rows


def attach_forward_returns(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    trade_cost: TradeCost | None = None,
    round_trip_cost_rate: float | None = None,
) -> pd.DataFrame:
    if prices.empty or signals.empty:
        return pd.DataFrame()

    cost = trade_cost or TradeCost()
    frame = normalize_price_frame(prices).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    signal_frame = signals.copy()
    signal_frame["signal_date"] = pd.to_datetime(signal_frame["signal_date"])
    by_symbol = {ts_code: group.reset_index(drop=True) for ts_code, group in frame.groupby("ts_code", sort=False)}
    position_by_key = {
        (ts_code, row.trade_date): index
        for ts_code, group in by_symbol.items()
        for index, row in group.iterrows()
    }

    events = []
    for signal in signal_frame.itertuples(index=False):
        symbol_prices = by_symbol.get(signal.ts_code)
        signal_index = position_by_key.get((signal.ts_code, signal.signal_date))
        if symbol_prices is None or signal_index is None:
            continue
        entry_index = signal_index + 1
        if entry_index >= len(symbol_prices):
            continue
        entry = symbol_prices.iloc[entry_index]
        if is_limit_blocked_buy(entry):
            continue
        event = signal._asdict()
        event["entry_date"] = entry["trade_date"]
        event["entry_open"] = entry["open"]
        event["entry_vol"] = entry.get("vol")
        event["entry_amount"] = entry.get("amount")
        for horizon in horizons:
            exit_index = entry_index + horizon - 1
            if exit_index >= len(symbol_prices):
                event[f"exit_{horizon}d_date"] = pd.NaT
                event[f"exit_{horizon}d_close"] = pd.NA
                event[f"return_{horizon}d"] = pd.NA
                continue
            exit_row = symbol_prices.iloc[exit_index]
            event[f"exit_{horizon}d_date"] = exit_row["trade_date"]
            event[f"exit_{horizon}d_close"] = exit_row["close"]
            if is_limit_blocked_sell(exit_row):
                event[f"return_{horizon}d"] = pd.NA
            else:
                event[f"return_{horizon}d"] = net_return(
                    entry["open"],
                    exit_row["close"],
                    cost=cost,
                    round_trip_cost_rate=round_trip_cost_rate,
                )
        events.append(event)
    return pd.DataFrame(events)


def net_return(
    entry_price: float,
    exit_price: float,
    cost: TradeCost,
    round_trip_cost_rate: float | None = None,
) -> float:
    if round_trip_cost_rate is not None:
        return float(exit_price) / float(entry_price) - 1 - round_trip_cost_rate
    buy_price = float(entry_price) * (1 + cost.buy_cost_rate + cost.slippage_rate)
    sell_price = float(exit_price) * (1 - cost.sell_cost_rate - cost.slippage_rate)
    return sell_price / buy_price - 1


def is_limit_blocked_buy(row: pd.Series) -> bool:
    pre_close = row.get("pre_close")
    open_price = row.get("open")
    if pd.isna(pre_close) or pd.isna(open_price) or pre_close <= 0:
        return False
    return open_price >= pre_close * 1.095


def is_limit_blocked_sell(row: pd.Series) -> bool:
    pre_close = row.get("pre_close")
    close_price = row.get("close")
    if pd.isna(pre_close) or pd.isna(close_price) or pre_close <= 0:
        return False
    return close_price <= pre_close * 0.905


def summarize_event_returns(events: pd.DataFrame, horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=["rule", "horizon", "event_count", "mean_return", "median_return", "win_rate"])
    rows = []
    for rule, rule_events in events.groupby("rule", sort=True):
        for horizon in horizons:
            column = f"return_{horizon}d"
            if column not in rule_events.columns:
                continue
            returns = pd.to_numeric(rule_events[column], errors="coerce").dropna()
            if returns.empty:
                rows.append(empty_summary_row(rule, horizon))
                continue
            rows.append(
                {
                    "rule": rule,
                    "horizon": horizon,
                    "event_count": int(returns.count()),
                    "mean_return": float(returns.mean()),
                    "median_return": float(returns.median()),
                    "win_rate": float((returns > 0).mean()),
                    "best_return": float(returns.max()),
                    "worst_return": float(returns.min()),
                }
            )
    return pd.DataFrame(rows)


def simulate_horizon_portfolio(
    events: pd.DataFrame,
    horizon: int,
    initial_cash: float = 1_000_000,
    max_positions: int = 20,
    target_position_pct: float = 0.05,
    lot_size: int = 100,
    volume_capacity_pct: float | None = 0.05,
    trade_cost: TradeCost | None = None,
) -> dict[str, Any]:
    if events.empty:
        return {
            "horizon": horizon,
            "initial_cash": initial_cash,
            "ending_equity": initial_cash,
            "total_return": 0.0,
            "trade_count": 0,
            "skipped_count": 0,
        }

    cost = trade_cost or TradeCost()
    exit_date_column = f"exit_{horizon}d_date"
    exit_price_column = f"exit_{horizon}d_close"
    frame = events.copy()
    frame["entry_date"] = pd.to_datetime(frame["entry_date"])
    frame[exit_date_column] = pd.to_datetime(frame[exit_date_column])
    frame["entry_open"] = pd.to_numeric(frame["entry_open"], errors="coerce")
    frame[exit_price_column] = pd.to_numeric(frame[exit_price_column], errors="coerce")
    frame = frame.dropna(subset=["entry_date", "entry_open", exit_date_column, exit_price_column])
    frame = frame.sort_values(["entry_date", "rule", "ts_code"]).reset_index(drop=True)

    cash = float(initial_cash)
    positions: list[dict[str, Any]] = []
    trade_count = 0
    skipped_count = 0

    for event in frame.itertuples(index=False):
        entry_date = getattr(event, "entry_date")
        cash = close_due_positions(cash, positions, entry_date, cost)
        positions = [position for position in positions if position["exit_date"] > entry_date]
        if len(positions) >= max_positions:
            skipped_count += 1
            continue

        entry_price = float(getattr(event, "entry_open"))
        if entry_price <= 0:
            skipped_count += 1
            continue
        equity = cash + sum(position["shares"] * position["entry_price"] for position in positions)
        target_value = equity * target_position_pct
        budget = min(cash, target_value)
        shares = int(budget // (entry_price * lot_size)) * lot_size
        if volume_capacity_pct is not None:
            shares = min(shares, capacity_limited_shares(event, lot_size, volume_capacity_pct))
        total_cost = shares * entry_price * (1 + cost.buy_cost_rate + cost.slippage_rate)
        if shares <= 0 or total_cost > cash:
            skipped_count += 1
            continue

        cash -= total_cost
        positions.append(
            {
                "shares": shares,
                "entry_price": entry_price,
                "exit_date": getattr(event, exit_date_column),
                "exit_price": float(getattr(event, exit_price_column)),
            }
        )
        trade_count += 1

    if positions:
        final_date = max(position["exit_date"] for position in positions)
        cash = close_due_positions(cash, positions, final_date, cost)
        positions = [position for position in positions if position["exit_date"] > final_date]

    ending_equity = cash + sum(position["shares"] * position["entry_price"] for position in positions)
    return {
        "horizon": horizon,
        "initial_cash": float(initial_cash),
        "ending_equity": float(ending_equity),
        "total_return": float(ending_equity / initial_cash - 1),
        "trade_count": trade_count,
        "skipped_count": skipped_count,
        "max_positions": max_positions,
        "target_position_pct": target_position_pct,
        "volume_capacity_pct": volume_capacity_pct,
    }


def capacity_limited_shares(event: Any, lot_size: int, volume_capacity_pct: float) -> int:
    entry_vol = getattr(event, "entry_vol", None)
    if entry_vol is None or pd.isna(entry_vol):
        return 10**18
    available_shares = float(entry_vol) * 100 * volume_capacity_pct
    return int(available_shares // lot_size) * lot_size


def close_due_positions(cash: float, positions: list[dict[str, Any]], trade_date: pd.Timestamp, cost: TradeCost) -> float:
    updated_cash = cash
    for position in positions:
        if position["exit_date"] <= trade_date:
            updated_cash += position["shares"] * position["exit_price"] * (1 - cost.sell_cost_rate - cost.slippage_rate)
    return updated_cash


def empty_summary_row(rule: str, horizon: int) -> dict[str, Any]:
    return {
        "rule": rule,
        "horizon": horizon,
        "event_count": 0,
        "mean_return": None,
        "median_return": None,
        "win_rate": None,
        "best_return": None,
        "worst_return": None,
    }


def empty_signals() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ts_code",
            "signal_date",
            "rule",
            "ma5",
            "ma20",
            "ma60",
            "ma_spread",
            "market_filter_pass",
            "market_regime",
        ]
    )
