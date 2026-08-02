from __future__ import annotations

import os
import unittest

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from backend.app import models  # noqa: F401
from backend.app.database import (
    PrivateBase,
    alembic_config,
    current_schema_heads,
    expected_schema_heads,
    schema_fingerprint,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.journey import PersonalResearchJourney
from backend.app.personal_workspace.persistence import PostgresPersonalJourneyStore
from backend.app.personal_workspace.synthetic import SyntheticWorkspaceAdapters


PRIVATE_TABLES = {
    "personal_workspaces",
    "personal_holdings",
    "personal_rule_evaluations",
    "personal_analysis_drafts",
    "personal_research_records",
}


class PersonalWorkspaceSchemaIdentityTest(unittest.TestCase):
    def test_private_orm_identity_is_separate_from_public_metadata(self) -> None:
        self.assertEqual(expected_schema_heads(), ("0013_personal_workspace_t0",))
        self.assertEqual(
            set(PrivateBase.metadata.tables),
            {f"private_workbench.{table}" for table in PRIVATE_TABLES},
        )
        self.assertTrue(set(PrivateBase.metadata.tables).isdisjoint(models.Base.metadata.tables))


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过私有 PostgreSQL 集成测试")
class PersonalWorkspacePostgresIntegrationTest(unittest.TestCase):
    def test_migration_creates_isolated_schema_with_fail_closed_public_grants(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        role_name = "synthetic_formal_research_role"
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
                command.upgrade(alembic_config(connection), "head")

            inspector = inspect(engine)
            with engine.connect() as connection:
                self.assertEqual(current_schema_heads(connection), expected_schema_heads())
                first_fingerprint = schema_fingerprint(connection, schema="private_workbench")
                second_fingerprint = schema_fingerprint(connection, schema="private_workbench")
            self.assertEqual(first_fingerprint, second_fingerprint)
            self.assertEqual(len(first_fingerprint["sha256"]), 64)
            self.assertEqual(set(first_fingerprint["tables"]), PRIVATE_TABLES)
            self.assertTrue(PRIVATE_TABLES.issubset(set(inspector.get_table_names(schema="private_workbench"))))

            cross_schema_foreign_keys = []
            for table in PRIVATE_TABLES:
                for foreign_key in inspector.get_foreign_keys(table, schema="private_workbench"):
                    if foreign_key.get("referred_schema") not in {None, "private_workbench"}:
                        cross_schema_foreign_keys.append((table, foreign_key))
            self.assertEqual(cross_schema_foreign_keys, [])

            with engine.begin() as connection:
                connection.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
                connection.execute(text(f"CREATE ROLE {role_name} NOLOGIN"))
                schema_usage, table_select = connection.execute(
                    text(
                        "SELECT has_schema_privilege(:role, 'private_workbench', 'USAGE'), "
                        "has_table_privilege(:role, 'private_workbench.personal_holdings', 'SELECT')"
                    ),
                    {"role": role_name},
                ).one()
                self.assertFalse(schema_usage)
                self.assertFalse(table_select)
                connection.execute(text(f"DROP ROLE {role_name}"))
        finally:
            engine.dispose()

    def test_synthetic_journey_persists_only_encrypted_business_values(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.begin() as connection:
                for table in (
                    "personal_research_records",
                    "personal_analysis_drafts",
                    "personal_rule_evaluations",
                    "personal_holdings",
                    "personal_workspaces",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            )
            journey = PersonalResearchJourney(
                store=PostgresPersonalJourneyStore(
                    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
                ),
                cipher=cipher,
                adapters=SyntheticWorkspaceAdapters(provider_available=False),
            )
            actor = PersonalActor(actor_id="local-owner")
            trace = journey.create_synthetic_trace(
                actor,
                idempotency_key="pg-trace-001",
                question="PostgreSQL 合成问题正文",
            )
            record = journey.save_synthetic_record(
                actor,
                analysis_id=trace.analysis_id,
                preview_sha256=trace.analysis_preview.preview_sha256,
                idempotency_key="pg-record-001",
            )

            self.assertEqual(journey.open_today(actor).record.record_id, record.record_id)
            with engine.connect() as connection:
                counts = {
                    table: connection.scalar(text(f"SELECT count(*) FROM private_workbench.{table}"))
                    for table in PRIVATE_TABLES
                }
                raw_ciphertexts = b"|".join(
                    bytes(value)
                    for table in PRIVATE_TABLES
                    for value in connection.execute(
                        text(f"SELECT ciphertext FROM private_workbench.{table}")
                    ).scalars()
                )
                database_projection = "|".join(
                    value
                    for table in PRIVATE_TABLES
                    for value in connection.execute(
                        text(f"SELECT to_jsonb(row_value)::text FROM private_workbench.{table} AS row_value")
                    ).scalars()
                )
            self.assertEqual(counts, {table: 1 for table in PRIVATE_TABLES})
            self.assertNotIn(b"SYNTH-001", raw_ciphertexts)
            self.assertNotIn("PostgreSQL 合成问题正文".encode("utf-8"), raw_ciphertexts)
            self.assertNotIn("SYNTH-001", database_projection)
            self.assertNotIn("12.5000", database_projection)
            self.assertNotIn("PostgreSQL 合成问题正文", database_projection)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
