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
    validate_research_pass_policy,
)


OOS_METRICS_SCHEMA_VERSION = "research-oos-metrics/v2"
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


def build_capacity_evidence(
    config: Mapping[str, Any],
    requests: pd.DataFrame,
    market_bars: pd.DataFrame,
    *,
    dates: pd.DatetimeIndex,
) -> dict[str, Any]:
    pass_policy = config.get("researchPassPolicy")
    if pass_policy is None:
        return {
            "status": "not_available",
            "reason": "运行配置未冻结容量策略",
        }
    policy = validate_research_pass_policy(pass_policy, config)["capacity"]
    required_requests = {"execution_date", "ts_code", "requested_change"}
    required_bars = {"trade_date", "ts_code", "amount"}
    if not required_requests.issubset(requests.columns) or not required_bars.issubset(
        market_bars.columns
    ):
        raise ValueError("容量证据缺少请求或市场成交额字段")
    date_values = set(pd.DatetimeIndex(dates))
    request_frame = requests.loc[:, sorted(required_requests)].copy()
    request_frame["execution_date"] = pd.to_datetime(
        request_frame["execution_date"], errors="raise"
    )
    request_frame["ts_code"] = request_frame["ts_code"].astype(str).str.upper()
    request_frame["requested_change"] = pd.to_numeric(
        request_frame["requested_change"], errors="raise"
    ).abs()
    request_frame = request_frame[
        request_frame["execution_date"].isin(date_values)
        & request_frame["requested_change"].gt(1e-12)
    ].sort_values(["execution_date", "ts_code"], kind="stable")
    if request_frame.empty:
        return {
            "status": "not_available",
            "policySha256": canonical_sha256(policy),
            "reason": "冻结 test/OOS 没有可用于容量估算的调仓请求",
        }
    bars = market_bars.loc[:, sorted(required_bars)].copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="raise")
    bars["ts_code"] = bars["ts_code"].astype(str).str.upper()
    bars["amount"] = pd.to_numeric(bars["amount"], errors="coerce")
    bars = bars.sort_values(["ts_code", "trade_date"], kind="stable")
    valid_amounts = bars["amount"].dropna()
    if (
        bars.duplicated(["ts_code", "trade_date"]).any()
        or (valid_amounts <= 0).any()
        or not valid_amounts.map(math.isfinite).all()
    ):
        raise ValueError("容量市场成交额必须按标的/日期唯一且为正数")
    expected_capital = float(policy["expectedCapital"])
    amount_scale = float(policy["marketAmountScale"])
    lookback = int(policy["advLookbackPeriods"])
    minimum = int(policy["minimumAdvObservations"])
    coefficient = float(policy["impactModel"]["coefficient"])
    observations: list[dict[str, Any]] = []
    uncovered: list[str] = []
    for request in request_frame.itertuples(index=False):
        history = bars[
            bars["ts_code"].eq(request.ts_code)
            & bars["trade_date"].lt(request.execution_date)
        ].tail(lookback).dropna(subset=["amount"])
        if len(history) < minimum:
            uncovered.append(
                f"{request.execution_date.date().isoformat()}:{request.ts_code}"
            )
            continue
        adv = float(history["amount"].mean()) * amount_scale
        participation = expected_capital * float(request.requested_change) / adv
        modeled_impact = coefficient * participation
        if not math.isfinite(participation) or not math.isfinite(modeled_impact):
            raise ValueError("容量参与率或冲击率不是有限数")
        observations.append(
            {
                "executionDate": request.execution_date.date().isoformat(),
                "tsCode": request.ts_code,
                "advObservations": int(len(history)),
                "requestedChange": float(request.requested_change),
                "advAmount": adv,
                "participationRate": participation,
                "modeledImpactRate": modeled_impact,
            }
        )
    if uncovered:
        return {
            "status": "not_available",
            "policySha256": canonical_sha256(policy),
            "reason": "部分 OOS 请求缺少事前 ADV 历史：" + ", ".join(uncovered[:10]),
            "requestCount": int(len(request_frame)),
            "coveredRequestCount": int(len(observations)),
        }
    rates = pd.Series(
        [item["participationRate"] for item in observations], dtype=float
    )
    impacts = pd.Series(
        [item["modeledImpactRate"] for item in observations], dtype=float
    )
    maximum_participation = float(policy["maximumAdvParticipationRate"])
    maximum_impact = float(policy["maximumModeledImpactRate"])
    return {
        "status": "complete",
        "policySha256": canonical_sha256(policy),
        "expectedCapital": policy["expectedCapital"],
        "advLookbackPeriods": lookback,
        "minimumAdvObservations": minimum,
        "marketAmountScale": policy["marketAmountScale"],
        "maximumAllowedAdvParticipationRate": policy[
            "maximumAdvParticipationRate"
        ],
        "impactModel": policy["impactModel"],
        "maximumAllowedModeledImpactRate": policy[
            "maximumModeledImpactRate"
        ],
        "requestCount": int(len(request_frame)),
        "coveredRequestCount": int(len(observations)),
        "medianAdvParticipationRate": float(rates.median()),
        "p95AdvParticipationRate": float(rates.quantile(0.95)),
        "maxAdvParticipationRate": float(rates.max()),
        "maxModeledImpactRate": float(impacts.max()),
        "passed": bool(
            float(rates.max()) <= maximum_participation
            and float(impacts.max()) <= maximum_impact
        ),
        "observations": observations,
    }


def build_risk_summary(
    exposures: pd.DataFrame | None,
    contributions: pd.DataFrame | None,
    *,
    dates: pd.DatetimeIndex,
) -> dict[str, Any]:
    if exposures is None or contributions is None:
        return {
            "status": "not_available",
            "reason": "运行未生成冻结风险暴露与风险贡献工件",
        }
    required_exposures = {
        "trade_date",
        "gross_exposure",
        "net_exposure",
        "cash_weight",
        "max_weight",
        "hhi",
    }
    required_contributions = {
        "trade_date",
        "ts_code",
        "close_weight",
        "total_risk_contribution",
        "portfolio_volatility",
    }
    if not required_exposures.issubset(
        exposures.columns
    ) or not required_contributions.issubset(contributions.columns):
        raise ValueError("风险汇总缺少暴露或风险贡献字段")
    date_values = set(pd.DatetimeIndex(dates))
    exposure_frame = exposures.copy()
    contribution_frame = contributions.copy()
    exposure_frame["trade_date"] = pd.to_datetime(
        exposure_frame["trade_date"], errors="raise"
    )
    contribution_frame["trade_date"] = pd.to_datetime(
        contribution_frame["trade_date"], errors="raise"
    )
    exposure_frame = exposure_frame[
        exposure_frame["trade_date"].isin(date_values)
    ].sort_values("trade_date", kind="stable")
    contribution_frame = contribution_frame[
        contribution_frame["trade_date"].isin(date_values)
    ].sort_values(["trade_date", "ts_code"], kind="stable")
    if exposure_frame.empty or len(exposure_frame) != len(date_values):
        raise ValueError("风险暴露未完整覆盖冻结 test/OOS 日期")
    numeric_exposure = (
        "gross_exposure",
        "net_exposure",
        "cash_weight",
        "max_weight",
        "hhi",
    )
    for column in numeric_exposure:
        exposure_frame[column] = pd.to_numeric(
            exposure_frame[column], errors="raise"
        )
    for column in (
        "close_weight",
        "total_risk_contribution",
        "portfolio_volatility",
    ):
        contribution_frame[column] = pd.to_numeric(
            contribution_frame[column], errors="coerce"
        )
    volatility = contribution_frame.dropna(subset=["portfolio_volatility"])[
        ["trade_date", "portfolio_volatility"]
    ].drop_duplicates("trade_date")
    contribution_available = contribution_frame.dropna(
        subset=["total_risk_contribution", "portfolio_volatility"]
    )
    ending_contributions: list[dict[str, Any]] = []
    ending_volatility: float | None = None
    risk_contribution_end_date: str | None = None
    if not contribution_available.empty:
        ending_date = contribution_available["trade_date"].max()
        risk_contribution_end_date = ending_date.date().isoformat()
        ending = contribution_available[
            contribution_available["trade_date"].eq(ending_date)
        ].copy()
        ending = ending.reindex(
            ending["total_risk_contribution"].abs().sort_values(
                ascending=False, kind="stable"
            ).index
        )
        ending_volatility = float(ending["portfolio_volatility"].iloc[0])
        ending_contributions = [
            {
                "tsCode": str(row.ts_code),
                "closeWeight": float(row.close_weight),
                "totalRiskContribution": float(row.total_risk_contribution),
            }
            for row in ending.itertuples(index=False)
        ]
    last = exposure_frame.iloc[-1]
    return {
        "status": "complete",
        "observations": int(len(exposure_frame)),
        "averageGrossExposure": float(exposure_frame["gross_exposure"].mean()),
        "endingGrossExposure": float(last["gross_exposure"]),
        "averageNetExposure": float(exposure_frame["net_exposure"].mean()),
        "endingNetExposure": float(last["net_exposure"]),
        "averageHhi": float(exposure_frame["hhi"].mean()),
        "endingHhi": float(last["hhi"]),
        "averagePortfolioVolatility": (
            float(volatility["portfolio_volatility"].mean())
            if not volatility.empty
            else None
        ),
        "endingPortfolioVolatility": ending_volatility,
        "riskContributionObservations": int(
            contribution_available["trade_date"].nunique()
        ),
        "riskContributionEndDate": risk_contribution_end_date,
        "endingRiskContributions": ending_contributions,
        "unavailableReason": (
            None
            if ending_contributions
            else "冻结风险窗口尚未形成可用组合波动与风险贡献"
        ),
    }


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
    parameter_neighborhood: Mapping[str, Any] | None = None,
    capacity: Mapping[str, Any] | None = None,
    risk_exposures: pd.DataFrame | None = None,
    risk_contributions: pd.DataFrame | None = None,
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
        "warmupStartDate": str(config["warmupStart"]),
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
        "parameterNeighborhood": (
            dict(parameter_neighborhood)
            if parameter_neighborhood is not None
            else {
                "status": "not_available",
                **(
                    {
                        "policySha256": canonical_sha256(
                            validate_research_pass_policy(
                                config["researchPassPolicy"], config
                            )["parameterNeighborhood"]
                        )
                    }
                    if "researchPassPolicy" in config
                    else {}
                ),
                "reason": "当前运行未绑定冻结参数邻域；不得据此判定研究通过",
            }
        ),
        "costStress": {
            "multiplier": policy["costStressMultiplier"],
            "baseTotalReturn": core["totalReturn"],
            "stressedTotalReturn": stressed_core["totalReturn"],
            "returnDifference": float(
                stressed_core["totalReturn"] - core["totalReturn"]
            ),
        },
        "capacity": (
            dict(capacity)
            if capacity is not None
            else {
                "status": "not_available",
                **(
                    {
                        "policySha256": canonical_sha256(
                            validate_research_pass_policy(
                                config["researchPassPolicy"], config
                            )["capacity"]
                        )
                    }
                    if "researchPassPolicy" in config
                    else {}
                ),
                "reason": "当前运行未绑定预期资金规模、ADV 参与率与冲击模型",
            }
        ),
        "riskSummary": build_risk_summary(
            risk_exposures,
            risk_contributions,
            dates=oos_dates,
        ),
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
        "warmupStartDate",
        "startDate",
        "endDate",
        "observations",
        "openTradingDays",
        "rebalanceCount",
        "requestCount",
        "executionCount",
        "blockedCount",
        "independentTradeCount",
        "totalReturn",
        "annualizedVolatility",
        "maxDrawdown",
        "benchmarkTotalReturn",
        "averageOneWayTurnover",
        "cumulativeTransactionCostRate",
        "blockedRequestRate",
        "maxSingleWeight",
        "averageGrossExposure",
        "endingGrossExposure",
        "averageNetExposure",
        "endingNetExposure",
        "averageHhi",
        "endingHhi",
        "var95",
        "es95",
        "yearly",
        "marketRegimes",
        "walkForward",
        "parameterNeighborhood",
        "costStress",
        "capacity",
        "riskSummary",
    }
    if (
        metrics.get("status") != "complete"
        or metrics.get("sampleRole") != "test_oos"
        or metrics.get("sampleStartDate") != test_split["startDate"]
        or metrics.get("sampleEndDate") != test_split["endDate"]
        or metrics.get("warmupStartDate") != str(config["warmupStart"])
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
    for field in (
        "observations",
        "openTradingDays",
        "rebalanceCount",
        "requestCount",
        "executionCount",
        "blockedCount",
        "independentTradeCount",
    ):
        value = metrics[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"OOS 计数字段无效：{field}")
    if metrics["openTradingDays"] != metrics["observations"]:
        raise ValueError("OOS 开市日数必须与 NAV 观察数一致")
    regimes = metrics["marketRegimes"]
    if not isinstance(regimes, dict) or regimes.get("policy") != policy["marketRegime"]:
        raise ValueError("OOS 市场环境拆分未绑定冻结策略")
    stress = metrics["costStress"]
    if not isinstance(stress, dict) or stress.get("multiplier") != policy["costStressMultiplier"]:
        raise ValueError("OOS 成本压力未绑定冻结倍数")
    walk_forward = metrics["walkForward"]
    validation_policy = config.get("validationPolicy")
    if (
        not isinstance(walk_forward, dict)
        or not isinstance(validation_policy, Mapping)
        or walk_forward.get("mode") != validation_policy.get("mode")
        or walk_forward.get("oosOnly") is not True
        or isinstance(walk_forward.get("windowCount"), bool)
        or not isinstance(walk_forward.get("windowCount"), int)
        or walk_forward["windowCount"] <= 0
        or isinstance(walk_forward.get("testObservationCount"), bool)
        or not isinstance(walk_forward.get("testObservationCount"), int)
        or walk_forward["testObservationCount"] <= 0
    ):
        raise ValueError("OOS 指标缺少结构化 walk-forward 证据")
    research_pass_policy = config.get("researchPassPolicy")
    normalized_pass_policy = (
        validate_research_pass_policy(research_pass_policy, config)
        if research_pass_policy is not None
        else None
    )
    for field, label in (
        ("parameterNeighborhood", "参数邻域"),
        ("capacity", "容量"),
    ):
        evidence = metrics[field]
        if not isinstance(evidence, dict) or evidence.get("status") not in {
            "complete",
            "not_available",
        }:
            raise ValueError(f"OOS {label}证据状态无效")
        if evidence["status"] == "not_available" and not isinstance(
            evidence.get("reason"), str
        ):
            raise ValueError(f"OOS {label}缺失必须说明原因")
        if normalized_pass_policy is not None:
            expected_policy = normalized_pass_policy[
                "parameterNeighborhood" if field == "parameterNeighborhood" else "capacity"
            ]
            if evidence.get("policySha256") != canonical_sha256(expected_policy):
                raise ValueError(f"OOS {label}证据未绑定冻结研究通过策略")
    risk_summary = metrics["riskSummary"]
    risk_policy = config.get("riskPolicy")
    if (
        not isinstance(risk_summary, dict)
        or risk_summary.get("status") not in {"complete", "not_available"}
    ):
        raise ValueError("OOS 风险汇总状态无效")
    if (
        isinstance(risk_policy, Mapping)
        and risk_policy.get("mode") != "none"
        and (
            risk_summary.get("status") != "complete"
            or risk_summary.get("observations") != metrics["observations"]
        )
    ):
        raise ValueError("OOS 风险汇总未完整覆盖冻结 test/OOS")


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
            returns = summarize_return_subperiod(
                subset["strategy_return"], subset["benchmark_return"]
            )
            returns["activeTotalReturn"] = float(
                returns["totalReturn"] - returns["benchmarkTotalReturn"]
            )
            cells[name] = {
                "status": "available",
                "startDate": dates.min().date().isoformat(),
                "endDate": dates.max().date().isoformat(),
                "observations": int(len(subset)),
                **returns,
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
