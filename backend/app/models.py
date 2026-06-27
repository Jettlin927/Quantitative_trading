from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Stock(Base):
    __tablename__ = "stocks"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    area: Mapped[str | None] = mapped_column(String(50))
    industry: Mapped[str | None] = mapped_column(String(80), index=True)
    market: Mapped[str | None] = mapped_column(String(50))
    list_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockDailyBar(Base):
    __tablename__ = "stock_daily_bars"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_stock_daily_bar_code_date"),
        Index("ix_stock_daily_bars_code_date", "ts_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    high: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    low: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    vol: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockDailyBasic(Base):
    __tablename__ = "stock_daily_basic"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_stock_daily_basic_code_date"),
        Index("ix_stock_daily_basic_code_date", "ts_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate_f: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pe: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    dv_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    dv_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    total_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    float_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    free_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    circ_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockFinancialIndicator(Base):
    __tablename__ = "stock_financial_indicators"
    __table_args__ = (
        UniqueConstraint("ts_code", "end_date", "ann_date", name="uq_stock_financial_indicator_period"),
        Index("ix_stock_financial_indicators_code_period", "ts_code", "end_date", "ann_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    ann_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    dt_eps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    bps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    netprofit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    grossprofit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe_waa: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roa: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    debt_to_assets: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    quick_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    assets_turn: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    basic_eps_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    op_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    netprofit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    tr_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    or_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    q_sales_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    q_profit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockPool(Base):
    __tablename__ = "stock_pools"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockPoolMember(Base):
    __tablename__ = "stock_pool_members"
    __table_args__ = (
        UniqueConstraint("pool_id", "ts_code", name="uq_stock_pool_member_pool_code"),
        Index("ix_stock_pool_members_pool_code", "pool_id", "ts_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("stock_pools.id", ondelete="CASCADE"), index=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("market", "symbol", name="uq_asset_market_symbol"),
        Index("ix_assets_market_symbol", "market", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    instrument_type: Mapped[str | None] = mapped_column(String(40))
    leverage_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    risk_tag: Mapped[str | None] = mapped_column(String(80))
    theme: Mapped[str | None] = mapped_column(String(120))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssetDailyPrice(Base):
    __tablename__ = "asset_daily_prices"
    __table_args__ = (
        UniqueConstraint("asset_natural_key", "trade_date", name="uq_asset_daily_price_key_date"),
        Index("ix_asset_daily_prices_key_date", "asset_natural_key", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    asset_natural_key: Mapped[str] = mapped_column(ForeignKey("assets.natural_key", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma20: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma50: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma200: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    return20d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    return60d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    volatility20d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_name", "asset_natural_key", name="uq_watchlist_item_name_asset"),
        Index("ix_watchlist_items_name_asset", "watchlist_name", "asset_natural_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    watchlist_name: Mapped[str] = mapped_column(String(80), index=True)
    asset_natural_key: Mapped[str] = mapped_column(ForeignKey("assets.natural_key", ondelete="CASCADE"), index=True)
    role: Mapped[str | None] = mapped_column(String(40))
    theme: Mapped[str | None] = mapped_column(String(120))
    subtheme: Mapped[str | None] = mapped_column(String(160))
    risk_tag: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(1000))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    source: Mapped[str | None] = mapped_column(String(120))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    holding_count: Mapped[int] = mapped_column(default=0)
    total_sample_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    holdings: Mapped[list[dict] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataSyncRun(Base):
    __tablename__ = "data_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    target: Mapped[str] = mapped_column(String(80), index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    rows_upserted: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
