from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .baselines import (
    load_frozen_calendar,
    open_strategy_inputs,
    validate_explicit_universe,
)
from .dataset import build_adjusted_price_panel
from .features import moving_average


ETF_TREND_LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "single_etf_daily_trend_rule",
    "fixed_120_open_day_moving_average",
    "fixed_month_end_rebalance_no_parameter_search",
    "next_trade_open_execution",
    "risk_free_rate_assumed_zero",
    "no_minute_options_or_financial_cross_section",
)


def validate_etf_trend_config(config: dict[str, Any]) -> None:
    if config.get("strategyId") != "etf_trend_120d":
        raise ValueError("ETF 趋势策略 ID 必须是 etf_trend_120d")
    if config.get("scope") != "etf_time_series":
        raise ValueError("ETF 趋势策略只允许 etf_time_series scope")
    if config.get("featureParameters") != {"movingAverageWindow": 120}:
        raise ValueError("ETF 趋势 featureParameters 只允许固定 120 日均线")
    expected_targets = {
        "rebalanceFrequency": "month_end",
        "riskOnWeight": "1",
        "riskOffWeight": "0",
    }
    if config.get("targetWeightParameters") != expected_targets:
        raise ValueError("ETF 趋势 targetWeightParameters 必须是固定月末 1/0 权重")
    if len(set(config["universe"]["members"])) != 1:
        raise ValueError("ETF 趋势策略只允许一只显式 ETF")


def build_etf_trend_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    validate_etf_trend_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    members = validate_explicit_universe(reader, config, compressed)
    bars = reader("fund_daily_bars")
    factors = reader("fund_adjust_factors")
    warmup_start = pd.Timestamp(config["warmupStart"])
    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    for frame in (bars, factors):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    bars = bars[
        bars["ts_code"].isin(members)
        & bars["trade_date"].between(warmup_start, end)
    ].copy()
    factors = factors[
        factors["ts_code"].isin(members)
        & factors["trade_date"].between(warmup_start, end)
    ].copy()
    if bars.empty or factors.empty:
        raise ValueError("ETF 趋势冻结行情或复权因子为空")
    prices = build_adjusted_price_panel(bars, factors).sort_values(
        ["ts_code", "trade_date"], kind="stable"
    ).reset_index(drop=True)
    average = moving_average(
        prices[["trade_date", "ts_code", "adj_close"]],
        "adj_close",
        window=120,
        min_periods=120,
        output_column="moving_average_120",
    )
    features = prices[["trade_date", "ts_code", "adj_close"]].merge(
        average,
        on=["trade_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )
    signal_dates = _month_end_signal_dates(calendar, research_start, end)
    selected = features[features["trade_date"].isin(signal_dates)].copy()
    if len(selected) != len(signal_dates):
        raise ValueError("ETF 趋势月末信号缺少冻结收盘价")
    if selected["moving_average_120"].isna().any():
        raise ValueError("ETF 趋势 warmup 不足 120 个开市日")
    selected["signal_date"] = selected["trade_date"]
    selected["available_date"] = selected["trade_date"]
    selected["target_weight"] = (
        selected["adj_close"] > selected["moving_average_120"]
    ).astype(float)
    return selected[
        ["signal_date", "available_date", "ts_code", "target_weight"]
    ].sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def etf_trend_limitations() -> list[str]:
    return list(ETF_TREND_LIMITATIONS)


def _month_end_signal_dates(
    calendar: Any,
    research_start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    open_dates = pd.DatetimeIndex(pd.to_datetime(calendar.open_dates))
    candidates: list[pd.Timestamp] = []
    for index, trade_date in enumerate(open_dates[:-1]):
        next_date = open_dates[index + 1]
        if research_start <= trade_date < end and trade_date.to_period("M") != next_date.to_period("M"):
            candidates.append(trade_date)
    if not candidates:
        raise ValueError("研究区间内没有可执行的完整月末信号日")
    return pd.DatetimeIndex(candidates)
