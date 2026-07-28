from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ResearchRun
from backend.app.quant_research.execution import (
    ExecutionRuntime,
    InterruptedRun,
    RequestRejected,
    ResumeRun,
    RunFailed,
    StartRun,
    SucceededRun,
    execute,
)
from backend.app.quant_research.runner import (
    InjectedResearchInterruption,
    mark_stale_research_runs,
)
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.tests.research_test_support import (
    a_share_snapshot_config,
    create_golden_database,
    golden_run_config,
)


class UnsupportedConfigValue:
    pass


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

    def test_concurrent_starts_return_their_own_interrupted_identity(self) -> None:
        second_outcome: InterruptedRun | None = None

        def stop_first_after_second_run_stops() -> bool:
            nonlocal second_outcome
            with Session(self.engine) as second_db:
                outcome = execute(
                    self._runtime(second_db),
                    StartRun(config=self.config),
                    stop_signal=lambda: True,
                )
            self.assertIsInstance(outcome, InterruptedRun)
            second_outcome = outcome
            return True

        with Session(self.engine) as first_db:
            first_outcome = execute(
                self._runtime(first_db),
                StartRun(config=self.config),
                stop_signal=stop_first_after_second_run_stops,
            )

        self.assertIsInstance(first_outcome, InterruptedRun)
        if second_outcome is None:
            self.fail("第二个并发运行未返回中断结果")
        self.assertNotEqual(first_outcome.run_id, second_outcome.run_id)
        self.assertEqual(
            first_outcome.checkpoint_ref.parents[1].name,
            f".{first_outcome.run_id}.tmp",
        )
        self.assertEqual(
            second_outcome.checkpoint_ref.parents[1].name,
            f".{second_outcome.run_id}.tmp",
        )

    def test_resume_stop_reads_checkpoint_from_promoted_directory(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(InjectedResearchInterruption):
                execute(
                    self._runtime(db, interrupt_after_stage="finalize"),
                    StartRun(config=self.config),
                )
            run = db.scalar(select(ResearchRun).where(ResearchRun.status == "running"))
            run_id = run.run_id
            temporary = self.output_root / "runs" / f".{run_id}.tmp"
            promoted = self.output_root / "runs" / run_id
            temporary.replace(promoted)
            now = datetime.now(timezone.utc)
            run.heartbeat_at = now - timedelta(minutes=10)
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
                self._runtime(db),
                ResumeRun(run_id=run_id),
                stop_signal=lambda: True,
            )

        self.assertIsInstance(outcome, InterruptedRun)
        self.assertEqual(outcome.run_id, run_id)
        self.assertEqual(outcome.checkpoint_ref, promoted / "checkpoints" / "recovery.json")
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

    def test_unsupported_nested_config_values_are_request_rejections(self) -> None:
        unsupported_values = (
            {"unexpected"},
            Path("relative/path"),
            UnsupportedConfigValue(),
        )
        with Session(self.engine) as db:
            for value in unsupported_values:
                with self.subTest(value_type=type(value).__name__):
                    config = deepcopy(self.config)
                    config["featureParameters"]["unsupported"] = value
                    with self.assertRaises(RequestRejected) as raised:
                        execute(
                            ExecutionRuntime(
                                registry_db=db,
                                output_root=self.output_root,
                            ),
                            StartRun(config=config),
                        )
                    self.assertEqual(raised.exception.category, "request")
                    self.assertIsInstance(raised.exception.__cause__, TypeError)
            self.assertIsNone(db.scalar(select(ResearchRun)))

    def test_strategy_specific_type_error_is_a_request_rejection(self) -> None:
        config = a_share_snapshot_config(self.config["qualityRunId"])
        config["targetWeightParameters"]["maxWeight"] = None

        with Session(self.engine) as db:
            with self.assertRaises(RequestRejected) as raised:
                execute(
                    ExecutionRuntime(registry_db=db, output_root=self.output_root),
                    StartRun(config=config),
                )
            self.assertEqual(raised.exception.category, "request")
            self.assertIsInstance(raised.exception.__cause__, TypeError)
            self.assertIsNone(db.scalar(select(ResearchRun)))

    def test_pipeline_type_error_remains_an_execution_failure(self) -> None:
        programming_error = TypeError("pipeline programming error")
        with Session(self.engine) as db:
            with patch(
                "backend.app.quant_research.execution._start_quant_research_pipeline",
                side_effect=programming_error,
            ):
                with self.assertRaises(RunFailed) as raised:
                    execute(
                        ExecutionRuntime(
                            registry_db=db,
                            output_root=self.output_root,
                        ),
                        StartRun(config=self.config),
                    )
            self.assertEqual(raised.exception.category, "execution")
            self.assertIs(raised.exception.__cause__, programming_error)

    def _runtime(
        self,
        db: Session,
        *,
        interrupt_after_stage: str | None = None,
    ) -> ExecutionRuntime:
        return ExecutionRuntime(
            registry_db=db,
            output_root=self.output_root,
            code_commit="golden-test-commit",
            schema_revision="test-schema",
            test_mode=True,
            capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            interrupt_after_stage=interrupt_after_stage,
        )


if __name__ == "__main__":
    unittest.main()
