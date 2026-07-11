from __future__ import annotations

from typing import Iterable

import pandas as pd


PRICE_COLUMNS = ("open", "high", "low", "close")


def build_adjusted_price_panel(bars: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Build causal adjusted OHLC without using a future end-date anchor."""
    _require_columns(bars, ("ts_code", "trade_date", *PRICE_COLUMNS), "日线")
    _require_columns(factors, ("ts_code", "trade_date", "adj_factor"), "复权因子")

    price_frame = bars.copy()
    factor_frame = factors.copy()
    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"])
    factor_frame["trade_date"] = pd.to_datetime(factor_frame["trade_date"])
    factor_frame["adj_factor"] = pd.to_numeric(factor_frame["adj_factor"], errors="coerce")
    factor_frame = factor_frame.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )

    merged = price_frame.merge(
        factor_frame[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
        validate="many_to_one",
    ).sort_values(["ts_code", "trade_date"])
    missing = merged["adj_factor"].isna() | (merged["adj_factor"] <= 0)
    if missing.any():
        sample = merged.loc[missing, ["ts_code", "trade_date"]].head(5).to_dict("records")
        raise ValueError(f"复权因子缺失或无效，禁止回退到原始价格：{sample}")

    anchor = merged.groupby("ts_code", sort=False)["adj_factor"].transform("first")
    for column in PRICE_COLUMNS:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
        merged[f"adj_{column}"] = merged[column] * merged["adj_factor"] / anchor
    invalid_prices = merged[[f"adj_{column}" for column in PRICE_COLUMNS]].isna().any(axis=1) | (
        merged[[f"adj_{column}" for column in PRICE_COLUMNS]] <= 0
    ).any(axis=1)
    if invalid_prices.any():
        sample = merged.loc[invalid_prices, ["ts_code", "trade_date"]].head(5).to_dict("records")
        raise ValueError(f"价格缺失或无效，无法生成因果复权序列：{sample}")
    merged["adjusted_return"] = merged.groupby("ts_code", sort=False)["adj_close"].pct_change(fill_method=None)
    first_close = merged.groupby("ts_code", sort=False)["adj_close"].transform("first")
    merged["total_return_index"] = merged["adj_close"] / first_close
    return merged.reset_index(drop=True)


def attach_fundamentals_asof(
    panel: pd.DataFrame,
    fundamentals: pd.DataFrame,
    *,
    trade_dates: Iterable[object],
    period_policy: str | None = None,
) -> pd.DataFrame:
    """Attach fundamentals from their conservative next-trading-day availability."""
    _require_columns(panel, ("ts_code", "trade_date"), "研究面板")
    _require_columns(fundamentals, ("ts_code", "ann_date", "end_date"), "财务指标")

    left = panel.copy()
    right = fundamentals.copy()
    left["trade_date"] = pd.to_datetime(left["trade_date"])
    right["ann_date"] = pd.to_datetime(right["ann_date"])
    right["end_date"] = pd.to_datetime(right["end_date"])
    right = right.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(
        ["ts_code", "end_date", "ann_date"], keep="last"
    )
    if period_policy not in {None, "latest_end_date"}:
        raise ValueError(f"不支持的 period_policy：{period_policy}")

    calendar = pd.DatetimeIndex(pd.to_datetime(list(trade_dates), errors="coerce")).dropna().drop_duplicates().sort_values()
    if calendar.empty:
        raise ValueError("官方开市交易日历不能为空")
    outside_calendar = sorted(set(left["trade_date"]) - set(calendar))
    if outside_calendar:
        sample = ", ".join(value.date().isoformat() for value in outside_calendar[:5])
        raise ValueError(f"研究面板包含非官方开市日：{sample}")
    right["available_date"] = right["ann_date"].map(lambda value: _next_trade_date(calendar, value))

    duplicate_available = right["available_date"].notna() & right.duplicated(
        ["ts_code", "available_date"], keep=False
    )
    if duplicate_available.any() and period_policy is None:
        sample = right.loc[duplicate_available, ["ts_code", "ann_date", "end_date", "available_date"]].head(5)
        raise ValueError(
            "同一可用日存在多个报告期，必须显式指定 period_policy："
            f"{sample.to_dict('records')}"
        )
    if period_policy == "latest_end_date":
        right = right.sort_values(["ts_code", "available_date", "end_date"]).drop_duplicates(
            ["ts_code", "available_date"], keep="last"
        )
    output_columns = [column for column in right.columns if column != "ts_code"]

    merged_parts: list[pd.DataFrame] = []
    for ts_code, price_group in left.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False):
        financial_group = (
            right[(right["ts_code"] == ts_code) & right["available_date"].notna()]
            .drop(columns=["ts_code"])
            .sort_values("available_date")
        )
        if financial_group.empty:
            merged = price_group.copy()
            for column in output_columns:
                merged[column] = pd.NaT if column in {"ann_date", "end_date", "available_date"} else pd.NA
        else:
            merged = pd.merge_asof(
                price_group.sort_values("trade_date"),
                financial_group,
                left_on="trade_date",
                right_on="available_date",
                direction="backward",
                allow_exact_matches=True,
            )
        merged_parts.append(merged)

    if not merged_parts:
        return left
    result = pd.concat(merged_parts, ignore_index=True).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    invalid = result["available_date"].notna() & (result["available_date"] > result["trade_date"])
    if invalid.any():
        raise AssertionError("point-in-time 财务关联出现未来可用日期")
    return result


def active_members_as_of(memberships: pd.DataFrame, trade_date: str | pd.Timestamp, index_code: str | None = None) -> set[str]:
    """Return constituents active on a historical date using inclusive membership bounds."""
    _require_columns(memberships, ("index_code", "con_code", "in_date", "out_date"), "行业成员")
    frame = memberships.copy()
    frame["in_date"] = pd.to_datetime(frame["in_date"])
    frame["out_date"] = pd.to_datetime(frame["out_date"])
    target_date = pd.Timestamp(trade_date)
    if index_code:
        frame = frame[frame["index_code"] == index_code]
    active = frame[(frame["in_date"] <= target_date) & (frame["out_date"].isna() | (frame["out_date"] >= target_date))]
    return set(active["con_code"].dropna().astype(str))


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少字段：{', '.join(missing)}")


def _next_trade_date(calendar: pd.DatetimeIndex, announced_at: pd.Timestamp) -> pd.Timestamp:
    index = calendar.searchsorted(announced_at, side="right")
    return calendar[index] if index < len(calendar) else pd.NaT
