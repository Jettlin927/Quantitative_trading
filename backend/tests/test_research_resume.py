from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import alembic_config, expected_schema_heads
from backend.app.models import ResearchRun
from backend.app.quant_research.runner import (
    InjectedResearchInterruption,
    ResearchStopRequested,
    ResumeIdentityError,
    ResumeIntegrityError,
    mark_stale_research_runs,
    reproduce_quant_research,
    resume_quant_research,
    run_quant_research,
)
from backend.app.quant_research.artifacts import atomic_write_json
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.tests.research_test_support import (
    create_golden_database,
    golden_run_config,
    seed_golden_database,
)
from scripts.research.run_quant_research import parse_args


class ResearchResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine, quality_run_id, universe_hash = create_golden_database(self.root / "golden.sqlite")
        self.config = golden_run_config(quality_run_id, universe_hash)
        self.output_root = self.root / "research-runs"

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_resume_after_snapshot_skips_snapshot_stage(self):
        run_id = self._interrupt_and_mark("input_snapshot")
        with patch("backend.app.quant_research.runner.freeze_input_snapshot") as snapshot_stage:
            result = self._resume(run_id)
        snapshot_stage.assert_not_called()
        self._assert_succeeded(result)

    def test_orchestrator_stop_at_safe_point_preserves_checkpoint_for_resume(self):
        with Session(self.engine) as db:
            with self.assertRaises(ResearchStopRequested):
                run_quant_research(
                    db,
                    self.config,
                    self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    should_stop=lambda: True,
                )
            row = db.scalar(select(ResearchRun).where(ResearchRun.status == "interrupted"))
            self.assertIsNotNone(row)
            self.assertTrue(self._temporary_path(row.run_id).is_dir())
            recovery = self._temporary_path(row.run_id) / "checkpoints" / "recovery.json"
            self.assertTrue(recovery.is_file())
            run_id = row.run_id

        result = self._resume(run_id)
        self._assert_succeeded(result)

    def test_resume_reuses_registered_snapshot_across_output_roots(self):
        with Session(self.engine) as db:
            first = run_quant_research(
                db,
                self.config,
                self.output_root,
                code_commit="golden-test-commit",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
        self.assertTrue(first.path.is_dir())
        self.output_root = self.root / "alternate-research-runs"
        run_id = self._interrupt_and_mark("input_snapshot")
        result = self._resume(run_id)
        self._assert_succeeded(result)

    def test_resume_after_simulation_skips_completed_computation(self):
        run_id = self._interrupt_and_mark("simulation")
        with (
            patch("backend.app.quant_research.runner.freeze_input_snapshot") as snapshot_stage,
            patch("backend.app.quant_research.runner._build_strategy_targets") as target_stage,
            patch("backend.app.quant_research.runner._simulate_strategy_targets") as simulation_stage,
        ):
            result = self._resume(run_id)
        snapshot_stage.assert_not_called()
        target_stage.assert_not_called()
        simulation_stage.assert_not_called()
        self._assert_succeeded(result)

    def test_resume_after_finalize_only_promotes_verified_archive(self):
        run_id = self._interrupt_and_mark("finalize")
        with (
            patch("backend.app.quant_research.runner.freeze_input_snapshot") as snapshot_stage,
            patch("backend.app.quant_research.runner._build_strategy_targets") as target_stage,
            patch("backend.app.quant_research.runner._simulate_strategy_targets") as simulation_stage,
            patch("backend.app.quant_research.runner._summarize_strategy_metrics") as metrics_stage,
        ):
            result = self._resume(run_id)
        snapshot_stage.assert_not_called()
        target_stage.assert_not_called()
        simulation_stage.assert_not_called()
        metrics_stage.assert_not_called()
        self._assert_succeeded(result)

    def test_resume_rejects_changed_config_code_environment_snapshot_or_repro_key(self):
        run_id = self._interrupt_and_mark("input_snapshot")

        with self.assertRaises(ResumeIdentityError):
            self._resume(run_id, code_commit="different-test-commit")

        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            original_config = dict(row.config)
            row.config = {**original_config, "randomSeed": 99}
            db.commit()
        with self.assertRaises(ResumeIdentityError):
            self._resume(run_id)
        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            row.config = original_config
            original_environment = row.environment_sha256
            row.environment_sha256 = "f" * 64
            db.commit()
        with self.assertRaises(ResumeIdentityError):
            self._resume(run_id)
        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            row.environment_sha256 = original_environment
            original_snapshot = row.data_snapshot_id
            row.data_snapshot_id = None
            db.commit()
        with self.assertRaises(ResumeIdentityError):
            self._resume(run_id)
        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            row.data_snapshot_id = original_snapshot
            original_repro = row.reproducibility_key
            row.reproducibility_key = "e" * 64
            db.commit()
        with self.assertRaises(ResumeIdentityError):
            self._resume(run_id)
        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            row.reproducibility_key = original_repro
            db.commit()
            self.assertEqual(row.status, "interrupted")

    def test_incomplete_v1_archive_cannot_cross_artifact_schema_on_resume(self):
        run_id = self._interrupt_and_mark("simulation")
        index_path = self._temporary_path(run_id) / "checkpoints" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index.pop("artifactSchemaVersion")
        atomic_write_json(index_path, index)
        with self.assertRaisesRegex(ResumeIdentityError, "v1"):
            self._resume(run_id)
        self._assert_still_interrupted(run_id)

    def test_corrupted_checkpoint_stops_without_recalculation(self):
        run_id = self._interrupt_and_mark("simulation")
        checkpoint = self._temporary_path(run_id) / "checkpoints" / "simulation.json"
        payload = bytearray(checkpoint.read_bytes())
        payload[-2] ^= 0x01
        checkpoint.write_bytes(payload)
        with (
            patch("backend.app.quant_research.runner._summarize_strategy_metrics") as metrics_stage,
            self.assertRaises(ResumeIntegrityError),
        ):
            self._resume(run_id)
        metrics_stage.assert_not_called()
        self._assert_still_interrupted(run_id)

    def test_corrupted_completed_archive_stops_without_recalculation(self):
        run_id = self._interrupt_and_mark("simulation")
        nav = self._temporary_path(run_id) / "nav.csv.gz"
        payload = bytearray(nav.read_bytes())
        payload[-1] ^= 0x01
        nav.write_bytes(payload)
        with (
            patch("backend.app.quant_research.runner._summarize_strategy_metrics") as metrics_stage,
            self.assertRaises(ResumeIntegrityError),
        ):
            self._resume(run_id)
        metrics_stage.assert_not_called()
        self._assert_still_interrupted(run_id)

    def test_cli_resume_is_mutually_exclusive_with_new_quality_run(self):
        args = parse_args(["--resume", "run-1"])
        self.assertEqual(args.resume, "run-1")
        self.assertIsNone(args.quality_run_id)
        with self.assertRaises(SystemExit):
            parse_args(["--resume", "run-1", "--quality-run-id", "quality-1"])

    def _interrupt_and_mark(self, stage: str) -> str:
        with Session(self.engine) as db:
            with self.assertRaises(InjectedResearchInterruption):
                run_quant_research(
                    db,
                    self.config,
                    self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    interrupt_after_stage=stage,
                )
            row = db.scalar(
                select(ResearchRun)
                .where(ResearchRun.status == "running")
                .order_by(ResearchRun.started_at.desc())
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.stage, stage)
            self.assertTrue(self._temporary_path(row.run_id).is_dir())
            self.assertFalse((self.output_root / "runs" / row.run_id).exists())
            now = datetime.now(timezone.utc)
            row.heartbeat_at = now - timedelta(minutes=10)
            db.commit()
            marked = mark_stale_research_runs(
                db,
                self.output_root,
                stale_after_seconds=60,
                now=now,
            )
            self.assertEqual(marked, [row.run_id])
            db.refresh(row)
            self.assertEqual(row.status, "interrupted")
            self.assertTrue((self._temporary_path(row.run_id) / "checkpoints" / "recovery.json").is_file())
            return row.run_id

    def _resume(self, run_id: str, *, code_commit: str = "golden-test-commit"):
        with Session(self.engine) as db:
            return resume_quant_research(
                db,
                run_id,
                self.output_root,
                code_commit=code_commit,
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

    def _assert_succeeded(self, result):
        self.assertTrue(result.path.is_dir())
        self.assertFalse(self._temporary_path(result.run_id).exists())
        self.assertTrue(reproduce_quant_research(result.path)["matches"])
        with Session(self.engine) as db:
            row = db.get(ResearchRun, result.run_id)
            self.assertEqual(row.status, "succeeded")
            self.assertEqual(row.stage, "finalized")

    def _assert_still_interrupted(self, run_id: str):
        with Session(self.engine) as db:
            row = db.get(ResearchRun, run_id)
            self.assertEqual(row.status, "interrupted")
            self.assertTrue(self._temporary_path(run_id).is_dir())

    def _temporary_path(self, run_id: str) -> Path:
        return self.output_root / "runs" / f".{run_id}.tmp"


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过研究续跑 PostgreSQL 集成测试")
class ResearchResumePostgresIntegrationTest(unittest.TestCase):
    def test_interrupted_simulation_resumes_on_postgres_registry(self):
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
            with tempfile.TemporaryDirectory() as temporary:
                output_root = Path(temporary)
                with Session(engine) as db:
                    with self.assertRaises(InjectedResearchInterruption):
                        run_quant_research(
                            db,
                            golden_run_config(quality_run_id, universe_hash),
                            output_root,
                            code_commit="postgres-resume-test",
                            schema_revision=expected_schema_heads()[0],
                            test_mode=True,
                            capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                            interrupt_after_stage="simulation",
                        )
                    row = db.scalar(select(ResearchRun).where(ResearchRun.status == "running"))
                    run_id = row.run_id
                    now = datetime.now(timezone.utc)
                    row.heartbeat_at = now - timedelta(minutes=10)
                    db.commit()
                    mark_stale_research_runs(db, output_root, stale_after_seconds=60, now=now)
                    result = resume_quant_research(
                        db,
                        run_id,
                        output_root,
                        code_commit="postgres-resume-test",
                        schema_revision=expected_schema_heads()[0],
                        test_mode=True,
                        capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    )
                self.assertEqual(result.manifest["runId"], run_id)
                self.assertTrue(reproduce_quant_research(result.path)["matches"])
        finally:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
