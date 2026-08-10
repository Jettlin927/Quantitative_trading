from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads
from backend.app.personal_workspace.automatic_briefing_store import (
    ActiveAnalysisBudgetGuard,
    BriefingCost,
    BriefingMode,
    BriefingProviderState,
    DailyBudgetPolicy,
    PostgresAutomaticBriefingStore,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
MARKET_DATE = date(2026, 8, 10)
POLICY = DailyBudgetPolicy(
    revision="budget-v1",
    fx_cny_per_usd=Decimal("7.20"),
    target_cny=Decimal("0.50"),
    soft_limit_cny=Decimal("1.00"),
    hard_limit_cny=Decimal("5.00"),
)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class PostgresAutomaticBriefingStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
            command.upgrade(alembic_config(connection), "head")
            assert current_schema_heads(connection) == expected_schema_heads()
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="briefing-key",
                data_keys={"briefing-key": bytes(range(32))},
                lookup_key=b"briefing-postgres-lookup-key-32-bytes",
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        self.actor_id = f"briefing-owner-{uuid4()}"

    def _store(self) -> PostgresAutomaticBriefingStore:
        return PostgresAutomaticBriefingStore(self.Session, cipher=self.cipher)

    def _claim(self, trigger_key: str):
        return self._store().claim(
            actor_id=self.actor_id,
            trigger_key=trigger_key,
            market_date=MARKET_DATE,
            trigger_kind="intraday_event",
            lease_owner="worker",
            lease_expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    def test_concurrent_duplicate_trigger_is_claimed_once(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: self._claim("event:duplicate"), range(2)))

        self.assertEqual(sum(item.acquired for item in claims), 1)
        self.assertEqual(sum(item.created for item in claims), 1)
        self.assertEqual(len({item.briefing_id for item in claims}), 1)

    def test_pending_scheduled_trigger_round_trips(self) -> None:
        store = self._store()
        store.claim(
            actor_id=self.actor_id,
            trigger_key="recipe:2026-08-10:premarket",
            market_date=MARKET_DATE,
            trigger_kind="premarket",
            lease_owner="worker",
            lease_expires_at=NOW,
            now=NOW,
        )
        self._claim("event:not-scheduled")

        pending = store.pending_scheduled(actor_id=self.actor_id)

        self.assertEqual(
            tuple(item.trigger_kind for item in pending),
            ("premarket",),
        )

    def test_concurrent_near_hard_limit_allows_only_one_reservation(self) -> None:
        first = self._claim("event:budget-1")
        second = self._claim("event:budget-2")

        def reserve(claim):
            return self._store().reserve_budget(
                claim=claim,
                mode=BriefingMode.ACTIVE,
                estimated_cost_usd=Decimal("0.40"),
                policy=DailyBudgetPolicy(
                    revision="hard-race-v1",
                    fx_cny_per_usd=Decimal("7.20"),
                    target_cny=Decimal("0.50"),
                    soft_limit_cny=Decimal("1.00"),
                    hard_limit_cny=Decimal("3.00"),
                ),
                now=NOW,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            decisions = list(executor.map(reserve, (first, second)))

        self.assertEqual(sum(item.allowed for item in decisions), 1)
        self.assertEqual(sorted(item.reason for item in decisions), ["hard_limit", "reserved"])

    def test_private_payload_is_encrypted_at_rest(self) -> None:
        claim = self._claim("event:private")
        failed = self._store().fail_before_provider(
            claim=claim,
            private_payload={"evidence": "绝密证据文本", "gaps": ["行情缺口"]},
            failure_code="evidence_insufficient",
            now=NOW,
        )
        self.assertEqual(failed.private_payload["evidence"], "绝密证据文本")

        with self.engine.connect() as connection:
            projection = repr(
                connection.execute(
                    text(
                        "select * from private_workbench.personal_automatic_briefings "
                        "where id = :id"
                    ),
                    {"id": claim.briefing_id},
                ).one()
            )
        self.assertNotIn("绝密证据文本", projection)
        self.assertNotIn("行情缺口", projection)

    def test_expired_started_call_is_conservatively_settled_without_reclaim(self) -> None:
        claim = self._claim("event:crash-after-send")
        decision = self._store().reserve_budget(
            claim=claim,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW,
        )
        self._store().mark_provider_started(
            claim=claim,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=5),
            now=NOW,
        )

        self.assertEqual(
            self._store().reconcile_expired_started(
                actor_id=self.actor_id,
                now=NOW + timedelta(minutes=4),
            ),
            0,
        )
        recovered_count = self._store().reconcile_expired_started(
            actor_id=self.actor_id,
            now=NOW + timedelta(minutes=6),
        )
        stored = self._store().get(
            actor_id=self.actor_id, trigger_key=claim.trigger_key
        )

        self.assertEqual(recovered_count, 1)
        self.assertEqual(
            stored.provider_state, BriefingProviderState.OUTCOME_UNKNOWN
        )
        self.assertEqual(stored.daily_cumulative_cny, Decimal("0.72000000"))

    def test_provider_deadline_allows_completion_after_claim_lease(self) -> None:
        store = self._store()
        claim = store.claim(
            actor_id=self.actor_id,
            trigger_key="event:slow-provider",
            market_date=MARKET_DATE,
            trigger_kind="intraday_event",
            lease_owner="worker",
            lease_expires_at=NOW + timedelta(minutes=1),
            now=NOW,
        )
        decision = store.reserve_budget(
            claim=claim,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW,
        )
        store.mark_provider_started(
            claim=claim,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=5),
            now=NOW,
        )
        store.renew_provider_deadline(
            briefing_id=claim.briefing_id,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=9),
            now=NOW + timedelta(minutes=4),
        )

        self.assertEqual(
            store.reconcile_expired_started(
                actor_id=self.actor_id,
                now=NOW + timedelta(minutes=6),
            ),
            0,
        )
        completed = store.complete(
            briefing_id=claim.briefing_id,
            reservation_id=decision.reservation_id,
            cost=BriefingCost(10, 5, Decimal("0.08")),
            private_payload={"status": "slow-success"},
            now=NOW + timedelta(minutes=6),
        )

        self.assertEqual(completed.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(completed.daily_cumulative_cny, Decimal("0.57600000"))

    def test_active_guard_and_automatic_runs_share_the_postgres_hard_limit(self) -> None:
        automatic = self._claim("event:mixed-budget")
        decision = self._store().reserve_budget(
            claim=automatic,
            mode=BriefingMode.AUTOMATIC,
            estimated_cost_usd=Decimal("0.10"),
            policy=POLICY,
            now=NOW,
        )
        self._store().mark_provider_started(
            claim=automatic,
            reservation_id=decision.reservation_id,
            provider_deadline=NOW + timedelta(minutes=5),
            now=NOW,
        )
        self._store().complete(
            briefing_id=automatic.briefing_id,
            reservation_id=decision.reservation_id,
            cost=BriefingCost(10, 5, Decimal("0.10")),
            private_payload={},
            now=NOW,
        )
        guard = ActiveAnalysisBudgetGuard(store=self._store(), policy=POLICY)
        active = guard.start_call(
            actor_id=self.actor_id,
            run_id="active-near-hard",
            worker_id="worker",
            estimated_cost_usd=Decimal("0.59"),
            now=NOW,
        )
        guard.complete_call(
            active,
            run_id="active-near-hard",
            cost=BriefingCost(10, 5, Decimal("0.59")),
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "hard_limit"):
            guard.start_call(
                actor_id=self.actor_id,
                run_id="active-over-hard",
                worker_id="worker",
                estimated_cost_usd=Decimal("0.01"),
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
