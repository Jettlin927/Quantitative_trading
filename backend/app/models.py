from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint, func
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
