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
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.tests.research_test_support import golden_run_config, seed_golden_database


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


if __name__ == "__main__":
    unittest.main()
