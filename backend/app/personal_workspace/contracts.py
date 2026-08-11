from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .portfolio import EquitySnapshotView, PortfolioView
    from .rules import AttentionItem


@dataclass(frozen=True)
class PersonalActor:
    actor_id: str


LOCAL_PERSONAL_ACTOR = PersonalActor(actor_id="local-owner")


@dataclass(frozen=True)
class SyntheticHoldingView:
    holding_id: str
    symbol: str
    name: str
    quantity: str
    average_cost: str
    currency: str


@dataclass(frozen=True)
class SyntheticMarketBar:
    date: str
    open: str
    high: str
    low: str
    close: str
    volume: str


@dataclass(frozen=True)
class SyntheticMarketView:
    source_health: str
    as_of: str
    bars: tuple[SyntheticMarketBar, ...]


@dataclass(frozen=True)
class SyntheticRuleEvaluation:
    rule_id: str
    label: str
    result: str
    reason: str


@dataclass(frozen=True)
class ExcludedAnalysisField:
    field: str
    reason_code: str


@dataclass(frozen=True)
class SyntheticAnalysisPreview:
    status: str
    provider: str
    model: str
    included_fields: tuple[str, ...]
    excluded_fields: tuple[ExcludedAnalysisField, ...]
    preview_sha256: str
    retention: str


@dataclass(frozen=True)
class SyntheticAnalysisClaim:
    claim_id: str
    kind: str
    statement: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SyntheticTraceView:
    workspace_id: str
    analysis_id: str
    synthetic: bool
    research_eligible: bool
    holding: SyntheticHoldingView
    market: SyntheticMarketView
    rule_evaluations: tuple[SyntheticRuleEvaluation, ...]
    analysis_preview: SyntheticAnalysisPreview
    analysis_claim: SyntheticAnalysisClaim
    issues: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvidenceView:
    evidence_id: str
    title: str
    summary: str
    source: str
    as_of: datetime
    url: str | None = None


@dataclass(frozen=True)
class InstrumentStateView:
    symbol: str
    is_holding: bool
    is_followed: bool
    follow_source: str
    preset_reasons: tuple[str, ...]
    custom_reason: str | None
    candidate_status: str | None
    relation_evidence_ids: tuple[str, ...]
    fact_evidence_ids: tuple[str, ...]
    relation_evidence: tuple[CandidateEvidenceView, ...]
    fact_evidence: tuple[CandidateEvidenceView, ...]
    candidate_refreshed_at: datetime | None
    candidate_archived_at: datetime | None


@dataclass(frozen=True)
class InstrumentStatesView:
    revision: int
    items: tuple[InstrumentStateView, ...]
    followed_items: tuple[InstrumentStateView, ...]
    watch_observations: tuple[InstrumentStateView, ...]
    active_candidates: tuple[InstrumentStateView, ...]
    archived_candidates: tuple[InstrumentStateView, ...]


@dataclass(frozen=True)
class TodayFactEventView:
    event_id: str
    evidence_id: str
    title: str
    url: str
    published_at: str
    fetched_at: str
    summary: str
    content_sha256: str
    source: str
    source_type: str
    sector: str
    related_symbols: tuple[str, ...]
    confirmation_state: str


@dataclass(frozen=True)
class TodayGapView:
    code: str
    subject: str


@dataclass(frozen=True)
class TodayEvidenceView:
    evidence_id: str
    source: str
    as_of: datetime
    content_sha256: str
    authorized_fields: tuple[str, ...]


@dataclass(frozen=True)
class TodayContextView:
    status: str
    as_of: str | None
    period: str | None
    field_coverage: Decimal | None
    freshness_seconds: int | None
    fact_events: tuple[TodayFactEventView, ...]
    evidence: tuple[TodayEvidenceView, ...]
    gaps: tuple[TodayGapView, ...]
    error_code: str | None


@dataclass(frozen=True)
class TodayPortfolioSummaryView:
    portfolio_revision: int
    total_equity_availability: str
    total_equity_value: str | None
    total_equity_as_of: datetime | None
    active_holding_count: int
    active_holding_symbols: tuple[str, ...]
    priced_holding_count: int
    issues: tuple[str, ...]
    equity_snapshot_status: str
    equity_snapshots: tuple["EquitySnapshotView", ...]


@dataclass(frozen=True)
class TodayReadModel:
    status: str
    as_of: str | None
    period: str | None
    portfolio: TodayPortfolioSummaryView
    attention_items: tuple["AttentionItem", ...]
    fact_events: tuple[TodayFactEventView, ...]
    watch_observations: tuple[InstrumentStateView, ...]
    active_candidates: tuple[InstrumentStateView, ...]
    archived_candidates: tuple[InstrumentStateView, ...]
    gaps: tuple[TodayGapView, ...]
    field_coverage: Decimal | None
    freshness_seconds: int | None


@dataclass(frozen=True)
class TodayWorkspace:
    trace: SyntheticTraceView | None
    portfolio: PortfolioView | None = None
    attention_items: tuple[AttentionItem, ...] = ()
    read_model: TodayReadModel | None = None

    def __getattr__(self, name: str):
        if self.trace is None:
            raise AttributeError(name)
        return getattr(self.trace, name)


class SyntheticTraceCommand(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class PortfolioCommand(BaseModel):
    expected_portfolio_revision: int = Field(ge=0)


class AddHoldingCommand(PortfolioCommand):
    type: Literal["add_holding"]
    symbol: str = Field(min_length=1, max_length=15)
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    average_cost: Decimal = Field(gt=0, max_digits=24, decimal_places=8)


class EditHoldingCommand(PortfolioCommand):
    type: Literal["edit_holding"]
    holding_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    average_cost: Decimal = Field(gt=0, max_digits=24, decimal_places=8)


class RemoveHoldingCommand(PortfolioCommand):
    type: Literal["remove_holding"]
    holding_id: str = Field(min_length=1, max_length=64)


class RestoreHoldingCommand(PortfolioCommand):
    type: Literal["restore_holding"]
    holding_id: str = Field(min_length=1, max_length=64)


class BuyHoldingCommand(PortfolioCommand):
    type: Literal["buy_holding"]
    holding_id: str = Field(min_length=1, max_length=64)
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    price: Decimal = Field(gt=0, max_digits=24, decimal_places=8)


class SellHoldingCommand(PortfolioCommand):
    type: Literal["sell_holding"]
    holding_id: str = Field(min_length=1, max_length=64)
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    price: Decimal = Field(gt=0, max_digits=24, decimal_places=8)


class SetUsdCashCommand(PortfolioCommand):
    type: Literal["set_usd_cash"]
    usd_cash: Decimal = Field(ge=0, max_digits=24, decimal_places=8)


class RequestPurgeHoldingCommand(PortfolioCommand):
    type: Literal["request_purge"]
    holding_id: str = Field(min_length=1, max_length=64)


class PurgeHoldingCommand(PortfolioCommand):
    type: Literal["confirm_purge"] = "confirm_purge"
    holding_id: str = Field(min_length=1, max_length=64)
    challenge: str = Field(min_length=1, max_length=2000)


class InstrumentStateCommand(BaseModel):
    expected_revision: int = Field(ge=0)


class FollowInstrumentCommand(InstrumentStateCommand):
    type: Literal["follow_symbol"]
    symbol: str = Field(min_length=1, max_length=15)
    preset_reasons: tuple[str, ...] = Field(default=(), max_length=20)
    custom_reason: str | None = Field(default=None, max_length=500)


class UnfollowInstrumentCommand(InstrumentStateCommand):
    type: Literal["unfollow_symbol"]
    symbol: str = Field(min_length=1, max_length=15)


class CreateObservationRuleCommand(BaseModel):
    type: Literal["create_rule"]
    template_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=15)
    parameters: dict[str, Any]


class SetObservationRuleStateCommand(BaseModel):
    type: Literal["set_rule_state"]
    rule_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    state: Literal["enabled", "paused", "archived"]


class EvaluateObservationRulesCommand(BaseModel):
    type: Literal["evaluate_rules"]
    symbol: str = Field(min_length=1, max_length=15)
    as_of: datetime


class PrepareAnalysisCommand(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    subject_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    selected_private_fields: tuple[str, ...] = Field(default=(), max_length=20)


class StartAnalysisCommand(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
