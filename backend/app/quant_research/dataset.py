from __future__ import annotations

from typing import Iterable

import pandas as pd


PRICE_COLUMNS = ("open", "high", "low", "close")


def build_adjusted_price_panel(bars: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Build end-date anchored adjusted OHLC without silently using raw prices."""
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

    anchor = merged.groupby("ts_code", sort=False)["adj_factor"].transform("last")
    for column in PRICE_COLUMNS:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
        merged[f"adj_{column}"] = merged[column] * merged["adj_factor"] / anchor
    merged["adjusted_return"] = merged.groupby("ts_code", sort=False)["adj_close"].pct_change(fill_method=None)
    return merged.reset_index(drop=True)


def attach_fundamentals_asof(panel: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    """Attach the latest financial record announced on or before each trade date."""
    _require_columns(panel, ("ts_code", "trade_date"), "研究面板")
    _require_columns(fundamentals, ("ts_code", "ann_date", "end_date"), "财务指标")

    left = panel.copy()
    right = fundamentals.copy()
    left["trade_date"] = pd.to_datetime(left["trade_date"])
    right["ann_date"] = pd.to_datetime(right["ann_date"])
    right["end_date"] = pd.to_datetime(right["end_date"])
    right = right.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(
        ["ts_code", "ann_date"], keep="last"
    )
    value_columns = [column for column in right.columns if column not in {"ts_code", "ann_date"}]

    merged_parts: list[pd.DataFrame] = []
    for ts_code, price_group in left.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False):
        financial_group = right[right["ts_code"] == ts_code].drop(columns=["ts_code"]).sort_values("ann_date")
        if financial_group.empty:
            merged = price_group.copy()
            merged["ann_date"] = pd.NaT
            for column in value_columns:
                merged[column] = pd.NA
        else:
            merged = pd.merge_asof(
                price_group.sort_values("trade_date"),
                financial_group,
                left_on="trade_date",
                right_on="ann_date",
                direction="backward",
                allow_exact_matches=True,
            )
        merged_parts.append(merged)

    if not merged_parts:
        return left
    result = pd.concat(merged_parts, ignore_index=True).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    invalid = result["ann_date"].notna() & (result["ann_date"] > result["trade_date"])
    if invalid.any():
        raise AssertionError("point-in-time 财务关联出现未来公告日期")
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
