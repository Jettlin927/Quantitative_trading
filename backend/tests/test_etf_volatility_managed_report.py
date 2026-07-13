from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.research.render_etf_volatility_managed_report import (
    TRIAL_DISPLAY_NAMES,
    TRIAL_GLOSSARY,
    _deflated_sharpe,
    _probability_backtest_overfitting,
    classify_run,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class EtfVolatilityManagedReportTest(unittest.TestCase):
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
        self.assertLess(glossary_position, html.index("0. 结论门禁"))
        self.assertLess(glossary_position, html.index("1. 样本外总体指标"))
        self.assertLess(glossary_position, html.index("T0"))
        self.assertEqual([item["id"] for item in TRIAL_GLOSSARY], ["T0", "T1", "T2", "T3"])
        for label in ("T0", "T1", "T2", "T3"):
            self.assertIn(TRIAL_DISPLAY_NAMES[label], html)
        for bare_label in (">T0 累计净收益<", ">T0<", ">T1<", ">T2<", ">T3<"):
            self.assertNotIn(bare_label, html[:glossary_position])

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

        dsr = _deflated_sharpe(returns, ("T0", "T1", "T2", "T3"))
        pbo = _probability_backtest_overfitting(
            returns,
            ("T0", "T1", "T2", "T3"),
        )

        self.assertTrue(0 <= dsr["probability"] <= 1)
        self.assertTrue(0 <= pbo["probability"] <= 1)
        self.assertEqual(pbo["combinations"], 70)
        self.assertEqual(dsr["trialCount"], 4)


if __name__ == "__main__":
    unittest.main()
