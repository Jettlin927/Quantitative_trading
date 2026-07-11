from __future__ import annotations

from datetime import date
import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine

from ..models import IndexDailyBar, StockAdjustFactor, StockDailyBar, StockLimitPrice, StockListing, StockSuspendEvent
from .dataset import build_adjusted_price_panel


def load_stock_research_panel(
    engine: Engine,
    ts_codes: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Load an explicit, historical-universe-safe A-share panel from PostgreSQL."""
    symbols = sorted({code.strip().upper() for code in ts_codes if code.strip()})
    if not symbols:
        raise ValueError("必须显式提供研究股票池，禁止隐式加载当前全市场")
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")

    suspended = (
        select(StockSuspendEvent.id)
        .where(
            StockSuspendEvent.ts_code == StockDailyBar.ts_code,
            StockSuspendEvent.trade_date == StockDailyBar.trade_date,
            StockSuspendEvent.suspend_type == "S",
        )
        .exists()
    )
    suspended_at_open = (
        select(StockSuspendEvent.id)
        .where(
            StockSuspendEvent.ts_code == StockDailyBar.ts_code,
            StockSuspendEvent.trade_date == StockDailyBar.trade_date,
            StockSuspendEvent.suspend_type == "S",
            or_(
                StockSuspendEvent.suspend_timing == "",
                StockSuspendEvent.suspend_timing.like("09:30%"),
                StockSuspendEvent.suspend_timing.like("9:30%"),
            ),
        )
        .exists()
    )
    stmt = (
        select(
            StockDailyBar.ts_code,
            StockDailyBar.trade_date,
            StockDailyBar.open,
            StockDailyBar.high,
            StockDailyBar.low,
            StockDailyBar.close,
            StockDailyBar.pre_close,
            StockDailyBar.vol,
            StockDailyBar.amount,
            StockAdjustFactor.adj_factor,
            StockListing.list_status,
            StockListing.list_date,
            StockListing.delist_date,
            StockLimitPrice.up_limit,
            StockLimitPrice.down_limit,
            suspended.label("is_suspended"),
            suspended_at_open.label("is_suspended_at_open"),
        )
        .select_from(StockDailyBar)
        .outerjoin(
            StockAdjustFactor,
            and_(
                StockAdjustFactor.ts_code == StockDailyBar.ts_code,
                StockAdjustFactor.trade_date == StockDailyBar.trade_date,
            ),
        )
        .outerjoin(StockListing, StockListing.ts_code == StockDailyBar.ts_code)
        .outerjoin(
            StockLimitPrice,
            and_(StockLimitPrice.ts_code == StockDailyBar.ts_code, StockLimitPrice.trade_date == StockDailyBar.trade_date),
        )
        .where(
            StockDailyBar.ts_code.in_(symbols),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date)
    )
    frame = pd.read_sql_query(stmt, engine, parse_dates=["trade_date", "list_date", "delist_date"])
    if frame.empty:
        raise ValueError("研究股票池在指定日期范围内没有日线数据")

    missing_listing = frame["list_date"].isna() | frame["list_status"].isna()
    if missing_listing.any():
        sample = sorted(frame.loc[missing_listing, "ts_code"].dropna().unique())[:10]
        raise ValueError(f"历史上市状态缺失：{', '.join(sample)}")
    eligible = (frame["list_date"] <= frame["trade_date"]) & (
        frame["delist_date"].isna() | (frame["delist_date"] >= frame["trade_date"])
    )
    frame = frame[eligible].copy()
    missing_limit = frame["up_limit"].isna() | frame["down_limit"].isna()
    if missing_limit.any():
        sample = frame.loc[missing_limit, ["ts_code", "trade_date"]].head(5).to_dict("records")
        raise ValueError(f"涨跌停价格缺失：{sample}")

    factors = frame[["ts_code", "trade_date", "adj_factor"]]
    adjusted = build_adjusted_price_panel(frame.drop(columns=["adj_factor"]), factors)
    open_suspended = adjusted["is_suspended_at_open"].astype(bool)
    adjusted["is_buyable_at_open"] = (~open_suspended) & (adjusted["open"] < adjusted["up_limit"])
    adjusted["is_sellable_at_open"] = (~open_suspended) & (adjusted["open"] > adjusted["down_limit"])
    adjusted["is_valuation_carried"] = False
    return _append_full_day_suspension_rows(engine, adjusted, symbols, start_date, end_date)


def load_index_benchmark(engine: Engine, ts_code: str, start_date: date, end_date: date) -> pd.DataFrame:
    stmt = (
        select(IndexDailyBar.trade_date, IndexDailyBar.close)
        .where(
            IndexDailyBar.ts_code == ts_code.upper(),
            IndexDailyBar.trade_date >= start_date,
            IndexDailyBar.trade_date <= end_date,
        )
        .order_by(IndexDailyBar.trade_date)
    )
    frame = pd.read_sql_query(stmt, engine, parse_dates=["trade_date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"])
    if frame.empty or (frame["close"] <= 0).any():
        raise ValueError(f"基准 {ts_code.upper()} 在指定范围内没有有效日线")
    frame["nav"] = frame["close"] / frame["close"].iloc[0]
    return frame[["trade_date", "nav"]]


def _append_full_day_suspension_rows(
    engine: Engine,
    panel: pd.DataFrame,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    stmt = (
        select(StockSuspendEvent.ts_code, StockSuspendEvent.trade_date)
        .where(
            StockSuspendEvent.ts_code.in_(symbols),
            StockSuspendEvent.trade_date >= start_date,
            StockSuspendEvent.trade_date <= end_date,
            StockSuspendEvent.suspend_type == "S",
            StockSuspendEvent.suspend_timing == "",
        )
        .order_by(StockSuspendEvent.ts_code, StockSuspendEvent.trade_date)
    )
    events = pd.read_sql_query(stmt, engine, parse_dates=["trade_date"])
    if events.empty:
        return panel.reset_index(drop=True)
    existing = set(zip(panel["ts_code"].astype(str), panel["trade_date"], strict=True))
    price_columns = {
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vol",
        "amount",
        "up_limit",
        "down_limit",
        "adj_factor",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adjusted_return",
        "total_return_index",
    }
    carried_rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        key = (str(event.ts_code), event.trade_date)
        if key in existing:
            continue
        symbol_rows = panel[panel["ts_code"] == event.ts_code]
        if symbol_rows.empty:
            continue
        row = symbol_rows.iloc[0].to_dict()
        row["trade_date"] = event.trade_date
        for column in price_columns & set(row):
            row[column] = float("nan")
        row["is_suspended"] = True
        row["is_suspended_at_open"] = True
        row["is_buyable_at_open"] = False
        row["is_sellable_at_open"] = False
        row["is_valuation_carried"] = True
        carried_rows.append(row)
    if not carried_rows:
        return panel.reset_index(drop=True)
    return (
        pd.concat([panel, pd.DataFrame(carried_rows)], ignore_index=True)
        .sort_values(["ts_code", "trade_date"])
        .reset_index(drop=True)
    )
