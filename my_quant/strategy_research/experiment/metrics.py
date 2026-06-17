from __future__ import annotations

import numpy as np
import pandas as pd

from .config import TRADING_DAYS


def calculate_metrics(nav: pd.Series) -> dict[str, float]:
    nav = nav.dropna()
    if len(nav) < 2:
        raise ValueError("NAV series must contain at least two observations")

    rets = nav.pct_change().dropna()
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1)
    years = max((len(nav) - 1) / TRADING_DAYS, 1 / TRADING_DAYS)
    annual_return = float((1 + total_return) ** (1 / years) - 1)
    annual_volatility = float(rets.std(ddof=0) * np.sqrt(TRADING_DAYS))
    drawdown = nav / nav.cummax() - 1
    max_drawdown = float(drawdown.min())
    downside = rets[rets < 0]
    downside_vol = float(downside.std(ddof=0) * np.sqrt(TRADING_DAYS)) if len(downside) else 0.0
    sharpe = annual_return / annual_volatility if annual_volatility > 0 else np.nan
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else np.nan
    sortino = annual_return / downside_vol if downside_vol > 0 else np.nan
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "max_drawdown": max_drawdown,
        "sharpe": float(sharpe),
        "calmar": float(calmar),
        "sortino": float(sortino),
    }
