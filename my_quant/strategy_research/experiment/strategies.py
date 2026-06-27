from __future__ import annotations

from backend.app.research_engine.portfolio import (
    make_equal_weight,
    make_ram_topn as make_backend_ram_topn,
    make_risk_parity as make_backend_risk_parity,
    normalize_weights as normalize_backend_weights,
)

from .config import ALL_ASSETS, DEFENSE_ASSET, RISK_ASSETS


def normalize_weights(weights: dict[str, float], columns: list[str]):
    return normalize_backend_weights(weights, columns, DEFENSE_ASSET)


def make_risk_parity(prices, volatility_window: int, assets: list[str] | None = None):
    return make_backend_risk_parity(prices, volatility_window, assets or ALL_ASSETS)


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
    return make_backend_ram_topn(
        prices,
        top_n,
        momentum_window,
        volatility_window,
        selected_risk_assets,
        defense_asset,
        trend_filter_window=trend_filter_window,
        trend_filter_symbols={"510300", "513100"},
    )
