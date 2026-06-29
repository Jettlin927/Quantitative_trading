from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import (
    Asset,
    AssetDailyPrice,
    DataSyncRun,
    PortfolioSnapshot,
    Stock,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockPool,
    StockPoolMember,
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
    SyncDailyRequest,
    SyncFundamentalsRequest,
    SyncMarketDataRequest,
    SyncMarketFundamentalsRequest,
    SyncStockBasicRequest,
)
from .tushare_client import decimal_or_none, get_pro_api, parse_tushare_date, tushare_date
from .us_research import build_us_research_import_preview, build_us_research_overview
from .strategy_results import build_strategy_results_overview


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Quant Data Workspace", version="0.3.0")
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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "quant-data-workspace", "status": "ok"}


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(select(1))
    return {
        "status": "ok",
        "service": "quant-data-workspace",
        "database": "ok",
        "tables": get_table_counts(db),
    }


@app.get("/api/db/overview")
def get_db_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "source": "postgresql",
        "tables": get_table_counts(db),
        "aShare": {
            "stocks": db.scalar(select(func.count(Stock.ts_code))) or 0,
            "dailyBars": query_date_coverage(db, StockDailyBar.trade_date, StockDailyBar.ts_code),
            "dailyBasic": query_date_coverage(db, StockDailyBasic.trade_date, StockDailyBasic.ts_code),
            "financialIndicators": query_date_coverage(db, StockFinancialIndicator.ann_date, StockFinancialIndicator.ts_code),
        },
        "usSample": build_us_research_db_overview(db),
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


@app.get("/api/tushare/sync-progress")
def get_sync_progress(db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = list(db.scalars(select(DataSyncRun).order_by(DataSyncRun.created_at.desc()).limit(20)).all())
    return {
        "runs": [sync_run_to_dict(row) for row in runs],
        "coverage": {
            "daily": query_date_coverage(db, StockDailyBar.trade_date, StockDailyBar.ts_code),
            "dailyBasic": query_date_coverage(db, StockDailyBasic.trade_date, StockDailyBasic.ts_code),
            "financialIndicators": query_date_coverage(db, StockFinancialIndicator.ann_date, StockFinancialIndicator.ts_code),
        },
    }


@app.get("/api/daily-bars", response_model=list[DailyBarOut])
def get_daily_bars(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[DailyBarOut]:
    bars = list(
        db.scalars(
            select(StockDailyBar)
            .where(StockDailyBar.ts_code == ts_code.upper(), StockDailyBar.trade_date >= start_date, StockDailyBar.trade_date <= end_date)
            .order_by(StockDailyBar.trade_date)
        ).all()
    )
    return [daily_bar_to_schema(row) for row in bars]


@app.get("/api/stocks/{ts_code}/fundamentals", response_model=StockFundamentalsOut)
def get_stock_fundamentals(ts_code: str, db: Session = Depends(get_db)) -> StockFundamentalsOut:
    code = ts_code.upper()
    valuation = db.scalars(select(StockDailyBasic).where(StockDailyBasic.ts_code == code).order_by(StockDailyBasic.trade_date.desc()).limit(1)).first()
    financial = db.scalars(select(StockFinancialIndicator).where(StockFinancialIndicator.ts_code == code).order_by(StockFinancialIndicator.ann_date.desc()).limit(1)).first()
    return StockFundamentalsOut(ts_code=code, valuation=daily_basic_to_dict(valuation), financial=financial_indicator_to_dict(financial))


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


def get_table_counts(db: Session) -> dict[str, int]:
    return {
        "stocks": db.scalar(select(func.count(Stock.ts_code))) or 0,
        "stockDailyBars": db.scalar(select(func.count(StockDailyBar.id))) or 0,
        "stockDailyBasic": db.scalar(select(func.count(StockDailyBasic.id))) or 0,
        "stockFinancialIndicators": db.scalar(select(func.count(StockFinancialIndicator.id))) or 0,
        "stockPools": db.scalar(select(func.count(StockPool.id))) or 0,
        "stockPoolMembers": db.scalar(select(func.count(StockPoolMember.id))) or 0,
        "assets": db.scalar(select(func.count(Asset.id))) or 0,
        "assetDailyPrices": db.scalar(select(func.count(AssetDailyPrice.id))) or 0,
        "watchlistItems": db.scalar(select(func.count(WatchlistItem.id))) or 0,
        "portfolioSnapshots": db.scalar(select(func.count(PortfolioSnapshot.id))) or 0,
        "dataSyncRuns": db.scalar(select(func.count(DataSyncRun.id))) or 0,
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
            stmt = stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_cols)
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


def decimal_to_float(value: Decimal | int | float | None) -> float | None:
    if value is None:
        return None
    return float(value)
