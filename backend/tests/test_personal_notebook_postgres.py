from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import os
import unittest

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads
from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    AnalysisWorkspace,
    EvidenceCandidate,
    PostgresAnalysisStore,
    ScriptedResponsesAdapter,
)
from backend.app.personal_workspace.contracts import AddHoldingCommand, PersonalActor, PurgeHoldingCommand
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.notebook import (
    PostgresNotebookStore,
    PrivateFragmentInput,
    ResearchNotebook,
)
from backend.app.personal_workspace.portfolio import (
    PortfolioBook,
    PostgresPortfolioStore,
    UnavailablePortfolioMarketReader,
)


NOW = datetime(2026, 8, 3, 7, 0, tzinfo=timezone.utc)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置")
class PersonalNotebookPostgresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        with self.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
            command.upgrade(alembic_config(connection), "head")
            self.assertEqual(current_schema_heads(connection), expected_schema_heads())
        with self.engine.begin() as connection:
            for table in (
                "personal_verification_observations", "personal_verification_items",
                "personal_record_private_fragments", "personal_record_versions",
                "personal_redaction_events", "personal_research_records",
                "personal_ai_claims", "personal_analysis_events", "personal_analysis_attempts",
                "personal_analysis_runs", "personal_evidence_refs", "personal_evidence_packs",
                "personal_analysis_drafts", "personal_audit_events", "personal_portfolio_revisions",
                "personal_holdings", "personal_workspaces",
            ):
                connection.execute(text(f"DELETE FROM private_workbench.{table}"))
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.cipher = PersonalDataCipher(FixedKeyring(
            active_key_id="notebook-pg-key", data_keys={"notebook-pg-key": bytes(range(32))},
            lookup_key=b"notebook-pg-lookup-key-for-tests",
        ))
        self.analysis_store = PostgresAnalysisStore(self.session_factory, cipher=self.cipher)
        self.analyses = AnalysisWorkspace(
            store=self.analysis_store,
            evidence_reader=lambda actor, intent: (
                EvidenceCandidate("sec-pg-1", "official_filing", "sec", "official_facts",
                                  "PostgreSQL 官方事实", "2" * 64, True, NOW),
            ),
            provider=ScriptedResponsesAdapter.completed(claims=(
                {"kind": "confirmed_fact", "statement": "PostgreSQL 已确认事实",
                 "evidence_ids": ["sec-pg-1"], "opposing_evidence_ids": [], "assumptions": [],
                 "horizon": "当前", "invalidation_conditions": ["官方更正"]},
            )), clock=lambda: NOW,
        )
        self.notebook = ResearchNotebook(
            store=PostgresNotebookStore(self.session_factory, cipher=self.cipher),
            analyses=self.analysis_store, challenge_key=b"notebook-pg-challenge" * 2,
            clock=lambda: NOW,
        )
        self.portfolio = PortfolioBook(
            store=PostgresPortfolioStore(self.session_factory, cipher=self.cipher),
            market=UnavailablePortfolioMarketReader(),
            challenge_key=b"portfolio-pg-challenge" * 2, clock=lambda: NOW,
        )
        self.actor = PersonalActor("notebook-pg-owner")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_versions_concurrency_fragment_redaction_and_backup_window(self) -> None:
        portfolio = self.portfolio.revise(
            self.actor,
            AddHoldingCommand(type="add_holding", symbol="ACME", name="敏感公司名",
                              quantity=Decimal("2"), average_cost=Decimal("123.45"),
                              expected_portfolio_revision=0),
            idempotency_key="pg-add-holding",
        )
        holding_id = portfolio.holdings[0].holding_id
        draft = self.analyses.prepare(
            self.actor,
            AnalysisIntent("PostgreSQL 问题正文", ("ACME",)),
            idempotency_key="pg-prepare",
        )
        run = self.analyses.start(
            self.actor, draft_id=draft.draft_id, preview_sha256=draft.preview_sha256,
            idempotency_key="pg-start",
        )
        self.analyses.run_next(worker_id="pg-worker")
        completed = self.analyses.observe(self.actor, run.run_id)
        record = self.notebook.save_analysis(
            self.actor, analysis_id=run.run_id,
            accepted_claim_ids=(completed.claims[0].claim_id,), user_supplement="不含精确值的说明",
            fragments=(PrivateFragmentInput(holding_id, "精确成本 123.45 与数量 2"),),
            verification_drafts=(), idempotency_key="pg-save-record",
        )

        def append(index: int):
            try:
                return self.notebook.append_supplement(
                    self.actor, record_id=record.record_id, expected_version=1,
                    supplement=f"并发补充 {index}", fragments=(), idempotency_key=f"pg-append-{index}",
                )
            except ValueError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(append, (1, 2)))
        self.assertEqual(sum(not isinstance(item, str) for item in results), 1)
        self.assertIn("revision_conflict", results)

        current = self.notebook.open(self.actor, record.record_id)
        challenge = self.portfolio.request_purge(
            self.actor, holding_id=holding_id,
            expected_portfolio_revision=portfolio.portfolio_revision,
        )
        receipt = self.portfolio.purge(
            self.actor,
            PurgeHoldingCommand(
                holding_id=holding_id,
                expected_portfolio_revision=portfolio.portfolio_revision,
                challenge=challenge.challenge,
            ),
            idempotency_key="pg-purge-holding",
        )
        redacted = self.notebook.open(self.actor, record.record_id)

        self.assertEqual(current.current_version, 2)
        self.assertEqual(receipt.backup_expires_at, NOW.replace(day=2, month=9))
        self.assertEqual(redacted.private_fragments[0].status, "redacted")
        self.assertIsNone(redacted.private_fragments[0].text)
        self.assertEqual(redacted.redactions[-1].reason, "source_holding_purged")
        self.assertFalse(redacted.formal_research_eligible)

        with self.engine.connect() as connection:
            projection = "|".join(
                str(value)
                for table in (
                    "personal_research_records", "personal_record_versions",
                    "personal_record_private_fragments", "personal_verification_items",
                    "personal_verification_observations", "personal_redaction_events",
                )
                for row in connection.execute(text(f"SELECT * FROM private_workbench.{table}"))
                for value in row
            )
        self.assertNotIn("PostgreSQL 问题正文", projection)
        self.assertNotIn("精确成本", projection)
        self.assertNotIn("敏感公司名", projection)
        self.assertNotIn("123.45", projection)


if __name__ == "__main__":
    unittest.main()
