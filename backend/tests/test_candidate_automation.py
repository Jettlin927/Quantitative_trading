from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
)
from backend.app.personal_workspace.candidate_automation import CandidateLifecycleAutomation
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.watchlist import (
    CandidateEvidence,
    HoldingWatchState,
    InMemoryInstrumentStateStore,
    InstrumentStateBook,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


class CandidateLifecycleAutomationTest(unittest.TestCase):
    def test_worker_discovers_without_auto_following_and_archives_stale(self) -> None:
        holdings = {"NVDA": HoldingWatchState("active", 1, "holding-nvda")}
        current = [NOW - timedelta(days=30)]
        book = InstrumentStateBook(
            store=InMemoryInstrumentStateStore(),
            holding_states_reader=lambda _actor_id: holdings,
            clock=lambda: current[0],
        )
        actor = PersonalActor("actor-1")
        book.consider_candidate(
            actor,
            CandidateEvidence(
                symbol="TSLA",
                relation_evidence_ids=("relation:old",),
                fact_evidence_ids=("fact:old",),
                observed_at=current[0],
                expected_revision=0,
            ),
            idempotency_key="old-candidate",
        )
        current[0] = NOW
        envelope = EvidenceEnvelope(
            evidence_id="candidate-source",
            source="synthetic",
            as_of=NOW,
            content_sha256="a" * 64,
            authorized_fields=("candidates",),
        )
        tools = DomainToolRegistry(
            handlers={
                "discover_related_candidates": lambda _context, _arguments: DomainToolResult.success(
                    data={
                        "candidates": [
                            {
                                "symbol": "AMD",
                                "relation_evidence_ids": ["relation:semis"],
                                "fact_evidence_ids": ["fact:earnings"],
                                "relation_evidence": [{
                                    "evidence_id": "relation:semis", "title": "NVDA → AMD", "summary": "配置的市场关联",
                                    "source": "instrument_relation_map", "as_of": NOW.isoformat(), "url": None,
                                }],
                                "fact_evidence": [{
                                    "evidence_id": "fact:earnings", "title": "AMD 发布季度更新", "summary": "收入指引已更新",
                                    "source": "Synthetic Wire", "as_of": NOW.isoformat(), "url": "https://example.com/amd",
                                }],
                                "latest_fact_at": NOW.isoformat(),
                            }
                        ]
                    },
                    evidence=(envelope,),
                )
            }
        )

        result = CandidateLifecycleAutomation(watchlist=book, tools=tools).run_once(
            actor, as_of=NOW
        )
        view = book.open(actor)

        self.assertEqual((result.considered_count, result.archived_count), (1, 1))
        amd = next(item for item in view.items if item.symbol == "AMD")
        tsla = next(item for item in view.items if item.symbol == "TSLA")
        self.assertEqual(amd.candidate_status, "active")
        self.assertFalse(amd.is_followed)
        self.assertEqual(amd.relation_evidence[0].title, "NVDA → AMD")
        self.assertEqual(amd.fact_evidence[0].summary, "收入指引已更新")
        self.assertEqual(tsla.candidate_status, "archived")


if __name__ == "__main__":
    unittest.main()
