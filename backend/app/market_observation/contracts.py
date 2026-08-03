from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar


T = TypeVar("T")
SourceHealth = Literal["fresh", "stale", "degraded", "unavailable"]
Availability = Literal["available", "not_available", "not_applicable"]
Qualification = Literal["online_observation", "traceable_history", "formal_research"]


AuthorizationPurpose = Literal[
    "display",
    "internal_analysis",
    "ai_context",
    "persist",
    "backfill",
    "redistribute",
    "formal_research",
]
_AUTHORIZATION_PURPOSES = frozenset(AuthorizationPurpose.__args__)


class AuthorizationDenied(PermissionError):
    """当前来源授权快照不允许所请求用途。"""


@dataclass(frozen=True)
class SourceAuthorizationSnapshot:
    snapshot_id: str
    source: str
    dataset: str
    plan: str
    display: bool
    internal_analysis: bool
    ai_context: bool
    persist: bool
    backfill: bool
    redistribute: bool
    formal_research: bool
    terms_url: str
    checked_at: datetime
    retention_policy: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at_requires_timezone")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise ValueError("authorization_evidence_sha256_invalid")

    def allows(self, purpose: AuthorizationPurpose) -> bool:
        if purpose not in _AUTHORIZATION_PURPOSES:
            raise ValueError("authorization_purpose_invalid")
        return bool(getattr(self, purpose))


@dataclass(frozen=True)
class ProvenanceEnvelope:
    source: str
    dataset: str
    provider_record_id: str | None
    source_url: str
    fetched_at: datetime
    content_sha256: str
    authorization_snapshot_id: str
    qualification: Qualification
    source_health: SourceHealth
    ai_context: bool
    formal_research: bool
    adjustment_policy: str | None = None
    fallback_identity: str | None = None
    missing_reason: str | None = None


@dataclass(frozen=True)
class ObservedValue(Generic[T]):
    availability: Availability
    value: T | None
    reason_code: str | None
    source_health: SourceHealth
    as_of: datetime | None
    provenance: ProvenanceEnvelope


@dataclass(frozen=True)
class AssetIdentity:
    provider_asset_id: str
    symbol: str
    name: str
    asset_class: str
    exchange: str
    status: str
    tradable: bool
    fractionable: bool


@dataclass(frozen=True)
class DelayedPrice:
    symbol: str
    price: Decimal
    currency: str
    feed: str
    delay_seconds: int


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class DailyBarsObservation:
    raw: ObservedValue[tuple[DailyBar, ...]]
    provider_adjusted: ObservedValue[tuple[DailyBar, ...]]


@dataclass(frozen=True)
class CorporateAction:
    provider_record_id: str
    action_type: str
    symbol: str
    process_date: date
    effective_date: date
    cash_amount: Decimal | None
    currency: str | None
    ratio_numerator: Decimal | None
    ratio_denominator: Decimal | None


class AppendOnlyAuthorizationRegistry:
    """T0/T1 可替换为 PostgreSQL 的不可覆盖授权快照读写 seam。"""

    def __init__(self) -> None:
        self._snapshots: list[SourceAuthorizationSnapshot] = []
        self._snapshot_ids: set[str] = set()

    @property
    def snapshots(self) -> tuple[SourceAuthorizationSnapshot, ...]:
        return tuple(self._snapshots)

    def append(self, snapshot: SourceAuthorizationSnapshot) -> None:
        if snapshot.snapshot_id in self._snapshot_ids:
            raise ValueError("authorization_snapshot_not_append_only")
        self._snapshots.append(snapshot)
        self._snapshot_ids.add(snapshot.snapshot_id)

    def require(
        self,
        source: str,
        dataset: str,
        plan: str,
        purpose: AuthorizationPurpose,
    ) -> SourceAuthorizationSnapshot:
        matches = [
            snapshot
            for snapshot in self._snapshots
            if snapshot.source == source
            and snapshot.dataset == dataset
            and snapshot.plan == plan
        ]
        if not matches:
            raise AuthorizationDenied("authorization_snapshot_missing")
        latest_checked_at = max(snapshot.checked_at for snapshot in matches)
        latest_matches = [
            snapshot for snapshot in matches if snapshot.checked_at == latest_checked_at
        ]
        if len(latest_matches) != 1:
            raise AuthorizationDenied("authorization_snapshot_ambiguous")
        latest = latest_matches[0]
        if not latest.allows(purpose):
            raise AuthorizationDenied("entitlement_denied")
        return latest
