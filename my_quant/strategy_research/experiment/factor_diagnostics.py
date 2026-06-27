from __future__ import annotations

import pandas as pd

from .config import RISK_ASSETS


def _market_regime(realized_volatility: pd.Series) -> pd.Series:
    median_volatility = realized_volatility.median()
    return pd.Series(
        ["high_vol" if value >= median_volatility else "low_vol" for value in realized_volatility],
        index=realized_volatility.index,
    )


def factor_ic_panel(
    prices: pd.DataFrame,
    momentum_window: int = 20,
    volatility_window: int = 120,
    forward_window: int = 21,
    assets: list[str] | None = None,
) -> pd.DataFrame:
    selected_assets = assets or RISK_ASSETS
    returns = prices[selected_assets].pct_change()
    market_volatility = returns.mean(axis=1).rolling(volatility_window).std()
    regimes = _market_regime(market_volatility.dropna())
    rows: list[dict[str, object]] = []

    start_pos = max(momentum_window, volatility_window)
    end_pos = len(prices.index) - forward_window
    for pos in range(start_pos, end_pos):
        date = prices.index[pos]
        factor_values: dict[str, dict[str, float]] = {
            "momentum": {},
            "ram": {},
            "low_volatility": {},
            "trend_strength": {},
        }
        forward_returns: dict[str, float] = {}
        for symbol in selected_assets:
            current = prices[symbol].iloc[pos]
            momentum = current / prices[symbol].iloc[pos - momentum_window] - 1.0
            volatility = returns[symbol].iloc[pos - volatility_window + 1 : pos + 1].std(ddof=0)
            moving_average = prices[symbol].iloc[pos - momentum_window + 1 : pos + 1].mean()
            forward_return = prices[symbol].iloc[pos + forward_window] / current - 1.0
            if pd.notna(momentum) and pd.notna(volatility) and volatility > 0 and pd.notna(forward_return):
                factor_values["momentum"][symbol] = float(momentum)
                factor_values["ram"][symbol] = float(momentum / volatility)
                factor_values["low_volatility"][symbol] = float(-volatility)
                factor_values["trend_strength"][symbol] = float(current / moving_average - 1.0)
                forward_returns[symbol] = float(forward_return)

        if len(forward_returns) < 2:
            continue

        y = pd.Series(forward_returns)
        regime = regimes.get(date, "unknown")
        for factor, values in factor_values.items():
            x = pd.Series(values).reindex(y.index).dropna()
            aligned_y = y.reindex(x.index).dropna()
            x = x.reindex(aligned_y.index)
            if len(x) < 2:
                continue
            ic = x.rank().corr(aligned_y.rank())
            if pd.notna(ic):
                rows.append({"date": date, "factor": factor, "regime": regime, "ic": float(ic)})
    return pd.DataFrame(rows)


def summarize_factor_ic(
    prices: pd.DataFrame,
    momentum_window: int = 20,
    volatility_window: int = 120,
    forward_window: int = 21,
    assets: list[str] | None = None,
) -> pd.DataFrame:
    panel = factor_ic_panel(
        prices,
        momentum_window=momentum_window,
        volatility_window=volatility_window,
        forward_window=forward_window,
        assets=assets,
    )
    if panel.empty:
        return pd.DataFrame(columns=["factor", "regime", "mean_ic", "ic_win_rate", "observations"])

    grouped = (
        panel.groupby(["factor", "regime"])["ic"]
        .agg(mean_ic="mean", ic_win_rate=lambda values: float((values > 0).mean()), observations="count")
        .reset_index()
    )
    all_regime = (
        panel.groupby("factor")["ic"]
        .agg(mean_ic="mean", ic_win_rate=lambda values: float((values > 0).mean()), observations="count")
        .reset_index()
    )
    all_regime.insert(1, "regime", "all")
    return pd.concat([all_regime, grouped], ignore_index=True).sort_values(["factor", "regime"])
