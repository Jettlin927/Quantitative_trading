from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Availability(str, Enum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    NOT_APPLICABLE = "not_applicable"


class SourceHealth(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class EvidenceQualification(str, Enum):
    ONLINE_OBSERVATION = "online_observation"
    TRACEABLE_HISTORY = "traceable_history"
    FORMAL_RESEARCH = "formal_research"


@dataclass(frozen=True)
class SourceAuthorization:
    snapshot_id: str
    display: bool
    internal_analysis: bool
    ai_context: bool
    persist: bool
    formal_research: bool
    terms_url: str
    checked_at: datetime


@dataclass(frozen=True)
class EvidenceFetchContext:
    fetched_at: datetime
    health: SourceHealth
    qualification: EvidenceQualification
    authorization: SourceAuthorization
    fallback_identity: str | None = None


@dataclass(frozen=True)
class TransportRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    final_url: str
    content_type: str
    body: bytes


@dataclass(frozen=True)
class SecSubmissionsQuery:
    cik: str


@dataclass(frozen=True)
class SecAccessionAvailability:
    accession: str
    available_from: datetime


@dataclass(frozen=True)
class SecCompanyFactsQuery:
    cik: str
    taxonomy: str
    tag: str
    unit: str
    accession_availability: tuple[SecAccessionAvailability, ...]


@dataclass(frozen=True)
class IssuerIrDocumentQuery:
    issuer_id: str
    document_url: str
    document_type: str
    published_at: datetime
    available_from: datetime
    vintage: str
    revision: str
    effective_at: datetime | None = None
    reference_period: str | None = None


@dataclass(frozen=True)
class ReleaseMetadata:
    published_at: datetime
    effective_at: datetime | None
    available_from: datetime
    vintage: str
    revision: str


@dataclass(frozen=True)
class BlsSeriesQuery:
    series_id: str
    start_year: int
    end_year: int
    release: ReleaseMetadata


@dataclass(frozen=True)
class BeaDataQuery:
    dataset_name: str
    table_name: str
    year: str
    release: ReleaseMetadata


@dataclass(frozen=True)
class FederalReserveSeriesQuery:
    series_id: str
    start_date: str
    end_date: str
    release: ReleaseMetadata


@dataclass(frozen=True)
class TreasuryFiscalDataQuery:
    dataset_path: str
    date_field: str
    period: str
    value_field: str
    context_fields: tuple[str, ...]
    release: ReleaseMetadata


@dataclass(frozen=True)
class CensusDataQuery:
    dataset_path: str
    value_field: str
    period_field: str
    period: str
    context_fields: tuple[str, ...]
    release: ReleaseMetadata


@dataclass(frozen=True)
class OfficialDocument:
    identity: str
    source: str
    dataset: str
    document_type: str
    source_record_id: str
    canonical_url: str
    artifact_sha256: str
    published_at: datetime
    effective_at: datetime | None
    reference_period: str | None
    available_from: datetime
    fetched_at: datetime
    vintage: str
    revision: str
    authorization: SourceAuthorization
    qualification: EvidenceQualification
    health: SourceHealth
    fallback_identity: str | None
    instructions_allowed: bool = False


@dataclass(frozen=True)
class OfficialEvent:
    identity: str
    source: str
    event_type: str
    evidence_identity: str
    occurred_at: datetime
    available_from: datetime
    fetched_at: datetime
    authorization: SourceAuthorization
    qualification: EvidenceQualification
    health: SourceHealth

    @property
    def document_identity(self) -> str:
        """兼容公司文档事件调用者；宏观事件返回其 fact identity。"""
        return self.evidence_identity


@dataclass(frozen=True)
class OfficialFact:
    identity: str
    logical_identity: str
    source: str
    dataset: str
    series_id: str
    reference_period: str
    context: str
    value: str
    unit: str | None
    source_record_id: str
    canonical_url: str
    artifact_sha256: str
    published_at: datetime
    effective_at: datetime | None
    available_from: datetime
    fetched_at: datetime
    vintage: str
    revision: str
    authorization: SourceAuthorization
    qualification: EvidenceQualification
    health: SourceHealth
    fallback_identity: str | None


@dataclass(frozen=True)
class OfficialEvidenceBatch:
    availability: Availability
    health: SourceHealth
    documents: tuple[OfficialDocument, ...] = ()
    facts: tuple[OfficialFact, ...] = ()
    events: tuple[OfficialEvent, ...] = ()
    issues: tuple[str, ...] = ()
