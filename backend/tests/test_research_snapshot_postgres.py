from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import alembic_config, expected_schema_heads
from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.models import ResearchRun
from backend.app.quant_research.runner import (
    InjectedResearchInterruption,
    mark_stale_research_runs,
    reproduce_quant_research,
    resume_quant_research,
    run_quant_research,
    validate_research_archive,
)
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy, freeze_input_snapshot
from backend.tests.research_test_support import (
    a_share_snapshot_config,
    golden_run_config,
    seed_a_share_snapshot_database,
    seed_golden_database,
)
from backend.tests.test_a_share_price_baseline import (
    _config as a_share_price_config,
    _seed_database as seed_a_share_price_database,
    _synthetic_frames as synthetic_a_share_price_frames,
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

    def test_a_share_run_resumes_archives_and_reproduces_twice_without_database(self):
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
            frames, dates = synthetic_a_share_price_frames()
            with tempfile.TemporaryDirectory() as temporary:
                output_root = Path(temporary)
                with Session(engine) as db:
                    seed_a_share_price_database(db, frames)
                    quality = run_data_quality_check(
                        db,
                        QualityCheckContract.create(
                            scope="a_share_cross_section",
                            start_date=dates[0].date(),
                            end_date=dates[-1].date(),
                            universe=[],
                            universe_type="industry_membership",
                            universe_source="industry_members",
                            universe_source_key="SYNIND.SI",
                            benchmark="SYNIDX.SH",
                        ),
                        code_commit="postgres-a-share-run",
                    )
                    self.assertEqual(quality["status"], "ready")
                    with self.assertRaises(InjectedResearchInterruption):
                        run_quant_research(
                            db,
                            a_share_price_config(quality["qualityRunId"], dates),
                            output_root,
                            code_commit="postgres-a-share-run",
                            schema_revision=expected_schema_heads()[0],
                            test_mode=True,
                            capacity_policy=SnapshotCapacityPolicy(
                                min_remaining_bytes=0
                            ),
                            interrupt_after_stage="simulation",
                        )
                    row = db.scalar(
                        select(ResearchRun).where(ResearchRun.status == "running")
                    )
                    self.assertIsNotNone(row)
                    now = datetime.now(timezone.utc)
                    row.heartbeat_at = now - timedelta(minutes=10)
                    db.commit()
                    self.assertEqual(
                        mark_stale_research_runs(
                            db,
                            output_root,
                            stale_after_seconds=60,
                            now=now,
                        ),
                        [row.run_id],
                    )
                    result = resume_quant_research(
                        db,
                        row.run_id,
                        output_root,
                        code_commit="postgres-a-share-run",
                        schema_revision=expected_schema_heads()[0],
                        test_mode=True,
                        capacity_policy=SnapshotCapacityPolicy(
                            min_remaining_bytes=0
                        ),
                    )
                manifest, _config = validate_research_archive(result.path)
                self.assertEqual(manifest["runId"], result.run_id)
                self.assertEqual(
                    manifest["dataSnapshot"]["transaction"]["isolation"],
                    "REPEATABLE READ",
                )
                self.assertIn(
                    "walk_forward_metrics.csv.gz",
                    manifest["artifactHashes"],
                )
                self.assertIn(
                    "risk_contributions.csv.gz",
                    manifest["artifactHashes"],
                )
                with patch(
                    "backend.app.quant_research.runner.Session",
                    side_effect=AssertionError("database disabled"),
                ):
                    first = reproduce_quant_research(result.path)
                    second = reproduce_quant_research(result.path)
                self.assertTrue(first["matches"])
                self.assertEqual(
                    first["actualResultFingerprint"],
                    second["actualResultFingerprint"],
                )
        finally:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
