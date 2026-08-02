from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class PersonalActor:
    actor_id: str


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
class SyntheticRecordView:
    record_id: str
    analysis_id: str
    version: int
    status: str
    synthetic: bool
    research_eligible: bool


@dataclass(frozen=True)
class TodayWorkspace:
    trace: SyntheticTraceView
    record: SyntheticRecordView | None

    def __getattr__(self, name: str):
        return getattr(self.trace, name)


class SyntheticTraceCommand(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class SaveSyntheticRecordCommand(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=64)
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
