from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config
from backend.app.personal_workspace.agent.evidence import (
    EvidenceLedgerError,
    EvidenceReadContext,
    PostgresEvidenceStore,
)
from backend.app.personal_workspace.agent.fact_market import MarketFactService
from backend.app.personal_workspace.agent.fact_private import (
    ActorOwnedFactService,
    PRIVATE_FACT_POLICY_HISTORY,
    PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
    _actor_owned_record,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.market_observation.contracts import DailyBarsObservation
from backend.tests.test_today_domain_tools import FakeAiContextMarketAdapter


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
MARKET_RETENTION = {
    ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
    ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
    ("alpaca", "auth-alpaca_daily_bars-old"): "encrypted_payload",
    ("alpaca", "auth-alpaca_daily_bars-new"): "encrypted_payload",
}
RETENTION = {
    **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
    **MARKET_RETENTION,
}


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class FactSourcesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="fact-sources-key",
                data_keys={"fact-sources-key": bytes(range(32))},
                lookup_key=b"s" * 32,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _store(self, retention=RETENTION) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization=retention,
        )

    def test_private_and_market_facts_restart_freeze_and_isolate_actor(self) -> None:
        actor = f"fact-sources-{uuid4()}"
        context = EvidenceReadContext(
            actor_id=actor,
            permissions=frozenset({"portfolio:read", "market:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        store = self._store()
        holdings = ActorOwnedFactService(store).record(
            context=context,
            source="personal_portfolio",
            logical_identity="holdings:7",
            payload={"holdings": [], "count": 0, "usd_cash": "500"},
            observed_at=NOW,
        )
        market = MarketFactService(
            adapter=FakeAiContextMarketAdapter(),
            evidence_ledger=store,
            retention_by_authorization=MARKET_RETENTION,
        ).read_dossier(
            context=context,
            symbol="NVDA",
            bar_days=30,
            bar_limit=1,
        )
        evidence_ids = (
            holdings.evidence_id,
            *(record.evidence_id for record in market.records),
        )

        restarted = self._store()
        frozen = restarted.freeze(context, evidence_ids)

        self.assertEqual(
            tuple(record.evidence_id for record in frozen), evidence_ids
        )
        self.assertEqual(
            frozen[0].authorization_snapshot_id,
            "actor-owned-personal-portfolio-v3",
        )
        self.assertEqual(
            {record.authorization_snapshot_id for record in frozen[1:]},
            {"auth-alpaca_assets", "auth-alpaca_daily_bars"},
        )
        self.assertTrue(all(record.payload is not None for record in frozen))
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_found"):
            restarted.read(
                EvidenceReadContext(
                    actor_id=f"other-{uuid4()}",
                    permissions=context.permissions,
                    purpose=context.purpose,
                    now=NOW,
                ),
                holdings.evidence_id,
            )

    def test_historical_market_snapshot_survives_restart_and_rotation(self) -> None:
        class SnapshotAdapter(FakeAiContextMarketAdapter):
            def __init__(self, snapshot_id: str) -> None:
                super().__init__()
                self.snapshot_id = snapshot_id

            def observe_daily_bars(self, symbol, **kwargs):
                observed = super().observe_daily_bars(symbol, **kwargs)
                selected = replace(
                    observed.raw,
                    provenance=replace(
                        observed.raw.provenance,
                        authorization_snapshot_id=self.snapshot_id,
                    ),
                )
                return DailyBarsObservation(
                    raw=selected, provider_adjusted=selected
                )

        actor = f"fact-history-{uuid4()}"
        context = EvidenceReadContext(
            actor_id=actor,
            permissions=frozenset({"market:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        old = MarketFactService(
            adapter=SnapshotAdapter("auth-alpaca_daily_bars-old"),
            evidence_ledger=self._store(),
            retention_by_authorization=MARKET_RETENTION,
        ).read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=1
        ).records[0]
        new = MarketFactService(
            adapter=SnapshotAdapter("auth-alpaca_daily_bars-new"),
            evidence_ledger=self._store(),
            retention_by_authorization=MARKET_RETENTION,
        ).read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=1
        ).records[0]

        restarted = self._store()
        frozen = restarted.freeze(
            context, (old.evidence_id, new.evidence_id)
        )

        self.assertNotEqual(old.evidence_id, new.evidence_id)
        self.assertEqual(old.content_sha256, new.content_sha256)
        self.assertEqual(
            tuple(record.authorization_snapshot_id for record in frozen),
            (
                "auth-alpaca_daily_bars-old",
                "auth-alpaca_daily_bars-new",
            ),
        )
        self.assertTrue(all(record.payload is not None for record in frozen))
        without_history = PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization={
                key: value
                for key, value in RETENTION.items()
                if key != ("alpaca", "auth-alpaca_daily_bars-old")
            },
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_unknown"
        ):
            without_history.read(context, old.evidence_id)

    def test_historical_private_policy_survives_restart_and_rotation(self) -> None:
        v1 = PRIVATE_FACT_POLICY_HISTORY["personal_portfolio"][0]
        v2 = PRIVATE_FACT_POLICY_HISTORY["personal_portfolio"][1]
        v3 = PRIVATE_FACT_POLICY_HISTORY["personal_portfolio"][2]
        retention = RETENTION
        actor = f"fact-private-history-{uuid4()}"
        context = EvidenceReadContext(
            actor_id=actor,
            permissions=frozenset({"portfolio:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        arguments = {
            "context": context,
            "logical_identity": "holdings:7",
            "payload": {"holdings": [], "count": 0, "usd_cash": "500"},
            "observed_at": NOW,
        }
        store = self._store(retention)
        old = store.put(
            context, _actor_owned_record(policy=v1, **arguments)
        )
        new = store.put(
            context, _actor_owned_record(policy=v2, **arguments)
        )
        remote = store.put(
            context, _actor_owned_record(policy=v3, **arguments)
        )

        frozen = self._store(retention).freeze(
            context, (old.evidence_id, new.evidence_id, remote.evidence_id)
        )

        self.assertNotEqual(old.evidence_id, new.evidence_id)
        self.assertEqual(old.content_sha256, new.content_sha256)
        self.assertEqual(
            tuple(record.authorization_snapshot_id for record in frozen),
            (
                "actor-owned-personal-portfolio-v1",
                "actor-owned-personal-portfolio-v2",
                "actor-owned-personal-portfolio-v3",
            ),
        )
        mcp_context = replace(context, purpose="mcp_stdio")
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_purpose_denied"
        ):
            self._store().read(mcp_context, old.evidence_id)
        self.assertEqual(
            self._store().read(mcp_context, new.evidence_id).evidence_id,
            new.evidence_id,
        )
        remote_context = replace(context, purpose="mcp_remote_read")
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_purpose_denied"
        ):
            self._store().read(remote_context, new.evidence_id)
        self.assertEqual(
            self._store().read(remote_context, remote.evidence_id).evidence_id,
            remote.evidence_id,
        )
        without_v1_retention = {
            key: value
            for key, value in retention.items()
            if key != (v1.source, v1.authorization_snapshot_id)
        }
        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_unknown"
        ):
            self._store(without_v1_retention).read(context, old.evidence_id)


if __name__ == "__main__":
    unittest.main()
