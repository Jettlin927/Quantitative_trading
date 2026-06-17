from __future__ import annotations

import pandas as pd

from .config import ALL_ASSETS, DEFENSE_ASSET, RISK_ASSETS


def normalize_weights(weights: dict[str, float], columns: list[str]) -> pd.Series:
    series = pd.Series(0.0, index=columns)
    for symbol, weight in weights.items():
        if symbol not in series.index:
            raise ValueError(f"Unknown symbol in weights: {symbol}")
        series.loc[symbol] = float(weight)

    total = float(series.sum())
    if total <= 0:
        series.loc[DEFENSE_ASSET] = 1.0
        return series
    return series / total


def make_equal_weight(symbols: list[str]):
    weight = 1.0 / len(symbols)
    return lambda _date: {symbol: weight for symbol in symbols}


def make_risk_parity(prices: pd.DataFrame, volatility_window: int, assets: list[str] | None = None):
    selected_assets = assets or ALL_ASSETS
    returns = prices.pct_change()

    def _weights(date: pd.Timestamp) -> dict[str, float]:
        hist = returns.loc[:date, selected_assets].tail(volatility_window)
        vol = hist.std(ddof=0).replace(0, pd.NA).dropna()
        inv_vol = 1.0 / vol
        return inv_vol.to_dict()

    return _weights


def make_ram_topn(
    prices: pd.DataFrame,
    top_n: int,
    momentum_window: int,
    volatility_window: int,
    trend_filter_window: int | None = None,
    risk_assets: list[str] | None = None,
    defense_asset: str = DEFENSE_ASSET,
):
    selected_risk_assets = risk_assets or RISK_ASSETS
    returns = prices.pct_change()

    def _weights(date: pd.Timestamp) -> dict[str, float]:
        loc = prices.index.get_loc(date)
        if loc < max(momentum_window, volatility_window):
            return {defense_asset: 1.0}

        score: dict[str, float] = {}
        for symbol in selected_risk_assets:
            current = prices.loc[date, symbol]
            past = prices[symbol].iloc[loc - momentum_window]
            momentum = current / past - 1.0
            volatility = returns[symbol].iloc[loc - volatility_window + 1 : loc + 1].std(ddof=0)
            if trend_filter_window is not None and symbol in {"510300", "513100"}:
                if loc < trend_filter_window:
                    continue
                moving_average = prices[symbol].iloc[loc - trend_filter_window + 1 : loc + 1].mean()
                if current < moving_average:
                    continue
            if pd.notna(momentum) and pd.notna(volatility) and volatility > 0:
                ram = momentum / volatility
                if ram > 0:
                    score[symbol] = float(ram)

        if not score:
            return {defense_asset: 1.0}

        selected = sorted(score.items(), key=lambda item: item[1], reverse=True)[:top_n]
        total = sum(value for _, value in selected)
        return {symbol: value / total for symbol, value in selected}

    return _weights
