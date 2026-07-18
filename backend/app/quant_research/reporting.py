from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from .metrics import summarize_performance


def returns_from_initial_nav(nav: pd.Series, *, initial_nav: float = 1.0) -> pd.Series:
    """Return a dated NAV series' returns, including its first move from initial NAV."""

    if not math.isfinite(initial_nav) or initial_nav <= 0:
        raise ValueError("initial_nav 必须是有限正数")
    numeric = pd.to_numeric(nav, errors="raise")
    returns = numeric.pct_change(fill_method=None)
    if not returns.empty:
        returns.iloc[0] = float(numeric.iloc[0]) / initial_nav - 1.0
    return returns


def summarize_nav_window(
    nav: pd.DataFrame,
    *,
    start: object,
    end: object,
    benchmark_nav: pd.DataFrame | None = None,
    include_extended: bool = True,
) -> dict[str, Any]:
    """Summarize a NAV subwindow using the last NAV before the window as its base."""

    strategy = _normalized_nav_frame(nav, "策略")
    window_start = pd.Timestamp(start)
    window_end = pd.Timestamp(end)
    strategy_window = strategy[
        strategy["trade_date"].between(window_start, window_end)
    ]
    strategy_prior = strategy[strategy["trade_date"].lt(window_start)]
    if strategy_window.empty or strategy_prior.empty:
        raise ValueError("策略子区间为空或缺少窗口前净值")

    benchmark_window = None
    benchmark_initial = None
    if benchmark_nav is not None:
        benchmark = _normalized_nav_frame(benchmark_nav, "基准")
        benchmark_window = benchmark[
            benchmark["trade_date"].between(window_start, window_end)
        ]
        benchmark_prior = benchmark[benchmark["trade_date"].lt(window_start)]
        if benchmark_window.empty or benchmark_prior.empty:
            raise ValueError("基准子区间为空或缺少窗口前净值")
        benchmark_initial = float(benchmark_prior.iloc[-1]["nav"])

    return summarize_performance(
        strategy_window,
        benchmark_window,
        include_extended=include_extended,
        initial_strategy_nav=float(strategy_prior.iloc[-1]["nav"]),
        initial_benchmark_nav=benchmark_initial,
    )


def summarize_return_subperiod(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    *,
    periods_per_year: int = 252,
) -> dict[str, float | None]:
    """Summarize an already selected return subperiod with initial wealth fixed at one."""

    strategy = pd.to_numeric(strategy_returns, errors="raise")
    if benchmark_returns is not None:
        frame = pd.concat(
            [
                strategy.rename("strategy"),
                pd.to_numeric(benchmark_returns, errors="raise").rename("benchmark"),
            ],
            axis=1,
        ).dropna()
        strategy = frame["strategy"]
        benchmark = frame["benchmark"]
    else:
        strategy = strategy.dropna()
        benchmark = None
    if strategy.empty:
        raise ValueError("收益子区间不能为空")

    wealth = pd.concat(
        [pd.Series([1.0]), (1.0 + strategy.reset_index(drop=True)).cumprod()],
        ignore_index=True,
    )
    drawdown = wealth / wealth.cummax() - 1.0
    result: dict[str, float | None] = {
        "totalReturn": float(wealth.iloc[-1] - 1.0),
        "annualizedVolatility": (
            float(strategy.std(ddof=1) * math.sqrt(periods_per_year))
            if len(strategy) > 1
            else None
        ),
        "maxDrawdown": float(drawdown.min()),
    }
    if benchmark is not None:
        result["benchmarkTotalReturn"] = float((1.0 + benchmark).prod() - 1.0)
    return result


def _normalized_nav_frame(nav: pd.DataFrame, label: str) -> pd.DataFrame:
    required = {"trade_date", "nav"}
    if not required.issubset(nav.columns):
        raise ValueError(f"{label}净值缺少 trade_date 或 nav")
    frame = nav[["trade_date", "nav"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="raise")
    frame = frame.sort_values("trade_date", kind="stable").reset_index(drop=True)
    if frame.empty or frame["trade_date"].duplicated().any():
        raise ValueError(f"{label}净值日期为空或重复")
    return frame


def tail_metrics(returns: pd.Series) -> dict[str, float]:
    series = pd.to_numeric(returns, errors="raise").dropna()
    losses = -series
    var95 = float(losses.quantile(0.95))
    return {
        "skew": float(series.skew()),
        "excessKurtosis": float(series.kurt()),
        "var95": var95,
        "es95": float(losses[losses >= var95].mean()),
    }


def hac_alpha(strategy: pd.Series, benchmark: pd.Series) -> dict[str, float | int]:
    frame = pd.concat(
        [strategy.rename("strategy"), benchmark.rename("benchmark")], axis=1
    ).dropna()
    y = frame["strategy"].to_numpy(dtype=float)
    x = np.column_stack(
        [np.ones(len(frame)), frame["benchmark"].to_numpy(dtype=float)]
    )
    coefficients = np.linalg.solve(x.T @ x, x.T @ y)
    residuals = y - x @ coefficients
    lag = int(math.floor(4 * (len(frame) / 100) ** (2 / 9)))
    xu = x * residuals[:, None]
    meat = xu.T @ xu
    for offset in range(1, lag + 1):
        weight = 1 - offset / (lag + 1)
        gamma = xu[offset:].T @ xu[:-offset]
        meat += weight * (gamma + gamma.T)
    inverse = np.linalg.inv(x.T @ x)
    covariance = inverse @ meat @ inverse
    alpha = float(coefficients[0] * 252)
    standard_error = float(math.sqrt(covariance[0, 0]) * 252)
    return {
        "observations": int(len(frame)),
        "neweyWestLag": lag,
        "annualizedAlpha": alpha,
        "beta": float(coefficients[1]),
        "annualizedAlphaStandardError": standard_error,
        "alphaTStatistic": float(coefficients[0] / math.sqrt(covariance[0, 0])),
        "ci95Low": alpha - 1.96 * standard_error,
        "ci95High": alpha + 1.96 * standard_error,
        "strategyLag1Autocorrelation": float(frame["strategy"].autocorr(1)),
    }


def deflated_sharpe(
    returns: pd.DataFrame,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    sharpes = {
        candidate: float(returns[candidate].mean() / returns[candidate].std(ddof=1))
        for candidate in candidates
    }
    winner = max(candidates, key=lambda candidate: sharpes[candidate])
    trial_std = float(pd.Series(sharpes).std(ddof=1))
    count = len(candidates)
    normal = NormalDist()
    euler_gamma = 0.5772156649015329
    expected_max = trial_std * (
        (1 - euler_gamma) * normal.inv_cdf(1 - 1 / count)
        + euler_gamma * normal.inv_cdf(1 - 1 / (count * math.e))
    )
    series = returns[winner].dropna()
    observed = sharpes[winner]
    skew = float(series.skew())
    kurtosis = float(series.kurt() + 3)
    denominator = math.sqrt(
        1 - skew * observed + ((kurtosis - 1) / 4) * observed**2
    )
    statistic = (observed - expected_max) * math.sqrt(len(series) - 1) / denominator
    return {
        "winner": winner,
        "trialCount": count,
        "observations": int(len(series)),
        "dailySharpe": observed,
        "expectedMaximumDailySharpe": expected_max,
        "probability": normal.cdf(statistic),
        "zStatistic": statistic,
    }


def probability_backtest_overfitting(
    returns: pd.DataFrame,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    monthly = (
        returns.set_index("trade_date")[list(candidates)]
        .resample("ME")
        .apply(lambda values: float((1 + values).prod() - 1))
        .dropna()
    )
    partition_count = 8
    blocks = [
        list(values) for values in np.array_split(range(len(monthly)), partition_count)
    ]
    logits: list[float] = []
    winner_counts = {candidate: 0 for candidate in candidates}

    def sharpe(candidate: str, positions: list[int]) -> float:
        series = monthly.iloc[positions][candidate]
        volatility = float(series.std(ddof=1))
        return float(series.mean() / volatility) if volatility > 0 else -math.inf

    for selected in itertools.combinations(range(partition_count), partition_count // 2):
        train = [position for block in selected for position in blocks[block]]
        test = [
            position
            for block in range(partition_count)
            if block not in selected
            for position in blocks[block]
        ]
        winner = max(
            candidates, key=lambda candidate: (sharpe(candidate, train), candidate)
        )
        winner_counts[winner] += 1
        ordered = sorted(
            candidates,
            key=lambda candidate: (sharpe(candidate, test), candidate),
        )
        rank = ordered.index(winner) + 1
        percentile = rank / (len(candidates) + 1)
        logits.append(math.log(percentile / (1 - percentile)))
    return {
        "method": "CSCV, 8 个连续月度分块",
        "monthlyObservations": int(len(monthly)),
        "combinations": int(len(logits)),
        "probability": float(sum(value <= 0 for value in logits) / len(logits)),
        "medianLogit": float(pd.Series(logits).median()),
        "trainingWinnerCounts": winner_counts,
    }
