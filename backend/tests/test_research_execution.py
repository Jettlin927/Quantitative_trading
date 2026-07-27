from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ResearchRun
from backend.app.quant_research.execution import (
    ExecutionRuntime,
    InterruptedRun,
    RequestRejected,
    ResumeRun,
    StartRun,
    SucceededRun,
    execute,
)
from backend.app.quant_research.runner import (
    InjectedResearchInterruption,
    mark_stale_research_runs,
)
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.tests.research_test_support import create_golden_database, golden_run_config


class ResearchExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine, quality_run_id, universe_hash = create_golden_database(
            self.root / "golden.sqlite"
        )
        self.config = golden_run_config(quality_run_id, universe_hash)
        self.output_root = self.root / "research-runs"

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_start_run_returns_complete_success_identity(self) -> None:
        with Session(self.engine) as db:
            outcome = execute(
                ExecutionRuntime(
                    registry_db=db,
                    output_root=self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                ),
                StartRun(config=self.config),
            )

        self.assertIsInstance(outcome, SucceededRun)
        self.assertEqual(outcome.archive_ref, outcome.path)
        self.assertEqual(
            outcome.reproducibility_key,
            outcome.manifest["reproducibilityKey"],
        )
        self.assertEqual(
            outcome.result_fingerprint,
            outcome.manifest["resultFingerprint"],
        )
        self.assertTrue(outcome.archive_ref.is_dir())

    def test_hard_interruption_resumes_same_identity_with_run_id_only(self) -> None:
        with Session(self.engine) as db:
            runtime = ExecutionRuntime(
                registry_db=db,
                output_root=self.output_root,
                code_commit="golden-test-commit",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                interrupt_after_stage="simulation",
            )
            with self.assertRaises(InjectedResearchInterruption):
                execute(runtime, StartRun(config=self.config))
            row = db.scalar(select(ResearchRun).where(ResearchRun.status == "running"))
            run_id = row.run_id
            now = datetime.now(timezone.utc)
            row.heartbeat_at = now - timedelta(minutes=10)
            db.commit()
            self.assertEqual(
                mark_stale_research_runs(
                    db,
                    self.output_root,
                    stale_after_seconds=60,
                    now=now,
                ),
                [run_id],
            )
            outcome = execute(
                ExecutionRuntime(
                    registry_db=db,
                    output_root=self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                ),
                ResumeRun(run_id=run_id),
            )

        self.assertIsInstance(outcome, SucceededRun)
        self.assertEqual(outcome.run_id, run_id)
        with self.assertRaises(TypeError):
            ResumeRun(run_id=run_id, config=self.config)  # type: ignore[call-arg]

    def test_stop_signal_returns_auditable_interrupted_outcome(self) -> None:
        with Session(self.engine) as db:
            outcome = execute(
                ExecutionRuntime(
                    registry_db=db,
                    output_root=self.output_root,
                    code_commit="golden-test-commit",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                ),
                StartRun(config=self.config),
                stop_signal=lambda: True,
            )

        self.assertIsInstance(outcome, InterruptedRun)
        self.assertNotEqual(outcome.run_id, "unknown")
        self.assertIsNone(outcome.last_stage)
        self.assertTrue(outcome.checkpoint_ref.is_file())

    def test_invalid_start_request_is_rejected_before_creating_a_run(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(RequestRejected) as raised:
                execute(
                    ExecutionRuntime(registry_db=db, output_root=self.output_root),
                    StartRun(config={"strategyId": "missing-contract"}),
                )
            self.assertEqual(raised.exception.category, "request")
            self.assertIsNone(db.scalar(select(ResearchRun)))


if __name__ == "__main__":
    unittest.main()
