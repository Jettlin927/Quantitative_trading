from __future__ import annotations

import math
from numbers import Integral, Real

import pandas as pd


def simple_returns(
    frame: pd.DataFrame,
    value_column: str,
    output_column: str = "simple_return",
) -> pd.DataFrame:
    normalized = _normalize_feature_frame(frame, value_column, ("ts_code", "trade_date"))
    values = normalized[value_column]
    result = values.groupby(normalized["ts_code"], sort=False).pct_change(fill_method=None)
    return _feature_result(normalized, output_column, result)


def interval_returns(
    frame: pd.DataFrame,
    value_column: str,
    *,
    window: int,
    output_column: str = "interval_return",
) -> pd.DataFrame:
    window = _positive_window(window)
    normalized = _normalize_feature_frame(frame, value_column, ("ts_code", "trade_date"))
    values = normalized[value_column]
    previous = values.groupby(normalized["ts_code"], sort=False).shift(window)
    result = values / previous - 1.0
    return _feature_result(normalized, output_column, result)


def moving_average(
    frame: pd.DataFrame,
    value_column: str,
    *,
    window: int,
    min_periods: int | None = None,
    output_column: str = "moving_average",
) -> pd.DataFrame:
    window = _positive_window(window)
    minimum = _min_periods(window, min_periods)
    normalized = _normalize_feature_frame(frame, value_column, ("ts_code", "trade_date"))
    result = (
        normalized[value_column]
        .groupby(normalized["ts_code"], sort=False)
        .rolling(window=window, min_periods=minimum)
        .mean()
        .reset_index(level=0, drop=True)
    )
    return _feature_result(normalized, output_column, result)


def rolling_volatility(
    frame: pd.DataFrame,
    value_column: str,
    *,
    window: int,
    min_periods: int | None = None,
    output_column: str = "rolling_volatility",
) -> pd.DataFrame:
    window = _positive_window(window)
    minimum = _min_periods(window, min_periods)
    normalized = _normalize_feature_frame(frame, value_column, ("ts_code", "trade_date"))
    returns = normalized[value_column].groupby(normalized["ts_code"], sort=False).pct_change(fill_method=None)
    result = (
        returns.groupby(normalized["ts_code"], sort=False)
        .rolling(window=window, min_periods=minimum)
        .std(ddof=1)
        .reset_index(level=0, drop=True)
    )
    return _feature_result(normalized, output_column, result)


def rolling_zscore(
    frame: pd.DataFrame,
    value_column: str,
    *,
    window: int,
    min_periods: int | None = None,
    output_column: str = "rolling_zscore",
) -> pd.DataFrame:
    window = _positive_window(window)
    minimum = _min_periods(window, min_periods)
    normalized = _normalize_feature_frame(frame, value_column, ("ts_code", "trade_date"))
    grouped = normalized[value_column].groupby(normalized["ts_code"], sort=False)
    mean = grouped.rolling(window=window, min_periods=minimum).mean().reset_index(level=0, drop=True)
    standard_deviation = (
        grouped.rolling(window=window, min_periods=minimum).std(ddof=0).reset_index(level=0, drop=True)
    )
    result = (normalized[value_column] - mean) / standard_deviation.where(standard_deviation > 0)
    return _feature_result(normalized, output_column, result)


def cross_section_percentile_rank(
    frame: pd.DataFrame,
    value_column: str,
    *,
    ascending: bool = True,
    output_column: str = "percentile_rank",
) -> pd.DataFrame:
    normalized = _normalize_feature_frame(frame, value_column, ("trade_date", "ts_code"))
    result = normalized.groupby("trade_date", sort=False)[value_column].rank(
        method="average",
        ascending=ascending,
        pct=True,
        na_option="keep",
    )
    return _feature_result(normalized, output_column, result)


def cross_section_winsorize(
    frame: pd.DataFrame,
    value_column: str,
    *,
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.99,
    output_column: str = "winsorized",
) -> pd.DataFrame:
    if not 0 <= lower_quantile < upper_quantile <= 1:
        raise ValueError("winsorize quantile 必须满足 0 <= lower < upper <= 1")
    normalized = _normalize_feature_frame(frame, value_column, ("trade_date", "ts_code"))
    grouped = normalized.groupby("trade_date", sort=False)[value_column]
    lower = grouped.transform(lambda values: values.quantile(lower_quantile))
    upper = grouped.transform(lambda values: values.quantile(upper_quantile))
    result = normalized[value_column].clip(lower=lower, upper=upper)
    return _feature_result(normalized, output_column, result)


def equal_weight_targets(
    frame: pd.DataFrame,
    score_column: str,
    *,
    top_n: int,
    max_weight: float = 1.0,
) -> pd.DataFrame:
    if isinstance(top_n, bool) or not isinstance(top_n, Integral) or top_n <= 0:
        raise ValueError("top_n 必须是正整数")
    if isinstance(max_weight, bool) or not isinstance(max_weight, Real) or not 0 < float(max_weight) <= 1:
        raise ValueError("max_weight 必须在 (0, 1] 范围")
    normalized = _normalize_feature_frame(frame, score_column, ("trade_date", "ts_code"))
    rows: list[dict[str, object]] = []
    for trade_date, group in normalized.groupby("trade_date", sort=True):
        eligible = group.dropna(subset=[score_column]).sort_values(
            [score_column, "ts_code"],
            ascending=[False, True],
            kind="stable",
        )
        selected = eligible.head(int(top_n))
        if selected.empty:
            continue
        weight = min(1.0 / len(selected), float(max_weight))
        rows.extend(
            {
                "signal_date": trade_date,
                "available_date": trade_date,
                "ts_code": row.ts_code,
                "target_weight": weight,
            }
            for row in selected.itertuples(index=False)
        )
    return pd.DataFrame(
        rows,
        columns=["signal_date", "available_date", "ts_code", "target_weight"],
    ).sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def _normalize_feature_frame(
    frame: pd.DataFrame,
    value_column: str,
    order: tuple[str, str],
) -> pd.DataFrame:
    required = {"trade_date", "ts_code", value_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"特征输入缺少字段：{', '.join(missing)}")
    if frame.empty:
        raise ValueError("特征输入不能为空")
    normalized = frame[["trade_date", "ts_code", value_column]].copy().reset_index(drop=True)
    try:
        dates = pd.to_datetime(normalized["trade_date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("trade_date 必须是有效日期") from exc
    if getattr(dates.dt, "tz", None) is not None:
        raise ValueError("trade_date 禁止带时区或混合时区")
    normalized["trade_date"] = dates
    normalized["ts_code"] = normalized["ts_code"].astype(str).str.strip().str.upper()
    if normalized["ts_code"].eq("").any():
        raise ValueError("ts_code 不能为空")
    if normalized.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("特征输入存在重复 trade_date + ts_code")
    expected_keys = normalized.sort_values(list(order), kind="stable")[[*order]].reset_index(drop=True)
    if not normalized[[*order]].reset_index(drop=True).equals(expected_keys):
        raise ValueError(f"特征输入必须按 {' + '.join(order)} 稳定排序")
    original = normalized[value_column]
    numeric = pd.to_numeric(original, errors="coerce")
    invalid_cast = original.notna() & numeric.isna()
    infinite = numeric.notna() & ~numeric.map(math.isfinite)
    if invalid_cast.any() or infinite.any():
        raise ValueError(f"{value_column} 包含非数值或非有限值")
    normalized[value_column] = numeric.astype(float)
    return normalized


def _feature_result(
    frame: pd.DataFrame,
    output_column: str,
    values: pd.Series,
) -> pd.DataFrame:
    if not output_column or output_column in {"trade_date", "ts_code"}:
        raise ValueError("特征输出列名无效")
    if (values.notna() & ~values.map(math.isfinite)).any():
        raise ValueError(f"{output_column} 产生非有限值")
    return pd.DataFrame(
        {
            "trade_date": frame["trade_date"],
            "ts_code": frame["ts_code"],
            output_column: values,
        }
    )


def _positive_window(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError("window 必须是正整数")
    return int(value)


def _min_periods(window: int, value: int | None) -> int:
    if value is None:
        return window
    if isinstance(value, bool) or not isinstance(value, Integral) or not 1 <= value <= window:
        raise ValueError("min_periods 必须在 1..window 范围")
    return int(value)
