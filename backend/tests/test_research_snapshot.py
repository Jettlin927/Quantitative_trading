from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import DataQualityResult, DataQualityRun, DataSnapshot, FundDailyBar
from backend.app.quant_research.snapshot import (
    SnapshotCapacityPolicy,
    SnapshotCapacityError,
    SnapshotError,
    SnapshotIntegrityError,
    freeze_input_snapshot,
    verify_snapshot,
)
from backend.app.quant_research.artifacts import NULL_VALUE, canonical_cell, read_canonical_csv_gz, write_canonical_csv_gz
from backend.app.quant_research.run_config import canonical_sha256
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

    def test_canonical_null_is_unambiguous_in_round_trip_and_hash(self):
        null_path = Path(self.tmp.name) / "null.csv.gz"
        empty_path = Path(self.tmp.name) / "empty.csv.gz"
        null_artifact = write_canonical_csv_gz(
            null_path,
            columns=("id", "note"),
            rows=[{"id": "A", "note": None}],
            natural_key=("id",),
        )
        empty_artifact = write_canonical_csv_gz(
            empty_path,
            columns=("id", "note"),
            rows=[{"id": "A", "note": ""}],
            natural_key=("id",),
        )

        self.assertNotEqual(null_artifact["contentSha256"], empty_artifact["contentSha256"])
        self.assertTrue(pd.isna(read_canonical_csv_gz(null_path).iloc[0]["note"]))
        self.assertEqual(read_canonical_csv_gz(empty_path).iloc[0]["note"], "")
        self.assertEqual(canonical_cell(None), NULL_VALUE)
        with self.assertRaisesRegex(ValueError, "null 哨兵"):
            write_canonical_csv_gz(
                Path(self.tmp.name) / "literal-sentinel.csv.gz",
                columns=("id", "note"),
                rows=[{"id": "A", "note": NULL_VALUE}],
                natural_key=("id",),
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
            (
                "required dataset drift",
                {"requiredDatasets": ["stock_financial_indicators"]},
                None,
                "keep",
            ),
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

    def test_forged_ready_run_cannot_hide_blocked_or_failed_persisted_result(self):
        with Session(self.engine) as db:
            result = db.scalar(
                select(DataQualityResult).where(
                    DataQualityResult.run_id == self.config["qualityRunId"]
                )
            )
            for status in ("blocked", "failed"):
                with self.subTest(status=status):
                    result.status = status
                    db.commit()
                    with self.assertRaisesRegex(SnapshotError, "质量.*明细|质量.*一致"):
                        freeze_input_snapshot(
                            db,
                            self.config,
                            self.snapshot_root,
                            capacity_policy=self.capacity,
                        )
                    result.status = "passed"
                    db.commit()

    def test_quality_gate_rejects_missing_results_or_summary_count_drift(self):
        with Session(self.engine) as db:
            run = db.get(DataQualityRun, self.config["qualityRunId"])
            original_summary = dict(run.summary)
            run.summary = {**original_summary, "resultCount": 2}
            db.commit()
            with self.assertRaisesRegex(SnapshotError, "质量.*明细|质量.*一致"):
                freeze_input_snapshot(
                    db,
                    self.config,
                    self.snapshot_root,
                    capacity_policy=self.capacity,
                )

            run.summary = original_summary
            result = db.scalar(
                select(DataQualityResult).where(DataQualityResult.run_id == run.id)
            )
            db.delete(result)
            db.commit()
            with self.assertRaisesRegex(SnapshotError, "质量.*明细"):
                freeze_input_snapshot(
                    db,
                    self.config,
                    self.snapshot_root,
                    capacity_policy=self.capacity,
                )

    def test_snapshot_transaction_contract_is_validated_and_bound_to_identity(self):
        with Session(self.engine) as db:
            snapshot = freeze_input_snapshot(
                db,
                self.config,
                self.snapshot_root,
                capacity_policy=self.capacity,
            )
            row = db.get(DataSnapshot, snapshot.snapshot_id)
            manifest_path = snapshot.path / "snapshot.json"
            original = manifest_path.read_bytes()
            mutations = (
                ("dialect", "forged"),
                ("isolation", "READ COMMITTED"),
                ("readOnly", True),
            )
            for field, value in mutations:
                with self.subTest(field=field):
                    manifest = json.loads(original.decode("utf-8"))
                    manifest["transaction"][field] = value
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    with self.assertRaisesRegex(SnapshotIntegrityError, "transaction|snapshotId"):
                        freeze_input_snapshot(
                            db,
                            self.config,
                            self.snapshot_root,
                            capacity_policy=self.capacity,
                        )
                    db.refresh(row)
                    self.assertEqual(row.status, "failed")
                    manifest_path.write_bytes(original)
                    row.status = "complete"
                    db.commit()

    def test_legacy_v1_snapshot_identity_remains_verifiable(self):
        with Session(self.engine) as db:
            snapshot = freeze_input_snapshot(
                db,
                self.config,
                self.snapshot_root,
                capacity_policy=self.capacity,
            )
        manifest = json.loads(
            (snapshot.path / "snapshot.json").read_text(encoding="utf-8")
        )
        legacy_identity = {
            "scope": manifest["scope"],
            "warmupStart": manifest["warmupStart"],
            "startDate": manifest["startDate"],
            "endDate": manifest["endDate"],
            "benchmark": manifest["benchmark"],
            "universeHash": manifest["universeHash"],
            "transaction": manifest["transaction"],
            "tableArtifacts": {
                name: {
                    "contentSha256": artifact["contentSha256"],
                    "columns": artifact["columns"],
                    "naturalKey": artifact["naturalKey"],
                    "rowCount": artifact["rowCount"],
                }
                for name, artifact in sorted(manifest["tableArtifacts"].items())
            },
        }
        legacy_id = canonical_sha256(legacy_identity)
        legacy_path = snapshot.path.with_name(legacy_id)
        snapshot.path.rename(legacy_path)
        manifest["schemaVersion"] = 1
        manifest["snapshotId"] = legacy_id
        manifest.pop("requiredDatasets")
        (legacy_path / "snapshot.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        verified = verify_snapshot(legacy_path)

        self.assertEqual(verified["schemaVersion"], 1)
        self.assertNotIn("requiredDatasets", verified)

    def test_reuse_rejects_valid_but_different_transaction_identity_from_registry(self):
        with Session(self.engine) as db:
            snapshot = freeze_input_snapshot(
                db,
                self.config,
                self.snapshot_root,
                capacity_policy=self.capacity,
            )
            row = db.get(DataSnapshot, snapshot.snapshot_id)
            manifest = json.loads((snapshot.path / "snapshot.json").read_text(encoding="utf-8"))
            manifest["transaction"] = {
                "dialect": "postgresql",
                "isolation": "REPEATABLE READ",
                "readOnly": True,
            }
            forged_identity = {
                "scope": manifest["scope"],
                "warmupStart": manifest["warmupStart"],
                "startDate": manifest["startDate"],
                "endDate": manifest["endDate"],
                "benchmark": manifest["benchmark"],
                "universeHash": manifest["universeHash"],
                "requiredDatasets": manifest["requiredDatasets"],
                "transaction": manifest["transaction"],
                "tableArtifacts": {
                    name: {
                        "contentSha256": artifact["contentSha256"],
                        "columns": artifact["columns"],
                        "naturalKey": artifact["naturalKey"],
                        "rowCount": artifact["rowCount"],
                    }
                    for name, artifact in sorted(manifest["tableArtifacts"].items())
                },
            }
            forged_id = canonical_sha256(forged_identity)
            forged_path = snapshot.path.with_name(forged_id)
            snapshot.path.rename(forged_path)
            manifest["snapshotId"] = forged_id
            (forged_path / "snapshot.json").write_text(json.dumps(manifest), encoding="utf-8")
            row.artifact_root = str(forged_path)
            db.commit()

            with self.assertRaisesRegex(SnapshotIntegrityError, "registry"):
                freeze_input_snapshot(
                    db,
                    self.config,
                    self.snapshot_root,
                    capacity_policy=self.capacity,
                )
            db.refresh(row)
            self.assertEqual(row.status, "failed")

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
