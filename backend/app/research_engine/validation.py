from __future__ import annotations

import pandas as pd


WalkForwardWindow = tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]


def build_walk_forward_windows(
    index: pd.DatetimeIndex,
    train_size: int,
    test_size: int,
    step_size: int,
    anchored: bool,
) -> list[WalkForwardWindow]:
    windows: list[WalkForwardWindow] = []
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
