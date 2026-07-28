from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import FundDailyBar, ResearchRun
from backend.app.quant_research.runner import (
    _build_primary_benchmark_nav,
    reproduce_quant_research,
    run_quant_research,
)
from backend.app.quant_research.run_config import FormalRunConfigurationError, canonical_json_bytes
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy, SnapshotIntegrityError
from backend.app.quant_research.strategy_registry import resolve_strategy_definition
from backend.app.quant_research.artifacts import (
    atomic_write_json,
    read_canonical_csv_gz,
    write_dataframe_csv_gz,
)
from backend.app.quant_research.baselines import summarize_sentinel_metrics
from backend.app.quant_research.manifest import build_result_fingerprint
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

    def _run(self, config=None):
        with Session(self.engine) as db:
            return run_quant_research(
                db,
                config or self.config,
                self.output_root,
                code_commit="golden-test-commit",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

    def _formal_config(self):
        config = dict(self.config)
        config["validationPolicy"] = {
            "mode": "anchored",
            "trainPeriods": 5,
            "testPeriods": 2,
            "stepPeriods": 2,
        }
        config["evaluationSampleSplits"] = [
            {
                "role": "train",
                "startDate": "2026-01-05",
                "endDate": "2026-01-09",
            },
            {
                "role": "validation",
                "startDate": "2026-01-12",
                "endDate": "2026-01-15",
            },
            {
                "role": "test_oos",
                "startDate": "2026-01-16",
                "endDate": "2026-01-23",
            },
        ]
        config["evaluationPolicy"] = {
            "marketRegime": {
                "directionLookbackPeriods": 2,
                "upThreshold": "0.004",
                "downThreshold": "-0.004",
                "volatilityLookbackPeriods": 2,
                "highVolatilityThreshold": "0.01",
            },
            "costStressMultiplier": "2",
        }
        return config

    def test_two_runs_have_byte_identical_deterministic_outputs(self):
        first = self._run()
        second = self._run()
        for name in (
            "targets.csv.gz",
            "nav.csv.gz",
            "benchmark_nav.csv.gz",
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
            "metrics.json",
            "oos_metrics.json",
        ):
            with self.subTest(name=name):
                self.assertEqual((first.path / name).read_bytes(), (second.path / name).read_bytes())
        self.assertEqual(first.manifest["resultFingerprint"], second.manifest["resultFingerprint"])
        self.assertEqual(first.manifest["reproducibilityKey"], second.manifest["reproducibilityKey"])
        self.assertNotEqual(first.run_id, second.run_id)

    def test_v5_archive_contains_oos_benchmark_ledgers_and_extended_metrics(self):
        run = self._run()
        self.assertEqual(run.manifest["artifactSchemaVersion"], 5)
        for name in (
            "benchmark_nav.csv.gz",
            "oos_metrics.json",
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
        ):
            self.assertTrue((run.path / name).is_file())
            self.assertIn(name, run.manifest["artifactHashes"])
        metrics = json.loads((run.path / "metrics.json").read_text(encoding="utf-8"))
        self.assertIn("sortino", metrics)
        self.assertIn("averageOneWayTurnover", metrics)
        self.assertIn("cumulativeTransactionCostRate", metrics)

    def test_formal_run_freezes_distinct_test_oos_metrics_and_reproduces(self):
        run = self._run(self._formal_config())
        full = json.loads((run.path / "metrics.json").read_text(encoding="utf-8"))
        oos = json.loads(
            (run.path / "oos_metrics.json").read_text(encoding="utf-8")
        )

        self.assertEqual(oos["sampleRole"], "test_oos")
        self.assertEqual(oos["sampleStartDate"], "2026-01-16")
        self.assertEqual(oos["sampleEndDate"], "2026-01-23")
        self.assertNotEqual(oos["startDate"], full["startDate"])
        self.assertNotEqual(oos["totalReturn"], full["totalReturn"])
        self.assertGreaterEqual(
            len(oos["marketRegimes"]["coverage"]["directionStates"]), 2
        )
        self.assertGreaterEqual(
            len(oos["marketRegimes"]["coverage"]["volatilityStates"]), 2
        )
        self.assertTrue(reproduce_quant_research(run.path)["matches"])

    def test_market_reference_benchmark_uses_research_boundary_pre_close(self):
        run = self._run()
        config = dict(self.config)
        config["startDate"] = "2026-01-07"
        benchmark = _build_primary_benchmark_nav(
            resolve_strategy_definition(config),
            run.path / "inputs",
            config,
            compressed=True,
            table_artifacts=run.manifest["dataSnapshot"]["tableArtifacts"],
        )

        self.assertEqual(benchmark.iloc[0]["trade_date"].date().isoformat(), "2026-01-07")
        self.assertAlmostEqual(float(benchmark.iloc[0]["nav"]), 3012 / 3006)
        self.assertNotAlmostEqual(float(benchmark.iloc[0]["nav"]), 3012 / 3000)

    def test_v2_archive_rejects_tampered_ledger_before_recalculation(self):
        for name in (
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
        ):
            with self.subTest(name=name):
                run = self._run()
                path = run.path / name
                payload = bytearray(path.read_bytes())
                payload[-1] ^= 0x01
                path.write_bytes(payload)
                with (
                    patch("backend.app.quant_research.runner._build_strategy_targets") as calculation,
                    self.assertRaisesRegex(SnapshotIntegrityError, "归档研究产物"),
                ):
                    reproduce_quant_research(run.path)
                calculation.assert_not_called()

    def test_v2_archive_rejects_resigned_semantically_invalid_ledger_before_recalculation(self):
        run = self._run()
        name = "rebalance_executions.csv.gz"
        frame = read_canonical_csv_gz(run.path / name)
        frame.loc[0, "status"] = "unexpected"
        artifact = write_dataframe_csv_gz(
            run.path / name,
            frame,
            columns=tuple(frame.columns),
            natural_key=("execution_date", "ts_code"),
        )
        self._resign_v2_artifact(run.path, name, artifact)
        with (
            patch("backend.app.quant_research.runner._build_strategy_targets") as calculation,
            self.assertRaisesRegex(SnapshotIntegrityError, "模拟账本无法对账"),
        ):
            reproduce_quant_research(run.path)
        calculation.assert_not_called()

    def test_completed_v1_archive_still_validates_and_reproduces(self):
        run = self._run()
        self._downgrade_archive_to_v1(run.path)
        result = reproduce_quant_research(run.path)
        self.assertTrue(result["matches"])

    def test_v2_plus_zero_request_archive_keeps_legacy_rate_on_reproduction(self):
        config = dict(self.config)
        config["targetWeightParameters"] = {
            **config["targetWeightParameters"],
            "targetWeight": "0.000000000000001",
        }
        run = self._run(config)
        metrics_path = run.path / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertEqual(metrics["requestCount"], 0)
        self.assertIsNone(metrics["blockedRequestRate"])
        metrics["blockedRequestRate"] = 0.0
        self._resign_metrics_artifact(run.path, metrics)

        self.assertTrue(reproduce_quant_research(run.path)["matches"])

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
        for name in (
            "targets.csv.gz",
            "nav.csv.gz",
            "benchmark_nav.csv.gz",
            "metrics.json",
            "quality.json",
            "limitations.json",
            "oos_metrics.json",
        ):
            with self.subTest(name=name):
                run = self._run()
                path = run.path / name
                payload = bytearray(path.read_bytes())
                payload[-1] ^= 0x01
                path.write_bytes(payload)
                with self.assertRaisesRegex(SnapshotIntegrityError, "归档研究产物"):
                    reproduce_quant_research(run.path)

    def test_reproduce_rejects_tampered_manifest_input_metadata_before_calculation(self):
        run = self._run()
        manifest_path = run.path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_artifact = manifest["artifactHashes"]["inputs/universe.csv.gz"]
        input_artifact["contentSha256"] = "f" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with (
            patch("backend.app.quant_research.runner._build_strategy_targets") as calculation,
            self.assertRaisesRegex(SnapshotIntegrityError, "输入 artifact 元数据"),
        ):
            reproduce_quant_research(run.path)
        calculation.assert_not_called()

    def test_reproduce_rejects_tampered_checkpoint_chain_before_calculation(self):
        run = self._run()
        checkpoint_root = run.path / "checkpoints"
        index_path = checkpoint_root / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        previous_hash = None
        for entry in index["completed"]:
            checkpoint_path = checkpoint_root / f"{entry['stage']}.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["previousCheckpointSha256"] = previous_hash
            if entry["stage"] == "input_snapshot":
                checkpoint["outputs"]["tableArtifacts"]["universe"]["contentSha256"] = "f" * 64
            if entry["stage"] == "features_targets":
                checkpoint["inputs"]["tableContentSha256"]["universe"] = "f" * 64
            payload = canonical_json_bytes(checkpoint) + b"\n"
            checkpoint_path.write_bytes(payload)
            previous_hash = sha256(payload).hexdigest()
            entry["contentSha256"] = previous_hash
        index_path.write_bytes(canonical_json_bytes(index) + b"\n")

        with (
            patch("backend.app.quant_research.runner._build_strategy_targets") as calculation,
            self.assertRaisesRegex(SnapshotIntegrityError, "checkpoint"),
        ):
            reproduce_quant_research(run.path)
        calculation.assert_not_called()

    def _downgrade_archive_to_v1(self, run_path: Path) -> None:
        manifest_path = run_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        config = json.loads((run_path / "config.json").read_text(encoding="utf-8"))
        for name in (
            "benchmark_nav.csv.gz",
            "oos_metrics.json",
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
        ):
            (run_path / name).unlink()
            manifest["artifactHashes"].pop(name)
        nav = read_canonical_csv_gz(run_path / "nav.csv.gz")
        metrics = summarize_sentinel_metrics(
            run_path / "inputs",
            config,
            nav,
            compressed=True,
            table_artifacts=manifest["dataSnapshot"]["tableArtifacts"],
        )
        manifest["artifactHashes"]["metrics.json"] = atomic_write_json(
            run_path / "metrics.json",
            metrics,
        )
        manifest.pop("artifactSchemaVersion")
        manifest["resultFingerprint"] = build_result_fingerprint(manifest["artifactHashes"])
        manifest_artifact = atomic_write_json(manifest_path, manifest)

        checkpoint_root = run_path / "checkpoints"
        index_path = checkpoint_root / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index.pop("artifactSchemaVersion")
        previous_hash = None
        for entry in index["completed"]:
            stage = entry["stage"]
            checkpoint_path = checkpoint_root / f"{stage}.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["previousCheckpointSha256"] = previous_hash
            if stage == "simulation":
                checkpoint["outputs"] = {"nav": checkpoint["outputs"]["nav"]}
            elif stage == "metrics":
                checkpoint["inputs"] = {"nav": checkpoint["inputs"]["nav"]}
                checkpoint["outputs"]["metrics"] = manifest["artifactHashes"]["metrics.json"]
                checkpoint["outputs"].pop("primaryBenchmarkNav")
                checkpoint["outputs"].pop("oosMetrics")
            elif stage == "manifest":
                checkpoint["inputs"] = {
                    "artifactContentSha256": {
                        name: artifact["contentSha256"]
                        for name, artifact in sorted(manifest["artifactHashes"].items())
                    }
                }
                checkpoint["outputs"] = {
                    "manifest": manifest_artifact,
                    "resultFingerprint": manifest["resultFingerprint"],
                }
            elif stage == "finalize":
                checkpoint["inputs"] = {"manifest": manifest_artifact}
                checkpoint["outputs"] = {"resultFingerprint": manifest["resultFingerprint"]}
            checkpoint_artifact = atomic_write_json(checkpoint_path, checkpoint)
            previous_hash = checkpoint_artifact["fileSha256"]
            entry["contentSha256"] = previous_hash
        atomic_write_json(index_path, index)

    def _resign_metrics_artifact(
        self,
        run_path: Path,
        metrics: dict[str, object],
    ) -> None:
        manifest_path = run_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics_artifact = atomic_write_json(run_path / "metrics.json", metrics)
        manifest["artifactHashes"]["metrics.json"] = metrics_artifact
        manifest["resultFingerprint"] = build_result_fingerprint(manifest["artifactHashes"])
        manifest_artifact = atomic_write_json(manifest_path, manifest)

        checkpoint_root = run_path / "checkpoints"
        index_path = checkpoint_root / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        previous_hash = None
        for entry in index["completed"]:
            stage = entry["stage"]
            checkpoint_path = checkpoint_root / f"{stage}.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["previousCheckpointSha256"] = previous_hash
            if stage == "metrics":
                checkpoint["outputs"]["metrics"] = metrics_artifact
            elif stage == "manifest":
                checkpoint["inputs"]["artifactContentSha256"] = {
                    key: value["contentSha256"]
                    for key, value in sorted(manifest["artifactHashes"].items())
                }
                checkpoint["outputs"] = {
                    "manifest": manifest_artifact,
                    "resultFingerprint": manifest["resultFingerprint"],
                }
            elif stage == "finalize":
                checkpoint["inputs"] = {"manifest": manifest_artifact}
                checkpoint["outputs"] = {
                    "resultFingerprint": manifest["resultFingerprint"]
                }
            checkpoint_artifact = atomic_write_json(checkpoint_path, checkpoint)
            previous_hash = checkpoint_artifact["fileSha256"]
            entry["contentSha256"] = previous_hash
        atomic_write_json(index_path, index)

    def _resign_v2_artifact(
        self,
        run_path: Path,
        name: str,
        artifact: dict[str, object],
    ) -> None:
        manifest_path = run_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifactHashes"][name] = artifact
        manifest["resultFingerprint"] = build_result_fingerprint(manifest["artifactHashes"])
        manifest_artifact = atomic_write_json(manifest_path, manifest)

        simulation_key, metrics_key = {
            "rebalance_requests.csv.gz": ("rebalanceRequests", "rebalanceRequests"),
            "rebalance_executions.csv.gz": ("rebalanceExecutions", "rebalanceExecutions"),
            "positions.csv.gz": ("positions", "positions"),
        }[name]
        checkpoint_root = run_path / "checkpoints"
        index_path = checkpoint_root / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        previous_hash = None
        for entry in index["completed"]:
            stage = entry["stage"]
            checkpoint_path = checkpoint_root / f"{stage}.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["previousCheckpointSha256"] = previous_hash
            if stage == "simulation":
                checkpoint["outputs"][simulation_key] = artifact
            elif stage == "metrics":
                checkpoint["inputs"][metrics_key] = artifact
            elif stage == "manifest":
                checkpoint["inputs"]["artifactContentSha256"] = {
                    key: value["contentSha256"]
                    for key, value in sorted(manifest["artifactHashes"].items())
                }
                checkpoint["outputs"] = {
                    "manifest": manifest_artifact,
                    "resultFingerprint": manifest["resultFingerprint"],
                }
            elif stage == "finalize":
                checkpoint["inputs"] = {"manifest": manifest_artifact}
                checkpoint["outputs"] = {
                    "resultFingerprint": manifest["resultFingerprint"]
                }
            checkpoint_artifact = atomic_write_json(checkpoint_path, checkpoint)
            previous_hash = checkpoint_artifact["fileSha256"]
            entry["contentSha256"] = previous_hash
        atomic_write_json(index_path, index)


if __name__ == "__main__":
    unittest.main()
