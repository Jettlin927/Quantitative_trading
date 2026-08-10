"""Worker 驱动的候选发现与过期归档；只更新候选态，绝不自动关注。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Mapping
from zoneinfo import ZoneInfo

from .agent.domain_tools import DomainToolContext, DomainToolRegistry
from .contracts import PersonalActor
from .watchlist import CandidateEvidence, InstrumentStateBook, xnys_trading_days_elapsed


@dataclass(frozen=True)
class CandidateAutomationResult:
    considered_count: int
    archived_count: int
    failed_count: int


class CandidateLifecycleAutomation:
    def __init__(self, *, watchlist: InstrumentStateBook, tools: DomainToolRegistry) -> None:
        self._watchlist = watchlist
        self._tools = tools

    def run_once(
        self, actor: PersonalActor, *, as_of: datetime
    ) -> CandidateAutomationResult:
        if as_of.tzinfo is None:
            raise ValueError("as_of_requires_timezone")
        view = self._watchlist.open(actor)
        stale = tuple(
            item
            for item in view.active_candidates
            if item.candidate_refreshed_at is not None
            and xnys_trading_days_elapsed(item.candidate_refreshed_at, as_of) >= 14
        )
        archived_count = 0
        if stale:
            view = self._watchlist.archive_stale_candidates(
                actor,
                as_of=as_of,
                expected_revision=view.revision,
                idempotency_key=(
                    "candidate-archive:"
                    + as_of.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                ),
            )
            archived_count = len(stale)

        subjects = tuple(
            sorted(
                {
                    item.symbol
                    for item in view.items
                    if item.is_holding or item.is_followed
                }
            )
        )
        if not subjects:
            return CandidateAutomationResult(0, archived_count, 0)
        result = self._tools.invoke(
            "discover_related_candidates",
            context=DomainToolContext(
                actor_id=actor.actor_id,
                granted_permissions=frozenset({"market:read", "news:read"}),
                clock=lambda: as_of,
            ),
            arguments={"subject_ids": list(subjects)},
        )
        if result.status == "unavailable":
            return CandidateAutomationResult(0, archived_count, 1)

        considered = 0
        failed = 0
        for raw in result.data.get("candidates", ()):
            if not isinstance(raw, Mapping):
                failed += 1
                continue
            try:
                observed_at = datetime.fromisoformat(
                    str(raw["latest_fact_at"]).replace("Z", "+00:00")
                )
                identity = json.dumps(
                    {
                        "symbol": raw["symbol"],
                        "relation": raw["relation_evidence_ids"],
                        "fact": raw["fact_evidence_ids"],
                        "observed_at": observed_at.isoformat(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                view = self._watchlist.consider_candidate(
                    actor,
                    CandidateEvidence(
                        symbol=str(raw["symbol"]),
                        relation_evidence_ids=tuple(raw["relation_evidence_ids"]),
                        fact_evidence_ids=tuple(raw["fact_evidence_ids"]),
                        observed_at=observed_at,
                        expected_revision=view.revision,
                    ),
                    idempotency_key=(
                        "candidate-discovery:"
                        + sha256(identity.encode("utf-8")).hexdigest()
                    ),
                )
                considered += 1
            except (KeyError, TypeError, ValueError):
                failed += 1
        return CandidateAutomationResult(considered, archived_count, failed)
