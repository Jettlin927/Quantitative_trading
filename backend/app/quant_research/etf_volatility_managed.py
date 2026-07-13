from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .baselines import (
    load_frozen_calendar,
    open_strategy_inputs,
    simulate_etf_targets_with_ledger,
    validate_explicit_universe,
)
from .dataset import build_adjusted_price_panel
from .metrics import summarize_performance


ETF_VOLATILITY_MANAGED_LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "single_etf_inverse_realized_variance_exposure",
    "calibration_period_precedes_test_oos",
    "no_leverage_target_weight_capped_at_one",
    "monthly_close_signal_next_trade_open_execution",
    "cash_return_assumed_zero",
    "daily_data_only_no_intraday_volatility",
    "passive_adjusted_etf_is_primary_benchmark",
)

_ALLOWED_TRIALS = {
    ("previous_month", "1", "0"),
    ("previous_month", "0.5", "0"),
    ("trailing_3_month_mean", "1", "0"),
    ("previous_month", "1", "0.1"),
}


def validate_etf_volatility_managed_config(config: dict[str, Any]) -> None:
    if config.get("strategyId") != "etf_volatility_managed":
        raise ValueError("ETF 波动率管理策略 ID 必须是 etf_volatility_managed")
    if config.get("scope") != "etf_time_series":
        raise ValueError("ETF 波动率管理策略只允许 etf_time_series scope")
    universe = config.get("universe") or {}
    members = universe.get("members") or []
    if universe.get("mode") != "explicit_snapshot" or len(set(members)) != 1:
        raise ValueError("ETF 波动率管理策略只允许一只显式 ETF")

    features = config.get("featureParameters") or {}
    expected_feature_fields = {
        "calibrationStartDate",
        "calibrationEndDate",
        "realizedVarianceEstimator",
        "exposurePower",
    }
    if set(features) != expected_feature_fields:
        raise ValueError("ETF 波动率管理 featureParameters 字段无效")
    calibration_start = _date(features.get("calibrationStartDate"), "calibrationStartDate")
    calibration_end = _date(features.get("calibrationEndDate"), "calibrationEndDate")
    warmup_start = _date(config.get("warmupStart"), "warmupStart")
    research_start = _date(config.get("startDate"), "startDate")
    if not warmup_start <= calibration_start <= calibration_end < research_start:
        raise ValueError("ETF 波动率管理日期必须满足 warmup <= calibration <= calibrationEnd < startDate")

    targets = config.get("targetWeightParameters") or {}
    expected_target_fields = {"rebalanceFrequency", "maxWeight", "rebalanceBand"}
    if set(targets) != expected_target_fields:
        raise ValueError("ETF 波动率管理 targetWeightParameters 字段无效")
    if targets.get("rebalanceFrequency") != "month_end" or targets.get("maxWeight") != "1":
        raise ValueError("ETF 波动率管理只允许月末调仓和 100% 权重上限")
    trial = (
        features.get("realizedVarianceEstimator"),
        features.get("exposurePower"),
        targets.get("rebalanceBand"),
    )
    if trial not in _ALLOWED_TRIALS:
        raise ValueError("ETF 波动率管理配置不在预登记的四个试验内")

    expected_execution = {
        "calendarExchange": "SSE",
        "executionPrice": "next_trade_open",
        "signalPrice": "close",
    }
    if config.get("executionPolicy") != expected_execution:
        raise ValueError("ETF 波动率管理只允许月末收盘信号和下一开市日开盘执行")


def build_etf_volatility_managed_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    validate_etf_volatility_managed_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    members = validate_explicit_universe(reader, config, compressed)
    prices = _load_adjusted_prices(reader, config, members)
    monthly = _monthly_statistics(prices, config)
    scale, _ = _calibrate_scale(monthly, config)
    return _targets_from_monthly(monthly, calendar, config, scale)


def simulate_etf_volatility_managed_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[Any, Any]:
    validate_etf_volatility_managed_config(config)
    return simulate_etf_targets_with_ledger(
        input_root,
        config,
        targets,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )


def summarize_etf_volatility_managed_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_etf_volatility_managed_config(config)
    _, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    members = tuple(sorted(set(config["universe"]["members"])))
    prices = _load_adjusted_prices(reader, config, members)
    monthly = _monthly_statistics(prices, config)
    scale, calibration_observations = _calibrate_scale(monthly, config)

    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    strategy_nav = nav.copy()
    strategy_nav["trade_date"] = pd.to_datetime(strategy_nav["trade_date"])
    strategy_nav = strategy_nav[strategy_nav["trade_date"].between(research_start, end)]
    passive = prices[prices["trade_date"].between(research_start, end)][
        ["trade_date", "adj_close"]
    ].copy()
    if strategy_nav.empty or passive.empty:
        raise ValueError("ETF 波动率管理 OOS 策略或被动基准为空")
    passive["nav"] = passive["adj_close"] / passive["adj_close"].iloc[0]
    metrics = summarize_performance(
        strategy_nav[["trade_date", "nav"]],
        passive[["trade_date", "nav"]],
        include_extended=True,
    )
    targets = _targets_from_monthly(monthly, _CalendarProxy(prices), config, scale)
    weights = targets["target_weight"].astype(float)
    metrics.update(
        {
            "primaryBenchmarkType": "passive_adjusted_etf",
            "primaryBenchmarkCode": members[0],
            "marketReferenceCode": config["benchmark"],
            "calibratedVarianceScale": float(scale),
            "calibrationObservations": int(calibration_observations),
            "averageTargetWeight": float(weights.mean()),
            "medianTargetWeight": float(weights.median()),
            "minimumTargetWeight": float(weights.min()),
            "maximumTargetWeight": float(weights.max()),
            "exposureCapHitRate": float(weights.eq(1.0).mean()),
        }
    )
    return metrics


def etf_volatility_managed_limitations() -> list[str]:
    return list(ETF_VOLATILITY_MANAGED_LIMITATIONS)


def _load_adjusted_prices(
    reader: Any,
    config: dict[str, Any],
    members: tuple[str, ...],
) -> pd.DataFrame:
    bars = reader("fund_daily_bars")
    factors = reader("fund_adjust_factors")
    warmup_start = pd.Timestamp(config["warmupStart"])
    end = pd.Timestamp(config["endDate"])
    for frame in (bars, factors):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    bars = bars[
        bars["ts_code"].isin(members)
        & bars["trade_date"].between(warmup_start, end)
    ].copy()
    factors = factors[
        factors["ts_code"].isin(members)
        & factors["trade_date"].between(warmup_start, end)
    ].copy()
    if bars.empty or factors.empty:
        raise ValueError("ETF 波动率管理冻结行情或复权因子为空")
    prices = build_adjusted_price_panel(bars, factors).sort_values(
        ["ts_code", "trade_date"], kind="stable"
    ).reset_index(drop=True)
    if prices["ts_code"].nunique() != 1 or prices["adj_close"].isna().any():
        raise ValueError("ETF 波动率管理必须得到一只 ETF 的完整复权价格")
    return prices


def _monthly_statistics(prices: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    frame = prices[["trade_date", "adj_close"]].copy()
    frame["daily_return"] = frame["adj_close"].pct_change(fill_method=None)
    frame["month"] = frame["trade_date"].dt.to_period("M")
    rows: list[dict[str, object]] = []
    for month, group in frame.groupby("month", sort=True):
        returns = group["daily_return"].dropna()
        variance = float(((returns - returns.mean()) ** 2).sum()) if len(returns) >= 10 else None
        rows.append(
            {
                "month": month,
                "signal_date": group["trade_date"].iloc[-1],
                "month_end_close": float(group["adj_close"].iloc[-1]),
                "realized_variance": variance,
                "daily_observations": int(len(returns)),
            }
        )
    monthly = pd.DataFrame(rows).sort_values("signal_date", kind="stable").reset_index(drop=True)
    monthly["monthly_return"] = monthly["month_end_close"].pct_change(fill_method=None)
    estimator = config["featureParameters"]["realizedVarianceEstimator"]
    if estimator == "previous_month":
        monthly["variance_estimate"] = monthly["realized_variance"]
    elif estimator == "trailing_3_month_mean":
        monthly["variance_estimate"] = monthly["realized_variance"].rolling(
            3, min_periods=3
        ).mean()
    else:  # guarded by config validation
        raise ValueError("ETF 波动率管理 realizedVarianceEstimator 无效")
    return monthly


def _calibrate_scale(
    monthly: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[float, int]:
    features = config["featureParameters"]
    calibration_start = pd.Timestamp(features["calibrationStartDate"])
    calibration_end = pd.Timestamp(features["calibrationEndDate"])
    power = float(features["exposurePower"])
    frame = monthly.copy()
    frame["next_signal_date"] = frame["signal_date"].shift(-1)
    frame["next_month_return"] = frame["monthly_return"].shift(-1)
    frame = frame[
        frame["signal_date"].ge(calibration_start)
        & frame["next_signal_date"].le(calibration_end)
    ].dropna(subset=["variance_estimate", "next_month_return"])
    frame = frame[frame["variance_estimate"] > 0].copy()
    if len(frame) < 24:
        raise ValueError("ETF 波动率管理校准期有效月数不足 24")
    raw_managed = frame["next_month_return"] / (frame["variance_estimate"] ** power)
    base_volatility = float(frame["next_month_return"].std(ddof=1))
    raw_volatility = float(raw_managed.std(ddof=1))
    if not base_volatility > 0 or not raw_volatility > 0:
        raise ValueError("ETF 波动率管理校准波动率必须为正")
    return base_volatility / raw_volatility, len(frame)


def _targets_from_monthly(
    monthly: pd.DataFrame,
    calendar: Any,
    config: dict[str, Any],
    scale: float,
) -> pd.DataFrame:
    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    signal_dates = _month_end_signal_dates(calendar, research_start, end)
    selected = monthly[monthly["signal_date"].isin(signal_dates)].copy()
    if len(selected) != len(signal_dates) or selected["variance_estimate"].isna().any():
        raise ValueError("ETF 波动率管理月末缺少完整已实现方差")
    power = float(config["featureParameters"]["exposurePower"])
    max_weight = float(config["targetWeightParameters"]["maxWeight"])
    band = float(config["targetWeightParameters"]["rebalanceBand"])
    raw_weights = scale / (selected["variance_estimate"].astype(float) ** power)
    selected["target_weight"] = raw_weights.clip(lower=0.0, upper=max_weight)
    previous: float | None = None
    adjusted: list[float] = []
    for target in selected["target_weight"].astype(float):
        if previous is not None and abs(target - previous) < band:
            target = previous
        adjusted.append(target)
        previous = target
    selected["target_weight"] = adjusted
    selected["available_date"] = selected["signal_date"]
    selected["ts_code"] = config["universe"]["members"][0]
    return selected[
        ["signal_date", "available_date", "ts_code", "target_weight"]
    ].sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def _month_end_signal_dates(
    calendar: Any,
    research_start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    open_dates = pd.DatetimeIndex(pd.to_datetime(calendar.open_dates))
    candidates = [
        trade_date
        for index, trade_date in enumerate(open_dates[:-1])
        if research_start <= open_dates[index + 1] <= end
        and trade_date.to_period("M") != open_dates[index + 1].to_period("M")
    ]
    if not candidates:
        raise ValueError("ETF 波动率管理研究区间内没有可执行月末信号")
    return pd.DatetimeIndex(candidates)


def _date(value: object, label: str) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是有效日期") from exc
    if parsed.tzinfo is not None or parsed.date().isoformat() != str(value):
        raise ValueError(f"{label} 必须是 YYYY-MM-DD")
    return parsed


class _CalendarProxy:
    def __init__(self, prices: pd.DataFrame) -> None:
        self.open_dates = tuple(
            prices["trade_date"].dt.date.astype(str).drop_duplicates().tolist()
        )
