from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.strategy_results import build_strategy_results_overview


REPO_ROOT = Path(__file__).resolve().parents[2]


class StrategyResultsTest(unittest.TestCase):
    def test_builds_readonly_strategy_result_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results_root = root / "docs" / "research" / "strategy-results"
            results_root.mkdir(parents=True)
            (results_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": "docs/research/strategy-results",
                        "mode": "readonly",
                        "executionEnabled": False,
                        "resultSets": [
                            {
                                "id": "b1",
                                "title": "B1",
                                "artifacts": {
                                    "phasedJson": "phases.json",
                                    "scoreScanCsv": "scan.csv",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (results_root / "phases.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "phase": "a",
                                "annual_return": 0.2,
                                "max_drawdown": -0.1,
                                "passes_return_gate": False,
                                "passes_drawdown_gate": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results_root / "scan.csv").write_text(
                "strategy,annual_return,passes_return_gate\nx,0.1,False\n",
                encoding="utf-8",
            )

            overview = build_strategy_results_overview(root)

        self.assertEqual(overview["mode"], "readonly")
        self.assertFalse(overview["executionEnabled"])
        self.assertEqual(overview["resultSets"][0]["summary"]["phaseCount"], 1)
        self.assertEqual(overview["resultSets"][0]["scoreScanTop"][0]["annual_return"], 0.1)
        self.assertFalse(overview["resultSets"][0]["scoreScanTop"][0]["passes_return_gate"])

    def test_reads_generic_report_summary_without_legacy_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results_root = root / "docs" / "research" / "strategy-results"
            report_root = results_root / "example"
            report_root.mkdir(parents=True)
            (results_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "resultSets": [
                            {
                                "id": "example",
                                "title": "示例报告",
                                "artifacts": {
                                    "reportHtml": "example/index.html",
                                    "summaryJson": "example/summary.json",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (report_root / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "不通过",
                        "reportGeneratedAt": "2026-07-19T03:00:00+08:00",
                        "conclusion": {"oneLine": "核心门禁失败。"},
                    }
                ),
                encoding="utf-8",
            )
            (report_root / "index.html").write_text("<!doctype html>", encoding="utf-8")

            overview = build_strategy_results_overview(root)

        result = overview["resultSets"][0]
        self.assertEqual(result["summary"]["status"], "不通过")
        self.assertEqual(result["summary"]["conclusion"], "核心门禁失败。")
        self.assertEqual(
            result["summary"]["reportGeneratedAt"], "2026-07-19T03:00:00+08:00"
        )
        self.assertEqual(result["phases"], [])
        self.assertEqual(result["scoreScanTop"], [])

    def test_repository_manifest_unifies_current_report_packages(self):
        overview = build_strategy_results_overview(REPO_ROOT)
        result_ids = {item["id"] for item in overview["resultSets"]}

        self.assertTrue(
            {
                "etf-volatility-managed-20260713",
                "etf-trend-120d-long-history-20260713",
                "a-share-b1-trend-pullback-20260713",
            }.issubset(result_ids)
        )
        self.assertTrue(
            (REPO_ROOT / "docs" / "research" / "strategy-results" / "index.html").is_file()
        )


if __name__ == "__main__":
    unittest.main()
