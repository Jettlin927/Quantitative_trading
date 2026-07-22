from datetime import date, datetime
import re
from typing import Any, Literal
from uuid import UUID

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


class StockScreenPageOut(BaseModel):
    items: list[StockScreenOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


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
    benchmark: str = Field(default="000300.SH", min_length=1, max_length=16)

    @field_validator("benchmark")
    @classmethod
    def normalize_market_benchmark(cls, value: str) -> str:
        normalized = value.strip().upper()
        if "." not in normalized:
            raise ValueError("benchmark 必须是 Tushare 指数代码")
        return normalized


class SyncMarketFundamentalsRequest(BaseModel):
    start_date: date
    end_date: date
    token: str | None = Field(default=None, repr=False)
    max_stocks: int = Field(default=0, ge=0)
    rate_per_minute: int = Field(default=150, ge=1, le=150)
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


class SyncUsExperimentPricesRequest(BaseModel):
    start_date: date
    end_date: date
    source_codes: list[str] = Field(min_length=1, max_length=100)
    validation_source_codes: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("source_codes")
    @classmethod
    def normalize_us_source_codes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            code = value.strip().upper()
            market, separator, symbol = code.partition(".")
            if separator != "." or market not in {"105", "106", "107", "TGT"} or not symbol:
                raise ValueError("美股实验代码必须使用 105/106/107/TGT 前缀，例如 105.AAPL")
            if code not in normalized:
                normalized.append(code)
        return normalized

    @field_validator("validation_source_codes")
    @classmethod
    def normalize_validation_source_codes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            code = value.strip().upper()
            market, separator, symbol = code.partition(".")
            if separator != "." or market not in {"105", "106", "107"} or not symbol:
                raise ValueError("AKShare 校验只接受 105/106/107 目录代码")
            if code not in normalized:
                normalized.append(code)
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> "SyncUsExperimentPricesRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        unknown = set(self.validation_source_codes) - set(self.source_codes)
        if unknown:
            raise ValueError("validation_source_codes 必须包含在 source_codes 中")
        return self


class SyncUsExperimentTargetedUniverseRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            symbol = value.strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}", symbol):
                raise ValueError("显式美股 ticker 必须以字母开头，且只能包含字母、数字、点或连字号")
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized


class SyncJobCreate(BaseModel):
    action: Literal[
        "stock_listings",
        "trade_calendar",
        "market_bundle",
        "daily_market",
        "market_fundamentals",
        "us_sample",
        "us_experiment_universe",
        "us_experiment_targeted_universe",
        "us_experiment_prices",
        "us_experiment_overview_refresh",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class StockFundamentalsOut(BaseModel):
    ts_code: str
    valuation: dict[str, Any] = Field(default_factory=dict)
    financial: dict[str, Any] = Field(default_factory=dict)


class StockDetailOut(BaseModel):
    stock: StockOut
    latest_bar: DailyBarOut | None = None
    valuation: dict[str, Any] = Field(default_factory=dict)
    financial: dict[str, Any] = Field(default_factory=dict)
    valuation_history: list[dict[str, Any]] = Field(default_factory=list)
    financial_history: list[dict[str, Any]] = Field(default_factory=list)
    listing: dict[str, Any] = Field(default_factory=dict)
    latest_limit_price: dict[str, Any] = Field(default_factory=dict)
    latest_suspend_event: dict[str, Any] = Field(default_factory=dict)
    latest_adjust_factor: dict[str, Any] = Field(default_factory=dict)


class DataQualityRunRequest(BaseModel):
    scope: Literal["a_share_cross_section", "etf_time_series"]
    start_date: date
    end_date: date
    universe: list[str] = Field(default_factory=list, max_length=5000)
    universe_type: Literal[
        "explicit_snapshot",
        "static_current",
        "industry_membership",
        "industry_level_membership",
    ] = "explicit_snapshot"
    universe_source: str | None = Field(default=None, max_length=200)
    universe_source_key: str | None = Field(default=None, max_length=32)
    universe_classification_src: str | None = Field(default=None, max_length=32)
    universe_classification_level: str | None = Field(default=None, max_length=2)
    universe_as_of_date: date | None = None
    required_datasets: list[str] = Field(default_factory=list, max_length=20)
    benchmark: str | None = None
    statement_timeout_ms: int = Field(default=30_000, ge=500, le=60_000)
    code_commit: str | None = Field(default=None, max_length=64)

    @field_validator("universe")
    @classmethod
    def normalize_universe(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().upper() for item in value if item.strip()})
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
        if self.universe_type == "industry_membership":
            if self.scope != "a_share_cross_section":
                raise ValueError("industry_membership 只允许 A 股横截面")
            if self.universe:
                raise ValueError("industry_membership 禁止 inline 当前成员列表")
            if self.universe_source != "industry_members":
                raise ValueError("industry_membership universe_source 必须为 industry_members")
            if not self.universe_source_key or not self.universe_source_key.strip():
                raise ValueError("industry_membership 必须提供 universe_source_key")
            if self.universe_as_of_date is not None:
                raise ValueError("industry_membership 禁止 universe_as_of_date")
            if self.universe_classification_src is not None or self.universe_classification_level is not None:
                raise ValueError("industry_membership 不接受行业分类范围")
            self.universe_source_key = self.universe_source_key.strip().upper()
        elif self.universe_type == "industry_level_membership":
            if self.scope != "a_share_cross_section":
                raise ValueError("industry_level_membership 只允许 A 股横截面")
            if self.universe:
                raise ValueError("industry_level_membership 禁止 inline 当前成员列表")
            if self.universe_source != "industry_classifications+industry_members":
                raise ValueError(
                    "industry_level_membership universe_source 必须为 "
                    "industry_classifications+industry_members"
                )
            if self.universe_source_key is not None:
                raise ValueError("industry_level_membership 不接受 universe_source_key")
            if self.universe_as_of_date is not None:
                raise ValueError("industry_level_membership 禁止 universe_as_of_date")
            source = str(self.universe_classification_src or "").strip().upper()
            level = str(self.universe_classification_level or "").strip().upper()
            if not source:
                raise ValueError("industry_level_membership 必须提供 universe_classification_src")
            if level not in {"L1", "L2", "L3"}:
                raise ValueError("universe_classification_level 只允许 L1、L2 或 L3")
            self.universe_classification_src = source
            self.universe_classification_level = level
        else:
            if not self.universe:
                raise ValueError("universe 必须包含至少一个有效代码")
            if self.universe_source_key is not None:
                raise ValueError("非 industry_membership 不接受 universe_source_key")
            if self.universe_classification_src is not None or self.universe_classification_level is not None:
                raise ValueError("非 industry_level_membership 不接受行业分类范围")
        return self


class ResearchRunOut(BaseModel):
    run_id: str
    reproducibility_key: str | None = None
    strategy_id: str
    status: Literal["queued", "running", "retrying", "succeeded", "failed", "interrupted"]
    stage: str
    config_sha256: str
    data_snapshot_id: str | None = None
    code_commit: str
    environment_sha256: str
    random_seed: int
    result_fingerprint: str | None = None
    artifact_root: str
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class StrategyProfileSummaryOut(BaseModel):
    strategy_id: str
    display_name: str
    lifecycle_status: Literal["活跃", "暂停", "停止研究", "已归档"]
    registry_version: str
    code_commit: str
    formal_research_count: int = 0
    latest_publication_id: UUID | None = None
    latest_publication_evaluation_id: UUID | None = None
    latest_publication_conclusion: Literal[
        "研究通过", "有条件候选", "证据不足", "受阻", "不通过"
    ] | None = None
    latest_publication_status: Literal["pending", "published", "failed"] | None = None


class FrozenResearchPlanOut(BaseModel):
    id: UUID
    strategy_id: str
    issue_number: int
    version: int
    schema_version: str
    plan_sha256: str
    code_commit: str
    plan_json: dict[str, Any]
    created_at: datetime | None = None


class ResearchPlanApprovalOut(BaseModel):
    id: UUID
    plan_id: UUID
    action: Literal["approved", "invalidated", "stopped", "historical_import"]
    actor_login: str
    comment_id: int | None = None
    source_uri: str | None = None
    comment_body: str
    plan_sha256: str
    created_at: datetime | None = None


class ResearchRunSummaryOut(BaseModel):
    run_id: str
    formal_research_id: UUID | None = None
    reproducibility_key: str | None = None
    strategy_id: str
    status: Literal["queued", "running", "retrying", "succeeded", "failed", "interrupted"]
    stage: str
    config_sha256: str
    data_snapshot_id: str | None = None
    code_commit: str
    environment_sha256: str
    random_seed: int
    result_fingerprint: str | None = None
    artifact_root: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class ResearchEventOut(BaseModel):
    id: UUID
    formal_research_id: UUID
    run_id: str | None = None
    sequence_no: int
    event_type: str
    payload_json: dict[str, Any]
    occurred_at: datetime | None = None


class ResearchEvidenceRefOut(BaseModel):
    id: UUID
    evaluation_id: UUID
    run_id: str | None = None
    kind: Literal[
        "input_snapshot",
        "code",
        "environment",
        "parameters",
        "ledger",
        "statistics",
        "report",
        "limitation",
    ]
    uri: str
    sha256: str | None = None
    metadata_json: dict[str, Any]
    created_at: datetime | None = None


class ResearchEvaluationOut(BaseModel):
    id: UUID
    formal_research_id: UUID
    version: int
    conclusion: Literal["研究通过", "有条件候选", "证据不足", "受阻", "不通过"]
    evaluation_sha256: str
    supersedes_evaluation_id: UUID | None = None
    supporting_evidence: list[dict[str, Any]] = Field(default_factory=list)
    opposing_evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_evidence: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[ResearchEvidenceRefOut] = Field(default_factory=list)
    created_at: datetime | None = None


class ResearchPublicationOut(BaseModel):
    id: UUID
    formal_research_id: UUID
    evaluation_id: UUID
    version: int
    status: Literal["pending", "published", "failed"]
    publication_sha256: str
    supersedes_publication_id: UUID | None = None
    artifact_manifest_uri: str
    issue_number: int
    issue_comment_id: int | None = None
    created_at: datetime | None = None
    published_at: datetime | None = None


class ResearchPublicationRunOut(BaseModel):
    run_id: str
    status: Literal["succeeded", "failed", "interrupted"]
    result_fingerprint: str | None = None
    artifact_root: str


class ResearchPublicationProjectionOut(BaseModel):
    publication_id: UUID
    formal_research_id: UUID
    publication_version: int
    status: Literal["pending", "published", "failed"]
    publication_sha256: str
    supersedes_publication_id: UUID | None = None
    evaluation_id: UUID
    evaluation_version: int
    conclusion: Literal["研究通过", "有条件候选", "证据不足", "受阻", "不通过"]
    evaluation_sha256: str
    supersedes_evaluation_id: UUID | None = None
    superseded_by_evaluation_id: UUID | None = None
    runs: list[ResearchPublicationRunOut] = Field(default_factory=list)
    manifest_url: str
    summary_url: str
    analytics_url: str
    report_url: str
    issue_number: int
    issue_comment_id: int | None = None
    published_at: datetime | None = None


class ResearchPublicationAnalyticsOut(BaseModel):
    publication_id: UUID
    evaluation_id: UUID
    evaluation_version: int
    data_status: Literal["complete", "not_available", "not_applicable", "legacy_provenance_only"]
    primary_run_id: str | None = None
    primary_label: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    chart_series: dict[str, Any] = Field(default_factory=dict)
    yearly: list[dict[str, Any]] = Field(default_factory=list)
    regimes: list[dict[str, Any]] = Field(default_factory=list)
    robustness: dict[str, Any] = Field(default_factory=dict)
    capacity: dict[str, Any] = Field(default_factory=dict)
    availability: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class FollowUpResearchProposalOut(BaseModel):
    id: UUID
    strategy_id: str
    source_evaluation_id: UUID
    source_evidence_ref_id: UUID | None = None
    title: str
    rationale: str
    status: Literal["proposed", "accepted", "rejected", "converted"]
    proposal_json: dict[str, Any]
    converted_plan_id: UUID | None = None
    created_at: datetime | None = None


class FormalResearchSummaryOut(BaseModel):
    id: UUID
    plan_id: UUID
    origin: Literal["native", "historical_import"]
    phase: Literal["approved", "active", "evaluating", "published", "stopped"]
    run_count: int = 0
    latest_publication_id: UUID | None = None
    latest_publication_evaluation_id: UUID | None = None
    latest_publication_conclusion: Literal[
        "研究通过", "有条件候选", "证据不足", "受阻", "不通过"
    ] | None = None
    latest_publication_status: Literal["pending", "published", "failed"] | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class StrategyProfileOut(BaseModel):
    strategy_id: str
    display_name: str
    lifecycle_status: Literal["活跃", "暂停", "停止研究", "已归档"]
    economic_thesis: str
    registry_version: str
    code_commit: str
    metadata_json: dict[str, Any]
    formal_researches: list[FormalResearchSummaryOut] = Field(default_factory=list)
    follow_up_proposals: list[FollowUpResearchProposalOut] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FormalResearchDetailOut(BaseModel):
    id: UUID
    origin: Literal["native", "historical_import"]
    phase: Literal["approved", "active", "evaluating", "published", "stopped"]
    plan: FrozenResearchPlanOut
    approval: ResearchPlanApprovalOut
    runs: list[ResearchRunSummaryOut] = Field(default_factory=list)
    events: list[ResearchEventOut] = Field(default_factory=list)
    evaluations: list[ResearchEvaluationOut] = Field(default_factory=list)
    publications: list[ResearchPublicationOut] = Field(default_factory=list)
    follow_up_proposals: list[FollowUpResearchProposalOut] = Field(default_factory=list)
    created_at: datetime | None = None
    completed_at: datetime | None = None
