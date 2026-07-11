from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import DataQualityRun, DataSnapshot, FundDailyBar
from backend.app.quant_research.snapshot import (
    SnapshotCapacityPolicy,
    SnapshotCapacityError,
    SnapshotError,
    SnapshotIntegrityError,
    freeze_input_snapshot,
    verify_snapshot,
)
from backend.app.quant_research.artifacts import write_canonical_csv_gz
from backend.app.quant_research.artifacts import read_canonical_csv_gz
from backend.tests.research_test_support import create_golden_database, golden_run_config


class ResearchSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.engine, quality_run_id, universe_hash = create_golden_database(root / "golden.sqlite")
        self.config = golden_run_config(quality_run_id, universe_hash)
        self.snapshot_root = root / "snapshots"
        self.capacity = SnapshotCapacityPolicy(min_remaining_bytes=0)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_same_slice_reuses_same_snapshot_and_old_snapshot_survives_db_change(self):
        with Session(self.engine) as db:
            first = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
            second = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
            self.assertEqual(first.snapshot_id, second.snapshot_id)
            self.assertEqual(
                second.manifest,
                json.loads((second.path / "snapshot.json").read_text(encoding="utf-8")),
            )
            snapshot_text = (second.path / "snapshot.json").read_text(encoding="utf-8")
            self.assertNotIn(str(Path.cwd()), snapshot_text)
            universe = read_canonical_csv_gz(second.path / "inputs" / "universe.csv.gz")
            self.assertEqual(universe.columns.tolist(), ["ts_code"])
            verify_snapshot(first.path)

            row = db.scalar(select(FundDailyBar).where(FundDailyBar.trade_date == "2026-01-13"))
            row.close += 1
            db.commit()
            changed = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)

        self.assertNotEqual(first.snapshot_id, changed.snapshot_id)
        verify_snapshot(first.path)
        verify_snapshot(changed.path)

    def test_same_snapshot_reuses_registered_artifact_root_across_output_roots(self):
        second_root = Path(self.tmp.name) / "other-snapshots"
        with Session(self.engine) as db:
            first = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
            reused = freeze_input_snapshot(db, self.config, second_root, capacity_policy=self.capacity)
            row = db.get(DataSnapshot, first.snapshot_id)

        self.assertTrue(reused.reused)
        self.assertEqual(reused.path, first.path)
        self.assertEqual(row.status, "complete")
        self.assertFalse((second_root / first.snapshot_id).exists())
        verify_snapshot(first.path)

    def test_canonical_writer_rejects_unsorted_or_duplicate_natural_keys(self):
        for rows in (
            [{"ts_code": "B"}, {"ts_code": "A"}],
            [{"ts_code": "A"}, {"ts_code": "A"}],
        ):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "natural_key"):
                write_canonical_csv_gz(
                    Path(self.tmp.name) / "invalid.csv.gz",
                    columns=("ts_code",),
                    rows=rows,
                    natural_key=("ts_code",),
                )

    def test_corrupted_input_is_rejected(self):
        with Session(self.engine) as db:
            snapshot = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
        path = next((snapshot.path / "inputs").glob("*.csv.gz"))
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 0x01
        path.write_bytes(payload)
        with self.assertRaises(SnapshotIntegrityError):
            verify_snapshot(snapshot.path)

    def test_snapshot_rejects_noncanonical_artifact_path(self):
        with Session(self.engine) as db:
            snapshot = freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
        manifest_path = snapshot.path / "snapshot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tableArtifacts"]["universe"]["filename"] = "../universe.csv.gz"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(SnapshotIntegrityError, "文件名非 canonical"):
            verify_snapshot(snapshot.path)

    def test_capacity_gate_blocks_before_export_and_never_deletes_candidates(self):
        candidate = self.snapshot_root / "old-candidate"
        candidate.mkdir(parents=True)
        with Session(self.engine) as db:
            with self.assertRaisesRegex(SnapshotCapacityError, "不会自动删除"):
                freeze_input_snapshot(
                    db,
                    self.config,
                    self.snapshot_root,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=10**30),
                )
            self.assertEqual(db.query(DataSnapshot).count(), 0)
        self.assertTrue(candidate.is_dir())

    def test_forged_ready_quality_row_cannot_open_snapshot_gate(self):
        mutations = (
            ("unverified source", {"universeSourceVerified": False}, None, "keep"),
            ("source issue", {"universeSourceIssue": "missing"}, None, "keep"),
            ("source hash", {"universeSourceSha256": "f" * 64}, None, "keep"),
            ("registry hash", {"universeHash": "f" * 64}, None, "keep"),
            ("summary status", {}, {"status": "blocked"}, "keep"),
            ("summary blockers", {}, {"blockers": [{"ruleId": "forged"}]}, "keep"),
            ("unfinished", {}, None, None),
        )
        with Session(self.engine) as db:
            row = db.get(DataQualityRun, self.config["qualityRunId"])
            original_config = dict(row.config)
            original_summary = dict(row.summary)
            original_finished_at = row.finished_at
            for label, config_update, summary_update, finished_at in mutations:
                with self.subTest(label=label):
                    row.config = {**original_config, **config_update}
                    row.summary = {**original_summary, **(summary_update or {})}
                    row.finished_at = (
                        original_finished_at
                        if finished_at == "keep"
                        else finished_at
                    )
                    db.commit()
                    with self.assertRaisesRegex(
                        SnapshotError,
                        "质量运行|universe",
                    ):
                        freeze_input_snapshot(
                            db,
                            self.config,
                            self.snapshot_root,
                            capacity_policy=self.capacity,
                        )
            row.config = original_config
            row.summary = original_summary
            row.finished_at = original_finished_at
            db.commit()

    def test_finalize_failure_leaves_registry_failed_not_complete(self):
        with Session(self.engine) as db:
            with patch("backend.app.quant_research.snapshot._atomic_promote", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    freeze_input_snapshot(db, self.config, self.snapshot_root, capacity_policy=self.capacity)
            failed = db.scalar(select(DataSnapshot).where(DataSnapshot.status == "failed"))
            self.assertIsNotNone(failed)
            self.assertFalse((self.snapshot_root / failed.snapshot_id).exists())


if __name__ == "__main__":
    unittest.main()
