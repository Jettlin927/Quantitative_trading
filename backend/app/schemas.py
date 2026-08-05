from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

class DataQualityRunRequest(BaseModel):
    scope: Literal["etf_time_series"]
    start_date: date
    end_date: date
    universe: list[str] = Field(default_factory=list, max_length=5000)
    universe_type: Literal["explicit_snapshot", "static_current"] = "explicit_snapshot"
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
        if not self.universe:
            raise ValueError("universe 必须包含至少一个有效代码")
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
