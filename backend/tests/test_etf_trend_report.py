from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from scripts.research.render_etf_trend_120d_report import classify_run


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "etf-trend-120d-long-history-20260713"
)


class EtfTrendReportTest(unittest.TestCase):
    def test_classifies_only_the_three_preregistered_cost_scenarios(self):
        config_dir = REPO_ROOT / "configs" / "research"
        expected = {
            "etf_trend_120d_long_history.json": "base_cost",
            "etf_trend_120d_long_history_zero_cost.json": "zero_cost",
            "etf_trend_120d_long_history_double_cost.json": "double_cost",
        }
        base = None
        for filename, label in expected.items():
            config = json.loads((config_dir / filename).read_text(encoding="utf-8"))
            self.assertEqual(classify_run(config), label)
            if label == "base_cost":
                base = config

        invalid = deepcopy(base)
        invalid["costModel"]["slippageRate"] = "0.003"
        with self.assertRaisesRegex(ValueError, "未登记的成本场景"):
            classify_run(invalid)

        disguised_rule = deepcopy(base)
        disguised_rule["targetWeightParameters"]["riskOffWeight"] = "0.1"
        with self.assertRaisesRegex(ValueError, "偏离事前登记"):
            classify_run(disguised_rule)

    def test_report_leads_with_the_full_history_not_one_year_rows(self):
        html = (REPORT_DIR / "index.html").read_text(encoding="utf-8")

        full_period = html.index("完整正式回测周期")
        yearly = html.index("逐年稳定性：每一行都只是子区间")
        self.assertLess(full_period, yearly)
        self.assertIn("这不是一年回测", html)
        self.assertIn("2012-11-19 → 2026-06-29", html)
        self.assertIn("约 <b>13.6 年</b>、3303 个开市日", html)
        self.assertIn("2012和2026明确是边界部分年度", html)
        self.assertNotIn(">T0<", html)
        self.assertNotIn("<th>窗口</th>", html)
        self.assertNotIn("not_available", html)

    def test_summary_uses_one_hundred_thousand_and_canonical_metrics(self):
        summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))
        comparison = {row["labelKey"]: row for row in summary["comparison"]}

        self.assertEqual(summary["status"], "不通过")
        self.assertEqual(summary["initialCapital"], 100_000)
        self.assertEqual(summary["period"]["formalStart"], "2012-11-19")
        self.assertEqual(summary["period"]["formalEnd"], "2026-06-29")
        self.assertEqual(summary["period"]["openDays"], 3303)
        self.assertEqual(summary["period"]["returnObservations"], 3303)
        self.assertTrue(summary["period"]["yearRowsAreSubperiods"])
        self.assertEqual(summary["researchDate"], "2026-07-13")
        self.assertEqual(
            summary["reportGeneratedAt"],
            "2026-07-19T04:05:03+08:00",
        )
        self.assertAlmostEqual(
            comparison["base_cost"]["finalCapital"], 100_682.99208697966
        )
        self.assertAlmostEqual(comparison["base_cost"]["cagr"], 0.0005194473274949818)
        self.assertAlmostEqual(comparison["base_cost"]["maxDrawdown"], -0.52818204070411)
        self.assertAlmostEqual(comparison["passive"]["finalCapital"], 285_097.5)
        self.assertAlmostEqual(comparison["passive"]["totalReturn"], 1.850975)
        self.assertAlmostEqual(comparison["passive"]["cagr"], 0.08321182802874971)
        self.assertAlmostEqual(comparison["static"]["finalCapital"], 173_370.09631649856)
        self.assertEqual(comparison["passive"]["informationRatioDisplay"], "not_applicable")
        self.assertAlmostEqual(summary["targetGap"]["targetCagr"], 0.5)
        self.assertAlmostEqual(summary["targetGap"]["metricYears"], 3303 / 252)
        self.assertEqual(summary["hacAlpha"]["observations"], 3303)
        self.assertIn("strategyLag1Autocorrelation", summary["hacAlpha"])

    def test_report_preserves_cost_reproduction_and_coverage_evidence(self):
        summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))
        yearly = {row["year"]: row for row in summary["yearly"]}

        self.assertEqual(summary["execution"]["signalDecisions"], 163)
        self.assertEqual(summary["execution"]["filledRequests"], 37)
        self.assertEqual(summary["execution"]["blockedRequests"], 0)
        self.assertEqual(summary["gateSummary"], {"passed": 1, "failed": 8})
        self.assertEqual(len(summary["regimes"]), 6)
        self.assertEqual(summary["walkForward"]["windowCount"], 11)
        self.assertEqual(summary["walkForward"]["returnWinCount"], 1)
        self.assertTrue(yearly[2012]["coverage"].startswith("部分年度"))
        self.assertTrue(yearly[2026]["coverage"].startswith("部分年度"))
        self.assertTrue(summary["reproduction"]["allMatched"])
        self.assertTrue(summary["reproduction"]["networkDisabled"])
        self.assertEqual(summary["reproduction"]["baseRepeatedMatches"], 2)
        self.assertEqual(summary["reproduction"]["zeroCostMatches"], 2)
        self.assertEqual(summary["reproduction"]["doubleCostMatches"], 2)
        self.assertEqual(summary["reproductionAudit"]["runCount"], 3)
        self.assertEqual(
            summary["reproductionAudit"]["evidenceFile"],
            "reproduction-evidence-20260719.json",
        )
        self.assertEqual(len(summary["runIdentities"]), 3)
        self.assertEqual(
            {row["runId"] for row in summary["runIdentities"]},
            {
                "73c82e27-754f-4f6a-bc85-4fc43c4b5be3",
                "0e3af953-a064-4db2-beb3-0a84416f6ce8",
                "7d5e9489-78dc-4b32-94a7-b264c16be486",
            },
        )


if __name__ == "__main__":
    unittest.main()
