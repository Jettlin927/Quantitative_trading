from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, assert_schema_revision_at_head, engine, get_db
from .models import (
    Asset,
    AssetDailyPrice,
    DataOverviewSnapshot,
    DataSyncJob,
    DataSyncRun,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    IndustryClassification,
    IndustryMember,
    PortfolioSnapshot,
    Stock,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockPool,
    StockPoolMember,
    StockSuspendEvent,
    TradeCalendar,
    WatchlistItem,
)
from .schemas import (
    DailyBarOut,
    StockFundamentalsOut,
    StockOut,
    StockPoolCreate,
    StockPoolDetailOut,
    StockPoolMembersRequest,
    StockPoolOut,
    StockPoolMemberOut,
    StockScreenOut,
    SyncAdjustFactorsRequest,
    SyncDailyRequest,
    SyncFundamentalsRequest,
    SyncFundBasicRequest,
    SyncFundDailyRequest,
    SyncIndexBasicRequest,
    SyncIndexDailyRequest,
    SyncIndustryClassificationsRequest,
    SyncJobCreate,
    SyncMarketDataRequest,
    SyncMarketFundamentalsRequest,
    SyncStockBasicRequest,
    SyncStockListingsRequest,
    SyncSuspendEventsRequest,
    SyncTradeCalendarRequest,
)
from .quant_research.readiness import evaluate_research_readiness
from .tushare_client import decimal_or_none, get_pro_api, parse_tushare_date, tushare_date
from .us_research import build_us_research_import_preview, build_us_research_overview
from .strategy_results import build_strategy_results_overview


@asynccontextmanager
async def lifespan(_: FastAPI):
    assert_schema_revision_at_head(engine)
    yield


app = FastAPI(title="Quant Data Workspace", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPO_ROOT = Path(__file__).resolve().parents[2]

STOCK_FIELDS = "ts_code,symbol,name,area,industry,market,list_date"
DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
    "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,"
    "free_share,total_mv,circ_mv"
)
FINA_INDICATOR_FIELDS = (
    "ts_code,ann_date,end_date,eps,dt_eps,bps,netprofit_margin,grossprofit_margin,"
    "roe,roe_waa,roa,debt_to_assets,current_ratio,quick_ratio,assets_turn,"
    "basic_eps_yoy,op_yoy,netprofit_yoy,tr_yoy,or_yoy,q_sales_yoy,q_profit_yoy"
)
TRADE_CALENDAR_FIELDS = "exchange,cal_date,is_open,pretrade_date"
ADJUST_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
INDEX_BASIC_FIELDS = "ts_code,name,market,publisher,category,base_date,list_date"
INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
FUND_BASIC_FIELDS = "ts_code,name,management,custodian,fund_type,list_date,market"
FUND_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
INDUSTRY_CLASSIFY_FIELDS = "index_code,industry_name,level,industry_code,parent_code,src"
INDUSTRY_MEMBER_FIELDS = "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new"
STOCK_LISTING_FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date"
STOCK_LIMIT_FIELDS = "ts_code,trade_date,pre_close,up_limit,down_limit"
STOCK_SUSPEND_FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "quant-data-workspace", "status": "ok"}


@app.get("/api/health")
def health(db: Session = Depends(get_db), include_counts: bool = False) -> dict[str, Any]:
    db.execute(select(1))
    payload = {
        "status": "ok",
        "service": "quant-data-workspace",
        "database": "ok",
    }
    if include_counts:
        payload["tables"] = get_table_counts(db)
    return payload


@app.get("/api/db/overview")
def get_db_overview(db: Session = Depends(get_db), refresh: bool = False) -> dict[str, Any]:
    snapshot = db.get(DataOverviewSnapshot, "default")
    if snapshot and not refresh:
        payload = dict(snapshot.payload)
        payload["snapshotAt"] = snapshot.updated_at.isoformat() if snapshot.updated_at else None
        return payload

    payload = build_db_overview_payload(db)
    snapshot = snapshot or DataOverviewSnapshot(key="default", payload=payload)
    snapshot.payload = payload
    db.add(snapshot)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        snapshot = db.get(DataOverviewSnapshot, "default")
        if not snapshot:
            raise
        snapshot.payload = payload
        db.commit()
    db.refresh(snapshot)
    payload["snapshotAt"] = snapshot.updated_at.isoformat() if snapshot.updated_at else None
    return payload


def build_db_overview_payload(db: Session) -> dict[str, Any]:
    stocks = db.scalar(select(func.count(Stock.ts_code))) or 0
    daily_bars = query_date_coverage(db, StockDailyBar.trade_date, StockDailyBar.ts_code)
    daily_basic = query_date_coverage(db, StockDailyBasic.trade_date, StockDailyBasic.ts_code)
    financial_indicators = query_date_coverage(db, StockFinancialIndicator.ann_date, StockFinancialIndicator.ts_code)
    stock_listings = query_listing_coverage(db)
    limit_prices = query_stock_limit_coverage(db)
    suspend_events = query_date_coverage(db, StockSuspendEvent.trade_date, StockSuspendEvent.ts_code)
    trade_calendar = query_trade_calendar_coverage(db)
    adjust_factors = query_date_coverage(db, StockAdjustFactor.trade_date, StockAdjustFactor.ts_code)
    indices = query_entity_coverage(db, Index.ts_code)
    index_daily_bars = query_date_coverage(db, IndexDailyBar.trade_date, IndexDailyBar.ts_code)
    funds = query_entity_coverage(db, Fund.ts_code)
    fund_daily_bars = query_date_coverage(db, FundDailyBar.trade_date, FundDailyBar.ts_code)
    fund_adjust_factors = query_date_coverage(db, FundAdjustFactor.trade_date, FundAdjustFactor.ts_code)
    industries = query_entity_coverage(db, IndustryClassification.index_code)
    industry_members = db.scalar(select(func.count(IndustryMember.id))) or 0
    us_sample = build_us_research_db_overview(db)
    return {
        "source": "postgresql",
        "tables": {
            "stocks": stocks,
            "stockDailyBars": daily_bars["rows"],
            "stockDailyBasic": daily_basic["rows"],
            "stockFinancialIndicators": financial_indicators["rows"],
            "stockListings": stock_listings["rows"],
            "stockLimitPrices": limit_prices["rows"],
            "stockSuspendEvents": suspend_events["rows"],
            "tradeCalendars": trade_calendar["rows"],
            "stockAdjustFactors": adjust_factors["rows"],
            "indices": indices["rows"],
            "indexDailyBars": index_daily_bars["rows"],
            "funds": funds["rows"],
            "fundDailyBars": fund_daily_bars["rows"],
            "fundAdjustFactors": fund_adjust_factors["rows"],
            "industryClassifications": industries["rows"],
            "industryMembers": industry_members,
            "stockPools": db.scalar(select(func.count(StockPool.id))) or 0,
            "stockPoolMembers": db.scalar(select(func.count(StockPoolMember.id))) or 0,
            "assets": us_sample["counts"]["assets"],
            "assetDailyPrices": us_sample["counts"]["assetDailyPrices"],
            "watchlistItems": us_sample["counts"]["watchlistItems"],
            "portfolioSnapshots": us_sample["counts"]["portfolioSnapshots"],
            "dataSyncRuns": db.scalar(select(func.count(DataSyncRun.id))) or 0,
            "dataSyncJobs": db.scalar(select(func.count(DataSyncJob.id))) or 0,
        },
        "aShare": {
            "stocks": stocks,
            "dailyBars": daily_bars,
            "dailyBasic": daily_basic,
            "financialIndicators": financial_indicators,
            "stockListings": stock_listings,
            "limitPrices": limit_prices,
            "suspendEvents": suspend_events,
            "tradeCalendar": trade_calendar,
            "adjustFactors": adjust_factors,
            "indices": indices,
            "indexDailyBars": index_daily_bars,
            "funds": funds,
            "fundDailyBars": fund_daily_bars,
            "fundAdjustFactors": fund_adjust_factors,
            "industries": industries,
            "industryMembers": industry_members,
        },
        "usSample": us_sample,
    }


@app.get("/api/stocks", response_model=list[StockOut])
def list_stocks(
    q: str | None = None,
    industry: str | None = None,
    market: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Stock]:
    stmt = select(Stock).order_by(Stock.ts_code).offset(max(offset, 0)).limit(min(max(limit, 1), 1000))
    filters = build_stock_filters(q=q, industry=industry, market=market)
    if filters:
        stmt = stmt.where(*filters)
    return list(db.scalars(stmt).all())


@app.get("/api/stocks/screen", response_model=list[StockScreenOut])
def screen_stocks(
    q: str | None = None,
    industry: str | None = None,
    market: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> list[StockScreenOut]:
    filters = build_stock_filters(q=q, industry=industry, market=market)
    stmt = select(Stock).order_by(Stock.ts_code).limit(min(max(limit, 1), 1000))
    if filters:
        stmt = stmt.where(*filters)
    stocks = list(db.scalars(stmt).all())
    ts_codes = [stock.ts_code for stock in stocks]
    latest_bar_by_code = query_latest_bars(db, ts_codes)
    latest_basic_by_code = query_latest_daily_basic(db, ts_codes)
    bar_count_by_code = query_bar_counts(db, ts_codes)

    rows: list[StockScreenOut] = []
    for stock in stocks:
        bar = latest_bar_by_code.get(stock.ts_code)
        valuation = latest_basic_by_code.get(stock.ts_code)
        rows.append(
            StockScreenOut(
                **stock_to_dict(stock),
                latest_date=bar.trade_date if bar else None,
                close=decimal_to_float(bar.close if bar else None),
                pct_chg=decimal_to_float(bar.pct_chg if bar else None),
                data_bars=bar_count_by_code.get(stock.ts_code, 0),
                valuation=daily_basic_to_dict(valuation),
            )
        )
    return rows


@app.get("/api/stock-pools", response_model=list[StockPoolOut])
def list_stock_pools(db: Session = Depends(get_db)) -> list[StockPoolOut]:
    pools = list(db.scalars(select(StockPool).order_by(StockPool.id)).all())
    counts = dict(db.execute(select(StockPoolMember.pool_id, func.count(StockPoolMember.id)).group_by(StockPoolMember.pool_id)).all())
    return [pool_to_schema(pool, counts.get(pool.id, 0)) for pool in pools]


@app.post("/api/stock-pools", response_model=StockPoolOut)
def create_stock_pool(payload: StockPoolCreate, db: Session = Depends(get_db)) -> StockPoolOut:
    existing = db.scalar(select(StockPool).where(StockPool.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="股票池名称已存在。")
    pool = StockPool(name=payload.name, description=payload.description)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return pool_to_schema(pool, 0)


@app.get("/api/stock-pools/{pool_id}", response_model=StockPoolDetailOut)
def get_stock_pool(pool_id: int, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_pool_or_404(db, pool_id)
    return pool_detail_to_schema(db, pool)


@app.post("/api/stock-pools/{pool_id}/members", response_model=StockPoolDetailOut)
def add_stock_pool_members(pool_id: int, payload: StockPoolMembersRequest, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_pool_or_404(db, pool_id)
    requested = sorted({code.strip().upper() for code in payload.ts_codes if code.strip()})
    if not requested:
        return pool_detail_to_schema(db, pool)
    existing_stocks = set(db.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(requested))).all())
    missing = sorted(set(requested) - existing_stocks)
    if missing:
        raise HTTPException(status_code=404, detail=f"股票代码不存在：{', '.join(missing[:10])}")
    current = set(db.scalars(select(StockPoolMember.ts_code).where(StockPoolMember.pool_id == pool_id)).all())
    for ts_code in requested:
        if ts_code not in current:
            db.add(StockPoolMember(pool_id=pool_id, ts_code=ts_code))
    db.commit()
    return pool_detail_to_schema(db, pool)


@app.delete("/api/stock-pools/{pool_id}/members/{ts_code}", response_model=StockPoolDetailOut)
def remove_stock_pool_member(pool_id: int, ts_code: str, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_pool_or_404(db, pool_id)
    db.execute(delete(StockPoolMember).where(StockPoolMember.pool_id == pool_id, StockPoolMember.ts_code == ts_code.upper()))
    db.commit()
    return pool_detail_to_schema(db, pool)


@app.delete("/api/stock-pools/{pool_id}")
def delete_stock_pool(pool_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    pool = get_pool_or_404(db, pool_id)
    db.delete(pool)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/tushare/sync-stock-basic")
def sync_stock_basic(payload: SyncStockBasicRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    df = pro.stock_basic(exchange="", list_status="L", fields=STOCK_FIELDS)
    rows = [row for item in df.to_dict("records") if (row := stock_basic_record_to_row(item))]
    upserted = upsert_rows(db, Stock, rows, ["ts_code"])
    record_sync_run(db, target="stock_basic", rows_upserted=upserted, message=f"stocks={upserted}")
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-stock-listings")
def sync_stock_listings(payload: SyncStockListingsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    rows: list[dict[str, Any]] = []
    failed_statuses: list[str] = []
    statuses = list(dict.fromkeys(payload.statuses))
    for list_status in statuses:
        try:
            df = pro.stock_basic(exchange="", list_status=list_status, fields=STOCK_LISTING_FIELDS)
            rows.extend(row for item in df.to_dict("records") if (row := stock_listing_record_to_row(item)))
        except Exception as exc:  # noqa: BLE001
            failed_statuses.append(f"{list_status}:{exc}")
    upserted = upsert_rows(db, StockListing, dedupe_rows(rows, ("ts_code",)), ["ts_code"])
    status = "partial" if failed_statuses else "ok"
    record_sync_run(
        db,
        target="stock_listings",
        rows_upserted=upserted,
        status=status,
        message=f"statuses={','.join(statuses)}, failed_statuses={len(failed_statuses)}",
    )
    return {"status": status, "rows_upserted": upserted, "failed_statuses": failed_statuses}


@app.post("/api/tushare/sync-daily")
def sync_daily(payload: SyncDailyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    df = pro.daily(ts_code=payload.ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=DAILY_FIELDS)
    rows = [daily_record_to_row(item) for item in df.to_dict("records")]
    upserted = upsert_rows(db, StockDailyBar, rows, ["ts_code", "trade_date"])
    record_sync_run(db, target=payload.ts_code, start_date=payload.start_date, end_date=payload.end_date, rows_upserted=upserted)
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-market-daily")
def sync_market_daily(payload: SyncMarketDataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(db, StockDailyBar.trade_date, trade_dates, payload.min_existing_rows)
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]

    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.daily(trade_date=tushare_date(trade_day), fields=DAILY_FIELDS)
            rows = dedupe_rows([daily_record_to_row(item) for item in df.to_dict("records")], ("ts_code", "trade_date"))
            rows_upserted += upsert_rows(db, StockDailyBar, rows, ["ts_code", "trade_date"])
        except Exception as exc:  # noqa: BLE001
            failed_dates.append(f"{trade_day}:{exc}")

    status = "partial" if failed_dates else "ok"
    message = f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}"
    record_sync_run(db, target="market:daily", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=rows_upserted, status=status, message=message)
    return {"status": status, "rows_upserted": rows_upserted, "trade_dates": len(trade_dates), "failed_dates": failed_dates}


@app.post("/api/tushare/sync-market-limit-prices")
def sync_market_limit_prices(payload: SyncMarketDataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    stock_codes = set(db.scalars(select(StockListing.ts_code)).all()) or set(db.scalars(select(Stock.ts_code)).all())
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(db, StockLimitPrice.trade_date, trade_dates, payload.min_existing_rows)
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]

    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.stk_limit(trade_date=tushare_date(trade_day), fields=STOCK_LIMIT_FIELDS)
            rows = [
                row
                for item in df.to_dict("records")
                if (row := stock_limit_price_record_to_row(item)) and (not stock_codes or row["ts_code"] in stock_codes)
            ]
            rows_upserted += upsert_rows(
                db,
                StockLimitPrice,
                dedupe_rows(rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
        except Exception as exc:  # noqa: BLE001
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
    return {"status": status, "rows_upserted": rows_upserted, "trade_dates": len(trade_dates), "failed_dates": failed_dates}


@app.post("/api/tushare/sync-market-suspend-events")
def sync_market_suspend_events(payload: SyncSuspendEventsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]

    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.suspend_d(trade_date=tushare_date(trade_day), fields=STOCK_SUSPEND_FIELDS)
            rows = [row for item in df.to_dict("records") if (row := stock_suspend_event_record_to_row(item))]
            rows_upserted += upsert_rows(
                db,
                StockSuspendEvent,
                dedupe_rows(rows, ("ts_code", "trade_date", "suspend_type", "suspend_timing")),
                ["ts_code", "trade_date", "suspend_type", "suspend_timing"],
            )
        except Exception as exc:  # noqa: BLE001
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
    return {"status": status, "rows_upserted": rows_upserted, "trade_dates": len(trade_dates), "failed_dates": failed_dates}


@app.post("/api/tushare/sync-fundamentals")
def sync_fundamentals(payload: SyncFundamentalsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    daily_basic_df = pro.daily_basic(ts_code=payload.ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=DAILY_BASIC_FIELDS)
    daily_rows = [row for item in daily_basic_df.to_dict("records") if (row := daily_basic_record_to_row(item))]
    financial_df = pro.fina_indicator(ts_code=payload.ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=FINA_INDICATOR_FIELDS)
    financial_rows = [row for item in financial_df.to_dict("records") if (row := financial_indicator_record_to_row(item))]

    daily_upserted = upsert_rows(db, StockDailyBasic, dedupe_rows(daily_rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
    financial_upserted = upsert_rows(
        db,
        StockFinancialIndicator,
        dedupe_rows(financial_rows, ("ts_code", "end_date", "ann_date")),
        ["ts_code", "end_date", "ann_date"],
    )
    total = daily_upserted + financial_upserted
    record_sync_run(
        db,
        target=f"{payload.ts_code}:fundamentals",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=total,
        message=f"daily_basic={daily_upserted}, fina_indicator={financial_upserted}",
    )
    return {"status": "ok", "daily_basic_rows": daily_upserted, "financial_indicator_rows": financial_upserted}


@app.post("/api/tushare/sync-market-daily-basic")
def sync_market_daily_basic(payload: SyncMarketDataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    trade_dates = get_open_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.skip_existing:
        trade_dates = filter_sparse_trade_dates(db, StockDailyBasic.trade_date, trade_dates, payload.min_existing_rows)
    if payload.max_trade_dates:
        trade_dates = trade_dates[: payload.max_trade_dates]

    rows_upserted = 0
    failed_dates: list[str] = []
    for trade_day in trade_dates:
        try:
            df = pro.query("daily_basic", ts_code="", trade_date=tushare_date(trade_day), fields=DAILY_BASIC_FIELDS)
            rows = [row for item in df.to_dict("records") if (row := daily_basic_record_to_row(item))]
            rows_upserted += upsert_rows(db, StockDailyBasic, dedupe_rows(rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
        except Exception as exc:  # noqa: BLE001
            failed_dates.append(f"{trade_day}:{exc}")

    status = "partial" if failed_dates else "ok"
    message = f"trade_dates={len(trade_dates)}, failed_dates={len(failed_dates)}"
    record_sync_run(db, target="market:daily_basic", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=rows_upserted, status=status, message=message)
    return {"status": status, "rows_upserted": rows_upserted, "trade_dates": len(trade_dates), "failed_dates": failed_dates}


@app.post("/api/tushare/sync-market-fundamentals")
def sync_market_fundamentals(payload: SyncMarketFundamentalsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    stocks = list(db.scalars(select(Stock.ts_code).order_by(Stock.ts_code)).all())
    if payload.max_stocks:
        stocks = stocks[: payload.max_stocks]

    rows_upserted = 0
    failed_stocks: list[str] = []
    skipped_stocks = 0
    for ts_code in stocks:
        if payload.skip_existing:
            existing = db.scalar(
                select(func.count(StockFinancialIndicator.id)).where(
                    StockFinancialIndicator.ts_code == ts_code,
                    StockFinancialIndicator.ann_date >= payload.start_date,
                    StockFinancialIndicator.ann_date <= payload.end_date,
                )
            )
            if existing:
                skipped_stocks += 1
                continue
        try:
            df = pro.fina_indicator(ts_code=ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=FINA_INDICATOR_FIELDS)
            rows = [row for item in df.to_dict("records") if (row := financial_indicator_record_to_row(item))]
            rows_upserted += upsert_rows(
                db,
                StockFinancialIndicator,
                dedupe_rows(rows, ("ts_code", "end_date", "ann_date")),
                ["ts_code", "end_date", "ann_date"],
            )
        except Exception as exc:  # noqa: BLE001
            failed_stocks.append(f"{ts_code}:{exc}")

    status = "partial" if failed_stocks else "ok"
    message = f"stocks={len(stocks)}, skipped_stocks={skipped_stocks}, failed_stocks={len(failed_stocks)}"
    record_sync_run(db, target="market:fundamentals", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=rows_upserted, status=status, message=message)
    return {"status": status, "rows_upserted": rows_upserted, "skipped_stocks": skipped_stocks, "failed_stocks": failed_stocks}


@app.post("/api/tushare/sync-trade-calendar")
def sync_trade_calendar(payload: SyncTradeCalendarRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    exchange = payload.exchange or ""
    df = pro.trade_cal(exchange=exchange, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=TRADE_CALENDAR_FIELDS)
    rows = [row for item in df.to_dict("records") if (row := trade_calendar_record_to_row(item, fallback_exchange=exchange or "SSE"))]
    upserted = upsert_rows(db, TradeCalendar, dedupe_rows(rows, ("exchange", "cal_date")), ["exchange", "cal_date"])
    record_sync_run(db, target="trade_calendar", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=upserted)
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-adjust-factors")
def sync_adjust_factors(payload: SyncAdjustFactorsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    df = pro.adj_factor(ts_code=payload.ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=ADJUST_FACTOR_FIELDS)
    rows = [row for item in df.to_dict("records") if (row := adjust_factor_record_to_row(item))]
    upserted = upsert_rows(db, StockAdjustFactor, dedupe_rows(rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
    record_sync_run(db, target=f"{payload.ts_code}:adjust_factors", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=upserted)
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-index-basic")
def sync_index_basic(payload: SyncIndexBasicRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    rows: list[dict[str, Any]] = []
    failed_markets: list[str] = []
    for market in payload.markets or ["CSI", "SSE", "SZSE", "SW"]:
        try:
            df = pro.index_basic(market=market, fields=INDEX_BASIC_FIELDS)
            rows.extend(row for item in df.to_dict("records") if (row := index_basic_record_to_row(item, fallback_market=market)))
        except Exception as exc:  # noqa: BLE001
            failed_markets.append(f"{market}:{exc}")
    upserted = upsert_rows(db, Index, dedupe_rows(rows, ("ts_code",)), ["ts_code"])
    status = "partial" if failed_markets else "ok"
    record_sync_run(db, target="index_basic", rows_upserted=upserted, status=status, message=f"failed_markets={len(failed_markets)}")
    return {"status": status, "rows_upserted": upserted, "failed_markets": failed_markets}


@app.post("/api/tushare/sync-index-daily")
def sync_index_daily(payload: SyncIndexDailyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    rows_upserted = 0
    failed_indices: list[str] = []
    for ts_code in payload.ts_codes:
        try:
            df = pro.index_daily(ts_code=ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=INDEX_DAILY_FIELDS)
            rows = [row for item in df.to_dict("records") if (row := index_daily_record_to_row(item))]
            rows_upserted += upsert_rows(db, IndexDailyBar, dedupe_rows(rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
        except Exception as exc:  # noqa: BLE001
            failed_indices.append(f"{ts_code}:{exc}")
    status = "partial" if failed_indices else "ok"
    record_sync_run(db, target="index_daily", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=rows_upserted, status=status, message=f"indices={len(payload.ts_codes)}, failed_indices={len(failed_indices)}")
    return {"status": status, "rows_upserted": rows_upserted, "failed_indices": failed_indices}


@app.post("/api/tushare/sync-fund-basic")
def sync_fund_basic(payload: SyncFundBasicRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    df = pro.fund_basic(market=payload.market, fields=FUND_BASIC_FIELDS)
    rows = [row for item in df.to_dict("records") if (row := fund_basic_record_to_row(item, fallback_market=payload.market))]
    upserted = upsert_rows(db, Fund, dedupe_rows(rows, ("ts_code",)), ["ts_code"])
    record_sync_run(db, target="fund_basic", rows_upserted=upserted, message=f"market={payload.market}")
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-fund-daily")
def sync_fund_daily(payload: SyncFundDailyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    rows_upserted = 0
    failed_funds: list[str] = []
    for ts_code in payload.ts_codes:
        try:
            df = pro.fund_daily(ts_code=ts_code, start_date=tushare_date(payload.start_date), end_date=tushare_date(payload.end_date), fields=FUND_DAILY_FIELDS)
            rows = [row for item in df.to_dict("records") if (row := fund_daily_record_to_row(item))]
            rows_upserted += upsert_rows(db, FundDailyBar, dedupe_rows(rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
        except Exception as exc:  # noqa: BLE001
            failed_funds.append(f"{ts_code}:{exc}")
    status = "partial" if failed_funds else "ok"
    record_sync_run(db, target="fund_daily", start_date=payload.start_date, end_date=payload.end_date, rows_upserted=rows_upserted, status=status, message=f"funds={len(payload.ts_codes)}, failed_funds={len(failed_funds)}")
    return {"status": status, "rows_upserted": rows_upserted, "failed_funds": failed_funds}


@app.post("/api/tushare/sync-fund-adjust-factors")
def sync_fund_adjust_factors(payload: SyncAdjustFactorsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    df = pro.fund_adj(
        ts_code=payload.ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=ADJUST_FACTOR_FIELDS,
    )
    rows = [row for item in df.to_dict("records") if (row := fund_adjust_factor_record_to_row(item))]
    upserted = upsert_rows(db, FundAdjustFactor, dedupe_rows(rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
    record_sync_run(
        db,
        target=f"{payload.ts_code}:fund_adjust_factors",
        start_date=payload.start_date,
        end_date=payload.end_date,
        rows_upserted=upserted,
    )
    return {"status": "ok", "rows_upserted": upserted}


@app.post("/api/tushare/sync-industry-classifications")
def sync_industry_classifications(payload: SyncIndustryClassificationsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    pro = get_pro_api(payload.token)
    classify_df = pro.index_classify(src=payload.src, fields=INDUSTRY_CLASSIFY_FIELDS)
    classification_rows = [row for item in classify_df.to_dict("records") if (row := industry_classification_record_to_row(item, fallback_src=payload.src))]
    classification_upserted = upsert_rows(db, IndustryClassification, dedupe_rows(classification_rows, ("index_code",)), ["index_code"])
    classification_by_code = {row["index_code"]: row for row in classification_rows}
    requested_codes = payload.index_codes or [row["index_code"] for row in classification_rows]

    member_rows: list[dict[str, Any]] = []
    failed_indices: list[str] = []
    for index_code in requested_codes:
        try:
            member_df = pro.index_member_all(**industry_member_query_kwargs(index_code, classification_by_code.get(index_code)), fields=INDUSTRY_MEMBER_FIELDS)
            member_rows.extend(row for item in member_df.to_dict("records") if (row := industry_member_record_to_row(item, fallback_index_code=index_code)))
        except Exception as exc:  # noqa: BLE001
            failed_indices.append(f"{index_code}:{exc}")
    member_upserted = upsert_rows(db, IndustryMember, dedupe_rows(member_rows, ("index_code", "con_code", "in_date")), ["index_code", "con_code", "in_date"])
    rows_upserted = classification_upserted + member_upserted
    status = "partial" if failed_indices else "ok"
    record_sync_run(db, target="industry_classifications", rows_upserted=rows_upserted, status=status, message=f"classifications={classification_upserted}, members={member_upserted}, failed_indices={len(failed_indices)}")
    return {"status": status, "rows_upserted": rows_upserted, "classifications": classification_upserted, "members": member_upserted, "failed_indices": failed_indices}


@app.post("/api/sync-jobs", status_code=202)
def create_sync_job(payload: SyncJobCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> dict[str, Any]:
    normalized_payload = validate_sync_job_payload(payload.action, payload.payload)
    payload_hash = sync_job_payload_hash(payload.action, normalized_payload)
    existing = db.scalars(
        select(DataSyncJob).where(DataSyncJob.active_key == payload_hash).order_by(DataSyncJob.created_at.desc()).limit(1)
    ).first()
    if existing:
        return sync_job_to_dict(existing)

    job = DataSyncJob(
        id=str(uuid4()),
        action=payload.action,
        status="queued",
        payload=normalized_payload,
        payload_hash=payload_hash,
        active_key=payload_hash,
        rows_upserted=0,
        message="任务已进入后台队列",
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalars(
            select(DataSyncJob).where(DataSyncJob.active_key == payload_hash).order_by(DataSyncJob.created_at.desc()).limit(1)
        ).first()
        if not existing:
            raise
        return sync_job_to_dict(existing)

    db.refresh(job)
    background_tasks.add_task(run_sync_job, job.id)
    return sync_job_to_dict(job)


@app.get("/api/sync-jobs")
def list_sync_jobs(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    jobs = db.scalars(select(DataSyncJob).order_by(DataSyncJob.created_at.desc()).limit(min(max(limit, 1), 200))).all()
    return [sync_job_to_dict(job) for job in jobs]


@app.get("/api/sync-jobs/{job_id}")
def get_sync_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(DataSyncJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    return sync_job_to_dict(job)


@app.get("/api/tushare/sync-progress")
def get_sync_progress(db: Session = Depends(get_db), include_coverage: bool = True) -> dict[str, Any]:
    runs = list(db.scalars(select(DataSyncRun).order_by(DataSyncRun.created_at.desc()).limit(20)).all())
    payload = {"runs": [sync_run_to_dict(row) for row in runs]}
    if include_coverage:
        payload["coverage"] = {
            "daily": query_date_coverage(db, StockDailyBar.trade_date, StockDailyBar.ts_code),
            "dailyBasic": query_date_coverage(db, StockDailyBasic.trade_date, StockDailyBasic.ts_code),
            "financialIndicators": query_date_coverage(db, StockFinancialIndicator.ann_date, StockFinancialIndicator.ts_code),
            "tradeCalendar": query_trade_calendar_coverage(db),
            "adjustFactors": query_date_coverage(db, StockAdjustFactor.trade_date, StockAdjustFactor.ts_code),
            "indexDailyBars": query_date_coverage(db, IndexDailyBar.trade_date, IndexDailyBar.ts_code),
            "fundDailyBars": query_date_coverage(db, FundDailyBar.trade_date, FundDailyBar.ts_code),
            "fundAdjustFactors": query_date_coverage(db, FundAdjustFactor.trade_date, FundAdjustFactor.ts_code),
        }
    return payload


@app.get("/api/daily-bars", response_model=list[DailyBarOut])
def get_daily_bars(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
) -> list[DailyBarOut]:
    stmt = select(StockDailyBar).where(StockDailyBar.ts_code == ts_code.upper())
    if start_date:
        stmt = stmt.where(StockDailyBar.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(StockDailyBar.trade_date <= end_date)
    bars = list(db.scalars(stmt.order_by(StockDailyBar.trade_date)).all())
    return [daily_bar_to_schema(row) for row in bars]


@app.get("/api/stocks/{ts_code}/fundamentals", response_model=StockFundamentalsOut)
def get_stock_fundamentals(ts_code: str, db: Session = Depends(get_db)) -> StockFundamentalsOut:
    code = ts_code.upper()
    valuation = db.scalars(select(StockDailyBasic).where(StockDailyBasic.ts_code == code).order_by(StockDailyBasic.trade_date.desc()).limit(1)).first()
    financial = db.scalars(select(StockFinancialIndicator).where(StockFinancialIndicator.ts_code == code).order_by(StockFinancialIndicator.ann_date.desc()).limit(1)).first()
    return StockFundamentalsOut(ts_code=code, valuation=daily_basic_to_dict(valuation), financial=financial_indicator_to_dict(financial))


@app.get("/api/stock-listings")
def list_stock_listings(
    as_of: date | None = None,
    list_status: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(StockListing).order_by(StockListing.ts_code).limit(min(max(limit, 1), 5000))
    if as_of:
        stmt = stmt.where(StockListing.list_date.is_not(None), StockListing.list_date <= as_of).where(
            or_(StockListing.delist_date.is_(None), StockListing.delist_date >= as_of)
        )
    if list_status:
        stmt = stmt.where(StockListing.list_status == list_status.upper())
    return [stock_listing_to_dict(row) for row in db.scalars(stmt).all()]


@app.get("/api/stocks/{ts_code}/limit-prices")
def get_stock_limit_prices(
    ts_code: str,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(StockLimitPrice)
        .where(
            StockLimitPrice.ts_code == ts_code.upper(),
            StockLimitPrice.trade_date >= start_date,
            StockLimitPrice.trade_date <= end_date,
        )
        .order_by(StockLimitPrice.trade_date)
    ).all()
    return [stock_limit_price_to_dict(row) for row in rows]


@app.get("/api/stocks/{ts_code}/suspend-events")
def get_stock_suspend_events(
    ts_code: str,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(StockSuspendEvent)
        .where(
            StockSuspendEvent.ts_code == ts_code.upper(),
            StockSuspendEvent.trade_date >= start_date,
            StockSuspendEvent.trade_date <= end_date,
        )
        .order_by(StockSuspendEvent.trade_date, StockSuspendEvent.suspend_type)
    ).all()
    return [stock_suspend_event_to_dict(row) for row in rows]


@app.get("/api/trade-calendars/recent")
def get_recent_trade_calendars(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(TradeCalendar).order_by(TradeCalendar.cal_date.desc()).limit(min(max(limit, 1), 250))).all())
    return [trade_calendar_to_dict(row) for row in rows]


@app.get("/api/trade-calendars/{cal_date}")
def get_trade_calendar_day(cal_date: date, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalars(select(TradeCalendar).where(TradeCalendar.cal_date == cal_date).order_by(TradeCalendar.exchange)).first()
    if not row:
        raise HTTPException(status_code=404, detail="交易日历不存在。")
    return trade_calendar_to_dict(row)


@app.get("/api/stocks/{ts_code}/adjust-factors")
def get_stock_adjust_factors(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(StockAdjustFactor)
            .where(StockAdjustFactor.ts_code == ts_code.upper(), StockAdjustFactor.trade_date >= start_date, StockAdjustFactor.trade_date <= end_date)
            .order_by(StockAdjustFactor.trade_date)
        ).all()
    )
    return [adjust_factor_to_dict(row) for row in rows]


@app.get("/api/indices")
def list_indices(q: str | None = None, market: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Index).order_by(Index.ts_code).limit(min(max(limit, 1), 1000))
    filters = build_catalog_filters(Index.ts_code, Index.name, q=q, market_column=Index.market, market=market)
    if filters:
        stmt = stmt.where(*filters)
    return [index_to_dict(row) for row in db.scalars(stmt).all()]


@app.get("/api/indices/{ts_code}/daily-bars")
def get_index_daily_bars(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(IndexDailyBar)
            .where(IndexDailyBar.ts_code == ts_code.upper(), IndexDailyBar.trade_date >= start_date, IndexDailyBar.trade_date <= end_date)
            .order_by(IndexDailyBar.trade_date)
        ).all()
    )
    return [market_bar_to_dict(row) for row in rows]


@app.get("/api/funds")
def list_funds(q: str | None = None, market: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Fund).order_by(Fund.ts_code).limit(min(max(limit, 1), 1000))
    filters = build_catalog_filters(Fund.ts_code, Fund.name, q=q, market_column=Fund.market, market=market)
    if filters:
        stmt = stmt.where(*filters)
    return [fund_to_dict(row) for row in db.scalars(stmt).all()]


@app.get("/api/funds/{ts_code}/daily-bars")
def get_fund_daily_bars(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(FundDailyBar)
            .where(FundDailyBar.ts_code == ts_code.upper(), FundDailyBar.trade_date >= start_date, FundDailyBar.trade_date <= end_date)
            .order_by(FundDailyBar.trade_date)
        ).all()
    )
    return [market_bar_to_dict(row) for row in rows]


@app.get("/api/funds/{ts_code}/adjust-factors")
def get_fund_adjust_factors(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(FundAdjustFactor)
        .where(
            FundAdjustFactor.ts_code == ts_code.upper(),
            FundAdjustFactor.trade_date >= start_date,
            FundAdjustFactor.trade_date <= end_date,
        )
        .order_by(FundAdjustFactor.trade_date)
    ).all()
    return [fund_adjust_factor_to_dict(row) for row in rows]


@app.get("/api/industries")
def list_industries(q: str | None = None, src: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(IndustryClassification).order_by(IndustryClassification.index_code).limit(min(max(limit, 1), 1000))
    filters: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(IndustryClassification.index_code.ilike(like), IndustryClassification.industry_name.ilike(like), IndustryClassification.industry_code.ilike(like)))
    if src:
        filters.append(IndustryClassification.src == src)
    if filters:
        stmt = stmt.where(*filters)
    return [industry_to_dict(row) for row in db.scalars(stmt).all()]


@app.get("/api/industries/{index_code}/members")
def get_industry_members(index_code: str, db: Session = Depends(get_db), trade_date: date | None = None) -> list[dict[str, Any]]:
    code = index_code.upper()
    stmt = select(IndustryMember).where(IndustryMember.index_code == code).order_by(IndustryMember.id)
    if trade_date:
        stmt = stmt.where(IndustryMember.in_date <= trade_date).where(or_(IndustryMember.out_date.is_(None), IndustryMember.out_date >= trade_date))
    return [industry_member_to_dict(row) for row in db.scalars(stmt).all()]


@app.get("/api/us-research/overview")
def get_us_research_overview() -> dict[str, Any]:
    return build_us_research_overview(REPO_ROOT)


@app.get("/api/us-research/import-preview")
def get_us_research_import_preview() -> dict[str, Any]:
    return build_us_research_import_preview(REPO_ROOT)


@app.post("/api/us-research/import-sample")
def import_us_research_sample_to_db(db: Session = Depends(get_db)) -> dict[str, Any]:
    preview = build_us_research_import_preview(REPO_ROOT)
    records = preview["records"]

    summary = {
        "assets": upsert_rows(db, Asset, [asset_record_to_row(row) for row in records["assets"]], ["natural_key"]),
        "assetDailyPrices": upsert_rows(db, AssetDailyPrice, [asset_daily_price_record_to_row(row) for row in records["assetDailyPrices"]], ["natural_key"]),
        "watchlistItems": upsert_rows(db, WatchlistItem, [watchlist_record_to_row(row) for row in records["watchlistItems"]], ["natural_key"]),
        "portfolioSnapshots": upsert_rows(db, PortfolioSnapshot, [portfolio_snapshot_record_to_row(row) for row in records["portfolioSnapshots"]], ["snapshot_id"]),
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


@app.get("/api/us-research/db-overview")
def get_us_research_db_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return build_us_research_db_overview(db)


@app.get("/api/strategy-results/overview")
def get_strategy_results_overview() -> dict[str, Any]:
    return build_strategy_results_overview(REPO_ROOT)


@app.get("/api/research/readiness")
def get_research_readiness(scope: str = "a_share_cross_section", db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return evaluate_research_readiness(scope, Base.metadata.tables.keys(), get_research_table_counts(db))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def get_table_counts(db: Session) -> dict[str, int]:
    return {
        "stocks": db.scalar(select(func.count(Stock.ts_code))) or 0,
        "stockDailyBars": db.scalar(select(func.count(StockDailyBar.id))) or 0,
        "stockDailyBasic": db.scalar(select(func.count(StockDailyBasic.id))) or 0,
        "stockFinancialIndicators": db.scalar(select(func.count(StockFinancialIndicator.id))) or 0,
        "stockListings": db.scalar(select(func.count(StockListing.ts_code))) or 0,
        "stockLimitPrices": db.scalar(select(func.count(StockLimitPrice.id))) or 0,
        "stockSuspendEvents": db.scalar(select(func.count(StockSuspendEvent.id))) or 0,
        "tradeCalendars": db.scalar(select(func.count(TradeCalendar.id))) or 0,
        "stockAdjustFactors": db.scalar(select(func.count(StockAdjustFactor.id))) or 0,
        "indices": db.scalar(select(func.count(Index.ts_code))) or 0,
        "indexDailyBars": db.scalar(select(func.count(IndexDailyBar.id))) or 0,
        "funds": db.scalar(select(func.count(Fund.ts_code))) or 0,
        "fundDailyBars": db.scalar(select(func.count(FundDailyBar.id))) or 0,
        "fundAdjustFactors": db.scalar(select(func.count(FundAdjustFactor.id))) or 0,
        "industryClassifications": db.scalar(select(func.count(IndustryClassification.index_code))) or 0,
        "industryMembers": db.scalar(select(func.count(IndustryMember.id))) or 0,
        "stockPools": db.scalar(select(func.count(StockPool.id))) or 0,
        "stockPoolMembers": db.scalar(select(func.count(StockPoolMember.id))) or 0,
        "assets": db.scalar(select(func.count(Asset.id))) or 0,
        "assetDailyPrices": db.scalar(select(func.count(AssetDailyPrice.id))) or 0,
        "watchlistItems": db.scalar(select(func.count(WatchlistItem.id))) or 0,
        "portfolioSnapshots": db.scalar(select(func.count(PortfolioSnapshot.id))) or 0,
        "dataSyncRuns": db.scalar(select(func.count(DataSyncRun.id))) or 0,
        "dataSyncJobs": db.scalar(select(func.count(DataSyncJob.id))) or 0,
        "dataOverviewSnapshots": db.scalar(select(func.count(DataOverviewSnapshot.key))) or 0,
    }


def get_research_table_counts(db: Session) -> dict[str, int]:
    def exists(model: type[Base]) -> int:
        return int(db.scalar(select(1).select_from(model).limit(1)) is not None)

    return {
        "stocks": exists(Stock),
        "stock_daily_bars": exists(StockDailyBar),
        "stock_daily_basic": exists(StockDailyBasic),
        "stock_financial_indicators": exists(StockFinancialIndicator),
        "stock_listings": exists(StockListing),
        "stock_limit_prices": exists(StockLimitPrice),
        "stock_suspend_events": exists(StockSuspendEvent),
        "trade_calendars": exists(TradeCalendar),
        "stock_adjust_factors": exists(StockAdjustFactor),
        "indices": exists(Index),
        "index_daily_bars": exists(IndexDailyBar),
        "funds": exists(Fund),
        "fund_daily_bars": exists(FundDailyBar),
        "fund_adjust_factors": exists(FundAdjustFactor),
        "industry_classifications": exists(IndustryClassification),
        "industry_members": exists(IndustryMember),
    }


def build_stock_filters(q: str | None = None, industry: str | None = None, market: str | None = None) -> list[Any]:
    filters: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Stock.ts_code.ilike(like), Stock.symbol.ilike(like), Stock.name.ilike(like)))
    if industry:
        filters.append(Stock.industry == industry)
    if market:
        filters.append(Stock.market == market)
    return filters


def query_latest_bars(db: Session, ts_codes: list[str]) -> dict[str, StockDailyBar]:
    if not ts_codes:
        return {}
    latest = (
        select(StockDailyBar.ts_code, func.max(StockDailyBar.trade_date).label("latest_date"))
        .where(StockDailyBar.ts_code.in_(ts_codes))
        .group_by(StockDailyBar.ts_code)
        .subquery()
    )
    rows = db.scalars(
        select(StockDailyBar)
        .join(latest, and_(StockDailyBar.ts_code == latest.c.ts_code, StockDailyBar.trade_date == latest.c.latest_date))
        .order_by(StockDailyBar.ts_code)
    ).all()
    return {row.ts_code: row for row in rows}


def query_latest_daily_basic(db: Session, ts_codes: list[str]) -> dict[str, StockDailyBasic]:
    if not ts_codes:
        return {}
    latest = (
        select(StockDailyBasic.ts_code, func.max(StockDailyBasic.trade_date).label("latest_date"))
        .where(StockDailyBasic.ts_code.in_(ts_codes))
        .group_by(StockDailyBasic.ts_code)
        .subquery()
    )
    rows = db.scalars(
        select(StockDailyBasic)
        .join(latest, and_(StockDailyBasic.ts_code == latest.c.ts_code, StockDailyBasic.trade_date == latest.c.latest_date))
        .order_by(StockDailyBasic.ts_code)
    ).all()
    return {row.ts_code: row for row in rows}


def query_bar_counts(db: Session, ts_codes: list[str]) -> dict[str, int]:
    if not ts_codes:
        return {}
    return dict(db.execute(select(StockDailyBar.ts_code, func.count(StockDailyBar.id)).where(StockDailyBar.ts_code.in_(ts_codes)).group_by(StockDailyBar.ts_code)).all())


def query_date_coverage(db: Session, date_column: Any, code_column: Any) -> dict[str, Any]:
    min_date, max_date, rows, symbols, trade_dates = db.execute(
        select(func.min(date_column), func.max(date_column), func.count(), func.count(func.distinct(code_column)), func.count(func.distinct(date_column)))
    ).one()
    return {
        "minDate": min_date.isoformat() if min_date else None,
        "maxDate": max_date.isoformat() if max_date else None,
        "rows": int(rows or 0),
        "symbols": int(symbols or 0),
        "dates": int(trade_dates or 0),
    }


def query_entity_coverage(db: Session, code_column: Any) -> dict[str, Any]:
    rows, symbols = db.execute(select(func.count(), func.count(func.distinct(code_column)))).one()
    return {"rows": int(rows or 0), "symbols": int(symbols or 0)}


def query_stock_limit_coverage(db: Session) -> dict[str, Any]:
    min_date, max_date, rows, symbols, trade_dates = db.execute(
        select(
            func.min(StockLimitPrice.trade_date),
            func.max(StockLimitPrice.trade_date),
            func.count(),
            func.count(func.distinct(StockLimitPrice.ts_code)),
            func.count(func.distinct(StockLimitPrice.trade_date)),
        )
        .select_from(StockLimitPrice)
        .join(StockListing, StockListing.ts_code == StockLimitPrice.ts_code)
    ).one()
    return {
        "minDate": min_date.isoformat() if min_date else None,
        "maxDate": max_date.isoformat() if max_date else None,
        "rows": int(rows or 0),
        "symbols": int(symbols or 0),
        "dates": int(trade_dates or 0),
    }


def query_trade_calendar_coverage(db: Session) -> dict[str, Any]:
    coverage = query_date_coverage(db, TradeCalendar.cal_date, TradeCalendar.exchange)
    latest_open = db.scalar(select(func.max(TradeCalendar.cal_date)).where(TradeCalendar.is_open.is_(True)))
    coverage["latestOpenDate"] = latest_open.isoformat() if latest_open else None
    return coverage


def query_listing_coverage(db: Session) -> dict[str, Any]:
    rows = db.scalar(select(func.count(StockListing.ts_code))) or 0
    by_status = dict(db.execute(select(StockListing.list_status, func.count()).group_by(StockListing.list_status)).all())
    return {
        "rows": int(rows),
        "listed": int(by_status.get("L", 0)),
        "delisted": int(by_status.get("D", 0)),
        "paused": int(by_status.get("P", 0)),
        "untraded": int(by_status.get("G", 0)),
    }


def build_catalog_filters(code_column: Any, name_column: Any, q: str | None = None, market_column: Any | None = None, market: str | None = None) -> list[Any]:
    filters: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(code_column.ilike(like), name_column.ilike(like)))
    if market and market_column is not None:
        filters.append(market_column == market)
    return filters


def get_pool_or_404(db: Session, pool_id: int) -> StockPool:
    pool = db.get(StockPool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="股票池不存在。")
    return pool


def pool_to_schema(pool: StockPool, member_count: int) -> StockPoolOut:
    return StockPoolOut(id=pool.id, name=pool.name, description=pool.description, member_count=member_count, created_at=pool.created_at, updated_at=pool.updated_at)


def pool_detail_to_schema(db: Session, pool: StockPool) -> StockPoolDetailOut:
    rows = db.execute(
        select(StockPoolMember, Stock)
        .join(Stock, Stock.ts_code == StockPoolMember.ts_code)
        .where(StockPoolMember.pool_id == pool.id)
        .order_by(Stock.ts_code)
    ).all()
    members = [StockPoolMemberOut(**stock_to_dict(stock), added_at=member.created_at) for member, stock in rows]
    return StockPoolDetailOut(**pool_to_schema(pool, len(members)).model_dump(), members=members)


def stock_to_dict(stock: Stock) -> dict[str, Any]:
    return {
        "ts_code": stock.ts_code,
        "symbol": stock.symbol,
        "name": stock.name,
        "area": stock.area,
        "industry": stock.industry,
        "market": stock.market,
        "list_date": stock.list_date,
    }


def get_open_trade_dates(pro: Any, start_date: date, end_date: date) -> list[date]:
    df = pro.trade_cal(exchange="", start_date=tushare_date(start_date), end_date=tushare_date(end_date), fields="cal_date,is_open")
    dates = [parse_tushare_date(row["cal_date"]) for row in df.to_dict("records") if int(row.get("is_open", 0)) == 1]
    return sorted(day for day in dates if day is not None)


def filter_sparse_trade_dates(db: Session, date_column: Any, trade_dates: list[date], min_existing_rows: int) -> list[date]:
    if not trade_dates:
        return []
    counts = dict(db.execute(select(date_column, func.count()).where(date_column.in_(trade_dates)).group_by(date_column)).all())
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
    if not ts_code or not name or not list_status:
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
    if not ts_code or not trade_date or not suspend_type:
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


def financial_indicator_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    end_date = parse_tushare_date(item.get("end_date"))
    ann_date = parse_tushare_date(item.get("ann_date"))
    if not ts_code or not end_date or not ann_date:
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
    row = {"ts_code": str(ts_code), "ann_date": ann_date, "end_date": end_date}
    row.update({field: decimal_or_none(item.get(field)) for field in numeric_fields})
    return row


def trade_calendar_record_to_row(item: dict[str, Any], fallback_exchange: str = "SSE") -> dict[str, Any] | None:
    cal_date = parse_tushare_date(item.get("cal_date"))
    if not cal_date:
        return None
    return {
        "exchange": str(item.get("exchange") or fallback_exchange),
        "cal_date": cal_date,
        "is_open": bool(int(item.get("is_open") or 0)),
        "pretrade_date": parse_tushare_date(item.get("pretrade_date")),
    }


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


def fund_adjust_factor_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    return adjust_factor_record_to_row(item)


def index_basic_record_to_row(item: dict[str, Any], fallback_market: str | None = None) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    name = item.get("name")
    if not ts_code or not name:
        return None
    return {
        "ts_code": str(ts_code),
        "name": str(name),
        "market": item.get("market") or fallback_market,
        "publisher": item.get("publisher"),
        "category": item.get("category") or item.get("index_type"),
        "base_date": parse_tushare_date(item.get("base_date")),
        "list_date": parse_tushare_date(item.get("list_date")),
    }


def index_daily_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    return market_daily_record_to_row(item)


def fund_basic_record_to_row(item: dict[str, Any], fallback_market: str | None = None) -> dict[str, Any] | None:
    ts_code = item.get("ts_code")
    name = item.get("name")
    if not ts_code or not name:
        return None
    return {
        "ts_code": str(ts_code),
        "name": str(name),
        "market": item.get("market") or fallback_market,
        "fund_type": item.get("fund_type") or item.get("type"),
        "management": item.get("management"),
        "custodian": item.get("custodian"),
        "list_date": parse_tushare_date(item.get("list_date")),
    }


def fund_daily_record_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
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


def industry_classification_record_to_row(item: dict[str, Any], fallback_src: str = "SW2021") -> dict[str, Any] | None:
    index_code = item.get("index_code")
    industry_name = item.get("industry_name")
    if not index_code or not industry_name:
        return None
    return {
        "index_code": str(index_code),
        "industry_name": str(industry_name),
        "level": item.get("level"),
        "industry_code": item.get("industry_code"),
        "parent_code": item.get("parent_code"),
        "src": item.get("src") or fallback_src,
    }


def industry_member_record_to_row(item: dict[str, Any], fallback_index_code: str | None = None) -> dict[str, Any] | None:
    index_code = item.get("index_code") or fallback_index_code
    con_code = item.get("con_code") or item.get("ts_code")
    in_date = parse_tushare_date(item.get("in_date"))
    if not index_code or not con_code or not in_date:
        return None
    return {
        "index_code": str(index_code),
        "con_code": str(con_code),
        "con_name": item.get("con_name") or item.get("name"),
        "in_date": in_date,
        "out_date": parse_tushare_date(item.get("out_date")),
        "is_new": bool_from_tushare(item.get("is_new")),
    }


def industry_member_query_kwargs(index_code: str, classification: dict[str, Any] | None) -> dict[str, str]:
    level = (classification or {}).get("level")
    if level == "L1":
        return {"l1_code": index_code}
    if level == "L2":
        return {"l2_code": index_code}
    if level == "L3":
        return {"l3_code": index_code}
    return {"index_code": index_code}


def dedupe_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[tuple(row.get(key) for key in keys)] = row
    return list(deduped.values())


def upsert_rows(db: Session, model: type[Any], rows: list[dict[str, Any]], conflict_columns: list[str]) -> int:
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
                stmt = stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_cols)
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


def validate_sync_job_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    without_token = {key: value for key, value in payload.items() if key != "token"}
    if action == "us_sample":
        return {}
    request_models = {
        "stock_listings": SyncStockListingsRequest,
        "trade_calendar": SyncTradeCalendarRequest,
        "market_bundle": SyncMarketDataRequest,
        "daily_market": SyncMarketDataRequest,
    }
    try:
        request = request_models[action].model_validate(without_token)
    except (KeyError, ValidationError) as exc:
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else f"不支持的同步动作: {action}"
        raise HTTPException(status_code=422, detail=detail) from exc
    return request.model_dump(mode="json", exclude={"token"})


def sync_job_payload_hash(action: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"action": action, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def run_sync_job(job_id: str) -> None:
    with SessionLocal() as db:
        claimed = db.execute(
            update(DataSyncJob)
            .where(DataSyncJob.id == job_id, DataSyncJob.status == "queued")
            .values(status="running", started_at=datetime.now(timezone.utc), message="任务执行中")
        )
        db.commit()
        if claimed.rowcount != 1:
            return

        job = db.get(DataSyncJob, job_id)
        if not job:
            return
        action = job.action
        normalized_payload = dict(job.payload or {})

        try:
            raw_result = execute_sync_job_action(action, normalized_payload, db)
            result = json_safe_value(raw_result)
            status = normalize_sync_job_status(result.get("status") if isinstance(result, dict) else None)
            rows_upserted = sync_result_rows(result)
            message = sync_result_message(action, status, result)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            status = "failed"
            rows_upserted = 0
            message = f"{type(exc).__name__}: {exc}"[:1000]
            result = {"error": message}

        job = db.get(DataSyncJob, job_id)
        if not job:
            return
        job.status = status
        job.rows_upserted = rows_upserted
        job.message = message
        job.result = result
        job.finished_at = datetime.now(timezone.utc)
        job.active_key = None
        db.commit()


def execute_sync_job_action(action: str, payload: dict[str, Any], db: Session) -> dict[str, Any]:
    if action == "stock_listings":
        return sync_stock_listings(SyncStockListingsRequest.model_validate(payload), db)
    if action == "trade_calendar":
        return sync_trade_calendar(SyncTradeCalendarRequest.model_validate(payload), db)
    if action == "us_sample":
        result = import_us_research_sample_to_db(db)
        return {**result, "rows_upserted": sum(int(value or 0) for value in result.get("summary", {}).values())}
    if action in {"market_bundle", "daily_market"}:
        return execute_market_sync_bundle(action, SyncMarketDataRequest.model_validate(payload), db)
    raise ValueError(f"不支持的同步动作: {action}")


def execute_market_sync_bundle(action: str, payload: SyncMarketDataRequest, db: Session) -> dict[str, Any]:
    suspend_payload = SyncSuspendEventsRequest(
        start_date=payload.start_date,
        end_date=payload.end_date,
        max_trade_dates=payload.max_trade_dates,
    )
    components: list[tuple[str, Any]] = []
    if action == "daily_market":
        components.extend(
            [
                ("stock_basic", lambda: sync_stock_basic(SyncStockBasicRequest(), db)),
                ("daily_basic", lambda: sync_market_daily_basic(payload, db)),
            ]
        )
    components.extend(
        [
            ("daily", lambda: sync_market_daily(payload, db)),
            ("limit_prices", lambda: sync_market_limit_prices(payload, db)),
            ("suspend_events", lambda: sync_market_suspend_events(suspend_payload, db)),
        ]
    )

    component_results: dict[str, Any] = {}
    rows_upserted = 0
    statuses: list[str] = []
    for name, operation in components:
        try:
            result = json_safe_value(operation())
            status = normalize_sync_job_status(result.get("status") if isinstance(result, dict) else None)
            rows_upserted += sync_result_rows(result)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            status = "failed"
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
        statuses.append(status)
        component_results[name] = result

    failed = statuses.count("failed")
    partial = statuses.count("partial")
    status = "failed" if failed == len(statuses) else "partial" if failed or partial else "ok"
    return {
        "status": status,
        "rows_upserted": rows_upserted,
        "message": f"components={len(statuses)}, failed={failed}, partial={partial}",
        "components": component_results,
    }


def normalize_sync_job_status(status: Any) -> str:
    return status if status in {"ok", "partial", "failed"} else "ok"


def sync_result_rows(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    if "rows_upserted" in result:
        return int(result.get("rows_upserted") or 0)
    summary = result.get("summary")
    if isinstance(summary, dict):
        return sum(int(value or 0) for value in summary.values())
    return 0


def sync_result_message(action: str, status: str, result: Any) -> str:
    if isinstance(result, dict) and result.get("message"):
        return str(result["message"])[:1000]
    return f"{action} {status}"[:1000]


def json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe_value(item) for item in value]
    return str(value)


def sync_job_to_dict(job: DataSyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "action": job.action,
        "status": job.status,
        "rowsUpserted": job.rows_upserted,
        "message": job.message,
        "result": json_safe_value(job.result),
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }


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


def sync_run_to_dict(row: DataSyncRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "target": row.target,
        "startDate": row.start_date.isoformat() if row.start_date else None,
        "endDate": row.end_date.isoformat() if row.end_date else None,
        "rowsUpserted": row.rows_upserted,
        "status": row.status,
        "message": row.message,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def daily_bar_to_schema(row: StockDailyBar) -> DailyBarOut:
    return DailyBarOut(
        ts_code=row.ts_code,
        trade_date=row.trade_date,
        open=decimal_to_float(row.open) or 0.0,
        high=decimal_to_float(row.high) or 0.0,
        low=decimal_to_float(row.low) or 0.0,
        close=decimal_to_float(row.close) or 0.0,
        pre_close=decimal_to_float(row.pre_close),
        change_amount=decimal_to_float(row.change_amount),
        pct_chg=decimal_to_float(row.pct_chg),
        vol=decimal_to_float(row.vol),
        amount=decimal_to_float(row.amount),
    )


def daily_basic_to_dict(row: StockDailyBasic | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "tradeDate": row.trade_date.isoformat(),
        "close": decimal_to_float(row.close),
        "turnoverRate": decimal_to_float(row.turnover_rate),
        "turnoverRateF": decimal_to_float(row.turnover_rate_f),
        "volumeRatio": decimal_to_float(row.volume_ratio),
        "pe": decimal_to_float(row.pe),
        "peTtm": decimal_to_float(row.pe_ttm),
        "pb": decimal_to_float(row.pb),
        "ps": decimal_to_float(row.ps),
        "psTtm": decimal_to_float(row.ps_ttm),
        "dvRatio": decimal_to_float(row.dv_ratio),
        "dvTtm": decimal_to_float(row.dv_ttm),
        "totalShare": decimal_to_float(row.total_share),
        "floatShare": decimal_to_float(row.float_share),
        "freeShare": decimal_to_float(row.free_share),
        "totalMv": decimal_to_float(row.total_mv),
        "circMv": decimal_to_float(row.circ_mv),
    }


def financial_indicator_to_dict(row: StockFinancialIndicator | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "annDate": row.ann_date.isoformat(),
        "endDate": row.end_date.isoformat(),
        "eps": decimal_to_float(row.eps),
        "dtEps": decimal_to_float(row.dt_eps),
        "bps": decimal_to_float(row.bps),
        "netprofitMargin": decimal_to_float(row.netprofit_margin),
        "grossprofitMargin": decimal_to_float(row.grossprofit_margin),
        "roe": decimal_to_float(row.roe),
        "roeWaa": decimal_to_float(row.roe_waa),
        "roa": decimal_to_float(row.roa),
        "debtToAssets": decimal_to_float(row.debt_to_assets),
        "currentRatio": decimal_to_float(row.current_ratio),
        "quickRatio": decimal_to_float(row.quick_ratio),
        "assetsTurn": decimal_to_float(row.assets_turn),
        "basicEpsYoy": decimal_to_float(row.basic_eps_yoy),
        "opYoy": decimal_to_float(row.op_yoy),
        "netprofitYoy": decimal_to_float(row.netprofit_yoy),
        "trYoy": decimal_to_float(row.tr_yoy),
        "orYoy": decimal_to_float(row.or_yoy),
        "qSalesYoy": decimal_to_float(row.q_sales_yoy),
        "qProfitYoy": decimal_to_float(row.q_profit_yoy),
    }


def stock_listing_to_dict(row: StockListing) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "symbol": row.symbol,
        "name": row.name,
        "area": row.area,
        "industry": row.industry,
        "market": row.market,
        "exchange": row.exchange,
        "listStatus": row.list_status,
        "listDate": row.list_date.isoformat() if row.list_date else None,
        "delistDate": row.delist_date.isoformat() if row.delist_date else None,
    }


def stock_limit_price_to_dict(row: StockLimitPrice) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "tradeDate": row.trade_date.isoformat(),
        "preClose": decimal_to_float(row.pre_close),
        "upLimit": decimal_to_float(row.up_limit),
        "downLimit": decimal_to_float(row.down_limit),
    }


def stock_suspend_event_to_dict(row: StockSuspendEvent) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "tradeDate": row.trade_date.isoformat(),
        "suspendType": row.suspend_type,
        "suspendTiming": row.suspend_timing or None,
    }


def trade_calendar_to_dict(row: TradeCalendar) -> dict[str, Any]:
    return {
        "exchange": row.exchange,
        "calDate": row.cal_date.isoformat(),
        "isOpen": row.is_open,
        "pretradeDate": row.pretrade_date.isoformat() if row.pretrade_date else None,
    }


def adjust_factor_to_dict(row: StockAdjustFactor) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "tradeDate": row.trade_date.isoformat(),
        "adjFactor": decimal_to_float(row.adj_factor),
    }


def fund_adjust_factor_to_dict(row: FundAdjustFactor) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "tradeDate": row.trade_date.isoformat(),
        "adjFactor": decimal_to_float(row.adj_factor),
    }


def index_to_dict(row: Index) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "name": row.name,
        "market": row.market,
        "publisher": row.publisher,
        "category": row.category,
        "baseDate": row.base_date.isoformat() if row.base_date else None,
        "listDate": row.list_date.isoformat() if row.list_date else None,
    }


def fund_to_dict(row: Fund) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "name": row.name,
        "market": row.market,
        "fundType": row.fund_type,
        "management": row.management,
        "custodian": row.custodian,
        "listDate": row.list_date.isoformat() if row.list_date else None,
    }


def market_bar_to_dict(row: Any) -> dict[str, Any]:
    return {
        "tsCode": row.ts_code,
        "tradeDate": row.trade_date.isoformat(),
        "open": decimal_to_float(row.open),
        "high": decimal_to_float(row.high),
        "low": decimal_to_float(row.low),
        "close": decimal_to_float(row.close),
        "preClose": decimal_to_float(row.pre_close),
        "changeAmount": decimal_to_float(row.change_amount),
        "pctChg": decimal_to_float(row.pct_chg),
        "vol": decimal_to_float(row.vol),
        "amount": decimal_to_float(row.amount),
    }


def industry_to_dict(row: IndustryClassification) -> dict[str, Any]:
    return {
        "indexCode": row.index_code,
        "industryName": row.industry_name,
        "level": row.level,
        "industryCode": row.industry_code,
        "parentCode": row.parent_code,
        "src": row.src,
    }


def industry_member_to_dict(row: IndustryMember) -> dict[str, Any]:
    return {
        "indexCode": row.index_code,
        "conCode": row.con_code,
        "conName": row.con_name,
        "inDate": row.in_date.isoformat(),
        "outDate": row.out_date.isoformat() if row.out_date else None,
        "isNew": row.is_new,
    }


def build_us_research_db_overview(db: Session) -> dict[str, Any]:
    assets = list(db.scalars(select(Asset).order_by(Asset.symbol)).all())
    prices = list(db.scalars(select(AssetDailyPrice).order_by(AssetDailyPrice.asset_natural_key, AssetDailyPrice.trade_date)).all())
    watchlist = list(db.scalars(select(WatchlistItem).order_by(WatchlistItem.watchlist_name, WatchlistItem.asset_natural_key)).all())
    snapshots = list(db.scalars(select(PortfolioSnapshot).order_by(PortfolioSnapshot.id)).all())

    latest_price_by_asset: dict[str, AssetDailyPrice] = {}
    for price in prices:
        latest_price_by_asset[price.asset_natural_key] = price

    return {
        "source": "db-sample" if assets else "db-empty",
        "isSample": True,
        "counts": {
            "assets": len(assets),
            "assetDailyPrices": len(prices),
            "watchlistItems": len(watchlist),
            "portfolioSnapshots": len(snapshots),
        },
        "dataBoundary": {
            "brokerConnected": False,
            "realHoldingsImported": False,
            "dbPersistence": "sample_persisted" if assets else "empty",
            "executionEnabled": False,
        },
        "assets": [asset_to_contract(asset, latest_price_by_asset.get(asset.natural_key)) for asset in assets],
        "watchlist": [watchlist_item_to_contract(row) for row in watchlist],
        "portfolioSnapshots": [portfolio_snapshot_to_contract(row) for row in snapshots],
        "marketSnapshot": {
            "status": "ok" if prices else "empty",
            "source": "db:asset_daily_prices",
            "symbolCount": len({row.asset_natural_key for row in prices}),
            "symbols": [price_to_contract(row) for row in prices],
        },
    }


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


def asset_to_contract(asset: Asset, price: AssetDailyPrice | None) -> dict[str, Any]:
    return {
        "naturalKey": asset.natural_key,
        "market": asset.market,
        "symbol": asset.symbol,
        "name": asset.name,
        "instrumentType": asset.instrument_type,
        "leverageFactor": decimal_to_float(asset.leverage_factor),
        "riskTag": asset.risk_tag,
        "theme": asset.theme,
        "isSample": asset.is_sample,
        "source": asset.source,
        "latestPrice": price_to_contract(price) if price else None,
    }


def price_to_contract(price: AssetDailyPrice | None) -> dict[str, Any]:
    if not price:
        return {}
    return {
        "assetNaturalKey": price.asset_natural_key,
        "tradeDate": price.trade_date.isoformat(),
        "close": decimal_to_float(price.close),
        "ma20": decimal_to_float(price.ma20),
        "ma50": decimal_to_float(price.ma50),
        "ma200": decimal_to_float(price.ma200),
        "return20dPct": decimal_to_float(price.return20d_pct),
        "return60dPct": decimal_to_float(price.return60d_pct),
        "volatility20dPct": decimal_to_float(price.volatility20d_pct),
        "isSample": price.is_sample,
        "source": price.source,
        "isStale": price.is_stale,
    }


def watchlist_item_to_contract(row: WatchlistItem) -> dict[str, Any]:
    return {
        "naturalKey": row.natural_key,
        "watchlistName": row.watchlist_name,
        "assetNaturalKey": row.asset_natural_key,
        "role": row.role,
        "theme": row.theme,
        "subtheme": row.subtheme,
        "riskTag": row.risk_tag,
        "notes": row.notes,
        "isSample": row.is_sample,
        "source": row.source,
    }


def portfolio_snapshot_to_contract(row: PortfolioSnapshot) -> dict[str, Any]:
    return {
        "snapshotId": row.snapshot_id,
        "source": row.source,
        "isSample": row.is_sample,
        "holdingCount": row.holding_count,
        "totalSampleCostBasis": decimal_to_float(row.total_sample_cost_basis),
        "holdings": row.holdings or [],
    }


def parse_iso_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    return date.fromisoformat(str(value))


def bool_from_tushare(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"y", "yes", "true", "1"}:
        return True
    if text in {"n", "no", "false", "0"}:
        return False
    return None


def decimal_to_float(value: Decimal | int | float | None) -> float | None:
    if value is None:
        return None
    return float(value)
