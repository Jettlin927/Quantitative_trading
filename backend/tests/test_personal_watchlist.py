from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.watchlist import (
    CandidateEvidence,
    FollowSymbol,
    HoldingWatchState,
    InMemoryInstrumentStateStore,
    InstrumentStateBook,
    UnfollowSymbol,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


class MutableHoldings:
    def __init__(self) -> None:
        self.states: dict[str, HoldingWatchState] = {}

    def __call__(self, _actor_id: str) -> dict[str, HoldingWatchState]:
        return dict(self.states)


class InstrumentStateBookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = PersonalActor("actor-1")
        self.holdings = MutableHoldings()
        self.store = InMemoryInstrumentStateStore()
        self.now = NOW
        self.book = InstrumentStateBook(
            store=self.store,
            holding_states_reader=self.holdings,
            clock=lambda: self.now,
        )

    def test_holding_is_followed_and_exit_defaults_to_followed_until_user_unfollows(self) -> None:
        self.holdings.states["NVDA"] = HoldingWatchState("active", 1)
        active = self.book.open(self.actor)
        self.assertEqual(active.revision, 0)
        self.assertTrue(active.items[0].is_holding)
        self.assertTrue(active.items[0].is_followed)
        self.assertEqual(active.items[0].follow_source, "holding")

        self.holdings.states["NVDA"] = HoldingWatchState("removed", 2)
        exited = self.book.open(self.actor)
        self.assertFalse(exited.items[0].is_holding)
        self.assertTrue(exited.items[0].is_followed)
        self.assertEqual(exited.items[0].follow_source, "former_holding")

        unfollowed = self.book.revise(
            self.actor,
            UnfollowSymbol(symbol="nvda", expected_revision=0),
            idempotency_key="unfollow-nvda",
        )
        self.assertEqual(unfollowed.revision, 1)
        self.assertFalse(unfollowed.items[0].is_followed)

        self.holdings.states["NVDA"] = HoldingWatchState("active", 3)
        restored = self.book.open(self.actor)
        self.assertTrue(restored.items[0].is_followed)
        self.assertEqual(restored.items[0].follow_source, "holding")
        with self.assertRaisesRegex(ValueError, "holding_watch_required"):
            self.book.revise(
                self.actor,
                UnfollowSymbol(symbol="NVDA", expected_revision=1),
                idempotency_key="unfollow-active-nvda",
            )
        self.holdings.states["NVDA"] = HoldingWatchState("removed", 4)
        exited_again = self.book.open(self.actor)
        self.assertTrue(exited_again.items[0].is_followed)
        self.assertEqual(exited_again.items[0].follow_source, "former_holding")

    def test_manual_follow_reasons_are_idempotent_and_do_not_change_holdings(self) -> None:
        before = self.holdings.states.copy()
        command = FollowSymbol(
            symbol="msft",
            preset_reasons=("财报观察", "行业映射"),
            custom_reason="等待产品催化",
            expected_revision=0,
        )
        first = self.book.revise(
            self.actor, command, idempotency_key="follow-msft"
        )
        repeated = self.book.revise(
            self.actor, command, idempotency_key="follow-msft"
        )
        self.assertEqual(first, repeated)
        self.assertEqual(first.revision, 1)
        self.assertEqual(first.items[0].symbol, "MSFT")
        self.assertTrue(first.items[0].is_followed)
        self.assertEqual(first.items[0].preset_reasons, ("财报观察", "行业映射"))
        self.assertEqual(first.items[0].custom_reason, "等待产品催化")
        self.assertEqual(self.holdings.states, before)
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            self.book.revise(
                self.actor,
                FollowSymbol(symbol="AMD", expected_revision=0),
                idempotency_key="stale-follow-amd",
            )

    def test_candidate_requires_two_evidence_classes_merges_and_never_auto_follows(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate_evidence_insufficient"):
            self.book.consider_candidate(
                self.actor,
                CandidateEvidence(
                    symbol="AMD",
                    relation_evidence_ids=("relation:1",),
                    fact_evidence_ids=(),
                    observed_at=NOW,
                    expected_revision=0,
                ),
                idempotency_key="candidate-invalid",
            )
        with self.assertRaisesRegex(ValueError, "candidate_evidence_insufficient"):
            self.book.consider_candidate(
                self.actor,
                CandidateEvidence(
                    symbol="AMD",
                    relation_evidence_ids=("relation:1",),
                    fact_evidence_ids=("fact:stale",),
                    observed_at=NOW - timedelta(days=30),
                    expected_revision=0,
                ),
                idempotency_key="candidate-stale",
            )
        with self.assertRaisesRegex(ValueError, "candidate_evidence_insufficient"):
            self.book.consider_candidate(
                self.actor,
                CandidateEvidence(
                    symbol="AMD",
                    relation_evidence_ids=("relation:1",),
                    fact_evidence_ids=("fact:future",),
                    observed_at=NOW + timedelta(seconds=1),
                    expected_revision=0,
                ),
                idempotency_key="candidate-future",
            )

        first = self.book.consider_candidate(
            self.actor,
            CandidateEvidence(
                symbol="AMD",
                relation_evidence_ids=("relation:1",),
                fact_evidence_ids=("fact:1",),
                observed_at=NOW,
                expected_revision=0,
            ),
            idempotency_key="candidate-amd-1",
        )
        self.now = NOW + timedelta(days=1)
        merged = self.book.consider_candidate(
            self.actor,
            CandidateEvidence(
                symbol="amd",
                relation_evidence_ids=("relation:2",),
                fact_evidence_ids=("fact:2",),
                observed_at=NOW + timedelta(days=1),
                expected_revision=1,
            ),
            idempotency_key="candidate-amd-2",
        )
        self.assertEqual(len(merged.items), 1)
        item = merged.items[0]
        self.assertFalse(item.is_followed)
        self.assertEqual(item.candidate_status, "active")
        self.assertEqual(item.relation_evidence_ids, ("relation:1", "relation:2"))
        self.assertEqual(item.fact_evidence_ids, ("fact:1", "fact:2"))
        self.assertGreater(item.candidate_refreshed_at, first.items[0].candidate_refreshed_at)
        replayed = self.book.consider_candidate(
            self.actor,
            CandidateEvidence(
                symbol="AMD",
                relation_evidence_ids=("relation:1", "relation:2"),
                fact_evidence_ids=("fact:1", "fact:2"),
                observed_at=NOW + timedelta(days=1),
                expected_revision=2,
            ),
            idempotency_key="candidate-amd-replayed-evidence",
        )
        self.assertEqual(replayed.items[0].candidate_refreshed_at, item.candidate_refreshed_at)

    def test_candidate_archives_after_fourteen_trading_days_and_can_reactivate(self) -> None:
        self.book.consider_candidate(
            self.actor,
            CandidateEvidence(
                symbol="AVGO",
                relation_evidence_ids=("relation:1",),
                fact_evidence_ids=("fact:1",),
                observed_at=NOW,
                expected_revision=0,
            ),
            idempotency_key="candidate-avgo",
        )
        before = self.book.archive_stale_candidates(
            self.actor,
            as_of=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
            expected_revision=1,
            idempotency_key="archive-day-13",
        )
        self.assertEqual(before.items[0].candidate_status, "active")
        archived = self.book.archive_stale_candidates(
            self.actor,
            as_of=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
            expected_revision=2,
            idempotency_key="archive-day-14",
        )
        self.assertEqual(archived.items[0].candidate_status, "archived")
        self.now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        reactivated = self.book.consider_candidate(
            self.actor,
            CandidateEvidence(
                symbol="AVGO",
                relation_evidence_ids=("relation:2",),
                fact_evidence_ids=("fact:2",),
                observed_at=self.now,
                expected_revision=3,
            ),
            idempotency_key="candidate-avgo-reactivate",
        )
        self.assertEqual(reactivated.items[0].candidate_status, "active")
        self.assertIsNone(reactivated.items[0].candidate_archived_at)


if __name__ == "__main__":
    unittest.main()
