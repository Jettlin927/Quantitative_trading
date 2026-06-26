from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd


Action = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class KronosSlopeConfig:
    price_column: str = "median"
    downside_column: str = "p10"
    horizon_days: int = 20
    buy_daily_log_slope: float = 0.001
    sell_daily_log_slope: float = -0.001
    min_buy_horizon_return: float = 0.03
    min_buy_downside_return: float = -0.03


@dataclass(frozen=True)
class KronosSlopeSignal:
    action: Action
    daily_log_slope: float
    horizon_days: int
    horizon_return: float
    downside_return: float | None
    reason: str


def evaluate_kronos_slope_signal(
    stats: pd.DataFrame,
    last_close: float,
    config: KronosSlopeConfig | None = None,
) -> KronosSlopeSignal:
    cfg = config or KronosSlopeConfig()
    if last_close <= 0:
        raise ValueError("last_close must be positive")
    if cfg.price_column not in stats.columns:
        raise ValueError(f"Missing Kronos forecast column: {cfg.price_column}")

    prices = pd.to_numeric(stats[cfg.price_column], errors="coerce").dropna().reset_index(drop=True)
    if len(prices) < 2:
        raise ValueError("At least two forecast prices are required")
    if (prices <= 0).any():
        raise ValueError("Forecast prices must be positive")

    log_returns = [math.log(float(price) / last_close) for price in prices]
    daily_log_slope = _linear_slope(log_returns)
    horizon_pos = min(cfg.horizon_days, len(prices)) - 1
    horizon_return = float(prices.iloc[horizon_pos] / last_close - 1.0)
    downside_return = _horizon_return(stats, cfg.downside_column, last_close, horizon_pos)

    if daily_log_slope > cfg.buy_daily_log_slope:
        if horizon_return >= cfg.min_buy_horizon_return and (
            downside_return is None or downside_return >= cfg.min_buy_downside_return
        ):
            return KronosSlopeSignal(
                action="buy",
                daily_log_slope=daily_log_slope,
                horizon_days=horizon_pos + 1,
                horizon_return=horizon_return,
                downside_return=downside_return,
                reason="positive_slope_and_buy_filters_passed",
            )
        return KronosSlopeSignal(
            action="hold",
            daily_log_slope=daily_log_slope,
            horizon_days=horizon_pos + 1,
            horizon_return=horizon_return,
            downside_return=downside_return,
            reason="positive_slope_but_buy_filters_failed",
        )

    if daily_log_slope < cfg.sell_daily_log_slope:
        return KronosSlopeSignal(
            action="sell",
            daily_log_slope=daily_log_slope,
            horizon_days=horizon_pos + 1,
            horizon_return=horizon_return,
            downside_return=downside_return,
            reason="negative_slope_below_sell_threshold",
        )

    return KronosSlopeSignal(
        action="hold",
        daily_log_slope=daily_log_slope,
        horizon_days=horizon_pos + 1,
        horizon_return=horizon_return,
        downside_return=downside_return,
        reason="slope_inside_no_trade_band",
    )


def _linear_slope(values: list[float]) -> float:
    n = len(values)
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator


def _horizon_return(stats: pd.DataFrame, column: str, last_close: float, horizon_pos: int) -> float | None:
    if column not in stats.columns:
        return None
    values = pd.to_numeric(stats[column], errors="coerce").dropna().reset_index(drop=True)
    if len(values) <= horizon_pos:
        return None
    value = float(values.iloc[horizon_pos])
    if value <= 0:
        return None
    return value / last_close - 1.0
