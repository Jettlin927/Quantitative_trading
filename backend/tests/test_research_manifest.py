from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from backend.app.quant_research.manifest import (
    build_environment_fingerprint,
    build_research_manifest,
    build_result_fingerprint,
)


class ResearchManifestTest(unittest.TestCase):
    def test_environment_records_runtime_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text("pandas==2.3.3\n", encoding="utf-8")
            environment = build_environment_fingerprint(
                schema_revision="0004_research_runs",
                requirements_path=requirements,
                code_commit="abc123",
                python_version="3.12.test",
                build_identifier="image:test",
            )
        self.assertEqual(environment["schemaRevision"], "0004_research_runs")
        self.assertEqual(environment["pythonVersion"], "3.12.test")
        self.assertEqual(environment["timezone"], "Asia/Shanghai")
        self.assertEqual(environment["appGitCommit"], "abc123")
        self.assertEqual(len(environment["dependenciesSha256"]), 64)
        self.assertEqual(len(environment["sha256"]), 64)

    def test_result_fingerprint_excludes_run_metadata(self):
        artifacts = {
            "targets.csv.gz": {"contentSha256": "a" * 64},
            "nav.csv.gz": {"contentSha256": "b" * 64},
            "metrics.json": {"contentSha256": "c" * 64},
        }
        expected = build_result_fingerprint(artifacts)
        common = {
            "reproducibility_key": "d" * 64,
            "strategy_id": "sentinel_etf_baseline",
            "config": {"a": 1},
            "config_sha256": "e" * 64,
            "data_snapshot": {"snapshotId": "f" * 64},
            "quality_run": {"qualityRunId": "quality", "status": "ready"},
            "universe": {"members": ["SYNETF.SZ"]},
            "random_seed": 7,
            "environment": {"sha256": "1" * 64, "appGitCommit": "abc123"},
            "limitations": [],
            "artifact_hashes": artifacts,
        }
        first = build_research_manifest(
            run_id="run-one",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            **common,
        )
        second = build_research_manifest(
            run_id="run-two",
            generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            **common,
        )
        self.assertEqual(first["resultFingerprint"], expected)
        self.assertEqual(second["resultFingerprint"], expected)
        self.assertNotEqual(first["runId"], second["runId"])
        self.assertTrue(first["boundaries"]["researchOnly"])
        self.assertTrue(first["boundaries"]["notInvestmentAdvice"])
        self.assertFalse(first["boundaries"]["executionEnabled"])

    def test_v2_result_fingerprint_requires_all_three_ledger_artifacts(self):
        artifacts = {
            "targets.csv.gz": {"contentSha256": "a" * 64},
            "nav.csv.gz": {"contentSha256": "b" * 64},
            "metrics.json": {"contentSha256": "c" * 64},
            "rebalance_requests.csv.gz": {"contentSha256": "d" * 64},
            "rebalance_executions.csv.gz": {"contentSha256": "e" * 64},
            "positions.csv.gz": {"contentSha256": "f" * 64},
        }
        fingerprint = build_result_fingerprint(artifacts)
        changed = {name: dict(value) for name, value in artifacts.items()}
        changed["positions.csv.gz"]["contentSha256"] = "0" * 64
        self.assertNotEqual(fingerprint, build_result_fingerprint(changed))
        del changed["rebalance_executions.csv.gz"]
        with self.assertRaisesRegex(ValueError, "账本"):
            build_result_fingerprint(changed)

    def test_result_fingerprint_requires_both_risk_artifacts(self):
        artifacts = {
            "targets.csv.gz": {"contentSha256": "a" * 64},
            "nav.csv.gz": {"contentSha256": "b" * 64},
            "metrics.json": {"contentSha256": "c" * 64},
            "risk_exposures.csv.gz": {"contentSha256": "d" * 64},
            "risk_contributions.csv.gz": {"contentSha256": "e" * 64},
        }
        fingerprint = build_result_fingerprint(artifacts)
        changed = {name: dict(value) for name, value in artifacts.items()}
        changed["risk_contributions.csv.gz"]["contentSha256"] = "0" * 64
        self.assertNotEqual(fingerprint, build_result_fingerprint(changed))
        del changed["risk_exposures.csv.gz"]
        with self.assertRaisesRegex(ValueError, "风险"):
            build_result_fingerprint(changed)

    def test_deployment_injects_commit_and_uses_cross_release_artifact_volume(self):
        repo_root = Path(__file__).resolve().parents[2]
        dockerfile = (repo_root / "backend" / "Dockerfile").read_text(encoding="utf-8")
        compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
        deploy = (repo_root / "scripts" / "ops" / "deploy_server.sh").read_text(encoding="utf-8")
        self.assertIn("ARG APP_GIT_COMMIT", dockerfile)
        self.assertIn("ENV APP_GIT_COMMIT", dockerfile)
        self.assertIn("research_artifacts:/app/outputs/research-runs", compose)
        self.assertIn("RESEARCH_ARTIFACT_VOLUME:-quant_research_artifacts", compose)
        self.assertIn("set_deploy_identity", deploy)


if __name__ == "__main__":
    unittest.main()
