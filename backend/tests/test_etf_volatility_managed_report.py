from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from backend.app.quant_research.reporting import (
    deflated_sharpe,
    probability_backtest_overfitting,
    summarize_return_subperiod,
)
from scripts.research.render_etf_volatility_managed_report import (
    TRIAL_DISPLAY_NAMES,
    TRIAL_GLOSSARY,
    classify_gate_run,
    classify_run,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class EtfVolatilityManagedReportTest(unittest.TestCase):
    def test_subperiod_drawdown_includes_the_first_return_from_initial_wealth(self):
        metrics = summarize_return_subperiod(
            pd.Series([-0.10, 0.05]),
            pd.Series([-0.20, 0.10]),
        )

        self.assertAlmostEqual(metrics["totalReturn"], -0.055)
        self.assertAlmostEqual(metrics["benchmarkTotalReturn"], -0.12)
        self.assertAlmostEqual(metrics["maxDrawdown"], -0.10)

    def test_report_explains_internal_trial_ids_before_results(self):
        report_path = (
            REPO_ROOT
            / "docs"
            / "research"
            / "strategy-results"
            / "etf-volatility-managed-20260713"
            / "index.html"
        )
        html = report_path.read_text(encoding="utf-8")

        glossary_position = html.index("先看懂报告中的四个试验")
        self.assertLess(glossary_position, html.index("原始策略结论门禁"))
        self.assertLess(glossary_position, html.index("原始策略样本外总体指标"))
        self.assertLess(glossary_position, html.index("T0"))
        self.assertEqual([item["id"] for item in TRIAL_GLOSSARY], ["T0", "T1", "T2", "T3"])
        for label in ("T0", "T1", "T2", "T3"):
            self.assertIn(TRIAL_DISPLAY_NAMES[label], html)
        for bare_label in (">T0 累计净收益<", ">T0<", ">T1<", ">T2<", ">T3<"):
            self.assertNotIn(bare_label, html[:glossary_position])

    def test_report_uses_one_hundred_thousand_yuan_initial_capital(self):
        report_dir = (
            REPO_ROOT
            / "docs"
            / "research"
            / "strategy-results"
            / "etf-volatility-managed-20260713"
        )
        html = (report_dir / "index.html").read_text(encoding="utf-8")
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))

        self.assertIn("统一基准本金 ¥100,000", html)
        self.assertIn("策略期末资产</span><b>¥129,035", html)
        self.assertIn("累计盈亏</span><b>¥29,035", html)
        self.assertIn("高点到谷底损失：-¥83,749", html)
        self.assertLess(
            html.index("沪深300 ETF 低波动准入策略"),
            html.index("附录：原始 ETF 波动率管理策略复现"),
        )
        self.assertEqual(summary["initialCapital"], 100_000)
        followup = summary["lowVolatilityGateFollowup"]
        self.assertEqual(followup["initialCapital"], 100_000)
        self.assertTrue(
            all(row["initialCapital"] == 100_000 for row in followup["comparison"])
        )

    def test_low_volatility_report_includes_required_cost_and_robustness_evidence(self):
        report_dir = (
            REPO_ROOT
            / "docs"
            / "research"
            / "strategy-results"
            / "etf-volatility-managed-20260713"
        )
        html = (report_dir / "index.html").read_text(encoding="utf-8")
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        followup_html = html[: html.index("附录：原始 ETF 波动率管理策略复现")]
        followup = summary["lowVolatilityGateFollowup"]

        for heading in (
            "累计单边换手",
            "累计成本率",
            "多重试验与过拟合",
            "支持证据",
            "反对证据",
            "尚缺证据",
        ):
            self.assertIn(heading, followup_html)
        self.assertEqual(followup["multipleTesting"]["trialCount"], 5)
        self.assertEqual(len(followup["supportingEvidence"]), 3)
        self.assertEqual(len(followup["opposingEvidence"]), 3)
        self.assertEqual(len(followup["missingEvidence"]), 3)

    def test_failed_report_does_not_promote_any_variant_to_candidate(self):
        report_dir = (
            REPO_ROOT
            / "docs"
            / "research"
            / "strategy-results"
            / "etf-volatility-managed-20260713"
        )
        html = (report_dir / "index.html").read_text(encoding="utf-8")
        summary_text = (report_dir / "summary.json").read_text(encoding="utf-8")

        for forbidden in (
            "作为唯一下一轮候选",
            "当前只能是有条件候选",
            "结论上限为有条件候选",
        ):
            self.assertNotIn(forbidden, html)
            self.assertNotIn(forbidden, summary_text)

    def test_classifies_only_preregistered_trials_and_cost_scenarios(self):
        configs = {
            "T0": "etf_volatility_managed_baseline.json",
            "T1": "etf_volatility_managed_inverse_volatility.json",
            "T2": "etf_volatility_managed_smoothed_variance.json",
            "T3": "etf_volatility_managed_rebalance_band.json",
        }
        loaded = {}
        for expected, filename in configs.items():
            config = json.loads(
                (REPO_ROOT / "configs" / "research" / filename).read_text(
                    encoding="utf-8"
                )
            )
            loaded[expected] = config
            self.assertEqual(classify_run(config), expected)

        zero_cost = deepcopy(loaded["T0"])
        zero_cost["costModel"] = {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        }
        self.assertEqual(classify_run(zero_cost), "zero_cost")
        double_cost = deepcopy(loaded["T0"])
        double_cost["costModel"] = {
            "buyRate": "0.0007",
            "sellRate": "0.0017",
            "slippageRate": "0.002",
        }
        self.assertEqual(classify_run(double_cost), "double_cost")

        invalid = deepcopy(loaded["T0"])
        invalid["costModel"]["slippageRate"] = "0.003"
        with self.assertRaisesRegex(ValueError, "成本场景"):
            classify_run(invalid)

        disguised_zero_cost = deepcopy(loaded["T1"])
        disguised_zero_cost["costModel"] = {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        }
        with self.assertRaisesRegex(ValueError, "偏离事前登记"):
            classify_run(disguised_zero_cost)

    def test_classifies_low_volatility_gate_cost_scenarios(self):
        base = json.loads(
            (REPO_ROOT / "configs" / "research" / "etf_low_volatility_gate.json").read_text(
                encoding="utf-8"
            )
        )
        double = json.loads(
            (
                REPO_ROOT
                / "configs"
                / "research"
                / "etf_low_volatility_gate_double_cost.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(classify_gate_run(base), "base_cost")
        self.assertEqual(classify_gate_run(double), "double_cost")

        invalid = deepcopy(base)
        invalid["costModel"]["slippageRate"] = "0.003"
        with self.assertRaisesRegex(ValueError, "低波动准入未登记的成本场景"):
            classify_gate_run(invalid)

        disguised_threshold = deepcopy(base)
        disguised_threshold["featureParameters"]["thresholdMethod"] = (
            "calibration_60th_percentile"
        )
        with self.assertRaisesRegex(ValueError, "偏离事前登记"):
            classify_gate_run(disguised_threshold)

    def test_multiple_testing_diagnostics_are_finite_probabilities(self):
        dates = pd.bdate_range("2018-01-02", periods=2057)
        phase = np.arange(len(dates), dtype=float)
        common = 0.00025 + 0.008 * np.sin(phase / 11)
        returns = pd.DataFrame(
            {
                "trade_date": dates,
                "T0": common,
                "T1": common * 0.9 + 0.00003,
                "T2": common * 0.8 - 0.00002,
                "T3": common * 0.95,
            }
        )

        dsr = deflated_sharpe(returns, ("T0", "T1", "T2", "T3"))
        pbo = probability_backtest_overfitting(
            returns,
            ("T0", "T1", "T2", "T3"),
        )

        self.assertTrue(0 <= dsr["probability"] <= 1)
        self.assertTrue(0 <= pbo["probability"] <= 1)
        self.assertEqual(pbo["combinations"], 70)
        self.assertEqual(dsr["trialCount"], 4)


if __name__ == "__main__":
    unittest.main()
