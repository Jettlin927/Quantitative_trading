from __future__ import annotations

import pandas as pd

from .backtest import run_config
from .config import StrategyConfig


def _best_config_for_window(
    prices: pd.DataFrame,
    configs: list[StrategyConfig],
    train_start: str,
    train_end: str,
) -> tuple[StrategyConfig, pd.Series]:
    rows = [run_config(prices, config, train_start, train_end) for config in configs]
    df = pd.DataFrame(rows).sort_values(["calmar", "annual_return"], ascending=False)
    best_name = str(df.iloc[0]["strategy"])
    best_config = next(config for config in configs if config.name == best_name)
    return best_config, df.iloc[0]


def _walk_windows(
    index: pd.DatetimeIndex,
    train_size: int,
    test_size: int,
    step_size: int,
    anchored: bool,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    windows = []
    start_pos = 0
    while start_pos + train_size + test_size <= len(index):
        train_start_pos = 0 if anchored else start_pos
        train_end_pos = start_pos + train_size - 1
        test_start_pos = train_end_pos + 1
        test_end_pos = test_start_pos + test_size - 1
        windows.append(
            (
                index[train_start_pos],
                index[train_end_pos],
                index[test_start_pos],
                index[test_end_pos],
            )
        )
        start_pos += step_size
    return windows


def walk_forward_analysis(
    prices: pd.DataFrame,
    configs: list[StrategyConfig],
    train_size: int = 504,
    test_size: int = 126,
    step_size: int = 126,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for mode, anchored in [("rolling", False), ("anchored", True)]:
        windows = _walk_windows(prices.index, train_size, test_size, step_size, anchored)
        for window_id, (train_start, train_end, test_start, test_end) in enumerate(windows, start=1):
            best_config, train_best = _best_config_for_window(
                prices,
                configs,
                str(train_start.date()),
                str(train_end.date()),
            )
            oos = run_config(prices, best_config, str(test_start.date()), str(test_end.date()))
            rows.append(
                {
                    "mode": mode,
                    "window_id": window_id,
                    "train_start": str(train_start.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "best_strategy": best_config.name,
                    "train_annual_return": float(train_best["annual_return"]),
                    "train_max_drawdown": float(train_best["max_drawdown"]),
                    "train_calmar": float(train_best["calmar"]),
                    "oos_annual_return": float(oos["annual_return"]),
                    "oos_max_drawdown": float(oos["max_drawdown"]),
                    "oos_calmar": float(oos["calmar"]),
                    "oos_sharpe": float(oos["sharpe"]),
                }
            )
    return pd.DataFrame(rows)
