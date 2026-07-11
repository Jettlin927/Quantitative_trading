from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models import ResearchRun
from backend.app.quant_research.run_config import (
    FormalRunConfigurationError,
    build_reproducibility_key,
    canonical_run_config_sha256,
    canonical_sha256,
    validate_run_config,
)
from backend.tests.research_test_support import golden_run_config


class ResearchRunRegistryTest(unittest.TestCase):
    def test_canonical_config_hash_ignores_dictionary_order(self):
        first = {"b": {"z": 1, "a": 2}, "a": ["x", "y"]}
        second = {"a": ["x", "y"], "b": {"a": 2, "z": 1}}
        self.assertEqual(canonical_sha256(first), canonical_sha256(second))

    def test_formal_identity_requires_commit_snapshot_environment_and_seed(self):
        values = {
            "config_sha256": "a" * 64,
            "data_snapshot_id": "b" * 64,
            "code_commit": "c" * 40,
            "environment_sha256": "d" * 64,
            "random_seed": 0,
        }
        expected = build_reproducibility_key(**values)
        self.assertEqual(expected, build_reproducibility_key(**values))
        for field in ("data_snapshot_id", "code_commit", "environment_sha256", "random_seed"):
            broken = dict(values)
            broken[field] = None
            with self.subTest(field=field), self.assertRaises(FormalRunConfigurationError):
                build_reproducibility_key(**broken)

        for field, replacement in (
            ("config_sha256", "f" * 64),
            ("data_snapshot_id", "1" * 64),
            ("code_commit", "2" * 40),
            ("environment_sha256", "3" * 64),
            ("random_seed", 1),
        ):
            changed = dict(values)
            changed[field] = replacement
            with self.subTest(changed=field):
                self.assertNotEqual(expected, build_reproducibility_key(**changed))

    def test_validate_config_requires_all_research_contract_fields(self):
        config = golden_run_config("quality", "a" * 64)
        normalized = validate_run_config(config)
        self.assertEqual(normalized["timezone"], "Asia/Shanghai")
        for field in (
            "strategyId",
            "strategyVersion",
            "scope",
            "universe",
            "warmupStart",
            "startDate",
            "endDate",
            "benchmark",
            "featureParameters",
            "targetWeightParameters",
            "executionPolicy",
            "costModel",
            "randomSeed",
            "timezone",
            "qualityRunId",
            "allowedWarnings",
        ):
            with self.subTest(field=field):
                broken = dict(config)
                broken.pop(field)
                with self.assertRaises(ValueError):
                    validate_run_config(broken)

    def test_config_identity_excludes_universe_audit_path_but_rejects_absolute_source(self):
        first = golden_run_config("quality", "a" * 64)
        second = deepcopy(first)
        second["universe"]["source"] = "another/worktree/universe.txt"
        second["universe"]["sourceArtifact"]["path"] = "another/worktree/universe.txt"
        self.assertEqual(
            canonical_run_config_sha256(first),
            canonical_run_config_sha256(second),
        )

        absolute = deepcopy(first)
        absolute_path = str(
            (
                Path("backend/tests/fixtures/quant_research_golden/universe.txt")
                .resolve()
            )
        )
        absolute["universe"]["source"] = absolute_path
        absolute["universe"]["sourceArtifact"]["path"] = absolute_path
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            validate_run_config(absolute)

        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                self.assertEqual(
                    validate_run_config(first)["universe"]["members"],
                    ["SYNETF.SZ"],
                )
            finally:
                os.chdir(original_cwd)

    def test_research_run_allows_attempts_with_same_reproducibility_key(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            common = {
                "reproducibility_key": "a" * 64,
                "strategy_id": "sentinel_etf_baseline",
                "status": "running",
                "stage": "quality_gate",
                "config": {},
                "config_sha256": "b" * 64,
                "code_commit": "c" * 40,
                "environment_sha256": "d" * 64,
                "random_seed": 7,
                "artifact_root": "outputs/research-runs/runs",
            }
            with Session(engine) as db:
                db.add_all(
                    [
                        ResearchRun(run_id="attempt-1", **common),
                        ResearchRun(run_id="attempt-2", **common),
                    ]
                )
                db.commit()
                self.assertEqual(db.query(ResearchRun).count(), 2)
        finally:
            engine.dispose()

    def test_0004_migration_has_exact_parent_and_short_revision(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "0004_research_runs.py"
        )
        source = path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "0003_drop_duplicate_indexes"', source)
        namespace: dict[str, object] = {}
        exec(compile(source, str(path), "exec"), namespace)
        self.assertLessEqual(len(str(namespace["revision"])), 32)


if __name__ == "__main__":
    unittest.main()
