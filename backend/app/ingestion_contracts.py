from __future__ import annotations

from enum import StrEnum


class IngestionAction(StrEnum):
    STOCK_LISTINGS = "stock_listings"
    TRADE_CALENDAR = "trade_calendar"
    MARKET_BUNDLE = "market_bundle"
    DAILY_MARKET = "daily_market"
    MARKET_FUNDAMENTALS = "market_fundamentals"
    US_SAMPLE = "us_sample"
    US_EXPERIMENT_UNIVERSE = "us_experiment_universe"
    US_EXPERIMENT_TARGETED_UNIVERSE = "us_experiment_targeted_universe"
    US_EXPERIMENT_PRICES = "us_experiment_prices"
    US_EXPERIMENT_OVERVIEW_REFRESH = "us_experiment_overview_refresh"
