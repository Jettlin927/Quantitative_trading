from __future__ import annotations

from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.app.research_analytics import (
    HistoricalAnalyticsError,
    build_historical_source_analytics,
    read_historical_run_chart_series,
)
from backend.app.research_history_migration import load_history_source
from backend.app.models import ResearchRun
from backend.app.quant_research.manifest import build_result_fingerprint


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "configs" / "research-history-migration-v1.json"


class HistoricalResearchAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = load_history_source(REPO_ROOT, CONTRACT_PATH)

    def test_current_trustworthy_history_exposes_normalized_metrics_and_regimes(self) -> None:
        expected = {
            "etf_volatility_managed": {
                "totalReturn": 0.5255223001673612,
                "benchmarkTotalReturn": 0.4218047418736801,
                "primaryRunId": "f24663b1-4160-465f-b9e8-ea295c2407a0",
            },
            "etf_low_volatility_gate": {
                "totalReturn": 0.2903515156027765,
                "benchmarkTotalReturn": 0.21090237093684006,
                "primaryRunId": "251662f5-def5-4228-9330-e68e13a47748",
            },
            "etf_trend_120d": {
                "totalReturn": 0.006829920869796613,
                "benchmarkTotalReturn": 1.8509749999999991,
                "primaryRunId": "73c82e27-754f-4f6a-bc85-4fc43c4b5be3",
            },
            "a_share_b1_trend_pullback": {
                "totalReturn": -0.7335122197185693,
                "benchmarkTotalReturn": 0.9474347176989835,
                "primaryRunId": "d13d510b-67df-4a97-97da-8ff387f357db",
            },
        }
        by_strategy = {item.strategy_id: item for item in self.source.current_research}

        for strategy_id, values in expected.items():
            source = by_strategy[strategy_id]
            summary_path = REPO_ROOT / source.summary_uri.removeprefix("repo://")
            payload = summary_path.read_bytes()
            analytics = build_historical_source_analytics(
                strategy_id,
                json.loads(payload),
                source_uri=source.summary_uri,
                source_sha256=sha256(payload).hexdigest(),
            )

            with self.subTest(strategy_id=strategy_id):
                self.assertEqual(analytics["dataStatus"], "complete")
                self.assertAlmostEqual(
                    analytics["metrics"]["totalReturn"], values["totalReturn"]
                )
                self.assertAlmostEqual(
                    analytics["metrics"]["benchmarkTotalReturn"],
                    values["benchmarkTotalReturn"],
                )
                self.assertEqual(analytics["primaryRunId"], values["primaryRunId"])
                self.assertTrue(analytics["yearly"])
                self.assertTrue(analytics["regimes"])
                self.assertEqual(analytics["provenance"]["sha256"], sha256(payload).hexdigest())
                self.assertGreaterEqual(len(analytics["comparisons"]), 2)

        volatility = build_historical_source_analytics(
            "etf_volatility_managed",
            json.loads(
                (
                    REPO_ROOT
                    / by_strategy["etf_volatility_managed"].summary_uri.removeprefix(
                        "repo://"
                    )
                ).read_text(encoding="utf-8")
            ),
            source_uri=by_strategy["etf_volatility_managed"].summary_uri,
            source_sha256=by_strategy["etf_volatility_managed"].summary_sha256,
        )
        self.assertAlmostEqual(
            volatility["metrics"]["averageOneWayTurnover"],
            0.006197712755435061,
        )
        self.assertAlmostEqual(
            volatility["metrics"]["cumulativeOneWayTurnover"],
            21.20857304909878,
        )
        self.assertAlmostEqual(volatility["metrics"]["var95"], 0.01292668827761822)
        self.assertAlmostEqual(volatility["metrics"]["maximumWeight"], 1.0)
        self.assertAlmostEqual(
            volatility["metrics"]["averageHhi"], 0.6755228131589232
        )
        self.assertEqual(volatility["capacity"]["status"], "not_available")
        self.assertEqual(
            volatility["capacity"]["reason"],
            "未绑定目标资金规模与冲击模型",
        )
        self.assertEqual(volatility["robustness"]["walkForward"]["status"], "complete")
        self.assertEqual(volatility["robustness"]["walkForward"]["windowCount"], 6)
        self.assertEqual(volatility["robustness"]["dsr"]["status"], "complete")
        self.assertEqual(volatility["robustness"]["pbo"]["status"], "complete")

    def test_missing_benchmark_is_not_reported_as_complete(self) -> None:
        source = next(
            item
            for item in self.source.current_research
            if item.strategy_id == "etf_volatility_managed"
        )
        summary_path = REPO_ROOT / source.summary_uri.removeprefix("repo://")
        summary = deepcopy(json.loads(summary_path.read_text(encoding="utf-8")))
        passive = next(
            item for item in summary["comparison"] if item["label"] == "被动 ETF"
        )
        del passive["totalReturn"]

        analytics = build_historical_source_analytics(
            source.strategy_id,
            summary,
            source_uri=source.summary_uri,
            source_sha256=source.summary_sha256,
        )

        self.assertEqual(analytics["dataStatus"], "not_available")
        self.assertEqual(
            analytics["metricAvailability"]["benchmarkTotalReturn"]["status"],
            "not_available",
        )
        self.assertIn(
            "匹配基准累计收益",
            analytics["metricAvailability"]["benchmarkTotalReturn"]["reason"],
        )

    def test_unknown_historical_strategy_is_not_inferred(self) -> None:
        with self.assertRaisesRegex(HistoricalAnalyticsError, "没有冻结适配器"):
            build_historical_source_analytics(
                "legacy_unknown",
                {"comparison": [{"totalReturn": 9.9}]},
                source_uri="repo://unknown.json",
                source_sha256="f" * 64,
            )

    def test_historical_v2_chart_requires_frozen_file_and_result_hashes(self) -> None:
        with TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            nav_path = root / "nav.csv.gz"
            with gzip.open(nav_path, "wt", encoding="utf-8", newline="") as handle:
                handle.write(
                    "trade_date,nav,one_way_turnover,transaction_cost_rate,"
                    "gross_exposure,cash_weight\n"
                    "2025-01-02,1.0,0.0,0.0,0.0,1.0\n"
                    "2025-01-03,0.9,0.5,0.01,0.5,0.5\n"
                )
            (root / "targets.csv.gz").write_bytes(b"targets")
            (root / "metrics.json").write_text("{}", encoding="utf-8")
            with gzip.open(nav_path, "rb") as handle:
                nav_content_sha256 = sha256(handle.read()).hexdigest()
            artifact_hashes = {
                name: {
                    "fileSha256": sha256((root / name).read_bytes()).hexdigest(),
                    "contentSha256": (
                        nav_content_sha256
                        if name == "nav.csv.gz"
                        else sha256(f"content:{name}".encode()).hexdigest()
                    ),
                }
                for name in ("targets.csv.gz", "nav.csv.gz", "metrics.json")
            }
            result_fingerprint = build_result_fingerprint(artifact_hashes)
            run = ResearchRun(
                run_id="11111111-1111-4111-8111-111111111111",
                strategy_id="etf_trend_120d",
                status="succeeded",
                stage="finalized",
                config={},
                config_sha256="a" * 64,
                code_commit="b" * 40,
                environment_sha256="c" * 64,
                random_seed=7,
                reproducibility_key="d" * 64,
                result_fingerprint=result_fingerprint,
                artifact_root=str(root),
            )
            manifest = {
                "schemaVersion": 2,
                "artifactSchemaVersion": 2,
                "runId": run.run_id,
                "strategyId": run.strategy_id,
                "codeCommit": run.code_commit,
                "configSha256": run.config_sha256,
                "randomSeed": run.random_seed,
                "reproducibilityKey": run.reproducibility_key,
                "resultFingerprint": run.result_fingerprint,
                "environment": {"sha256": run.environment_sha256},
                "dataSnapshot": {"snapshotId": None},
                "artifactHashes": artifact_hashes,
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            projection = read_historical_run_chart_series(run)

            self.assertEqual(projection["chartSeries"]["nav"][-1]["value"], 0.9)
            self.assertAlmostEqual(
                projection["chartSeries"]["drawdown"][-1]["value"],
                -0.1,
            )
            self.assertEqual(
                projection["chartSeries"]["cumulativeTurnover"][-1]["value"],
                0.5,
            )
            self.assertEqual(projection["artifactSchemaVersion"], 2)

            with gzip.open(nav_path, "at", encoding="utf-8") as handle:
                handle.write("2025-01-04,1.1,0.0,0.0,0.5,0.5\n")
            with self.assertRaisesRegex(HistoricalAnalyticsError, "SHA-256"):
                read_historical_run_chart_series(run)


if __name__ == "__main__":
    unittest.main()
