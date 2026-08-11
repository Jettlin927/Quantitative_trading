from __future__ import annotations

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
    PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.tests.test_today_domain_tools import FakeAiContextMarketAdapter


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
MARKET_RETENTION = {
    ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
    ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
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

    def _store(self) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization=RETENTION,
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
            "actor-owned-personal-portfolio-v1",
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


if __name__ == "__main__":
    unittest.main()
