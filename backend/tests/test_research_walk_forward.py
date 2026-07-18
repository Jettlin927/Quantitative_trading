from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from sqlalchemy.orm import Session

from backend.app.quant_research.artifacts import read_canonical_csv_gz
from backend.app.quant_research.runner import (
    reproduce_quant_research,
    run_quant_research,
    validate_research_archive,
)
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy, SnapshotIntegrityError
from backend.app.quant_research.validation import (
    build_walk_forward_window_frame,
    evaluate_walk_forward,
    validate_validation_policy,
)
from backend.tests.research_test_support import (
    create_golden_database,
    golden_run_config,
)


class ResearchWalkForwardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine, self.quality_run_id, self.universe_hash = create_golden_database(
            self.root / "golden.sqlite"
        )

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_policy_is_fixed_and_rejects_tuning_or_overlapping_test_windows(self):
        self.assertEqual(validate_validation_policy(None), {"mode": "none"})
        self.assertEqual(
            validate_validation_policy(
                {
                    "mode": "anchored",
                    "trainPeriods": 5,
                    "testPeriods": 3,
                    "stepPeriods": 3,
                }
            )["mode"],
            "anchored",
        )
        for invalid in (
            {"mode": "grid_search"},
            {
                "mode": "rolling",
                "trainPeriods": 5,
                "testPeriods": 3,
                "stepPeriods": 2,
            },
            {
                "mode": "anchored",
                "trainPeriods": 5,
                "testPeriods": 3,
                "stepPeriods": 3,
                "parameterGrid": {"window": [20, 60]},
            },
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_validation_policy(invalid)

    def test_anchored_and_rolling_windows_keep_train_and_test_disjoint(self):
        dates = pd.bdate_range("2026-01-05", periods=12)
        anchored = build_walk_forward_window_frame(
            dates,
            {
                "mode": "anchored",
                "trainPeriods": 4,
                "testPeriods": 2,
                "stepPeriods": 2,
            },
        )
        rolling = build_walk_forward_window_frame(
            dates,
            {
                "mode": "rolling",
                "trainPeriods": 4,
                "testPeriods": 2,
                "stepPeriods": 2,
            },
        )

        self.assertEqual(anchored["train_start"].nunique(), 1)
        self.assertGreater(rolling["train_start"].nunique(), 1)
        self.assertTrue((anchored["train_end"] < anchored["test_start"]).all())
        self.assertTrue((rolling["train_end"] < rolling["test_start"]).all())

    def test_window_metrics_include_the_first_test_day_from_previous_nav(self):
        dates = pd.bdate_range("2026-01-05", periods=7)
        strategy = pd.DataFrame(
            {
                "trade_date": dates,
                "nav": [1.0, 1.0, 1.0, 1.1, 1.1, 1.21, 1.21],
            }
        )
        benchmark = pd.DataFrame(
            {
                "ts_code": "BENCH.SH",
                "trade_date": dates,
                "close": [100.0, 100.0, 100.0, 120.0, 120.0, 132.0, 132.0],
            }
        )

        _, metrics, _ = evaluate_walk_forward(
            strategy,
            benchmark,
            benchmark="BENCH.SH",
            research_start=dates[0],
            research_end=dates[-1],
            policy={
                "mode": "anchored",
                "trainPeriods": 3,
                "testPeriods": 2,
                "stepPeriods": 2,
            },
        )

        self.assertAlmostEqual(metrics.iloc[0]["total_return"], 0.10)
        self.assertAlmostEqual(metrics.iloc[0]["benchmark_total_return"], 0.20)

    def test_formal_run_archives_only_oos_window_metrics_and_reproduces(self):
        config = golden_run_config(self.quality_run_id, self.universe_hash)
        config["validationPolicy"] = {
            "mode": "anchored",
            "trainPeriods": 5,
            "testPeriods": 3,
            "stepPeriods": 3,
        }
        with Session(self.engine) as db:
            result = run_quant_research(
                db,
                config,
                self.root / "runs",
                code_commit="walk-forward-test",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

        for name in (
            "walk_forward_windows.csv.gz",
            "walk_forward_metrics.csv.gz",
        ):
            self.assertIn(name, result.manifest["artifactHashes"])
        windows = read_canonical_csv_gz(result.path / "walk_forward_windows.csv.gz")
        metrics = read_canonical_csv_gz(result.path / "walk_forward_metrics.csv.gz")
        self.assertEqual(len(windows), 3)
        self.assertEqual(len(metrics), 3)
        self.assertEqual(set(metrics["sample_role"]), {"test_oos"})
        self.assertEqual(
            list(metrics["window_id"]),
            list(windows["window_id"]),
        )
        self.assertTrue(
            (
                pd.to_datetime(metrics["start_date"])
                >= pd.to_datetime(windows["test_start"])
            ).all()
        )
        summary = json.loads((result.path / "metrics.json").read_text(encoding="utf-8"))[
            "walkForward"
        ]
        self.assertEqual(
            summary,
            {
                "mode": "anchored",
                "oosOnly": True,
                "testObservationCount": 9,
                "windowCount": 3,
            },
        )
        self.assertTrue(reproduce_quant_research(result.path)["matches"])

        payload = bytearray((result.path / "walk_forward_metrics.csv.gz").read_bytes())
        payload[-1] ^= 0x01
        (result.path / "walk_forward_metrics.csv.gz").write_bytes(payload)
        with self.assertRaises(SnapshotIntegrityError):
            validate_research_archive(result.path)

    def test_omitted_policy_keeps_legacy_v2_artifact_set(self):
        config = golden_run_config(self.quality_run_id, self.universe_hash)
        with Session(self.engine) as db:
            result = run_quant_research(
                db,
                config,
                self.root / "legacy-v2",
                code_commit="walk-forward-test",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
        self.assertNotIn("validationPolicy", result.manifest["config"])
        self.assertNotIn("walkForward", result.manifest["config"])
        self.assertNotIn(
            "walk_forward_windows.csv.gz",
            result.manifest["artifactHashes"],
        )
        self.assertNotIn("riskPolicy", result.manifest["config"])
        self.assertNotIn(
            "risk_exposures.csv.gz",
            result.manifest["artifactHashes"],
        )
        self.assertTrue(reproduce_quant_research(result.path)["matches"])


if __name__ == "__main__":
    unittest.main()
