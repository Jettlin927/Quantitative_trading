from __future__ import annotations

import unittest

import pandas as pd

from backend.app.research_engine.reports import build_manifest_payload, markdown_table, select_best_candidate


class ResearchEngineReportsTest(unittest.TestCase):
    def test_build_manifest_payload_records_artifacts_and_best_candidate(self):
        base_df = pd.DataFrame(
            [
                {
                    "strategy": "baseline_china_permanent_25_annual_no_cost",
                    "annual_return": 0.05,
                    "calmar": 0.5,
                    "max_drawdown": -0.10,
                }
            ]
        )
        scan_df = pd.DataFrame(
            [
                {
                    "strategy": "ram_top2_m20_v120_f21_cost",
                    "annual_return": 0.12,
                    "calmar": 1.1,
                    "max_drawdown": -0.09,
                }
            ]
        )

        payload = build_manifest_payload(
            latest_price_date="2026-06-15",
            base_df=base_df,
            scan_df=scan_df,
            train_df=scan_df,
            evaluation_start="2021-01-04",
            evaluation_end="2026-06-15",
            train_start="2017-09-01",
            train_end="2020-12-31",
        )

        self.assertEqual(payload["best_research_candidate"], "ram_top2_m20_v120_f21_cost")
        self.assertEqual(payload["base_strategy_rows"], 1)
        self.assertEqual(payload["ram_scan_rows"], 1)
        self.assertEqual(payload["train_scan_rows"], 1)
        self.assertEqual(payload["evaluation_start"], "2021-01-04")
        self.assertIn("latest_summary.md", payload["artifacts"])

    def test_select_best_candidate_falls_back_to_calmar_when_no_row_passes_gate(self):
        base_df = pd.DataFrame(
            [
                {
                    "strategy": "baseline_china_permanent_25_annual_no_cost",
                    "annual_return": 0.10,
                    "calmar": 0.8,
                    "max_drawdown": -0.10,
                }
            ]
        )
        scan_df = pd.DataFrame(
            [
                {"strategy": "high_return_bad_drawdown", "annual_return": 0.20, "calmar": 1.1, "max_drawdown": -0.30},
                {"strategy": "lower_return_best_calmar", "annual_return": 0.08, "calmar": 1.4, "max_drawdown": -0.08},
            ]
        )

        best, gate_text = select_best_candidate(base_df, scan_df)

        self.assertEqual(best["strategy"], "lower_return_best_calmar")
        self.assertIn("没有参数组", gate_text)

    def test_markdown_table_falls_back_when_tabulate_is_missing(self):
        original = pd.DataFrame.to_markdown

        def missing_tabulate(_frame, *args, **kwargs):
            raise ImportError("Missing optional dependency 'tabulate'")

        try:
            pd.DataFrame.to_markdown = missing_tabulate
            table = markdown_table(pd.DataFrame({"annual_return": [0.12345], "strategy": ["unit"]}), index=False, floatfmt=".2f")
        finally:
            pd.DataFrame.to_markdown = original

        self.assertIn("| annual_return | strategy |", table)
        self.assertIn("| 0.12", table)
        self.assertIn("unit", table)


if __name__ == "__main__":
    unittest.main()
