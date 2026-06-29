from __future__ import annotations

from typing import Any

import pandas as pd


RULE_VALUE_SECTOR_STOPFALL = "value_quality_sector_stopfall"
DEFAULT_VALUE_HORIZONS = (20, 60, 120, 180, 360)


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    if "trade_date" in normalized.columns:
        normalized["trade_date"] = pd.to_datetime(normalized["trade_date"])
    for column in (
        "open",
        "close",
        "bar_return",
        "pe_ttm",
        "pb",
        "total_mv",
        "amount",
        "roe",
        "netprofit_margin",
        "debt_to_assets",
        "tr_yoy",
        "netprofit_yoy",
        "industry_rebound_20d",
        "industry_ma20_slope_5d",
    ):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized


def build_industry_stopfall_features(price_panel: pd.DataFrame) -> pd.DataFrame:
    if price_panel.empty:
        return pd.DataFrame()

    frame = normalize_frame(price_panel).sort_values(["industry", "trade_date", "ts_code"]).reset_index(drop=True)
    if "bar_return" not in frame.columns:
        frame["bar_return"] = frame.groupby("ts_code", sort=False)["close"].pct_change()

    industry_daily = (
        frame.dropna(subset=["industry", "trade_date"])
        .groupby(["industry", "trade_date"], as_index=False)
        .agg(industry_return=("bar_return", "mean"))
    )
    industry_daily["industry_return"] = industry_daily["industry_return"].fillna(0)
    industry_daily = industry_daily.sort_values(["industry", "trade_date"]).reset_index(drop=True)
    grouped = industry_daily.groupby("industry", sort=False)
    industry_daily["industry_nav"] = grouped["industry_return"].transform(lambda series: (1 + series).cumprod())
    industry_daily["industry_low_20d"] = grouped["industry_nav"].transform(lambda series: series.rolling(20, min_periods=10).min())
    industry_daily["industry_ma20"] = grouped["industry_nav"].transform(lambda series: series.rolling(20, min_periods=10).mean())
    industry_daily["industry_ma20_slope_5d"] = industry_daily["industry_ma20"] / grouped["industry_ma20"].shift(5) - 1
    industry_daily["industry_rebound_20d"] = industry_daily["industry_nav"] / industry_daily["industry_low_20d"] - 1
    industry_daily["industry_stopfall"] = (industry_daily["industry_rebound_20d"] >= 0.03) & (industry_daily["industry_ma20_slope_5d"] > 0)
    return industry_daily[
        [
            "industry",
            "trade_date",
            "industry_nav",
            "industry_rebound_20d",
            "industry_ma20_slope_5d",
            "industry_stopfall",
        ]
    ]


def detect_value_sector_signals(
    panel: pd.DataFrame,
    min_undervalued_days: int = 45,
    lookback_days: int = 60,
    max_per_day: int = 20,
    min_amount: float = 50_000,
    min_total_mv: float = 300_000,
) -> pd.DataFrame:
    if panel.empty:
        return empty_signals()

    frame = normalize_frame(panel).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("ts_code", sort=False)
    undervalued = (
        (frame["pe_ttm"] > 0)
        & (frame["pe_ttm"] <= 15)
        & (frame["pb"] > 0)
        & (frame["pb"] <= 1.5)
    )
    frame["undervalued_days_60"] = (
        undervalued.astype(int)
        .groupby(frame["ts_code"], sort=False)
        .rolling(lookback_days, min_periods=lookback_days)
        .sum()
        .reset_index(level=0, drop=True)
    )
    if "amount_ma20" not in frame.columns:
        frame["amount_ma20"] = grouped["amount"].transform(lambda series: series.rolling(20, min_periods=10).mean())

    quality = (
        (frame["roe"] >= 8)
        & (frame["netprofit_margin"] > 0)
        & (frame["debt_to_assets"] <= 70)
        & (frame["tr_yoy"] >= -10)
        & (frame["netprofit_yoy"] >= -20)
    )
    tradable = (frame["amount_ma20"] >= min_amount) & (frame["total_mv"] >= min_total_mv)
    candidate = (
        (frame["undervalued_days_60"] >= min_undervalued_days)
        & quality
        & tradable
        & frame["industry_stopfall"].fillna(False).astype(bool)
    )

    selected = frame[candidate].copy()
    if selected.empty:
        return empty_signals()
    selected["value_quality_score"] = (
        (15 - selected["pe_ttm"]).clip(lower=0) / 15
        + (1.5 - selected["pb"]).clip(lower=0) / 1.5
        + selected["roe"].clip(lower=0, upper=25) / 25
        + selected["netprofit_margin"].clip(lower=0, upper=30) / 30
        + selected["industry_rebound_20d"].clip(lower=0, upper=0.2)
        + selected["industry_ma20_slope_5d"].clip(lower=0, upper=0.1)
    )
    selected = (
        selected.sort_values(["trade_date", "value_quality_score", "ts_code"], ascending=[True, False, True])
        .groupby("trade_date", sort=False)
        .head(max_per_day)
        .copy()
    )
    selected["rule"] = RULE_VALUE_SECTOR_STOPFALL
    selected = selected.rename(columns={"trade_date": "signal_date", "value_quality_score": "score"})
    columns = [
        "ts_code",
        "industry",
        "signal_date",
        "rule",
        "score",
        "pe_ttm",
        "pb",
        "total_mv",
        "amount_ma20",
        "roe",
        "netprofit_margin",
        "debt_to_assets",
        "tr_yoy",
        "netprofit_yoy",
        "undervalued_days_60",
        "industry_rebound_20d",
        "industry_ma20_slope_5d",
    ]
    return selected[columns].sort_values(["signal_date", "score", "ts_code"], ascending=[True, False, True]).reset_index(drop=True)


def attach_value_forward_returns(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_VALUE_HORIZONS,
    round_trip_cost_rate: float = 0.0022,
) -> pd.DataFrame:
    if prices.empty or signals.empty:
        return pd.DataFrame()

    frame = normalize_frame(prices).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
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
        event = signal._asdict()
        event["entry_date"] = entry["trade_date"]
        event["entry_open"] = entry["open"]
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
            event[f"return_{horizon}d"] = float(exit_row["close"]) / float(entry["open"]) - 1 - round_trip_cost_rate
        events.append(event)
    return pd.DataFrame(events)


def summarize_forward_returns(events: pd.DataFrame, horizons: tuple[int, ...] = DEFAULT_VALUE_HORIZONS) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows = []
    for horizon in horizons:
        returns = pd.to_numeric(events[f"return_{horizon}d"], errors="coerce").dropna()
        if returns.empty:
            rows.append({"horizon": horizon, "event_count": 0})
            continue
        rows.append(
            {
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


def simulate_rebalanced_account(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    initial_cash: float = 1_000_000,
    rebalance_interval: int = 60,
    max_positions: int = 20,
    round_trip_cost_rate: float = 0.0022,
) -> pd.DataFrame:
    frame = normalize_frame(prices).sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame()
    signal_frame = signals.copy()
    if not signal_frame.empty:
        signal_frame["signal_date"] = pd.to_datetime(signal_frame["signal_date"])
    trade_dates = sorted(frame["trade_date"].dropna().unique())
    close_map = {(row.ts_code, row.trade_date): float(row.close) for row in frame.itertuples(index=False)}
    open_map = {(row.ts_code, row.trade_date): float(row.open) for row in frame.itertuples(index=False)}

    cash = float(initial_cash)
    positions: dict[str, float] = {}
    last_close_by_symbol: dict[str, float] = {}
    nav_rows = []
    next_rebalance_index = 0
    for index, trade_date in enumerate(trade_dates):
        day_rows = frame[frame["trade_date"] == trade_date]
        for row in day_rows.itertuples(index=False):
            if pd.notna(row.close):
                last_close_by_symbol[row.ts_code] = float(row.close)
        if index >= next_rebalance_index:
            sell_value = sum(shares * latest_close(symbol, trade_date, close_map, last_close_by_symbol) for symbol, shares in positions.items())
            cash += sell_value * (1 - round_trip_cost_rate / 2)
            positions = {}
            selected = select_rebalance_symbols(signal_frame, trade_date, max_positions)
            if selected:
                budget = cash / len(selected) / (1 + round_trip_cost_rate / 2)
                for symbol in selected:
                    open_price = open_map.get((symbol, trade_date))
                    if not open_price or open_price <= 0:
                        continue
                    shares = budget / open_price
                    cash -= shares * open_price * (1 + round_trip_cost_rate / 2)
                    positions[symbol] = shares
            next_rebalance_index = index + rebalance_interval if positions else index + 1

        equity = cash + sum(shares * latest_close(symbol, trade_date, close_map, last_close_by_symbol) for symbol, shares in positions.items())
        nav_rows.append(
            {
                "trade_date": trade_date,
                "equity": equity,
                "nav": equity / initial_cash,
                "cash": cash,
                "position_count": len(positions),
                "selected_count": len(positions),
            }
        )
    return pd.DataFrame(nav_rows)


def latest_close(
    symbol: str,
    trade_date: pd.Timestamp,
    close_map: dict[tuple[str, pd.Timestamp], float],
    last_close_by_symbol: dict[str, float],
) -> float:
    value = close_map.get((symbol, trade_date))
    if value is not None:
        return value
    return last_close_by_symbol.get(symbol, 0.0)


def select_rebalance_symbols(signals: pd.DataFrame, trade_date: pd.Timestamp, max_positions: int) -> list[str]:
    if signals.empty:
        return []
    eligible = signals[signals["signal_date"] < trade_date].copy()
    if eligible.empty:
        return []
    latest = eligible.sort_values(["signal_date", "score"], ascending=[False, False]).drop_duplicates("ts_code")
    selected = latest.sort_values(["score", "signal_date"], ascending=[False, False]).head(max_positions)
    return list(selected["ts_code"])


def empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts_code", "industry", "signal_date", "rule", "score"])
