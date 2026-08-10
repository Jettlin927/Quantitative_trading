from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from backend.app.personal_workspace.automatic_briefing_store import (
    ActiveAnalysisBudgetGuard,
    BriefingCost,
    BriefingMode,
    BriefingProviderState,
    DailyBudgetPolicy,
    InMemoryAutomaticBriefingStore,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
MARKET_DATE = date(2026, 8, 10)
POLICY = DailyBudgetPolicy(
    revision="budget-v1",
    fx_cny_per_usd=Decimal("7.20"),
    target_cny=Decimal("0.50"),
    soft_limit_cny=Decimal("1.00"),
    hard_limit_cny=Decimal("5.00"),
)


class InMemoryAutomaticBriefingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAutomaticBriefingStore()

    def _claim(self, trigger_key: str = "premarket:2026-08-10"):
        return self.store.claim(
            actor_id="owner",
            trigger_key=trigger_key,
            market_date=MARKET_DATE,
            trigger_kind="premarket",
            lease_owner="worker-a",
            lease_expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    def test_duplicate_trigger_has_one_claim_and_started_run_is_never_released(self) -> None:
        first = self._claim()
        duplicate = self._claim()

        self.assertTrue(first.created)
        self.assertTrue(first.acquired)
        self.assertFalse(duplicate.created)
        self.assertFalse(duplicate.acquired)
        self.assertIsNone(duplicate.lease_token)

        expired = self.store.claim(
            actor_id="owner",
            trigger_key=first.trigger_key,
            market_date=MARKET_DATE,
            trigger_kind="premarket",
            lease_owner="worker-b",
            lease_expires_at=NOW + timedelta(minutes=10),
            now=NOW + timedelta(minutes=6),
        )
        self.assertTrue(expired.acquired)
        self.assertFalse(expired.created)
        self.assertNotEqual(expired.lease_token, first.lease_token)

        reservation = self.store.reserve_budget(
            claim=expired,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW + timedelta(minutes=6),
        )
        self.assertTrue(reservation.allowed)
        started = self.store.mark_provider_started(
            claim=expired,
            reservation_id=reservation.reservation_id,
            provider_deadline=NOW + timedelta(minutes=8),
            now=NOW + timedelta(minutes=6),
        )
        self.assertEqual(started.provider_state, BriefingProviderState.STARTED)

        after_started = self.store.claim(
            actor_id="owner",
            trigger_key=first.trigger_key,
            market_date=MARKET_DATE,
            trigger_kind="premarket",
            lease_owner="worker-c",
            lease_expires_at=NOW + timedelta(hours=2),
            now=NOW + timedelta(minutes=7),
        )
        self.assertFalse(after_started.acquired)
        self.assertEqual(after_started.provider_state, BriefingProviderState.STARTED)
        self.assertEqual(
            self.store.reconcile_expired_started(
                actor_id="owner", now=NOW + timedelta(minutes=9)
            ),
            1,
        )
        recovered = self.store.get(actor_id="owner", trigger_key=first.trigger_key)
        self.assertEqual(
            recovered.provider_state, BriefingProviderState.OUTCOME_UNKNOWN
        )
        self.assertEqual(recovered.daily_cumulative_cny, Decimal("0.7200000000"))

    def test_daily_budget_reservation_obeys_automatic_soft_and_active_hard_limits(self) -> None:
        first = self._claim("intraday:event-1")
        with self.assertLogs(
            "backend.app.personal_workspace.automatic_briefing_store",
            level="WARNING",
        ) as target_logs:
            first_decision = self.store.reserve_budget(
                claim=first,
                mode=BriefingMode.AUTOMATIC,
                estimated_cost_usd=Decimal("0.10"),
                policy=POLICY,
                now=NOW,
            )
        self.assertEqual(
            target_logs.output.count(
                "WARNING:backend.app.personal_workspace.automatic_briefing_store:"
                "personal_ai_daily_target_exceeded"
            ),
            1,
        )
        self.assertTrue(first_decision.allowed)
        self.assertEqual(first_decision.projected_daily_cny, Decimal("0.7200000000"))

        second = self._claim("intraday:event-2")
        soft_block = self.store.reserve_budget(
            claim=second,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.05"),
            policy=POLICY,
            now=NOW,
        )
        self.assertFalse(soft_block.allowed)
        self.assertEqual(soft_block.reason, "soft_limit")

        active = self.store.reserve_budget(
            claim=second,
            mode=BriefingMode.ACTIVE,
            estimated_cost_usd=Decimal("0.05"),
            policy=POLICY,
            now=NOW,
        )
        self.assertTrue(active.allowed)

        third = self._claim("intraday:event-3")
        hard_block = self.store.reserve_budget(
            claim=third,
            mode=BriefingMode.ACTIVE,
            estimated_cost_usd=Decimal("0.60"),
            policy=POLICY,
            now=NOW,
        )
        self.assertFalse(hard_block.allowed)
        self.assertEqual(hard_block.reason, "hard_limit")

    def test_completion_settles_reservation_and_round_trips_private_payload(self) -> None:
        claim = self._claim()
        decision = self.store.reserve_budget(
            claim=claim,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW,
        )
        self.store.mark_provider_started(
            claim=claim,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=2),
            now=NOW,
        )
        completed = self.store.complete(
            briefing_id=claim.briefing_id,
            reservation_id=decision.reservation_id,
            cost=BriefingCost(
                input_tokens=120,
                output_tokens=30,
                cost_usd=Decimal("0.08"),
            ),
            private_payload={"tool_events": [{"tool": "get_today_context"}], "gaps": []},
            now=NOW + timedelta(seconds=2),
        )

        self.assertEqual(completed.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(completed.actual_cost_cny, Decimal("0.5760000000"))
        self.assertEqual(completed.daily_cumulative_cny, Decimal("0.5760000000"))
        self.assertEqual(completed.private_payload["gaps"], [])
        self.assertEqual(
            self.store.get(actor_id="owner", trigger_key=claim.trigger_key),
            completed,
        )

    def test_failure_before_provider_is_terminal_without_budget_charge(self) -> None:
        claim = self._claim()
        failed = self.store.fail_before_provider(
            claim=claim,
            private_payload={"gaps": ["evidence_unavailable"]},
            failure_code="evidence_insufficient",
            now=NOW,
        )

        self.assertEqual(failed.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(failed.failure_code, "evidence_insufficient")
        self.assertEqual(failed.daily_cumulative_cny, Decimal("0"))
        self.assertFalse(self._claim().acquired)

    def test_active_analysis_shares_automatic_daily_hard_limit(self) -> None:
        automatic = self._claim("intraday:mixed-budget")
        automatic_decision = self.store.reserve_budget(
            claim=automatic,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW,
        )
        self.store.mark_provider_started(
            claim=automatic,
            reservation_id=automatic_decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=2),
            now=NOW,
        )
        self.store.complete(
            briefing_id=automatic.briefing_id,
            reservation_id=automatic_decision.reservation_id,
            cost=BriefingCost(10, 5, Decimal("0.10")),
            private_payload={},
            now=NOW,
        )
        guard = ActiveAnalysisBudgetGuard(store=self.store, policy=POLICY)
        first = guard.start_call(
            actor_id="owner",
            run_id="active-1",
            worker_id="worker",
            estimated_cost_usd=Decimal("0.59"),
            now=NOW,
        )
        guard.complete_call(
            first,
            run_id="active-1",
            cost=BriefingCost(10, 5, Decimal("0.59")),
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "hard_limit"):
            guard.start_call(
                actor_id="owner",
                run_id="active-2",
                worker_id="worker",
                estimated_cost_usd=Decimal("0.01"),
                now=NOW,
            )

    def test_completion_can_settle_a_charged_output_validation_failure(self) -> None:
        claim = self._claim("postmarket:invalid-output")
        decision = self.store.reserve_budget(
            claim=claim,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.02"),
            policy=POLICY,
            now=NOW,
        )
        self.store.mark_provider_started(
            claim=claim,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=2),
            now=NOW,
        )

        failed = self.store.complete(
            briefing_id=claim.briefing_id,
            reservation_id=decision.reservation_id,
            cost=BriefingCost(10, 5, Decimal("0.01")),
            private_payload={"gaps": ["claims_invalid"]},
            failure_code="claims_invalid",
            now=NOW,
        )

        self.assertEqual(failed.failure_code, "claims_invalid")
        self.assertEqual(failed.accounted_cost_usd, Decimal("0.01"))


if __name__ == "__main__":
    unittest.main()
