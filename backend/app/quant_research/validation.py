from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def to_dict(self) -> dict[str, str]:
        return {
            "trainStart": self.train_start.date().isoformat(),
            "trainEnd": self.train_end.date().isoformat(),
            "testStart": self.test_start.date().isoformat(),
            "testEnd": self.test_end.date().isoformat(),
        }


def build_walk_forward_windows(
    trade_dates: Iterable[object],
    train_periods: int,
    test_periods: int,
    step_periods: int | None = None,
    anchored: bool = True,
) -> list[WalkForwardWindow]:
    if train_periods <= 0 or test_periods <= 0:
        raise ValueError("train_periods 和 test_periods 必须大于 0")
    step = step_periods or test_periods
    if step <= 0:
        raise ValueError("step_periods 必须大于 0")
    dates = list(pd.DatetimeIndex(pd.to_datetime(list(trade_dates))).drop_duplicates().sort_values())
    windows: list[WalkForwardWindow] = []
    test_start_index = train_periods
    while test_start_index + test_periods <= len(dates):
        train_start_index = 0 if anchored else test_start_index - train_periods
        window = WalkForwardWindow(
            train_start=dates[train_start_index],
            train_end=dates[test_start_index - 1],
            test_start=dates[test_start_index],
            test_end=dates[test_start_index + test_periods - 1],
        )
        windows.append(window)
        test_start_index += step
    return windows
