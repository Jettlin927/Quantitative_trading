from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
from typing import Any, Mapping

import pandas as pd

from .metrics import summarize_execution_metrics
from .reporting import (
    returns_from_initial_nav,
    summarize_nav_window,
    summarize_return_subperiod,
    tail_metrics,
)
from .run_config import (
    canonical_sha256,
    validate_evaluation_policy,
    validate_evaluation_sample_splits,
)


OOS_METRICS_SCHEMA_VERSION = "research-oos-metrics/v1"
STRESSED_COST_FIELDS = ("buyRate", "sellRate", "slippageRate")


def build_cost_stress_config(config: Mapping[str, Any]) -> dict[str, Any]:
    policy = validate_evaluation_policy(config.get("evaluationPolicy"))
    multiplier = Decimal(policy["costStressMultiplier"])
    stressed = deepcopy(dict(config))
    costs = stressed.get("costModel")
    if not isinstance(costs, dict) or set(costs) != set(STRESSED_COST_FIELDS):
        raise ValueError("成本压力只支持 buyRate/sellRate/slippageRate 固定成本模型")
    stressed["costModel"] = {
        field: format(Decimal(str(costs[field])) * multiplier, "f")
        for field in STRESSED_COST_FIELDS
    }
    return stressed


def build_oos_metrics(
    config: Mapping[str, Any],
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
    stressed_nav: pd.DataFrame,
    *,
    walk_forward: Mapping[str, Any] | None,
) -> dict[str, Any]:
    splits_value = config.get("evaluationSampleSplits")
    policy_value = config.get("evaluationPolicy")
    if splits_value is None and policy_value is None:
        return {
            "schemaVersion": OOS_METRICS_SCHEMA_VERSION,
            "status": "not_available",
            "reason": "该直接运行未绑定冻结研究计划的 train/validation/test_oos 边界",
        }
    splits = validate_evaluation_sample_splits(
        splits_value,
        start_date=str(config["startDate"]),
        end_date=str(config["endDate"]),
    )
    policy = validate_evaluation_policy(policy_value)
    test_split = next(item for item in splits if item["role"] == "test_oos")
    start = pd.Timestamp(test_split["startDate"])
    end = pd.Timestamp(test_split["endDate"])
    normalized_nav = _normalized_nav(nav, "策略")
    normalized_benchmark = _normalized_nav(benchmark_nav, "基准")
    core = summarize_nav_window(
        normalized_nav,
        start=start,
        end=end,
        benchmark_nav=normalized_benchmark,
        include_extended=True,
    )
    oos_dates = _window_dates(normalized_nav, start, end)
    execution = _execution_metrics_for_dates(
        normalized_nav,
        requests,
        executions,
        positions,
        oos_dates,
    )
    strategy_returns, benchmark_returns = _aligned_window_returns(
        normalized_nav,
        normalized_benchmark,
        start,
        end,
    )
    tails = {
        key: _finite_or_none(value)
        for key, value in tail_metrics(strategy_returns).items()
    }
    stressed_core = summarize_nav_window(
        _normalized_nav(stressed_nav, "成本压力策略"),
        start=start,
        end=end,
        benchmark_nav=normalized_benchmark,
        include_extended=True,
    )
    return {
        "schemaVersion": OOS_METRICS_SCHEMA_VERSION,
        "status": "complete",
        "sampleRole": "test_oos",
        "sampleStartDate": test_split["startDate"],
        "sampleEndDate": test_split["endDate"],
        "sampleSplitSha256": canonical_sha256(splits),
        "evaluationPolicy": policy,
        "evaluationPolicySha256": canonical_sha256(policy),
        **core,
        **execution,
        **tails,
        "yearly": _yearly_metrics(
            normalized_nav,
            normalized_benchmark,
            requests,
            executions,
            positions,
            start,
            end,
        ),
        "marketRegimes": _market_regime_metrics(
            normalized_nav,
            normalized_benchmark,
            requests,
            executions,
            positions,
            start,
            end,
            policy["marketRegime"],
        ),
        "walkForward": dict(walk_forward) if walk_forward is not None else None,
        "costStress": {
            "multiplier": policy["costStressMultiplier"],
            "baseTotalReturn": core["totalReturn"],
            "stressedTotalReturn": stressed_core["totalReturn"],
            "returnDifference": float(
                stressed_core["totalReturn"] - core["totalReturn"]
            ),
        },
    }


def validate_oos_metrics_contract(
    metrics: Any,
    config: Mapping[str, Any],
) -> None:
    if not isinstance(metrics, dict) or metrics.get("schemaVersion") != OOS_METRICS_SCHEMA_VERSION:
        raise ValueError("OOS 指标 schema 无效")
    splits_value = config.get("evaluationSampleSplits")
    policy_value = config.get("evaluationPolicy")
    if splits_value is None and policy_value is None:
        if metrics != {
            "schemaVersion": OOS_METRICS_SCHEMA_VERSION,
            "status": "not_available",
            "reason": "该直接运行未绑定冻结研究计划的 train/validation/test_oos 边界",
        }:
            raise ValueError("未绑定研究计划的 OOS 指标合同无效")
        return
    splits = validate_evaluation_sample_splits(
        splits_value,
        start_date=str(config["startDate"]),
        end_date=str(config["endDate"]),
    )
    policy = validate_evaluation_policy(policy_value)
    test_split = next(item for item in splits if item["role"] == "test_oos")
    required = {
        "startDate",
        "endDate",
        "observations",
        "totalReturn",
        "annualizedVolatility",
        "maxDrawdown",
        "benchmarkTotalReturn",
        "averageOneWayTurnover",
        "cumulativeTransactionCostRate",
        "blockedRequestRate",
        "maxSingleWeight",
        "var95",
        "es95",
        "yearly",
        "marketRegimes",
        "walkForward",
        "costStress",
    }
    if (
        metrics.get("status") != "complete"
        or metrics.get("sampleRole") != "test_oos"
        or metrics.get("sampleStartDate") != test_split["startDate"]
        or metrics.get("sampleEndDate") != test_split["endDate"]
        or metrics.get("sampleSplitSha256") != canonical_sha256(splits)
        or metrics.get("evaluationPolicy") != policy
        or metrics.get("evaluationPolicySha256") != canonical_sha256(policy)
    ):
        raise ValueError("OOS 指标与冻结计划边界或评价策略不一致")
    missing = sorted(required - set(metrics))
    if missing:
        raise ValueError("OOS 指标缺少字段：" + ", ".join(missing))
    if not isinstance(metrics["yearly"], dict) or not metrics["yearly"]:
        raise ValueError("OOS 指标缺少逐年拆分")
    regimes = metrics["marketRegimes"]
    if not isinstance(regimes, dict) or regimes.get("policy") != policy["marketRegime"]:
        raise ValueError("OOS 市场环境拆分未绑定冻结策略")
    stress = metrics["costStress"]
    if not isinstance(stress, dict) or stress.get("multiplier") != policy["costStressMultiplier"]:
        raise ValueError("OOS 成本压力未绑定冻结倍数")
    if metrics["walkForward"] is None:
        raise ValueError("OOS 指标缺少 walk-forward 证据")


def _yearly_metrics(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    dates = _window_dates(nav, start, end)
    result: dict[str, Any] = {}
    for year in sorted({int(value.year) for value in dates}):
        year_dates = dates[dates.year == year]
        year_start = year_dates.min()
        year_end = year_dates.max()
        result[str(year)] = {
            **summarize_nav_window(
                nav,
                start=year_start,
                end=year_end,
                benchmark_nav=benchmark_nav,
                include_extended=True,
            ),
            **_execution_metrics_for_dates(
                nav,
                requests,
                executions,
                positions,
                year_dates,
            ),
        }
    return result


def _market_regime_metrics(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    benchmark = benchmark_nav.set_index("trade_date")["nav"].astype(float)
    direction_lookback = int(policy["directionLookbackPeriods"])
    volatility_lookback = int(policy["volatilityLookbackPeriods"])
    prior_nav = benchmark.shift(1)
    direction_return = prior_nav / prior_nav.shift(direction_lookback) - 1.0
    annualized_volatility = (
        benchmark.pct_change(fill_method=None)
        .shift(1)
        .rolling(volatility_lookback, min_periods=volatility_lookback)
        .std(ddof=1)
        * math.sqrt(252)
    )
    strategy_returns, benchmark_returns = _aligned_window_returns(
        nav, benchmark_nav, start, end
    )
    frame = pd.concat(
        [
            strategy_returns.rename("strategy_return"),
            benchmark_returns.rename("benchmark_return"),
            direction_return.rename("direction_return"),
            annualized_volatility.rename("annualized_volatility"),
        ],
        axis=1,
        join="inner",
    )
    if frame.empty or frame[["direction_return", "annualized_volatility"]].isna().any().any():
        raise ValueError("冻结历史不足以在 OOS 开始前形成市场环境标签")
    up = float(policy["upThreshold"])
    down = float(policy["downThreshold"])
    high_volatility = float(policy["highVolatilityThreshold"])
    frame["direction"] = "震荡"
    frame.loc[frame["direction_return"] > up, "direction"] = "上涨"
    frame.loc[frame["direction_return"] < down, "direction"] = "下跌"
    frame["volatility"] = "低波"
    frame.loc[
        frame["annualized_volatility"] >= high_volatility, "volatility"
    ] = "高波"
    frame["cell"] = frame["direction"] + "_" + frame["volatility"]
    cells: dict[str, Any] = {}
    for direction in ("上涨", "下跌", "震荡"):
        for volatility in ("高波", "低波"):
            name = f"{direction}_{volatility}"
            subset = frame[frame["cell"] == name]
            if subset.empty:
                cells[name] = {
                    "status": "not_available",
                    "reason": "冻结 test/OOS 未覆盖该市场环境",
                    "observations": 0,
                }
                continue
            dates = pd.DatetimeIndex(subset.index)
            cells[name] = {
                "status": "available",
                "startDate": dates.min().date().isoformat(),
                "endDate": dates.max().date().isoformat(),
                "observations": int(len(subset)),
                **summarize_return_subperiod(
                    subset["strategy_return"], subset["benchmark_return"]
                ),
                **_execution_metrics_for_dates(
                    nav,
                    requests,
                    executions,
                    positions,
                    dates,
                ),
            }
    return {
        "policy": dict(policy),
        "classificationUsesBenchmarkThroughPreviousDay": True,
        "coverage": {
            "observations": int(len(frame)),
            "directionStates": sorted(set(frame["direction"])),
            "volatilityStates": sorted(set(frame["volatility"])),
        },
        "cells": cells,
    }


def _execution_metrics_for_dates(
    nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
    dates: pd.DatetimeIndex,
) -> dict[str, Any]:
    date_values = set(pd.DatetimeIndex(dates))
    nav_frame = nav[nav["trade_date"].isin(date_values)].copy()
    request_frame = requests.copy()
    execution_frame = executions.copy()
    position_frame = positions.copy()
    request_frame["execution_date"] = pd.to_datetime(
        request_frame["execution_date"], errors="raise"
    )
    execution_frame["execution_date"] = pd.to_datetime(
        execution_frame["execution_date"], errors="raise"
    )
    position_frame["trade_date"] = pd.to_datetime(
        position_frame["trade_date"], errors="raise"
    )
    return summarize_execution_metrics(
        nav_frame,
        request_frame[request_frame["execution_date"].isin(date_values)],
        execution_frame[execution_frame["execution_date"].isin(date_values)],
        position_frame[position_frame["trade_date"].isin(date_values)],
    )


def _aligned_window_returns(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.Series, pd.Series]:
    strategy_window = nav[nav["trade_date"].between(start, end)].set_index(
        "trade_date"
    )["nav"]
    benchmark_window = benchmark_nav[
        benchmark_nav["trade_date"].between(start, end)
    ].set_index("trade_date")["nav"]
    strategy_prior = nav[nav["trade_date"] < start]
    benchmark_prior = benchmark_nav[benchmark_nav["trade_date"] < start]
    if strategy_prior.empty or benchmark_prior.empty:
        raise ValueError("test/OOS 边界前缺少策略或基准初始净值")
    aligned = pd.concat(
        [strategy_window.rename("strategy"), benchmark_window.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    if aligned.empty or len(aligned) != len(strategy_window) or len(aligned) != len(benchmark_window):
        raise ValueError("test/OOS 策略与基准交易日不完全一致")
    return (
        returns_from_initial_nav(
            aligned["strategy"], initial_nav=float(strategy_prior.iloc[-1]["nav"])
        ),
        returns_from_initial_nav(
            aligned["benchmark"], initial_nav=float(benchmark_prior.iloc[-1]["nav"])
        ),
    )


def _normalized_nav(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if not {"trade_date", "nav"}.issubset(frame.columns):
        raise ValueError(f"{label}净值缺少 trade_date 或 nav")
    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    result["nav"] = pd.to_numeric(result["nav"], errors="raise")
    result = result.sort_values("trade_date", kind="stable").reset_index(drop=True)
    if (
        result.empty
        or result["trade_date"].duplicated().any()
        or not result["nav"].map(math.isfinite).all()
        or (result["nav"] <= 0).any()
    ):
        raise ValueError(f"{label}净值为空、重复或包含非有限正数")
    return result


def _window_dates(
    nav: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(nav.loc[nav["trade_date"].between(start, end), "trade_date"])
    if dates.empty:
        raise ValueError("test/OOS 交易日为空")
    return dates


def _finite_or_none(value: Any) -> float | None:
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None
