from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    AnalysisWorkspace,
    EvidenceCandidate,
    PostgresAnalysisStore,
    ScriptedResponsesAdapter,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads


NOW = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class PersonalAnalysisPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
            command.upgrade(alembic_config(connection), "head")
            assert current_schema_heads(connection) == expected_schema_heads()
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_encrypted_preview_run_claims_and_single_lease_round_trip(self) -> None:
        actor = PersonalActor(actor_id="analysis-pg-owner")
        cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="analysis-key",
                data_keys={"analysis-key": bytes(range(32))},
                lookup_key=b"analysis-postgres-lookup-key-32-bytes-minimum",
            )
        )
        provider = ScriptedResponsesAdapter.completed(
            claims=(
                {
                    "kind": "inference",
                    "statement": "资本开支可能影响短期自由现金流。",
                    "evidence_ids": ["sec-pg-1"],
                    "opposing_evidence_ids": [],
                    "assumptions": ["其他条件不变"],
                    "horizon": "未来两个季度",
                    "invalidation_conditions": ["现金流显著改善"],
                },
            )
        )
        workspace = AnalysisWorkspace(
            store=PostgresAnalysisStore(self.Session, cipher=cipher),
            evidence_reader=lambda request_actor, intent: (
                EvidenceCandidate(
                    evidence_id="sec-pg-1",
                    kind="official_filing",
                    source="sec",
                    field="official_facts",
                    excerpt="私有冻结摘录：资本开支增加。",
                    content_sha256="a" * 64,
                    authorized_for_ai=True,
                    as_of=NOW,
                ),
            ),
            provider=provider,
            clock=lambda: NOW,
        )
        draft = workspace.prepare(
            actor,
            AnalysisIntent(question="私有问题：现金流影响？", subject_ids=("ACME",)),
            idempotency_key="pg-prepare",
        )
        queued = workspace.start(
            actor,
            draft_id=draft.draft_id,
            preview_sha256=draft.preview_sha256,
            idempotency_key="pg-start",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda worker: workspace.run_next(worker_id=worker),
                    ("worker-a", "worker-b"),
                )
            )

        completed = next(item for item in results if item is not None)
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.run_id, queued.run_id)
        self.assertEqual(workspace.observe(actor, queued.run_id).claims[0].kind, "inference")

        with self.engine.connect() as connection:
            table_names = set(
                connection.execute(
                    text(
                        "select table_name from information_schema.tables "
                        "where table_schema = 'private_workbench'"
                    )
                ).scalars()
            )
            projection = "|".join(
                str(value)
                for table_name in (
                    "personal_analysis_drafts",
                    "personal_analysis_runs",
                    "personal_analysis_events",
                    "personal_evidence_packs",
                    "personal_evidence_refs",
                    "personal_ai_claims",
                )
                for row in connection.execute(
                    text(f"select * from private_workbench.{table_name}")
                )
                for value in row
            )
        self.assertTrue(
            {
                "personal_analysis_runs",
                "personal_analysis_attempts",
                "personal_analysis_events",
                "personal_evidence_packs",
                "personal_evidence_refs",
                "personal_ai_claims",
            }.issubset(table_names)
        )
        for private_value in (
            "analysis-pg-owner",
            "ACME",
            "私有问题",
            "私有冻结摘录",
            "资本开支可能影响短期自由现金流",
            "其他条件不变",
        ):
            self.assertNotIn(private_value, projection)

    def test_heartbeat_renews_postgres_lease_and_fences_stale_token(self) -> None:
        actor = PersonalActor(actor_id=f"analysis-pg-heartbeat-owner-{uuid4()}")
        cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="analysis-key",
                data_keys={"analysis-key": bytes(range(32))},
                lookup_key=b"analysis-postgres-lookup-key-32-bytes-minimum",
            )
        )
        store = PostgresAnalysisStore(self.Session, cipher=cipher)
        workspace = AnalysisWorkspace(
            store=store,
            evidence_reader=lambda request_actor, intent: (
                EvidenceCandidate(
                    evidence_id="heartbeat-evidence",
                    kind="official_filing",
                    source="sec",
                    field="official_facts",
                    excerpt="租约证据",
                    content_sha256="b" * 64,
                    authorized_for_ai=True,
                    as_of=NOW,
                ),
            ),
            provider=ScriptedResponsesAdapter.completed(claims=()),
            clock=lambda: NOW,
        )
        draft = workspace.prepare(
            actor,
            AnalysisIntent(question="租约测试", subject_ids=("ACME",)),
            idempotency_key="pg-heartbeat-prepare",
        )
        queued = workspace.start(
            actor,
            draft_id=draft.draft_id,
            preview_sha256=draft.preview_sha256,
            idempotency_key="pg-heartbeat-start",
        )
        _draft, first = store.lease_next(
            worker_id="worker-a", now=NOW, lease_seconds=30
        )
        renewed = store.heartbeat(
            first,
            now=NOW + timedelta(seconds=20),
            lease_seconds=30,
        )

        self.assertIsNone(
            store.lease_next(
                worker_id="worker-b",
                now=NOW + timedelta(seconds=40),
                lease_seconds=30,
            )
        )
        _draft, second = store.lease_next(
            worker_id="worker-b",
            now=NOW + timedelta(seconds=51),
            lease_seconds=30,
        )
        self.assertEqual(second.view.run_id, queued.run_id)
        store.save_run(
            replace(
                second,
                lease_owner=None,
                lease_expires_at=None,
                view=replace(second.view, status="completed", stage="completed"),
            )
        )
        with self.assertRaisesRegex(ValueError, "analysis_lease_lost"):
            store.save_run(renewed)
        self.assertEqual(store.get_run(actor.actor_id, queued.run_id).view.status, "completed")


if __name__ == "__main__":
    unittest.main()
