from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from scripts.research.render_a_share_b1_report import classify_run


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "a-share-b1-trend-pullback-20260713"
)


class AShareB1ReportTest(unittest.TestCase):
    def test_classifies_only_the_five_preregistered_scenarios(self):
        config_dir = REPO_ROOT / "configs" / "research"
        expected = {
            "a_share_b1_source_period_close_ideal.json": "source_ideal",
            "a_share_b1_source_period_realistic.json": "source_realistic",
            "a_share_b1_long_history.json": "long_primary",
            "a_share_b1_long_history_declared_t3_off.json": "long_t3_off",
            "a_share_b1_long_history_double_cost.json": "long_double_cost",
        }
        primary = None
        for filename, label in expected.items():
            config = json.loads((config_dir / filename).read_text(encoding="utf-8"))
            self.assertEqual(classify_run(config), label)
            if label == "long_primary":
                primary = config

        mutated = deepcopy(primary)
        mutated["featureParameters"]["kdjJThreshold"] = "12"
        with self.assertRaisesRegex(ValueError, "偏离事前登记"):
            classify_run(mutated)

    def test_summary_uses_full_history_and_canonical_results(self):
        summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], "不通过")
        self.assertEqual(summary["initialCapital"], 100_000)
        self.assertEqual(summary["period"]["formalStart"], "2012-06-26")
        self.assertEqual(summary["period"]["formalEnd"], "2026-07-10")
        self.assertEqual(summary["period"]["openDays"], 3411)
        self.assertTrue(summary["period"]["yearRowsAreSubperiods"])
        self.assertEqual(summary["researchDate"], "2026-07-13")
        self.assertEqual(
            summary["reportGeneratedAt"],
            "2026-07-19T04:12:31+08:00",
        )
        self.assertAlmostEqual(summary["primary"]["finalCapital"], 26_648.77802814307)
        self.assertAlmostEqual(summary["primary"]["totalReturn"], -0.7335122197185693)
        self.assertAlmostEqual(summary["primary"]["annualizedReturn"], -0.09310421379148415)
        self.assertAlmostEqual(summary["primary"]["maxDrawdown"], -0.9099473647843379)
        self.assertAlmostEqual(summary["benchmark"]["finalCapital"], 194_743.47176989834)
        self.assertEqual(summary["gateSummary"], {"passed": 1, "failed": 10})

    def test_source_comparison_is_normalized_and_not_claimed_as_exact(self):
        summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))
        source = {row["name"]: row for row in summary["sourceComparison"]}

        self.assertEqual(summary["publicSource"]["numericReplication"], "未数值复现")
        self.assertEqual(summary["publicSource"]["reportedInitialCapital"], 1_000_000)
        self.assertTrue(all(row["initialCapital"] == 100_000 for row in source.values()))
        self.assertAlmostEqual(source["网页机械口径对照"]["totalReturn"], 0.17454911522283156)
        self.assertAlmostEqual(source["同周期现实成交"]["totalReturn"], 0.01330575973582282)
        self.assertAlmostEqual(summary["publicSource"]["totalReturnGap"], 0.8549578847771684)

    def test_walk_forward_execution_and_reproduction_evidence_are_frozen(self):
        summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))
        execution = summary["execution"]["long_primary"]

        self.assertEqual(summary["walkForward"]["windowCount"], 11)
        self.assertEqual(summary["walkForward"]["positiveCount"], 2)
        self.assertEqual(summary["walkForward"]["beatBenchmarkCount"], 3)
        self.assertEqual(execution["requests"], 2887)
        self.assertEqual(execution["filled"], 1145)
        self.assertEqual(execution["partial"], 1342)
        self.assertEqual(execution["blocked"], 400)
        self.assertTrue(summary["reproduction"]["networkDisabled"])
        self.assertTrue(summary["reproduction"]["allMatched"])
        self.assertEqual(summary["reproduction"]["matchedRunCount"], 5)
        self.assertEqual(summary["reproduction"]["matchesPerRun"], 2)
        self.assertEqual(summary["reproductionAudit"]["runCount"], 5)
        self.assertEqual(
            summary["reproductionAudit"]["evidenceFile"],
            "reproduction-evidence-20260719.json",
        )
        self.assertEqual(len(summary["runIdentities"]), 5)
        self.assertIn("strategyLag1Autocorrelation", summary["risk"]["hacAlpha"])
        self.assertEqual(
            {row["runId"] for row in summary["runIdentities"]},
            {
                "fd68d6c7-1338-47ba-8bca-7ccaa9cc3713",
                "74dd5a99-932b-4e00-8197-fe82419c8c15",
                "d13d510b-67df-4a97-97da-8ff387f357db",
                "3d90dcc2-c14a-4af4-acf1-959e6cc4e683",
                "36c194a7-3d45-47ae-9593-ecd46bf29a84",
            },
        )

    def test_html_explains_scenarios_before_results_and_uses_plain_chinese(self):
        html = (REPORT_DIR / "index.html").read_text(encoding="utf-8")

        glossary = html.index("先看懂报告中的五个方案")
        self.assertLess(glossary, html.index("原网页复现出来了吗"))
        self.assertLess(glossary, html.index("长历史总体指标"))
        self.assertLess(html.index("完整正式回测周期"), html.index("逐年稳定性"))
        for name in (
            "网页机械口径对照",
            "同周期现实成交",
            "长历史主版本",
            "页面参数一致性对照",
            "双倍成本压力",
        ):
            self.assertIn(name, html)
        self.assertIn("统一基准本金 <strong>¥100,000</strong>", html)
        self.assertIn("这个策略究竟在做什么", html)
        self.assertIn("事前门禁：仅买入次数接近", html)
        self.assertIn("打开投资科学原始回测页面", html)
        self.assertNotIn(">T0<", html)
        self.assertNotIn("not_available", html)
        self.assertNotIn("not_applicable", html)
        self.assertNotIn("没有一项通过", html)


if __name__ == "__main__":
    unittest.main()
