from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .portfolio import PortfolioView
    from .rules import AttentionItem


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
    trace: SyntheticTraceView | None
    record: SyntheticRecordView | None
    portfolio: PortfolioView | None = None
    attention_items: tuple[AttentionItem, ...] = ()

    def __getattr__(self, name: str):
        if self.trace is None:
            raise AttributeError(name)
        return getattr(self.trace, name)


class SyntheticTraceCommand(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class SaveSyntheticRecordCommand(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=64)
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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


class PrivateFragmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holding_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


class VerificationDraftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str | None = Field(default=None, max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    target: str = Field(min_length=1, max_length=300)
    expected_at: datetime | None = None
    source: str = Field(min_length=1, max_length=200)
    criterion: str = Field(min_length=1, max_length=1000)


class SaveAnalysisRecordCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["save_analysis"]
    analysis_id: str = Field(min_length=1, max_length=64)
    accepted_claim_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    user_supplement: str = Field(default="", max_length=4000)
    private_fragments: tuple[PrivateFragmentCommand, ...] = Field(default=(), max_length=20)
    verification_drafts: tuple[VerificationDraftCommand, ...] = Field(default=(), max_length=20)


class AppendRecordSupplementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["append_supplement"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    supplement: str = Field(min_length=1, max_length=4000)
    private_fragments: tuple[PrivateFragmentCommand, ...] = Field(default=(), max_length=20)


class StartReasoningAuditCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["start_reasoning_audit"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class CreateVerificationItemCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["create_verification_item"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    item: VerificationDraftCommand


class AppendVerificationObservationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["append_verification_observation"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    item_id: str = Field(min_length=1, max_length=64)
    result: Literal["supports", "contradicts", "inconclusive", "data_unavailable"]
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=50)
    note: str = Field(min_length=1, max_length=4000)


class ChangeRecordStateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["archive", "trash", "restore"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class RequestRecordPurgeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["request_purge"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class ConfirmRecordPurgeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["confirm_purge"]
    record_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    challenge: str = Field(min_length=1, max_length=2000)
