from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .contracts import OfficialEvidenceBatch, OfficialFact


class EvidenceIdentityConflict(ValueError):
    """同一不可变证据身份被用于不同内容。"""


def append_fact_versions(
    existing: tuple[OfficialFact, ...], incoming: tuple[OfficialFact, ...]
) -> tuple[OfficialFact, ...]:
    versions = {fact.identity: fact for fact in existing}
    ordered = list(existing)
    for fact in incoming:
        current = versions.get(fact.identity)
        if current is None:
            versions[fact.identity] = fact
            ordered.append(fact)
        elif current != fact:
            raise EvidenceIdentityConflict(f"证据身份冲突: {fact.identity}")
    return tuple(ordered)


def read_fact_version(
    versions: tuple[OfficialFact, ...], identity: str
) -> OfficialFact:
    for fact in versions:
        if fact.identity == identity:
            return fact
    raise KeyError(identity)


@dataclass(frozen=True)
class EvidenceExclusion:
    identity: str
    reason: str


@dataclass(frozen=True)
class AiEvidenceSelection:
    document_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    exclusions: tuple[EvidenceExclusion, ...]


def select_ai_evidence(batch: OfficialEvidenceBatch) -> AiEvidenceSelection:
    document_ids: list[str] = []
    fact_ids: list[str] = []
    exclusions: list[EvidenceExclusion] = []
    for kind, evidence in (
        *(("document", item) for item in batch.documents),
        *(("fact", item) for item in batch.facts),
    ):
        authorization = evidence.authorization
        if not authorization.ai_context or not authorization.internal_analysis:
            exclusions.append(
                EvidenceExclusion(evidence.identity, "ai_context_not_authorized")
            )
        elif kind == "document":
            document_ids.append(evidence.identity)
        else:
            fact_ids.append(evidence.identity)
    return AiEvidenceSelection(
        document_ids=tuple(document_ids),
        fact_ids=tuple(fact_ids),
        exclusions=tuple(exclusions),
    )


class ReleaseState(str, Enum):
    SCHEDULED = "scheduled"
    PENDING = "pending"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


@dataclass(frozen=True)
class ReleasePlan:
    source: str
    release_id: str
    scheduled_at: datetime

    @property
    def planned_attempts(self) -> tuple[datetime, datetime, datetime]:
        return tuple(
            self.scheduled_at + timedelta(minutes=minutes)
            for minutes in (5, 15, 60)
        )


@dataclass(frozen=True)
class ReleaseObservation:
    source: str
    release_id: str
    scheduled_at: datetime
    observed_at: datetime
    state: ReleaseState
    reason: str | None
    evidence_count: int | None


def observe_release(
    plan: ReleasePlan,
    *,
    observed_at: datetime,
    fetch_completed: bool = False,
    evidence_count: int | None = None,
) -> ReleaseObservation:
    if fetch_completed:
        if evidence_count is None or evidence_count < 0:
            raise ValueError("完成抓取必须提供非负 evidence_count")
        state = ReleaseState.AVAILABLE
        reason = None
    else:
        elapsed = observed_at - plan.scheduled_at
        if elapsed.total_seconds() < 0:
            state = ReleaseState.SCHEDULED
            reason = "release_not_due"
        elif elapsed <= timedelta(minutes=15):
            state = ReleaseState.PENDING
            reason = "release_pending"
        elif elapsed < timedelta(minutes=60):
            state = ReleaseState.DEGRADED
            reason = "release_delayed"
        else:
            state = ReleaseState.UNAVAILABLE
            reason = "source_unavailable"
    return ReleaseObservation(
        source=plan.source,
        release_id=plan.release_id,
        scheduled_at=plan.scheduled_at,
        observed_at=observed_at,
        state=state,
        reason=reason,
        evidence_count=evidence_count if fetch_completed else None,
    )
