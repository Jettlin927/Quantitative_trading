from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import alembic_config, expected_schema_heads
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy, freeze_input_snapshot
from backend.tests.research_test_support import (
    a_share_snapshot_config,
    golden_run_config,
    seed_a_share_snapshot_database,
    seed_golden_database,
)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过真实 PostgreSQL 快照集成测试")
class ResearchSnapshotPostgresIntegrationTest(unittest.TestCase):
    def test_repeatable_read_snapshot_registry_and_offline_reproduction(self):
        database_url = os.environ["TEST_POSTGRES_URL"]
        parsed = make_url(database_url)
        self.assertIn(parsed.host, {"127.0.0.1", "localhost"})
        self.assertIn(parsed.database, {"quant_migration_test", "quant_phase3_test"})
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with Session(engine) as db:
                quality_run_id, universe_hash = seed_golden_database(db)
            with tempfile.TemporaryDirectory() as tmp:
                with Session(engine) as db:
                    result = run_quant_research(
                        db,
                        golden_run_config(quality_run_id, universe_hash),
                        Path(tmp),
                        code_commit="postgres-integration",
                        schema_revision=expected_schema_heads()[0],
                        test_mode=True,
                        capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    )
                self.assertEqual(result.manifest["dataSnapshot"]["transaction"]["isolation"], "REPEATABLE READ")
                self.assertTrue(result.manifest["dataSnapshot"]["transaction"]["readOnly"])
                self.assertTrue(reproduce_quant_research(result.path)["matches"])
        finally:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            engine.dispose()

    def test_a_share_snapshot_uses_read_only_repeatable_read_and_daily_membership(self):
        database_url = os.environ["TEST_POSTGRES_URL"]
        parsed = make_url(database_url)
        self.assertIn(parsed.host, {"127.0.0.1", "localhost"})
        self.assertIn(parsed.database, {"quant_migration_test", "quant_phase3_test"})
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with Session(engine) as db:
                contract = seed_a_share_snapshot_database(db)
                quality = run_data_quality_check(
                    db,
                    contract,
                    code_commit="postgres-a-share-snapshot",
                )
                self.assertEqual(quality["status"], "ready")
                with tempfile.TemporaryDirectory() as temporary:
                    snapshot = freeze_input_snapshot(
                        db,
                        a_share_snapshot_config(quality["qualityRunId"]),
                        Path(temporary),
                        capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    )
                    self.assertEqual(
                        snapshot.manifest["transaction"],
                        {
                            "dialect": "postgresql",
                            "isolation": "REPEATABLE READ",
                            "readOnly": True,
                        },
                    )
                    self.assertEqual(snapshot.manifest["rowCounts"]["universe"], 4)
                    self.assertEqual(
                        snapshot.manifest["universeHash"],
                        quality["universeHash"],
                    )
        finally:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
