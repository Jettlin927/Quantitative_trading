from __future__ import annotations

from math import sqrt
from typing import Any

import pandas as pd


def summarize_performance(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame | None = None,
    periods_per_year: int = 252,
    *,
    include_extended: bool = False,
) -> dict[str, Any]:
    strategy = _normalize_nav(nav, "策略")
    returns = strategy["nav"].pct_change(fill_method=None).dropna()
    total_return = strategy["nav"].iloc[-1] / strategy["nav"].iloc[0] - 1
    annualized_return = _annualized_return(strategy["nav"], periods_per_year)
    annualized_volatility = _finite_or_none(returns.std(ddof=1) * sqrt(periods_per_year)) if len(returns) > 1 else None
    sharpe = _ratio(returns.mean() * periods_per_year, annualized_volatility)
    drawdown = strategy["nav"] / strategy["nav"].cummax() - 1
    max_drawdown = float(drawdown.min())
    result: dict[str, Any] = {
        "startDate": strategy["trade_date"].iloc[0].date().isoformat(),
        "endDate": strategy["trade_date"].iloc[-1].date().isoformat(),
        "observations": int(len(strategy)),
        "totalReturn": float(total_return),
        "annualizedReturn": annualized_return,
        "annualizedVolatility": annualized_volatility,
        "sharpe": sharpe,
        "maxDrawdown": max_drawdown,
        "calmar": _ratio(annualized_return, abs(max_drawdown)),
        "positiveDayRate": float((returns > 0).mean()) if not returns.empty else None,
    }
    if include_extended:
        downside = returns.clip(upper=0)
        downside_volatility = (
            _finite_or_none(sqrt(float((downside**2).mean())) * sqrt(periods_per_year))
            if not downside.empty
            else None
        )
        result.update(
            {
                "downsideVolatility": downside_volatility,
                "sortino": _ratio(returns.mean() * periods_per_year, downside_volatility),
                "maxDrawdownDuration": _max_drawdown_duration(drawdown),
                "beta": None,
            }
        )

    if benchmark_nav is not None:
        benchmark = _normalize_nav(benchmark_nav, "基准")
        aligned = strategy.merge(benchmark, on="trade_date", how="inner", suffixes=("_strategy", "_benchmark"))
        if len(aligned) < 2:
            raise ValueError("策略与基准重叠日期不足")
        strategy_returns = aligned["nav_strategy"].pct_change(fill_method=None)
        benchmark_returns = aligned["nav_benchmark"].pct_change(fill_method=None)
        active_returns = (strategy_returns - benchmark_returns).dropna()
        tracking_error = _finite_or_none(active_returns.std(ddof=1) * sqrt(periods_per_year)) if len(active_returns) > 1 else None
        aligned_strategy_total = aligned["nav_strategy"].iloc[-1] / aligned["nav_strategy"].iloc[0] - 1
        benchmark_total = aligned["nav_benchmark"].iloc[-1] / aligned["nav_benchmark"].iloc[0] - 1
        result.update(
            {
                "benchmarkTotalReturn": float(benchmark_total),
                "excessTotalReturn": float(aligned_strategy_total - benchmark_total),
                "trackingError": tracking_error,
                "informationRatio": _ratio(active_returns.mean() * periods_per_year, tracking_error),
            }
        )
        if include_extended:
            paired = pd.concat(
                [strategy_returns.rename("strategy"), benchmark_returns.rename("benchmark")],
                axis=1,
            ).dropna()
            benchmark_variance = paired["benchmark"].var(ddof=1) if len(paired) > 1 else None
            beta = None
            if benchmark_variance is not None and pd.notna(benchmark_variance) and benchmark_variance > 0:
                beta = _finite_or_none(paired["strategy"].cov(paired["benchmark"]) / benchmark_variance)
            result["beta"] = beta
    return result


def summarize_execution_metrics(
    nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
) -> dict[str, Any]:
    required_nav = {"trade_date", "cash_weight", "one_way_turnover", "transaction_cost_rate"}
    required_requests = {"execution_date", "signal_date", "ts_code", "requested_change", "side"}
    required_executions = {
        "execution_date",
        "signal_date",
        "ts_code",
        "requested_change",
        "executed_change",
        "blocked_change",
        "status",
        "reason",
        "transaction_cost_rate",
    }
    required_positions = {"trade_date", "ts_code", "close_weight"}
    for label, frame, required in (
        ("NAV", nav, required_nav),
        ("调仓请求", requests, required_requests),
        ("模拟执行", executions, required_executions),
        ("持仓", positions, required_positions),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{label}指标输入缺少字段：{', '.join(missing)}")

    nav_frame = nav.copy()
    request_frame = requests.copy()
    execution_frame = executions.copy()
    position_frame = positions.copy()
    nav_frame["trade_date"] = pd.to_datetime(nav_frame["trade_date"])
    for frame in (request_frame, execution_frame):
        frame["execution_date"] = pd.to_datetime(frame["execution_date"])
        frame["signal_date"] = pd.to_datetime(frame["signal_date"])
        frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
    position_frame["trade_date"] = pd.to_datetime(position_frame["trade_date"])
    position_frame["ts_code"] = position_frame["ts_code"].astype(str).str.upper()

    _numeric_columns(nav_frame, ("cash_weight", "one_way_turnover", "transaction_cost_rate"), "NAV")
    _numeric_columns(request_frame, ("requested_change",), "调仓请求")
    _numeric_columns(
        execution_frame,
        ("requested_change", "executed_change", "blocked_change", "transaction_cost_rate"),
        "模拟执行",
    )
    _numeric_columns(position_frame, ("close_weight",), "持仓")
    key = ["execution_date", "signal_date", "ts_code"]
    if request_frame.duplicated(key).any() or execution_frame.duplicated(key).any():
        raise ValueError("调仓请求或模拟执行存在重复自然键")
    request_identity = request_frame[key + ["requested_change"]].sort_values(key).reset_index(drop=True)
    execution_identity = execution_frame[key + ["requested_change"]].sort_values(key).reset_index(drop=True)
    if not request_identity.equals(execution_identity):
        raise ValueError("调仓请求与模拟执行不一致")
    if not execution_frame["status"].isin({"filled", "partial", "blocked"}).all():
        raise ValueError("模拟执行 status 非法")
    if not request_frame["side"].isin({"buy", "sell"}).all():
        raise ValueError("调仓请求 side 非法")
    execution_frame["reason"] = execution_frame["reason"].fillna("").astype(str)
    allowed_reasons = {
        "",
        "cash_capacity",
        "valuation_carried",
        "suspended_at_open",
        "limit_up",
        "limit_down",
    }
    if not execution_frame["reason"].isin(allowed_reasons).all():
        raise ValueError("模拟执行 reason 非法")
    if (execution_frame[["requested_change", "executed_change", "blocked_change", "transaction_cost_rate"]] < 0).any().any():
        raise ValueError("模拟执行数值不能为负")
    reconciliation = (
        execution_frame["executed_change"] + execution_frame["blocked_change"] - execution_frame["requested_change"]
    ).abs()
    if (reconciliation > 1e-10).any():
        raise ValueError("模拟执行 requested/executed/blocked 不闭合")
    status_invalid = (
        (execution_frame["status"].eq("filled") & (
            execution_frame["blocked_change"].gt(1e-10)
            | execution_frame["reason"].ne("")
        ))
        | (execution_frame["status"].eq("partial") & (
            execution_frame["executed_change"].le(1e-10)
            | execution_frame["blocked_change"].le(1e-10)
            | execution_frame["reason"].ne("cash_capacity")
        ))
        | (execution_frame["status"].eq("blocked") & (
            execution_frame["executed_change"].gt(1e-10)
            | execution_frame["blocked_change"].le(1e-10)
            | execution_frame["reason"].eq("")
        ))
    )
    if status_invalid.any():
        raise ValueError("模拟执行 status、reason 与执行数量不一致")
    execution_with_side = execution_frame.drop(columns=["side"], errors="ignore").merge(
        request_frame[key + ["side"]],
        on=key,
        how="left",
        validate="one_to_one",
    )
    incompatible_limit_reason = (
        execution_with_side["side"].eq("buy")
        & execution_with_side["reason"].eq("limit_down")
    ) | (
        execution_with_side["side"].eq("sell")
        & execution_with_side["reason"].eq("limit_up")
    )
    if incompatible_limit_reason.any():
        raise ValueError("模拟执行涨跌停原因与买卖方向不一致")

    execution_costs = execution_frame.groupby("execution_date")["transaction_cost_rate"].sum()
    nav_costs = nav_frame.set_index("trade_date")["transaction_cost_rate"]
    for trade_date in nav_frame["trade_date"]:
        if abs(float(execution_costs.get(trade_date, 0.0)) - float(nav_costs.loc[trade_date])) > 1e-10:
            raise ValueError("模拟执行成本与 NAV 不闭合")

    if position_frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("持仓存在重复自然键")
    if (position_frame["close_weight"] < 0).any():
        raise ValueError("持仓权重不能为负")
    position_totals = position_frame.groupby("trade_date")["close_weight"].sum()
    holding_counts = position_frame.groupby("trade_date")["ts_code"].nunique()
    hhi = position_frame.assign(square=position_frame["close_weight"] ** 2).groupby("trade_date")["square"].sum()
    counts: list[float] = []
    hhi_values: list[float] = []
    for row in nav_frame.itertuples(index=False):
        invested = float(position_totals.get(row.trade_date, 0.0))
        if abs(invested + float(row.cash_weight) - 1.0) > 1e-9:
            raise ValueError("每日现金和持仓权重不闭合")
        counts.append(float(holding_counts.get(row.trade_date, 0)))
        hhi_values.append(float(hhi.get(row.trade_date, 0.0)))

    total_requests = len(execution_frame)
    return {
        "averageOneWayTurnover": float(nav_frame["one_way_turnover"].mean()),
        "maxOneWayTurnover": float(nav_frame["one_way_turnover"].max()),
        "cumulativeTransactionCostRate": float(nav_frame["transaction_cost_rate"].sum()),
        "averageHoldingCount": float(pd.Series(counts).mean()),
        "maxHoldingCount": int(max(counts, default=0)),
        "maxSingleWeight": float(position_frame["close_weight"].max()) if not position_frame.empty else 0.0,
        "averageHhi": float(pd.Series(hhi_values).mean()),
        "maxHhi": float(max(hhi_values, default=0.0)),
        "blockedRequestRate": (
            float(execution_frame["status"].eq("blocked").mean()) if total_requests else 0.0
        ),
        "partialRequestRate": (
            float(execution_frame["status"].eq("partial").mean()) if total_requests else 0.0
        ),
        "cumulativeBlockedChange": float(execution_frame["blocked_change"].sum()),
    }


def _normalize_nav(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if not {"trade_date", "nav"}.issubset(frame.columns):
        raise ValueError(f"{label}净值缺少 trade_date 或 nav")
    result = frame[["trade_date", "nav"]].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    result["nav"] = pd.to_numeric(result["nav"], errors="coerce")
    result = result.dropna().sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)
    if result.empty or (result["nav"] <= 0).any():
        raise ValueError(f"{label}净值为空或包含非正数")
    return result


def _annualized_return(nav: pd.Series, periods_per_year: int) -> float | None:
    periods = len(nav) - 1
    if periods <= 0:
        return None
    value = (nav.iloc[-1] / nav.iloc[0]) ** (periods_per_year / periods) - 1
    return _finite_or_none(value)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return _finite_or_none(numerator / denominator)


def _finite_or_none(value: float) -> float | None:
    numeric = float(value)
    return numeric if pd.notna(numeric) and abs(numeric) != float("inf") else None


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in drawdown:
        if float(value) < -1e-15:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _numeric_columns(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or (~values.map(lambda value: pd.notna(value) and abs(float(value)) != float("inf"))).any():
            raise ValueError(f"{label}.{column} 包含非有限数")
        frame[column] = values.astype(float)
