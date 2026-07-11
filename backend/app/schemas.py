from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class StockOut(BaseModel):
    ts_code: str
    symbol: str | None = None
    name: str
    area: str | None = None
    industry: str | None = None
    market: str | None = None
    list_date: date | None = None


class StockScreenOut(StockOut):
    latest_date: date | None = None
    close: float | None = None
    pct_chg: float | None = None
    data_bars: int = 0
    valuation: dict[str, Any] = Field(default_factory=dict)


class StockPoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)


class StockPoolMembersRequest(BaseModel):
    ts_codes: list[str] = Field(default_factory=list)


class StockPoolOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    member_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StockPoolMemberOut(StockOut):
    added_at: datetime | None = None


class StockPoolDetailOut(StockPoolOut):
    members: list[StockPoolMemberOut] = Field(default_factory=list)


class DailyBarOut(BaseModel):
    ts_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None = None
    change_amount: float | None = None
    pct_chg: float | None = None
    vol: float | None = None
    amount: float | None = None


class SyncDailyRequest(BaseModel):
    ts_code: str = Field(..., examples=["600703.SH"])
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)


class SyncFundamentalsRequest(BaseModel):
    ts_code: str = Field(..., examples=["600703.SH"])
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)


class SyncTradeCalendarRequest(BaseModel):
    start_date: date
    end_date: date
    exchange: str = ""
    token: str | None = Field(default=None, repr=False)


class SyncAdjustFactorsRequest(BaseModel):
    ts_code: str = Field(..., examples=["600703.SH"])
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)


class SyncIndexBasicRequest(BaseModel):
    markets: list[str] = Field(default_factory=lambda: ["CSI", "SSE", "SZSE", "SW"])
    token: str | None = Field(default=None, repr=False)


class SyncIndexDailyRequest(BaseModel):
    ts_codes: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)


class SyncFundBasicRequest(BaseModel):
    market: str = "E"
    token: str | None = Field(default=None, repr=False)


class SyncFundDailyRequest(BaseModel):
    ts_codes: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)


class SyncIndustryClassificationsRequest(BaseModel):
    src: str = "SW2021"
    index_codes: list[str] = Field(default_factory=list)
    token: str | None = Field(default=None, repr=False)


class SyncMarketDataRequest(BaseModel):
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)
    max_trade_dates: int = Field(default=0, ge=0)
    skip_existing: bool = True
    min_existing_rows: int = Field(default=5000, ge=1)


class SyncMarketFundamentalsRequest(BaseModel):
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)
    max_stocks: int = Field(default=0, ge=0)
    skip_existing: bool = True


class SyncStockBasicRequest(BaseModel):
    token: str | None = Field(default=None, repr=False)


class SyncStockListingsRequest(BaseModel):
    statuses: list[Literal["L", "D", "P", "G"]] = Field(default_factory=lambda: ["L", "D", "P", "G"], min_length=1)
    token: str | None = Field(default=None, repr=False)


class SyncSuspendEventsRequest(BaseModel):
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)
    max_trade_dates: int = Field(default=0, ge=0)


class SyncJobCreate(BaseModel):
    action: Literal["stock_listings", "trade_calendar", "market_bundle", "daily_market", "us_sample"]
    payload: dict[str, Any] = Field(default_factory=dict)


class StockFundamentalsOut(BaseModel):
    ts_code: str
    valuation: dict[str, Any] = Field(default_factory=dict)
    financial: dict[str, Any] = Field(default_factory=dict)
