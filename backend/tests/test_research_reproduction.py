from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import FundDailyBar, ResearchRun
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.run_config import FormalRunConfigurationError
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy, SnapshotIntegrityError
from backend.tests.research_test_support import create_golden_database, golden_run_config


class ResearchReproductionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.engine, quality_run_id, universe_hash = create_golden_database(root / "golden.sqlite")
        self.config = golden_run_config(quality_run_id, universe_hash)
        self.output_root = root / "research-runs"

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def _run(self):
        with Session(self.engine) as db:
            return run_quant_research(
                db,
                self.config,
                self.output_root,
                code_commit="golden-test-commit",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

    def test_two_runs_have_byte_identical_deterministic_outputs(self):
        first = self._run()
        second = self._run()
        for name in ("targets.csv.gz", "nav.csv.gz", "metrics.json"):
            with self.subTest(name=name):
                self.assertEqual((first.path / name).read_bytes(), (second.path / name).read_bytes())
        self.assertEqual(first.manifest["resultFingerprint"], second.manifest["resultFingerprint"])
        self.assertEqual(first.manifest["reproducibilityKey"], second.manifest["reproducibilityKey"])
        self.assertNotEqual(first.run_id, second.run_id)

    def test_formal_run_rejects_missing_deployment_commit(self):
        environment = dict(os.environ)
        environment.pop("APP_GIT_COMMIT", None)
        with patch.dict(os.environ, environment, clear=True), Session(self.engine) as db:
            with self.assertRaises(FormalRunConfigurationError):
                run_quant_research(
                    db,
                    self.config,
                    self.output_root,
                    schema_revision="test-schema",
                )

    def test_reproduce_uses_frozen_inputs_after_online_database_changes(self):
        run = self._run()
        with Session(self.engine) as db:
            row = db.scalar(select(FundDailyBar).where(FundDailyBar.trade_date == "2026-01-13"))
            row.close += 20
            db.commit()
        result = reproduce_quant_research(run.path)
        self.assertTrue(result["matches"])

    def test_corrupt_input_fails_before_calculation_and_run_never_succeeds_on_error(self):
        run = self._run()
        input_path = next((run.path / "inputs").glob("*.csv.gz"))
        payload = bytearray(input_path.read_bytes())
        payload[-1] ^= 0x01
        input_path.write_bytes(payload)
        with self.assertRaises(SnapshotIntegrityError):
            reproduce_quant_research(run.path)

        broken = dict(self.config)
        broken["benchmark"] = "MISSING.SH"
        with Session(self.engine) as db:
            with self.assertRaises(Exception):
                run_quant_research(
                    db,
                    broken,
                    self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                )
            failed = db.scalars(select(ResearchRun).where(ResearchRun.status == "failed")).all()
            self.assertTrue(failed)
            self.assertFalse(any(item.status == "succeeded" for item in failed))

    def test_reproduce_rejects_corrupted_archived_outputs_and_audit_files(self):
        for name in ("targets.csv.gz", "nav.csv.gz", "metrics.json", "quality.json", "limitations.json"):
            with self.subTest(name=name):
                run = self._run()
                path = run.path / name
                payload = bytearray(path.read_bytes())
                payload[-1] ^= 0x01
                path.write_bytes(payload)
                with self.assertRaisesRegex(SnapshotIntegrityError, "归档研究产物"):
                    reproduce_quant_research(run.path)


if __name__ == "__main__":
    unittest.main()
