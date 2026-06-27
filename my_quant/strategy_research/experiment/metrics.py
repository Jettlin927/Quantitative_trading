from __future__ import annotations

import pandas as pd

from backend.app.research_engine.metrics import calculate_beta, calculate_nav_metrics

from .config import TRADING_DAYS


def calculate_metrics(nav: pd.Series, benchmark_nav: pd.Series | None = None) -> dict[str, float]:
    nav = nav.dropna()
    if len(nav) < 2:
        raise ValueError("NAV series must contain at least two observations")

    stats = calculate_nav_metrics(nav.tolist(), trading_days=TRADING_DAYS)
    if benchmark_nav is not None:
        strategy_returns = nav.pct_change().dropna()
        benchmark_returns = benchmark_nav.dropna().pct_change().dropna()
        aligned = pd.concat([strategy_returns.rename("strategy"), benchmark_returns.rename("benchmark")], axis=1, join="inner").dropna()
        stats["beta"] = calculate_beta(aligned["strategy"].tolist(), aligned["benchmark"].tolist())
    return stats
