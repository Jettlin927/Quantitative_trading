from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from .metrics import summarize_performance


WALK_FORWARD_WINDOW_COLUMNS = (
    "window_id",
    "mode",
    "train_start",
    "train_end",
    "test_start",
    "test_end",
    "train_periods",
    "test_periods",
)
WALK_FORWARD_METRIC_COLUMNS = (
    "window_id",
    "sample_role",
    "start_date",
    "end_date",
    "observations",
    "total_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe",
    "max_drawdown",
    "calmar",
    "positive_day_rate",
    "downside_volatility",
    "sortino",
    "max_drawdown_duration",
    "beta",
    "benchmark_total_return",
    "excess_total_return",
    "tracking_error",
    "information_ratio",
)


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


def validate_validation_policy(value: Any) -> dict[str, Any]:
    if value is None:
        return {"mode": "none"}
    if not isinstance(value, dict):
        raise ValueError("validationPolicy 必须是 JSON object")
    mode = value.get("mode")
    if mode == "none":
        if set(value) != {"mode"}:
            raise ValueError("validationPolicy=none 只允许 mode 字段")
        return {"mode": "none"}
    if mode not in {"anchored", "rolling"}:
        raise ValueError("validationPolicy.mode 只允许 none、anchored 或 rolling")
    expected = {"mode", "trainPeriods", "testPeriods", "stepPeriods"}
    if set(value) != expected:
        raise ValueError("walk-forward validationPolicy 字段必须固定且禁止参数搜索")
    normalized = {"mode": mode}
    for field in ("trainPeriods", "testPeriods", "stepPeriods"):
        periods = value[field]
        if isinstance(periods, bool) or not isinstance(periods, int) or periods <= 0:
            raise ValueError(f"validationPolicy.{field} 必须是正整数")
        normalized[field] = periods
    if normalized["stepPeriods"] < normalized["testPeriods"]:
        raise ValueError("walk-forward test 窗口禁止重叠")
    return normalized


def build_walk_forward_window_frame(
    trade_dates: Iterable[object],
    policy: Any,
) -> pd.DataFrame:
    normalized = validate_validation_policy(policy)
    if normalized["mode"] == "none":
        return pd.DataFrame(columns=WALK_FORWARD_WINDOW_COLUMNS)
    windows = build_walk_forward_windows(
        trade_dates,
        train_periods=normalized["trainPeriods"],
        test_periods=normalized["testPeriods"],
        step_periods=normalized["stepPeriods"],
        anchored=normalized["mode"] == "anchored",
    )
    if not windows:
        raise ValueError("研究区间不足以形成一个 walk-forward test 窗口")
    return pd.DataFrame(
        [
            {
                "window_id": f"wf-{index:04d}",
                "mode": normalized["mode"],
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "train_periods": normalized["trainPeriods"],
                "test_periods": normalized["testPeriods"],
            }
            for index, window in enumerate(windows, start=1)
        ],
        columns=WALK_FORWARD_WINDOW_COLUMNS,
    )


def evaluate_walk_forward(
    nav: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    *,
    benchmark: str,
    research_start: object,
    research_end: object,
    policy: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    normalized = validate_validation_policy(policy)
    if normalized["mode"] == "none":
        raise ValueError("validationPolicy=none 不生成 walk-forward 工件")
    strategy = nav[["trade_date", "nav"]].copy()
    strategy["trade_date"] = pd.to_datetime(strategy["trade_date"], errors="raise")
    strategy["nav"] = pd.to_numeric(strategy["nav"], errors="raise")
    start = pd.Timestamp(research_start)
    end = pd.Timestamp(research_end)
    strategy = strategy[strategy["trade_date"].between(start, end)].sort_values(
        "trade_date"
    )
    if strategy.empty or strategy["trade_date"].duplicated().any():
        raise ValueError("walk-forward 策略净值日期为空或重复")
    benchmark_frame = benchmark_bars.copy()
    required_benchmark = {"ts_code", "trade_date", "close"}
    if not required_benchmark.issubset(benchmark_frame.columns):
        raise ValueError("walk-forward 冻结基准缺少字段")
    benchmark_frame["trade_date"] = pd.to_datetime(
        benchmark_frame["trade_date"], errors="raise"
    )
    benchmark_frame["close"] = pd.to_numeric(
        benchmark_frame["close"], errors="raise"
    )
    benchmark_frame = benchmark_frame[
        benchmark_frame["ts_code"].eq(benchmark)
        & benchmark_frame["trade_date"].between(start, end)
    ][["trade_date", "close"]].sort_values("trade_date")
    if benchmark_frame.empty or benchmark_frame["trade_date"].duplicated().any():
        raise ValueError("walk-forward 冻结基准日期为空或重复")
    windows = build_walk_forward_window_frame(strategy["trade_date"], normalized)
    rows: list[dict[str, Any]] = []
    for window in windows.itertuples(index=False):
        test_nav = strategy[
            strategy["trade_date"].between(window.test_start, window.test_end)
        ]
        test_benchmark = benchmark_frame[
            benchmark_frame["trade_date"].between(window.test_start, window.test_end)
        ].rename(columns={"close": "nav"})
        if len(test_nav) != window.test_periods or len(test_benchmark) != window.test_periods:
            raise ValueError("walk-forward test 窗口缺少策略或基准日期")
        metrics = summarize_performance(
            test_nav,
            test_benchmark,
            include_extended=True,
        )
        rows.append(
            {
                "window_id": window.window_id,
                "sample_role": "test_oos",
                "start_date": metrics["startDate"],
                "end_date": metrics["endDate"],
                "observations": metrics["observations"],
                "total_return": metrics["totalReturn"],
                "annualized_return": metrics["annualizedReturn"],
                "annualized_volatility": metrics["annualizedVolatility"],
                "sharpe": metrics["sharpe"],
                "max_drawdown": metrics["maxDrawdown"],
                "calmar": metrics["calmar"],
                "positive_day_rate": metrics["positiveDayRate"],
                "downside_volatility": metrics["downsideVolatility"],
                "sortino": metrics["sortino"],
                "max_drawdown_duration": metrics["maxDrawdownDuration"],
                "beta": metrics["beta"],
                "benchmark_total_return": metrics["benchmarkTotalReturn"],
                "excess_total_return": metrics["excessTotalReturn"],
                "tracking_error": metrics["trackingError"],
                "information_ratio": metrics["informationRatio"],
            }
        )
    metrics_frame = pd.DataFrame(rows, columns=WALK_FORWARD_METRIC_COLUMNS)
    return (
        windows,
        metrics_frame,
        {
            "mode": normalized["mode"],
            "oosOnly": True,
            "testObservationCount": int(metrics_frame["observations"].sum()),
            "windowCount": len(windows),
        },
    )
