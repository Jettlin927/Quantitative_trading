from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


class DataQualityRunRequest(BaseModel):
    scope: Literal["a_share_cross_section", "etf_time_series"]
    start_date: date
    end_date: date
    universe: list[str] = Field(min_length=1, max_length=5000)
    universe_type: Literal["explicit_snapshot", "static_current", "industry_membership"] = "explicit_snapshot"
    universe_source: str | None = Field(default=None, max_length=200)
    universe_as_of_date: date | None = None
    required_datasets: list[str] = Field(default_factory=list, max_length=20)
    benchmark: str | None = None
    statement_timeout_ms: int = Field(default=30_000, ge=500, le=60_000)
    code_commit: str | None = Field(default=None, max_length=64)

    @field_validator("universe")
    @classmethod
    def normalize_universe(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().upper() for item in value if item.strip()})
        if not normalized:
            raise ValueError("universe 必须包含至少一个有效代码")
        return normalized

    @field_validator("required_datasets")
    @classmethod
    def normalize_required_datasets(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    @field_validator("benchmark")
    @classmethod
    def normalize_benchmark(cls, value: str | None) -> str | None:
        return value.strip().upper() if value and value.strip() else None

    @model_validator(mode="after")
    def validate_date_range(self) -> "DataQualityRunRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self
