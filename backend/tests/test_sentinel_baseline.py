from __future__ import annotations

from pathlib import Path
import unittest

from backend.app.quant_research.baselines import run_sentinel_etf_baseline
from backend.tests.research_test_support import FIXTURE_DIR, golden_run_config


class SentinelBaselineTest(unittest.TestCase):
    def test_golden_baseline_is_simple_research_only_pipeline_sentinel(self):
        config = golden_run_config("quality", "a" * 64)
        result = run_sentinel_etf_baseline(FIXTURE_DIR, config, compressed=False)
        self.assertEqual(result.targets.iloc[0]["signal_date"].date().isoformat(), "2026-01-09")
        executed = result.nav.dropna(subset=["executed_signal_date"])
        self.assertEqual(executed.iloc[0]["trade_date"].date().isoformat(), "2026-01-12")
        self.assertAlmostEqual(result.metrics["totalReturn"], 0.0588235294117645, places=13)
        self.assertIn("research_only", result.limitations)
        self.assertIn("not_investment_advice", result.limitations)
        self.assertIn("pipeline_sentinel_not_alpha_research", result.limitations)

    def test_baseline_rejects_non_etf_scope_or_parameter_search(self):
        config = golden_run_config("quality", "a" * 64)
        config["scope"] = "a_share_cross_section"
        with self.assertRaises(ValueError):
            run_sentinel_etf_baseline(FIXTURE_DIR, config, compressed=False)

    def test_baseline_rejects_multiple_etfs_or_signal_outside_research_window(self):
        config = golden_run_config("quality", "a" * 64)
        config["universe"]["members"] = ["ANOTHER.SH", "SYNETF.SZ"]
        with self.assertRaisesRegex(ValueError, "一只 ETF"):
            run_sentinel_etf_baseline(FIXTURE_DIR, config, compressed=False)

        config = golden_run_config("quality", "a" * 64)
        config["targetWeightParameters"]["signalDate"] = config["endDate"]
        with self.assertRaisesRegex(ValueError, "研究区间"):
            run_sentinel_etf_baseline(FIXTURE_DIR, config, compressed=False)
        config = golden_run_config("quality", "a" * 64)
        config["featureParameters"] = {"lookbackGrid": [5, 10]}
        with self.assertRaises(ValueError):
            run_sentinel_etf_baseline(FIXTURE_DIR, config, compressed=False)


if __name__ == "__main__":
    unittest.main()
