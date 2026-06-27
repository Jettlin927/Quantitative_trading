from __future__ import annotations

from math import isnan, nan, sqrt
from typing import Sequence


DEFAULT_TRADING_DAYS = 252


def calculate_nav_metrics(
    nav_values: Sequence[float],
    benchmark_values: Sequence[float] | None = None,
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> dict[str, float]:
    nav = [float(value) for value in nav_values if value is not None]
    if len(nav) < 2:
        raise ValueError("NAV series must contain at least two observations")

    returns = pct_returns(nav)
    total_return = nav[-1] / nav[0] - 1
    years = max((len(nav) - 1) / trading_days, 1 / trading_days)
    annual_return = (1 + total_return) ** (1 / years) - 1
    annual_volatility = population_stdev(returns) * sqrt(trading_days)
    max_drawdown = calculate_max_drawdown(nav)
    downside_returns = [value for value in returns if value < 0]
    downside_volatility = population_stdev(downside_returns) * sqrt(trading_days) if downside_returns else 0.0
    benchmark_returns = pct_returns([float(value) for value in benchmark_values]) if benchmark_values is not None else None
    beta = calculate_beta(returns, benchmark_returns)
    return {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(annual_return / annual_volatility) if annual_volatility > 0 else nan,
        "beta": float(beta),
        "calmar": float(annual_return / abs(max_drawdown)) if max_drawdown < 0 else nan,
        "sortino": float(annual_return / downside_volatility) if downside_volatility > 0 else nan,
    }


def pct_returns(values: Sequence[float]) -> list[float]:
    return [float(values[index]) / float(values[index - 1]) - 1 for index in range(1, len(values)) if float(values[index - 1]) != 0]


def calculate_max_drawdown(values: Sequence[float]) -> float:
    peak = float(values[0])
    max_drawdown = 0.0
    for value in values:
        current = float(value)
        peak = max(peak, current)
        if peak > 0:
            max_drawdown = min(max_drawdown, current / peak - 1)
    return max_drawdown


def population_stdev(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def calculate_beta(strategy_returns: Sequence[float], benchmark_returns: Sequence[float] | None) -> float:
    if benchmark_returns is None:
        return nan
    pairs = [
        (float(strategy), float(benchmark))
        for strategy, benchmark in zip(strategy_returns, benchmark_returns)
        if not isnan(float(strategy)) and not isnan(float(benchmark))
    ]
    if len(pairs) < 2:
        return nan
    strategy_values = [strategy for strategy, _ in pairs]
    benchmark_values = [benchmark for _, benchmark in pairs]
    avg_strategy = sum(strategy_values) / len(strategy_values)
    avg_benchmark = sum(benchmark_values) / len(benchmark_values)
    covariance = sum((strategy - avg_strategy) * (benchmark - avg_benchmark) for strategy, benchmark in pairs) / (len(pairs) - 1)
    variance = sum((value - avg_benchmark) ** 2 for value in benchmark_values) / (len(benchmark_values) - 1)
    if variance <= 0:
        return nan
    return covariance / variance
