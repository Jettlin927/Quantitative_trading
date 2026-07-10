from __future__ import annotations

from math import sqrt
from typing import Any

import pandas as pd


def summarize_performance(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame | None = None,
    periods_per_year: int = 252,
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
    return result


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
