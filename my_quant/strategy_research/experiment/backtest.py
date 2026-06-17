from __future__ import annotations

import pandas as pd

from .config import ALL_ASSETS, BASELINE_WEIGHTS, StrategyConfig
from .metrics import calculate_metrics
from .strategies import make_equal_weight, make_ram_topn, make_risk_parity, normalize_weights


def annual_rebalance_dates(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    return set(index.to_series().groupby(index.year).head(1))


def interval_rebalance_dates(index: pd.DatetimeIndex, interval_days: int) -> set[pd.Timestamp]:
    return set(index[::interval_days])


def run_weighted_nav(
    prices: pd.DataFrame,
    eval_start: str,
    eval_end: str,
    rebalance_dates: set[pd.Timestamp],
    make_weights,
    cost_rate: float,
) -> tuple[pd.Series, pd.DataFrame, dict[str, float]]:
    eval_prices = prices.loc[eval_start:eval_end]
    if eval_prices.empty:
        raise ValueError(f"No prices in evaluation range {eval_start} to {eval_end}")

    returns = prices.pct_change().fillna(0.0)
    columns = list(prices.columns)
    nav = pd.Series(index=eval_prices.index, dtype=float)
    weights_history = pd.DataFrame(index=eval_prices.index, columns=columns, dtype=float)
    nav.iloc[0] = 1.0
    current_weights = pd.Series(0.0, index=columns)
    total_turnover = 0.0
    rebalance_count = 0

    for i, date in enumerate(eval_prices.index):
        if date in rebalance_dates or i == 0:
            target_weights = normalize_weights(make_weights(date), columns)
            turnover = float((target_weights - current_weights).abs().sum())
            if turnover > 1e-12:
                nav.iloc[i] = nav.iloc[i] * (1 - turnover * cost_rate)
                total_turnover += turnover
                rebalance_count += 1
            current_weights = target_weights

        weights_history.loc[date] = current_weights
        if i + 1 < len(eval_prices.index):
            next_date = eval_prices.index[i + 1]
            daily_return = float((current_weights * returns.loc[next_date, columns]).sum())
            nav.iloc[i + 1] = nav.iloc[i] * (1 + daily_return)

    stats = {
        "total_turnover": total_turnover,
        "rebalance_count": float(rebalance_count),
        "estimated_cost": total_turnover * cost_rate,
    }
    return nav, weights_history, stats


def build_weight_function(prices: pd.DataFrame, config: StrategyConfig):
    if config.kind == "permanent":
        return lambda _date: BASELINE_WEIGHTS
    if config.kind == "equal_weight":
        return make_equal_weight(ALL_ASSETS)
    if config.kind == "risk_parity":
        return make_risk_parity(prices, config.volatility_window or 60)
    if config.kind == "ram_topn":
        return make_ram_topn(
            prices,
            top_n=config.top_n or 2,
            momentum_window=config.momentum_window or 60,
            volatility_window=config.volatility_window or 60,
        )
    if config.kind == "ram_topn_trend_filter":
        return make_ram_topn(
            prices,
            top_n=config.top_n or 2,
            momentum_window=config.momentum_window or 60,
            volatility_window=config.volatility_window or 60,
            trend_filter_window=120,
        )
    raise ValueError(f"Unknown config kind: {config.kind}")


def rebalance_dates_for_config(index: pd.DatetimeIndex, config: StrategyConfig) -> set[pd.Timestamp]:
    if config.kind == "permanent":
        return annual_rebalance_dates(index)
    return interval_rebalance_dates(index, config.interval_days or 21)


def run_config(prices: pd.DataFrame, config: StrategyConfig, eval_start: str, eval_end: str) -> dict[str, float | str]:
    eval_index = prices.loc[eval_start:eval_end].index
    nav, _weights, run_stats = run_weighted_nav(
        prices=prices,
        eval_start=eval_start,
        eval_end=eval_end,
        rebalance_dates=rebalance_dates_for_config(eval_index, config),
        make_weights=build_weight_function(prices, config),
        cost_rate=config.cost_rate,
    )
    row: dict[str, float | str] = {"strategy": config.name}
    row.update(calculate_metrics(nav))
    row.update(run_stats)
    row.update(
        {
            "kind": config.kind,
            "top_n": config.top_n or "",
            "momentum_window": config.momentum_window or "",
            "volatility_window": config.volatility_window or "",
            "interval_days": config.interval_days or "",
            "cost_rate": config.cost_rate,
        }
    )
    return row
