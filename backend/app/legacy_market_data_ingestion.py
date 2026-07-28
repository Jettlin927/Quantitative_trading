from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .json_safety import json_safe_value
from .market_data_ingestion import (
    normalize_status as normalize_ingestion_status,
)
from .market_data_ingestion import (
    result_rows as ingestion_result_rows,
)
from .models import (
    Asset,
    AssetDailyPrice,
    DataSyncRun,
    IndexDailyBar,
    PortfolioSnapshot,
    Stock,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
    WatchlistItem,
)
from .schemas import (
    SyncIndexDailyRequest,
    SyncMarketDataRequest,
    SyncMarketFundamentalsRequest,
    SyncStockBasicRequest,
    SyncStockListingsRequest,
    SyncSuspendEventsRequest,
    SyncUsExperimentPricesRequest,
    SyncUsExperimentTargetedUniverseRequest,
)
from .tushare_client import (
    decimal_or_none,
    get_pro_api,
    parse_tushare_date,
    tushare_date,
)
from .us_experiment import (
    refresh_overview_snapshot as refresh_us_experiment_overview_snapshot,
)
from .us_experiment import (
    refresh_universe as refresh_us_experiment_universe,
)
from .us_experiment import (
    register_targeted_universe as register_us_experiment_targeted_universe,
)
from .us_experiment import (
    sync_daily_prices as sync_us_experiment_daily_prices,
)
from .us_research import build_us_research_import_preview

REPO_ROOT = Path(__file__).resolve().parents[2]
STOCK_FIELDS = "ts_code,symbol,name,area,industry,market,list_date"
DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
DAILY_BASIC_FIELDS = "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv"
FINA_INDICATOR_FIELDS = "ts_code,ann_date,end_date,eps,dt_eps,bps,netprofit_margin,grossprofit_margin,roe,roe_waa,roa,debt_to_assets,current_ratio,quick_ratio,assets_turn,basic_eps_yoy,op_yoy,netprofit_yoy,tr_yoy,or_yoy,q_sales_yoy,q_profit_yoy,update_flag"
ADJUST_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
INDEX_DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
STOCK_LISTING_FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date"
STOCK_LIMIT_FIELDS = "ts_code,trade_date,pre_close,up_limit,down_limit"
STOCK_SUSPEND_FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"


def sync_stock_basic(
    payload: SyncStockBasicRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    df = pro.stock_basic(exchange="", list_status="L", fields=STOCK_FIELDS)
    rows = [
        row
        for item in df.to_dict("records")
        if (row := stock_basic_record_to_row(item))
    ]
    upserted = upsert_rows(db, Stock, rows, ["ts_code"])
    record_sync_run(
        db, target="stock_basic", rows_upserted=upserted, message=f"stocks={upserted}"
    )
    return {"status": "ok", "rows_upserted": upserted}


def sync_stock_listings(
    payload: SyncStockListingsRequest,
    db: Session,
    *,
    provider_factory: Any = get_pro_api,
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    rows: list[dict[str, Any]] = []
    failed_statuses: list[str] = []
    statuses = list(dict.fromkeys(payload.statuses))
    for list_status in statuses:
        try:
            df = pro.stock_basic(
                exchange="", list_status=list_status, fields=STOCK_LISTING_FIELDS
            )
            rows.extend(
                row
                for item in df.to_dict("records")
                if (row := stock_listing_record_to_row(item))
            )
        except Exception as exc:
            failed_statuses.append(f"{list_status}:{exc}")
    upserted = upsert_rows(
        db, StockListing, dedupe_rows(rows, ("ts_code",)), ["ts_code"]
    )
    status = "partial" if failed_statuses else "ok"
    record_sync_run(
        db,
        target="stock_listings",
        rows_upserted=upserted,
        status=status,
        message=f"statuses={','.join(statuses)}, failed_statuses={len(failed_statuses)}",
    )
    return {
        "status": status,
        "rows_upserted": upserted,
        "failed_statuses": failed_statuses,
    }


def sync_market_daily(
    payload: SyncMarketDataRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(
            db, StockDailyBar.trade_date, trade_dates, payload.min_existing_rows
        )
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]
    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.daily(trade_date=tushare_date(trade_day), fields=DAILY_FIELDS)
            rows = dedupe_rows(
                [daily_record_to_row(item) for item in df.to_dict("records")],
                ("ts_code", "trade_date"),
            )
            rows_upserted += upsert_rows(
                db, StockDailyBar, rows, ["ts_code", "trade_date"]
            )
        except Exception as exc:
            failed_dates.append(f"{trade_day}:{exc}")
    status = "partial" if failed_dates else "ok"
    message = f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}"
    record_sync_run(
        db,
        target="market:daily",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=message,
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "trade_dates": len(trade_dates),
        "failed_dates": failed_dates,
    }


def sync_market_limit_prices(
    payload: SyncMarketDataRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    stock_codes = set(db.scalars(select(StockListing.ts_code)).all()) or set(
        db.scalars(select(Stock.ts_code)).all()
    )
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(
            db, StockLimitPrice.trade_date, trade_dates, payload.min_existing_rows
        )
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]
    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.stk_limit(
                trade_date=tushare_date(trade_day), fields=STOCK_LIMIT_FIELDS
            )
            rows = [
                row
                for item in df.to_dict("records")
                if (row := stock_limit_price_record_to_row(item))
                and (not stock_codes or row["ts_code"] in stock_codes)
            ]
            rows_upserted += upsert_rows(
                db,
                StockLimitPrice,
                dedupe_rows(rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
        except Exception as exc:
            failed_dates.append(f"{trade_day}:{exc}")
    status = "partial" if failed_dates else "ok"
    record_sync_run(
        db,
        target="market:limit_prices",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}",
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "trade_dates": len(trade_dates),
        "failed_dates": failed_dates,
    }


def sync_market_suspend_events(
    payload: SyncSuspendEventsRequest,
    db: Session,
    *,
    provider_factory: Any = get_pro_api,
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]
    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.suspend_d(
                trade_date=tushare_date(trade_day), fields=STOCK_SUSPEND_FIELDS
            )
            rows = [
                row
                for item in df.to_dict("records")
                if (row := stock_suspend_event_record_to_row(item))
            ]
            rows_upserted += upsert_rows(
                db,
                StockSuspendEvent,
                dedupe_rows(
                    rows, ("ts_code", "trade_date", "suspend_type", "suspend_timing")
                ),
                ["ts_code", "trade_date", "suspend_type", "suspend_timing"],
            )
        except Exception as exc:
            failed_dates.append(f"{trade_day}:{exc}")
    status = "partial" if failed_dates else "ok"
    record_sync_run(
        db,
        target="market:suspend_events",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}",
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "trade_dates": len(trade_dates),
        "failed_dates": failed_dates,
    }


def sync_market_daily_basic(
    payload: SyncMarketDataRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(
            db, StockDailyBasic.trade_date, trade_dates, payload.min_existing_rows
        )
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]
    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.query(
                "daily_basic",
                ts_code="",
                trade_date=tushare_date(trade_day),
                fields=DAILY_BASIC_FIELDS,
            )
            rows = [
                row
                for item in df.to_dict("records")
                if (row := daily_basic_record_to_row(item))
            ]
            rows_upserted += upsert_rows(
                db,
                StockDailyBasic,
                dedupe_rows(rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
        except Exception as exc:
            failed_dates.append(f"{trade_day}:{exc}")
    status = "partial" if failed_dates else "ok"
    message = f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}"
    record_sync_run(
        db,
        target="market:daily_basic",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=message,
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "trade_dates": len(trade_dates),
        "failed_dates": failed_dates,
    }


def sync_market_fundamentals(
    payload: SyncMarketFundamentalsRequest,
    db: Session,
    *,
    provider_factory: Any = get_pro_api,
    now_factory: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    stocks = list(db.scalars(select(Stock.ts_code).order_by(Stock.ts_code)).all())
    if payload.max_stocks:
        stocks = stocks[: payload.max_stocks]
    rows_upserted = 0
    failed_stocks: list[str] = []
    skipped_stocks = 0
    request_interval = 60.0 / payload.rate_per_minute
    last_request_at: float | None = None
    for ts_code in stocks:
        try:
            if last_request_at is not None:
                wait_seconds = request_interval - (time.monotonic() - last_request_at)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            last_request_at = time.monotonic()
            df = pro.fina_indicator(
                ts_code=ts_code,
                start_date=tushare_date(payload.start_date),
                end_date=tushare_date(payload.end_date),
                fields=FINA_INDICATOR_FIELDS,
            )
            observed_at = (now_factory or utc_now)()
            available_from = next_financial_available_date(db, observed_at)
            rows = [
                row
                for item in df.to_dict("records")
                if (
                    row := financial_indicator_record_to_row(
                        item,
                        source_observed_at=observed_at,
                        available_from=available_from,
                    )
                )
            ]
            rows_upserted += insert_financial_revision_rows(db, rows)
        except Exception as exc:
            failed_stocks.append(f"{ts_code}:{exc}")
    status = (
        "failed"
        if stocks and len(failed_stocks) == len(stocks)
        else "partial"
        if failed_stocks
        else "ok"
    )
    message = f"stocks={len(stocks)}, skipped_stocks={skipped_stocks}, failed_stocks={len(failed_stocks)}, rate_per_minute={payload.rate_per_minute}"
    record_sync_run(
        db,
        target="market:fundamentals",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=message,
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "skipped_stocks": skipped_stocks,
        "failed_stocks": failed_stocks,
        "rate_per_minute": payload.rate_per_minute,
    }


def sync_market_adjust_factors(
    payload: SyncMarketDataRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(
            db, StockAdjustFactor.trade_date, trade_dates, payload.min_existing_rows
        )
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]
    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.adj_factor(
                trade_date=tushare_date(trade_day), fields=ADJUST_FACTOR_FIELDS
            )
            rows = [
                row
                for item in df.to_dict("records")
                if (row := adjust_factor_record_to_row(item))
            ]
            rows_upserted += upsert_rows(
                db,
                StockAdjustFactor,
                dedupe_rows(rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
        except Exception as exc:
            failed_dates.append(f"{trade_day}:{exc}")
    status = "partial" if failed_dates else "ok"
    record_sync_run(
        db,
        target="market:adjust_factors",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}",
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "trade_dates": len(trade_dates),
        "failed_dates": failed_dates,
    }


def sync_index_daily(
    payload: SyncIndexDailyRequest, db: Session, *, provider_factory: Any = get_pro_api
) -> dict[str, Any]:
    pro = provider_factory(payload.token)
    rows_upserted = 0
    failed_indices: list[str] = []
    for ts_code in payload.ts_codes:
        try:
            df = pro.index_daily(
                ts_code=ts_code,
                start_date=tushare_date(payload.start_date),
                end_date=tushare_date(payload.end_date),
                fields=INDEX_DAILY_FIELDS,
            )
            rows = [
                row
                for item in df.to_dict("records")
                if (row := index_daily_record_to_row(item))
            ]
            rows_upserted += upsert_rows(
                db,
                IndexDailyBar,
                dedupe_rows(rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
        except Exception as exc:
            failed_indices.append(f"{ts_code}:{exc}")
    status = "partial" if failed_indices else "ok"
    record_sync_run(
        db,
        target="index_daily",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=rows_upserted,
        status=status,
        message=f"indices={len(payload.ts_codes)}, failed_indices={len(failed_indices)}",
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "failed_indices": failed_indices,
    }


def import_us_research_sample_to_db(db: Session) -> dict[str, Any]:
    preview = build_us_research_import_preview(REPO_ROOT)
    records = preview["records"]
    summary = {
        "assets": upsert_rows(
            db,
            Asset,
            [asset_record_to_row(row) for row in records["assets"]],
            ["natural_key"],
        ),
        "assetDailyPrices": upsert_rows(
            db,
            AssetDailyPrice,
            [
                asset_daily_price_record_to_row(row)
                for row in records["assetDailyPrices"]
            ],
            ["natural_key"],
        ),
        "watchlistItems": upsert_rows(
            db,
            WatchlistItem,
            [watchlist_record_to_row(row) for row in records["watchlistItems"]],
            ["natural_key"],
        ),
        "portfolioSnapshots": upsert_rows(
            db,
            PortfolioSnapshot,
            [
                portfolio_snapshot_record_to_row(row)
                for row in records["portfolioSnapshots"]
            ],
            ["snapshot_id"],
        ),
    }
    total = sum(summary.values())
    record_sync_run(
        db,
        source="local-sample",
        target="us_research_sample",
        rows_upserted=total,
        message="Imported sample US research records.",
    )
    return {
        "status": "ok",
        "source": preview["source"],
        "isSample": True,
        "dbPersistence": "sample_persisted",
        "brokerConnected": False,
        "realHoldingsImported": False,
        "executionEnabled": False,
        "summary": summary,
    }


def get_open_trade_dates(pro: Any, start_date: date, end_date: date) -> list[date]:
    df = pro.trade_cal(
        exchange="",
        start_date=tushare_date(start_date),
        end_date=tushare_date(end_date),
        fields="cal_date,is_open",
    )
    dates = [
        parse_tushare_date(row["cal_date"])
        for row in df.to_dict("records")
        if int(row.get("is_open", 0)) == 1
    ]
    return sorted(day for day in dates if day is not None)


def filter_sparse_trade_dates(
    db: Session, date_column: Any, trade_dates: list[date], min_existing_rows: int
) -> list[date]:
    if not trade_dates:
        return []
    counts = dict(
        db.execute(
            select(date_column, func.count())
            .where(date_column.in_(trade_dates))
            .group_by(date_column)
        ).all()
    )
    return [day for day in trade_dates if int(counts.get(day, 0)) < min_existing_rows]


def stock_basic_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    name = item.get("name")
    if not ts_code or not name:
        return None
    return {
        "ts_code": str(ts_code),
        "symbol": item.get("symbol"),
        "name": str(name),
        "area": item.get("area"),
        "industry": item.get("industry"),
        "market": item.get("market"),
        "list_date": parse_tushare_date(item.get("list_date")),
    }


def stock_listing_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    name = item.get("name")
    list_status = item.get("list_status")
    if not ts_code or not name or (not list_status):
        return None
    return {
        "ts_code": str(ts_code),
        "symbol": item.get("symbol"),
        "name": str(name),
        "area": item.get("area"),
        "industry": item.get("industry"),
        "market": item.get("market"),
        "exchange": item.get("exchange"),
        "list_status": str(list_status).upper(),
        "list_date": parse_tushare_date(item.get("list_date")),
        "delist_date": parse_tushare_date(item.get("delist_date")),
    }


def stock_limit_price_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not ts_code or not trade_date:
        return None
    return {
        "ts_code": str(ts_code),
        "trade_date": trade_date,
        "pre_close": decimal_or_none(item.get("pre_close")),
        "up_limit": decimal_or_none(item.get("up_limit")),
        "down_limit": decimal_or_none(item.get("down_limit")),
    }


def stock_suspend_event_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    trade_date = parse_tushare_date(item.get("trade_date"))
    suspend_type = item.get("suspend_type")
    if not ts_code or not trade_date or (not suspend_type):
        return None
    return {
        "ts_code": str(ts_code),
        "trade_date": trade_date,
        "suspend_type": str(suspend_type).upper(),
        "suspend_timing": str(item.get("suspend_timing") or "").strip(),
    }


def daily_record_to_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": str(item["ts_code"]),
        "trade_date": parse_tushare_date(item["trade_date"]),
        "open": decimal_or_none(item.get("open")),
        "high": decimal_or_none(item.get("high")),
        "low": decimal_or_none(item.get("low")),
        "close": decimal_or_none(item.get("close")),
        "pre_close": decimal_or_none(item.get("pre_close")),
        "change_amount": decimal_or_none(item.get("change")),
        "pct_chg": decimal_or_none(item.get("pct_chg")),
        "vol": decimal_or_none(item.get("vol")),
        "amount": decimal_or_none(item.get("amount")),
    }


def daily_basic_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not ts_code or not trade_date:
        return None
    return {
        "ts_code": str(ts_code),
        "trade_date": trade_date,
        "close": decimal_or_none(item.get("close")),
        "turnover_rate": decimal_or_none(item.get("turnover_rate")),
        "turnover_rate_f": decimal_or_none(item.get("turnover_rate_f")),
        "volume_ratio": decimal_or_none(item.get("volume_ratio")),
        "pe": decimal_or_none(item.get("pe")),
        "pe_ttm": decimal_or_none(item.get("pe_ttm")),
        "pb": decimal_or_none(item.get("pb")),
        "ps": decimal_or_none(item.get("ps")),
        "ps_ttm": decimal_or_none(item.get("ps_ttm")),
        "dv_ratio": decimal_or_none(item.get("dv_ratio")),
        "dv_ttm": decimal_or_none(item.get("dv_ttm")),
        "total_share": decimal_or_none(item.get("total_share")),
        "float_share": decimal_or_none(item.get("float_share")),
        "free_share": decimal_or_none(item.get("free_share")),
        "total_mv": decimal_or_none(item.get("total_mv")),
        "circ_mv": decimal_or_none(item.get("circ_mv")),
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_financial_available_date(db: Session, source_observed_at: datetime) -> date:
    normalized = (
        source_observed_at
        if source_observed_at.tzinfo is not None
        else source_observed_at.replace(tzinfo=timezone.utc)
    )
    observed_date = normalized.astimezone(ZoneInfo("Asia/Shanghai")).date()
    available_from = db.scalar(
        select(func.min(TradeCalendar.cal_date)).where(
            TradeCalendar.exchange == "SSE",
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date > observed_date,
        )
    )
    if available_from is None:
        raise ValueError("缺少首次观测日之后的 SSE 官方开市日，不能冻结财务可用日")
    return available_from


def financial_indicator_record_to_row(
    item: dict[str, Any],
    *,
    source_observed_at: datetime | None = None,
    available_from: date | None = None,
) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    end_date = parse_tushare_date(item.get("end_date"))
    ann_date = parse_tushare_date(item.get("ann_date"))
    if not ts_code or not end_date or (not ann_date):
        return None
    numeric_fields = [
        "eps",
        "dt_eps",
        "bps",
        "netprofit_margin",
        "grossprofit_margin",
        "roe",
        "roe_waa",
        "roa",
        "debt_to_assets",
        "current_ratio",
        "quick_ratio",
        "assets_turn",
        "basic_eps_yoy",
        "op_yoy",
        "netprofit_yoy",
        "tr_yoy",
        "or_yoy",
        "q_sales_yoy",
        "q_profit_yoy",
    ]
    if (source_observed_at is None) != (available_from is None):
        raise ValueError("source_observed_at 与 available_from 必须同时提供")
    row = {"ts_code": str(ts_code), "ann_date": ann_date, "end_date": end_date}
    row.update({field: decimal_or_none(item.get(field)) for field in numeric_fields})
    if source_observed_at is None:
        row.update(
            {
                "source_update_flag": item.get("update_flag"),
                "source_revision_sha256": None,
                "source_observed_at": None,
                "available_from": None,
                "revision_status": "legacy_unverified",
            }
        )
        return row
    normalized_observed_at = (
        source_observed_at
        if source_observed_at.tzinfo is not None
        else source_observed_at.replace(tzinfo=timezone.utc)
    )
    identity = {
        key: value.isoformat()
        if isinstance(value, (date, datetime))
        else format(value, "f")
        if isinstance(value, Decimal)
        else value
        for (key, value) in row.items()
    }
    row.update(
        {
            "source_update_flag": str(item.get("update_flag") or "") or None,
            "source_revision_sha256": sha256(
                json.dumps(
                    identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            "source_observed_at": normalized_observed_at,
            "available_from": available_from,
            "revision_status": "observed",
        }
    )
    return row


def insert_financial_revision_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    keys = ("ts_code", "end_date", "ann_date", "source_revision_sha256")
    deduped = dedupe_rows([row for row in rows if row], keys)
    observed = [row for row in deduped if row.get("revision_status") == "observed"]
    if not observed:
        return 0
    if db.bind and db.bind.dialect.name == "postgresql":
        inserted = 0
        for chunk in chunked(observed, 1000):
            result = db.execute(
                pg_insert(StockFinancialIndicator.__table__)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=list(keys))
                .returning(StockFinancialIndicator.id)
            )
            inserted += len(result.scalars().all())
        db.commit()
        return inserted
    inserted = 0
    for row in observed:
        existing = db.scalar(
            select(StockFinancialIndicator.id).where(
                StockFinancialIndicator.ts_code == row["ts_code"],
                StockFinancialIndicator.end_date == row["end_date"],
                StockFinancialIndicator.ann_date == row["ann_date"],
                StockFinancialIndicator.source_revision_sha256
                == row["source_revision_sha256"],
            )
        )
        if existing is None:
            db.add(StockFinancialIndicator(**row))
            inserted += 1
    db.commit()
    return inserted


def adjust_factor_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not ts_code or not trade_date:
        return None
    return {
        "ts_code": str(ts_code),
        "trade_date": trade_date,
        "adj_factor": decimal_or_none(item.get("adj_factor")),
    }


def index_daily_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    return market_daily_record_to_row(item)


def market_daily_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not ts_code or not trade_date:
        return None
    return {
        "ts_code": str(ts_code),
        "trade_date": trade_date,
        "open": decimal_or_none(item.get("open")),
        "high": decimal_or_none(item.get("high")),
        "low": decimal_or_none(item.get("low")),
        "close": decimal_or_none(item.get("close")),
        "pre_close": decimal_or_none(item.get("pre_close")),
        "change_amount": decimal_or_none(item.get("change")),
        "pct_chg": decimal_or_none(item.get("pct_chg")),
        "vol": decimal_or_none(item.get("vol")),
        "amount": decimal_or_none(item.get("amount")),
    }


def dedupe_rows(
    rows: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[tuple(row.get(key) for key in keys)] = row
    return list(deduped.values())


def upsert_rows(
    db: Session,
    model: type[Any],
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
) -> int:
    clean_rows = [row for row in rows if row]
    if not clean_rows:
        return 0
    if db.bind and db.bind.dialect.name == "postgresql":
        for chunk in chunked(clean_rows, 1000):
            stmt = pg_insert(model.__table__).values(chunk)
            update_cols = {
                key: getattr(stmt.excluded, key)
                for key in chunk[0]
                if key not in set(conflict_columns) and key not in {"id", "created_at"}
            }
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_columns, set_=update_cols
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=conflict_columns)
            db.execute(stmt)
        db.commit()
        return len(clean_rows)
    for row in clean_rows:
        filters = [getattr(model, key) == row[key] for key in conflict_columns]
        existing = db.scalar(select(model).where(*filters))
        if existing:
            for key, value in row.items():
                if key not in {"id", "created_at"}:
                    setattr(existing, key, value)
        else:
            db.add(model(**row))
    db.commit()
    return len(clean_rows)


def chunked(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def execute_legacy_sync_job_action(
    action: str,
    payload: dict[str, Any],
    db: Session,
    *,
    operations: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    ops = operations or {}
    if action == "stock_listings":
        operation = ops.get("stock_listings", sync_stock_listings)
        return operation(SyncStockListingsRequest.model_validate(payload), db)
    if action == "us_sample":
        result = import_us_research_sample_to_db(db)
        return {
            **result,
            "rows_upserted": sum(
                int(value or 0) for value in result.get("summary", {}).values()
            ),
        }
    if action == "us_experiment_universe":
        return refresh_us_experiment_universe(db)
    if action == "us_experiment_targeted_universe":
        request = SyncUsExperimentTargetedUniverseRequest.model_validate(payload)
        return register_us_experiment_targeted_universe(db, symbols=request.symbols)
    if action == "us_experiment_prices":
        return sync_us_experiment_daily_prices(
            db, SyncUsExperimentPricesRequest.model_validate(payload)
        )
    if action == "us_experiment_overview_refresh":
        snapshot = refresh_us_experiment_overview_snapshot(db)
        return {
            "status": "ok",
            "rows_upserted": 1,
            "snapshotAt": snapshot.updated_at.isoformat()
            if snapshot.updated_at
            else None,
        }
    if action in {"market_bundle", "daily_market"}:
        operation = ops.get("market_bundle", execute_market_sync_bundle)
        return operation(action, SyncMarketDataRequest.model_validate(payload), db)
    if action == "market_fundamentals":
        operation = ops.get("market_fundamentals", sync_market_fundamentals)
        return operation(SyncMarketFundamentalsRequest.model_validate(payload), db)
    raise ValueError(f"不支持的同步动作: {action}")


def execute_market_sync_bundle(
    action: str,
    payload: SyncMarketDataRequest,
    db: Session,
    *,
    operations: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    ops = {
        "stock_basic": sync_stock_basic,
        "stock_listings": sync_stock_listings,
        "daily_basic": sync_market_daily_basic,
        "daily": sync_market_daily,
        "limit_prices": sync_market_limit_prices,
        "suspend_events": sync_market_suspend_events,
        "adjust_factors": sync_market_adjust_factors,
        "benchmark_index_daily": sync_index_daily,
        **(operations or {}),
    }
    suspend_payload = SyncSuspendEventsRequest(
        start_date=payload.start_date,
        end_date=payload.end_date,
        max_trade_dates=payload.max_trade_dates,
    )
    components: list[tuple[str, Any]] = []
    if action == "daily_market":
        components.extend(
            [
                (
                    "stock_basic",
                    lambda: ops["stock_basic"](SyncStockBasicRequest(), db),
                ),
                (
                    "stock_listings",
                    lambda: ops["stock_listings"](SyncStockListingsRequest(), db),
                ),
                ("daily_basic", lambda: ops["daily_basic"](payload, db)),
            ]
        )
    components.extend(
        [
            ("daily", lambda: ops["daily"](payload, db)),
            ("limit_prices", lambda: ops["limit_prices"](payload, db)),
            ("suspend_events", lambda: ops["suspend_events"](suspend_payload, db)),
        ]
    )
    if action == "daily_market":
        components.extend(
            [
                ("adjust_factors", lambda: ops["adjust_factors"](payload, db)),
                (
                    "benchmark_index_daily",
                    lambda: ops["benchmark_index_daily"](
                        SyncIndexDailyRequest(
                            ts_codes=[payload.benchmark],
                            start_date=payload.start_date,
                            end_date=payload.end_date,
                        ),
                        db,
                    ),
                ),
            ]
        )
    component_results: dict[str, Any] = {}
    rows_upserted = 0
    statuses: list[str] = []
    for name, operation in components:
        try:
            result = json_safe_value(operation())
            status = normalize_sync_job_status(
                result.get("status") if isinstance(result, dict) else None
            )
            rows_upserted += sync_result_rows(result)
        except Exception as exc:
            db.rollback()
            status = "failed"
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
        statuses.append(status)
        component_results[name] = result
    failed = statuses.count("failed")
    partial = statuses.count("partial")
    status = (
        "failed"
        if failed == len(statuses)
        else "partial"
        if failed or partial
        else "ok"
    )
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "message": f"components={len(statuses)}, failed={failed}, partial={partial}",
        "components": component_results,
    }


def normalize_sync_job_status(status: Any) -> str:
    return normalize_ingestion_status(status)


def sync_result_rows(result: Any) -> int:
    return ingestion_result_rows(result)


def record_sync_run(
    db: Session,
    target: str,
    rows_upserted: int,
    source: str = "tushare",
    start_date: date | None = None,
    end_date: date | None = None,
    status: str = "ok",
    message: str | None = None,
) -> None:
    db.add(
        DataSyncRun(
            source=source,
            target=target,
            start_date=start_date,
            end_date=end_date,
            rows_upserted=rows_upserted,
            status=status,
            message=message,
        )
    )
    db.commit()


def asset_record_to_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_key": row["naturalKey"],
        "market": row["market"],
        "symbol": row["symbol"],
        "name": row.get("name"),
        "instrument_type": row.get("instrumentType"),
        "leverage_factor": decimal_or_none(row.get("leverageFactor")),
        "risk_tag": row.get("riskTag"),
        "theme": row.get("theme"),
        "is_sample": bool(row.get("isSample", True)),
        "source": row.get("source"),
    }


def asset_daily_price_record_to_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_key": row["naturalKey"],
        "asset_natural_key": row["assetNaturalKey"],
        "trade_date": parse_iso_date(row.get("tradeDate")),
        "close": decimal_or_none(row.get("close")),
        "ma20": decimal_or_none(row.get("ma20")),
        "ma50": decimal_or_none(row.get("ma50")),
        "ma200": decimal_or_none(row.get("ma200")),
        "return20d_pct": decimal_or_none(row.get("return20dPct")),
        "return60d_pct": decimal_or_none(row.get("return60dPct")),
        "volatility20d_pct": decimal_or_none(row.get("volatility20dPct")),
        "is_sample": bool(row.get("isSample", True)),
        "source": row.get("source"),
        "is_stale": bool(row.get("isStale", False)),
    }


def watchlist_record_to_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_key": row["naturalKey"],
        "watchlist_name": row["watchlistName"],
        "asset_natural_key": row["assetNaturalKey"],
        "role": row.get("role"),
        "theme": row.get("theme"),
        "subtheme": row.get("subtheme"),
        "risk_tag": row.get("riskTag"),
        "notes": row.get("notes"),
        "is_sample": bool(row.get("isSample", True)),
        "source": row.get("source"),
    }


def portfolio_snapshot_record_to_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshotId"],
        "source": row.get("source"),
        "is_sample": bool(row.get("isSample", True)),
        "holding_count": int(row.get("holdingCount") or 0),
        "total_sample_cost_basis": decimal_or_none(row.get("totalSampleCostBasis")),
        "holdings": row.get("holdings", []),
    }


def parse_iso_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    return date.fromisoformat(str(value))
