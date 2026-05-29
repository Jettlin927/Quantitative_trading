from contextlib import asynccontextmanager
from datetime import date, timedelta
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .backtest_engine import DEFAULT_CONFIG, enrich_rows, json_safe, run_backtest
from .database import Base, engine, get_db
from .models import DataSyncRun, Stock, StockDailyBar, StockDailyBasic, StockFinancialIndicator
from .schemas import (
    BacktestRequest,
    DailyBarOut,
    NewsTrendOut,
    StockFundamentalsOut,
    StockOut,
    StockScreenOut,
    SyncDailyRequest,
    SyncFundamentalsRequest,
    SyncStockBasicRequest,
)
from .tushare_client import decimal_or_none, get_pro_api, parse_tushare_date, tushare_date

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Quantitative Trading API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


DAILY_BASIC_FIELDS = ",".join(
    [
        "ts_code",
        "trade_date",
        "close",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    ]
)

FINA_INDICATOR_FIELDS = ",".join(
    [
        "ts_code",
        "ann_date",
        "end_date",
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
)

NEWS_SOURCES = {
    "cls": "财联社",
    "wallstreetcn": "华尔街见闻",
    "xueqiu": "雪球热榜",
    "weibo": "微博热搜",
    "zhihu": "知乎热榜",
    "baidu": "百度热搜",
    "toutiao": "今日头条",
    "thepaper": "澎湃新闻",
}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "quantitative-trading-api", "docs": "/docs"}


@app.get("/api/stocks", response_model=list[StockOut])
def list_stocks(q: str | None = None, limit: int = 50, db: Session = Depends(get_db)) -> list[StockOut]:
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Stock.ts_code.ilike(like)) | (Stock.symbol.ilike(like)) | (Stock.name.ilike(like)) | (Stock.industry.ilike(like)))
    stmt = stmt.order_by(Stock.ts_code).limit(min(limit, 200))
    return [stock_to_schema(stock) for stock in db.scalars(stmt).all()]


@app.get("/api/stocks/screen", response_model=list[StockScreenOut])
def screen_stocks(
    q: str | None = None,
    industry: str | None = None,
    market: str | None = None,
    technical: str = "all",
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 60,
    db: Session = Depends(get_db),
) -> list[StockScreenOut]:
    end = end_date or date.today()
    start = start_date or end - timedelta(days=730)
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Stock.ts_code.ilike(like)) | (Stock.symbol.ilike(like)) | (Stock.name.ilike(like)) | (Stock.industry.ilike(like)))
    if industry:
        stmt = stmt.where(Stock.industry.ilike(f"%{industry}%"))
    if market:
        stmt = stmt.where(Stock.market.ilike(f"%{market}%"))

    stocks = db.scalars(stmt.order_by(Stock.ts_code).limit(min(limit, 200))).all()
    screened = [build_screen_row(db, stock, start, end, technical) for stock in stocks]
    if technical != "all":
        screened = [row for row in screened if row.technical_score > 0 and "无本地日线" not in row.technical_tags]
    return sorted(screened, key=lambda row: (row.technical_score, row.data_bars), reverse=True)


@app.post("/api/tushare/sync-stock-basic")
def sync_stock_basic(payload: SyncStockBasicRequest, db: Session = Depends(get_db)) -> dict[str, int | str]:
    pro = get_pro_api(payload.token)
    df = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )
    rows = [
        {
            "ts_code": item["ts_code"],
            "symbol": item.get("symbol"),
            "name": item.get("name") or item["ts_code"],
            "area": item.get("area"),
            "industry": item.get("industry"),
            "market": item.get("market"),
            "list_date": parse_tushare_date(item.get("list_date")),
        }
        for item in df.to_dict("records")
    ]
    if rows:
        stmt = pg_insert(Stock.__table__).values(rows)
        update_cols = {col: getattr(stmt.excluded, col) for col in rows[0] if col != "ts_code"}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code"], set_=update_cols))
    db.add(DataSyncRun(target="stock_basic", rows_upserted=len(rows), status="ok"))
    db.commit()
    return {"status": "ok", "rows_upserted": len(rows)}


@app.post("/api/tushare/sync-daily")
def sync_daily(payload: SyncDailyRequest, db: Session = Depends(get_db)) -> dict[str, int | str]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    ts_code = resolve_ts_code(db, payload.ts_code)
    pro = get_pro_api(payload.token)
    df = pro.daily(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )
    rows = [daily_record_to_row(item) for item in df.to_dict("records")]
    if rows:
        stmt = pg_insert(StockDailyBar.__table__).values(rows)
        update_cols = {col: getattr(stmt.excluded, col) for col in rows[0] if col not in {"id", "ts_code", "trade_date", "created_at"}}
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["ts_code", "trade_date"],
                set_=update_cols,
            )
        )

    db.add(
        DataSyncRun(
            target=ts_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            rows_upserted=len(rows),
            status="ok",
        )
    )
    db.commit()
    return {"status": "ok", "ts_code": ts_code, "rows_upserted": len(rows)}


@app.post("/api/tushare/sync-fundamentals")
def sync_fundamentals(payload: SyncFundamentalsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    ts_code = resolve_ts_code(db, payload.ts_code)
    pro = get_pro_api(payload.token)
    daily_basic_df = pro.daily_basic(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=DAILY_BASIC_FIELDS,
    )
    fina_indicator_df = pro.fina_indicator(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=FINA_INDICATOR_FIELDS,
    )

    daily_rows = dedupe_rows(
        [row for item in daily_basic_df.to_dict("records") if (row := daily_basic_record_to_row(item))],
        ("ts_code", "trade_date"),
    )
    financial_rows = dedupe_rows(
        [row for item in fina_indicator_df.to_dict("records") if (row := financial_indicator_record_to_row(item))],
        ("ts_code", "end_date", "ann_date"),
    )

    if daily_rows:
        stmt = pg_insert(StockDailyBasic.__table__).values(daily_rows)
        update_cols = {col: getattr(stmt.excluded, col) for col in daily_rows[0] if col not in {"id", "ts_code", "trade_date", "created_at"}}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code", "trade_date"], set_=update_cols))

    if financial_rows:
        stmt = pg_insert(StockFinancialIndicator.__table__).values(financial_rows)
        update_cols = {col: getattr(stmt.excluded, col) for col in financial_rows[0] if col not in {"id", "ts_code", "end_date", "ann_date", "created_at"}}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code", "end_date", "ann_date"], set_=update_cols))

    db.add(
        DataSyncRun(
            target=f"{ts_code}:fundamentals",
            start_date=payload.start_date,
            end_date=payload.end_date,
            rows_upserted=len(daily_rows) + len(financial_rows),
            status="ok",
            message=f"daily_basic={len(daily_rows)}, fina_indicator={len(financial_rows)}",
        )
    )
    db.commit()
    return {
        "status": "ok",
        "ts_code": ts_code,
        "daily_basic_rows": len(daily_rows),
        "fina_indicator_rows": len(financial_rows),
    }


@app.get("/api/daily-bars", response_model=list[DailyBarOut])
def get_daily_bars(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[DailyBarOut]:
    ts_code = resolve_ts_code(db, ts_code)
    bars = query_daily_bars(db, ts_code, start_date, end_date)
    return enriched_bars_to_schema(bars)


@app.get("/api/stocks/{ts_code}/fundamentals", response_model=StockFundamentalsOut)
def get_stock_fundamentals(ts_code: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)) -> StockFundamentalsOut:
    resolved = resolve_ts_code(db, ts_code)
    valuation, financial = query_latest_fundamentals(db, resolved, start_date, end_date)
    profile = build_fundamental_profile(valuation, financial)
    return StockFundamentalsOut(
        ts_code=resolved,
        valuation=daily_basic_to_dict(valuation),
        financial=financial_indicator_to_dict(financial),
        score=profile["score"],
        tags=profile["tags"],
    )


@app.get("/api/news/trends", response_model=NewsTrendOut)
def get_news_trends(sources: str = "cls,wallstreetcn,xueqiu", count: int = 6, q: str | None = None) -> NewsTrendOut:
    selected_sources = [source.strip() for source in sources.split(",") if source.strip()]
    if not selected_sources:
        raise HTTPException(status_code=400, detail="请至少选择一个消息源。")

    items: list[dict[str, Any]] = []
    for source in selected_sources[:6]:
        items.extend(fetch_news_source(source, min(max(count, 1), 20)))

    if q:
        keyword = q.strip().lower()
        items = [item for item in items if keyword in item["title"].lower() or keyword in item["source_name"].lower()]

    return NewsTrendOut(status="ok", items=items[: min(max(count * len(selected_sources), 1), 60)])


@app.post("/api/backtests/run")
def run_db_backtest(payload: BacktestRequest, db: Session = Depends(get_db)) -> dict:
    ts_code = resolve_ts_code(db, payload.ts_code)
    bars = query_daily_bars(db, ts_code, payload.start_date, payload.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail="数据库里没有这个区间的行情，请先同步 Tushare 数据。")
    stock = db.get(Stock, ts_code)
    rows = [bar_to_backtest_row(bar) for bar in bars]
    config = dict(payload.config)
    config["symbolName"] = config.get("symbolName") or (f"{stock.name} {ts_code}" if stock else ts_code)
    return run_backtest(rows, config)


def resolve_ts_code(db: Session, text: str) -> str:
    query = text.strip().upper()
    if not query:
        raise HTTPException(status_code=400, detail="请填写股票代码或股票名称。")

    stock = db.get(Stock, query)
    if stock:
        return stock.ts_code

    stmt = (
        select(Stock)
        .where((Stock.symbol == query) | (Stock.name == text.strip()) | (Stock.name.ilike(f"%{text.strip()}%")))
        .order_by(Stock.ts_code)
        .limit(1)
    )
    stock = db.scalars(stmt).first()
    return stock.ts_code if stock else query


def query_daily_bars(db: Session, ts_code: str, start_date: date, end_date: date) -> list[StockDailyBar]:
    stmt = (
        select(StockDailyBar)
        .where(
            StockDailyBar.ts_code == ts_code,
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.trade_date)
    )
    return list(db.scalars(stmt).all())


def daily_record_to_row(item: dict) -> dict:
    return {
        "ts_code": item["ts_code"],
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


def daily_basic_record_to_row(item: dict) -> dict | None:
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not trade_date:
        return None
    return {
        "ts_code": item["ts_code"],
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


def financial_indicator_record_to_row(item: dict) -> dict | None:
    ann_date = parse_tushare_date(item.get("ann_date"))
    end_date = parse_tushare_date(item.get("end_date"))
    if not ann_date or not end_date:
        return None
    return {
        "ts_code": item["ts_code"],
        "ann_date": ann_date,
        "end_date": end_date,
        "eps": decimal_or_none(item.get("eps")),
        "dt_eps": decimal_or_none(item.get("dt_eps")),
        "bps": decimal_or_none(item.get("bps")),
        "netprofit_margin": decimal_or_none(item.get("netprofit_margin")),
        "grossprofit_margin": decimal_or_none(item.get("grossprofit_margin")),
        "roe": decimal_or_none(item.get("roe")),
        "roe_waa": decimal_or_none(item.get("roe_waa")),
        "roa": decimal_or_none(item.get("roa")),
        "debt_to_assets": decimal_or_none(item.get("debt_to_assets")),
        "current_ratio": decimal_or_none(item.get("current_ratio")),
        "quick_ratio": decimal_or_none(item.get("quick_ratio")),
        "assets_turn": decimal_or_none(item.get("assets_turn")),
        "basic_eps_yoy": decimal_or_none(item.get("basic_eps_yoy")),
        "op_yoy": decimal_or_none(item.get("op_yoy")),
        "netprofit_yoy": decimal_or_none(item.get("netprofit_yoy")),
        "tr_yoy": decimal_or_none(item.get("tr_yoy")),
        "or_yoy": decimal_or_none(item.get("or_yoy")),
        "q_sales_yoy": decimal_or_none(item.get("q_sales_yoy")),
        "q_profit_yoy": decimal_or_none(item.get("q_profit_yoy")),
    }


def dedupe_rows(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    by_key = {tuple(row[key] for key in keys): row for row in rows}
    return list(by_key.values())


def stock_to_schema(stock: Stock) -> StockOut:
    return StockOut(
        ts_code=stock.ts_code,
        symbol=stock.symbol,
        name=stock.name,
        area=stock.area,
        industry=stock.industry,
        market=stock.market,
        list_date=stock.list_date,
    )


def build_screen_row(db: Session, stock: Stock, start_date: date, end_date: date, technical: str) -> StockScreenOut:
    bars = query_daily_bars(db, stock.ts_code, start_date, end_date)
    valuation, financial = query_latest_fundamentals(db, stock.ts_code, start_date, end_date)
    fundamental_profile = build_fundamental_profile(valuation, financial)
    fundamentals = {
        "地区": stock.area,
        "行业": stock.industry,
        "市场": stock.market,
        "上市日期": stock.list_date.isoformat() if stock.list_date else None,
        "本地日线": len(bars),
        "估值": daily_basic_to_dict(valuation),
        "财务": financial_indicator_to_dict(financial),
    }
    if not bars:
        return StockScreenOut(
            **stock_to_schema(stock).model_dump(),
            data_bars=0,
            technical_score=0,
            technical_tags=["无本地日线"],
            fundamental_score=fundamental_profile["score"],
            fundamental_tags=fundamental_profile["tags"],
            signal_summary="先同步日线后再筛选",
            fundamentals=fundamentals,
        )

    rows = [bar_to_backtest_row(bar) for bar in bars]
    enriched = enrich_rows(rows, DEFAULT_CONFIG)
    latest = enriched[-1]
    previous = enriched[-2] if len(enriched) > 1 else None
    profile = classify_screen_signal(latest, previous, technical)
    latest_bar = bars[-1]
    return StockScreenOut(
        **stock_to_schema(stock).model_dump(),
        latest_date=latest_bar.trade_date,
        close=float(latest_bar.close),
        pct_chg=float(latest_bar.pct_chg) if latest_bar.pct_chg is not None else None,
        data_bars=len(bars),
        technical_score=profile["score"],
        technical_tags=profile["tags"],
        fundamental_score=fundamental_profile["score"],
        fundamental_tags=fundamental_profile["tags"],
        news_state="未刷新",
        signal_summary=profile["summary"],
        fundamentals=fundamentals,
    )


def query_latest_fundamentals(
    db: Session,
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[StockDailyBasic | None, StockFinancialIndicator | None]:
    valuation_stmt = select(StockDailyBasic).where(StockDailyBasic.ts_code == ts_code)
    financial_stmt = select(StockFinancialIndicator).where(StockFinancialIndicator.ts_code == ts_code)
    if start_date:
        valuation_stmt = valuation_stmt.where(StockDailyBasic.trade_date >= start_date)
        financial_stmt = financial_stmt.where(StockFinancialIndicator.end_date >= start_date)
    if end_date:
        valuation_stmt = valuation_stmt.where(StockDailyBasic.trade_date <= end_date)
        financial_stmt = financial_stmt.where(StockFinancialIndicator.end_date <= end_date)

    valuation = db.scalars(valuation_stmt.order_by(StockDailyBasic.trade_date.desc()).limit(1)).first()
    financial = db.scalars(financial_stmt.order_by(StockFinancialIndicator.end_date.desc(), StockFinancialIndicator.ann_date.desc()).limit(1)).first()
    return valuation, financial


def daily_basic_to_dict(row: StockDailyBasic | None) -> dict[str, Any]:
    if not row:
        return {"状态": "未同步基本面"}
    return json_safe(
        {
            "日期": row.trade_date.isoformat(),
            "收盘": decimal_to_float(row.close),
            "换手率": decimal_to_float(row.turnover_rate),
            "自由流通换手率": decimal_to_float(row.turnover_rate_f),
            "量比": decimal_to_float(row.volume_ratio),
            "PE": decimal_to_float(row.pe),
            "PE_TTM": decimal_to_float(row.pe_ttm),
            "PB": decimal_to_float(row.pb),
            "PS": decimal_to_float(row.ps),
            "PS_TTM": decimal_to_float(row.ps_ttm),
            "股息率": decimal_to_float(row.dv_ratio),
            "股息率_TTM": decimal_to_float(row.dv_ttm),
            "总市值_万元": decimal_to_float(row.total_mv),
            "流通市值_万元": decimal_to_float(row.circ_mv),
        }
    )


def financial_indicator_to_dict(row: StockFinancialIndicator | None) -> dict[str, Any]:
    if not row:
        return {"状态": "未同步财务指标"}
    return json_safe(
        {
            "公告日期": row.ann_date.isoformat(),
            "报告期": row.end_date.isoformat(),
            "EPS": decimal_to_float(row.eps),
            "每股净资产": decimal_to_float(row.bps),
            "ROE": decimal_to_float(row.roe),
            "加权ROE": decimal_to_float(row.roe_waa),
            "ROA": decimal_to_float(row.roa),
            "毛利率": decimal_to_float(row.grossprofit_margin),
            "净利率": decimal_to_float(row.netprofit_margin),
            "资产负债率": decimal_to_float(row.debt_to_assets),
            "流动比率": decimal_to_float(row.current_ratio),
            "速动比率": decimal_to_float(row.quick_ratio),
            "营收同比": decimal_to_float(row.tr_yoy),
            "营业收入同比": decimal_to_float(row.or_yoy),
            "净利润同比": decimal_to_float(row.netprofit_yoy),
            "单季营收同比": decimal_to_float(row.q_sales_yoy),
            "单季净利同比": decimal_to_float(row.q_profit_yoy),
        }
    )


def build_fundamental_profile(valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None) -> dict[str, Any]:
    score = 0
    tags: list[str] = []
    pe_ttm = decimal_to_float(valuation.pe_ttm if valuation else None)
    pb = decimal_to_float(valuation.pb if valuation else None)
    turnover = decimal_to_float(valuation.turnover_rate if valuation else None)
    roe = decimal_to_float(financial.roe if financial else None)
    gross_margin = decimal_to_float(financial.grossprofit_margin if financial else None)
    net_margin = decimal_to_float(financial.netprofit_margin if financial else None)
    debt = decimal_to_float(financial.debt_to_assets if financial else None)
    revenue_growth = decimal_to_float(financial.tr_yoy if financial else None)
    profit_growth = decimal_to_float(financial.netprofit_yoy if financial else None)

    if pe_ttm is not None and 0 < pe_ttm <= 40:
        score += 16
        tags.append("PE可比")
    elif valuation:
        tags.append("PE偏高/亏损")

    if pb is not None and 0 < pb <= 5:
        score += 12
        tags.append("PB可比")

    if turnover is not None and 0.4 <= turnover <= 8:
        score += 8
        tags.append("换手正常")

    if roe is not None and roe >= 10:
        score += 20
        tags.append("ROE较高")
    elif financial:
        tags.append("ROE待改善")

    if gross_margin is not None and gross_margin >= 20:
        score += 12
        tags.append("毛利率较好")

    if net_margin is not None and net_margin >= 5:
        score += 10
        tags.append("净利率为正")

    if debt is not None and debt <= 65:
        score += 12
        tags.append("负债可控")

    if revenue_growth is not None and revenue_growth > 0:
        score += 5
        tags.append("营收增长")

    if profit_growth is not None and profit_growth > 0:
        score += 5
        tags.append("利润增长")

    if not valuation:
        tags.append("估值未同步")
    if not financial:
        tags.append("财务未同步")

    return {"score": min(score, 100), "tags": tags[:8]}


def decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def fetch_news_source(source: str, count: int) -> list[dict[str, Any]]:
    if source not in NEWS_SOURCES:
        return []
    url = f"https://newsnow.busiyi.world/api/s?id={source}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 QuantitativeTradingResearch/0.1",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []

    items = payload.get("items", []) if isinstance(payload, dict) else []
    normalized = []
    for index, item in enumerate(items[:count], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "source": source,
                "source_name": NEWS_SOURCES[source],
                "title": title,
                "url": item.get("url"),
                "rank": index,
                "heat": (item.get("extra") or {}).get("info") if isinstance(item.get("extra"), dict) else None,
            }
        )
    return normalized


def classify_screen_signal(row: dict, prev_row: dict | None, technical: str) -> dict:
    checks = {
        "ma-bullish": row.get("close", 0) > row.get("ma20", float("inf")) and row.get("ma20", 0) >= row.get("ma60", float("inf")),
        "macd-bullish": row.get("macdDif", 0) >= row.get("macdDea", 1) and row.get("macdHist", 0) > 0,
        "rsi-neutral": 40 <= (row.get("rsiStrategy") or -1) <= 70,
        "boll-lower": bool(row.get("bollLower")) and row["low"] <= row["bollLower"] * 1.02,
        "boll-breakout": bool(row.get("bollUpper")) and row["close"] > row["bollUpper"] and screen_volume_ok(row, 1.05),
        "volume-breakout": screen_volume_ok(row, 1.5),
        "boll-squeeze": bool(row.get("bollBandwidthPct")) and row["bollBandwidthPct"] <= 0.08,
    }
    if prev_row:
        checks["macd-cross"] = prev_row.get("macdDif", 0) <= prev_row.get("macdDea", 0) and row.get("macdDif", 0) > row.get("macdDea", 0)
        checks["ma-cross"] = prev_row.get("trendFastMa", 0) <= prev_row.get("trendSlowMa", 0) and row.get("trendFastMa", 0) > row.get("trendSlowMa", 0)

    label_map = {
        "ma-bullish": "均线多头",
        "macd-bullish": "MACD多头",
        "macd-cross": "MACD金叉",
        "rsi-neutral": "RSI健康",
        "boll-lower": "靠近BOLL下轨",
        "boll-breakout": "BOLL突破",
        "boll-squeeze": "BOLL收口",
        "volume-breakout": "放量",
        "ma-cross": "均线金叉",
    }
    tags = [label_map[key] for key, value in checks.items() if value]
    if technical != "all" and technical in checks and not checks[technical]:
        return {"score": 0, "tags": tags or ["未命中"], "summary": f"未命中 {label_map.get(technical, technical)}"}

    score = 0
    score += 18 if checks.get("ma-bullish") else 0
    score += 18 if checks.get("macd-bullish") else 0
    score += 14 if checks.get("rsi-neutral") else 0
    score += 15 if checks.get("boll-lower") else 0
    score += 18 if checks.get("boll-breakout") else 0
    score += 12 if checks.get("boll-squeeze") else 0
    score += 10 if checks.get("volume-breakout") else 0
    score += 18 if checks.get("macd-cross") or checks.get("ma-cross") else 0
    score = min(100, score)
    return {"score": score, "tags": tags or ["中性"], "summary": " / ".join(tags[:3]) if tags else "暂无明显技术形态"}


def screen_volume_ok(row: dict, multiplier: float) -> bool:
    return bool(row.get("volume")) and bool(row.get("volMa")) and row["volume"] >= row["volMa"] * multiplier


def enriched_bars_to_schema(bars: list[StockDailyBar]) -> list[DailyBarOut]:
    rows = [bar_to_backtest_row(bar) for bar in bars]
    enriched = enrich_rows(rows, DEFAULT_CONFIG)
    return [bar_to_schema(row, bars[index]) for index, row in enumerate(enriched)]


def bar_to_schema(row: dict, bar: StockDailyBar) -> DailyBarOut:
    return DailyBarOut(
        ts_code=row["ts_code"],
        date=row["date"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        amount=float(bar.amount) if bar.amount is not None else None,
        **json_safe(
            {
                "ma5": row.get("ma5"),
                "ma10": row.get("ma10"),
                "ma20": row.get("ma20"),
                "ma30": row.get("ma30"),
                "ma60": row.get("ma60"),
                "trendFastMa": row.get("trendFastMa"),
                "trendSlowMa": row.get("trendSlowMa"),
                "trendLongMa": row.get("trendLongMa"),
                "bollMid": row.get("bollMid"),
                "bollUpper": row.get("bollUpper"),
                "bollLower": row.get("bollLower"),
                "bollBandwidthPct": row.get("bollBandwidthPct"),
                "volMa": row.get("volMa"),
                "macdDif": row.get("macdDif"),
                "macdDea": row.get("macdDea"),
                "macdHist": row.get("macdHist"),
                "rsi6": row.get("rsi6"),
                "rsi12": row.get("rsi12"),
                "rsi24": row.get("rsi24"),
                "rsiStrategy": row.get("rsiStrategy"),
                "kdjK": row.get("kdjK"),
                "kdjD": row.get("kdjD"),
                "kdjJ": row.get("kdjJ"),
                "atr14": row.get("atr14"),
                "atrStrategy": row.get("atrStrategy"),
            }
        ),
    )


def bar_to_backtest_row(bar: StockDailyBar) -> dict:
    return {
        "ts_code": bar.ts_code,
        "date": bar.trade_date.isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.vol) if bar.vol is not None else 0,
    }
