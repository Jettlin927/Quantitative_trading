from datetime import date
from typing import Any

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
    technical_score: int = 0
    technical_tags: list[str] = Field(default_factory=list)
    fundamental_score: int = 0
    fundamental_tags: list[str] = Field(default_factory=list)
    news_state: str = "未刷新"
    signal_summary: str = "暂无本地行情"
    fundamentals: dict[str, Any] = Field(default_factory=dict)


class DailyBarOut(BaseModel):
    ts_code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma30: float | None = None
    ma60: float | None = None
    trendFastMa: float | None = None
    trendSlowMa: float | None = None
    trendLongMa: float | None = None
    bollMid: float | None = None
    bollUpper: float | None = None
    bollLower: float | None = None
    bollBandwidthPct: float | None = None
    volMa: float | None = None
    macdDif: float | None = None
    macdDea: float | None = None
    macdHist: float | None = None
    rsi6: float | None = None
    rsi12: float | None = None
    rsi24: float | None = None
    rsiStrategy: float | None = None
    kdjK: float | None = None
    kdjD: float | None = None
    kdjJ: float | None = None
    atr14: float | None = None
    atrStrategy: float | None = None


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


class SyncStockBasicRequest(BaseModel):
    token: str | None = Field(default=None, repr=False)


class BacktestRequest(BaseModel):
    ts_code: str = Field(..., examples=["600703.SH"])
    start_date: date
    end_date: date
    config: dict


class StockFundamentalsOut(BaseModel):
    ts_code: str
    valuation: dict[str, Any] = Field(default_factory=dict)
    financial: dict[str, Any] = Field(default_factory=dict)
    score: int = 0
    tags: list[str] = Field(default_factory=list)


class NewsItemOut(BaseModel):
    source: str
    source_name: str
    title: str
    url: str | None = None
    rank: int | None = None
    heat: str | int | float | None = None


class NewsTrendOut(BaseModel):
    status: str = "ok"
    items: list[NewsItemOut] = Field(default_factory=list)
    message: str | None = None
