from __future__ import annotations

import unittest

import pandas as pd

from backend.app.quant_research.metrics import (
    summarize_execution_metrics,
    summarize_performance,
)


class ResearchMetricsExtendedTest(unittest.TestCase):
    def test_extended_performance_adds_downside_drawdown_duration_and_beta(self):
        dates = pd.bdate_range("2026-01-05", periods=6)
        nav = pd.DataFrame({"trade_date": dates, "nav": [1.0, 1.02, 0.99, 0.98, 1.01, 1.03]})
        benchmark = pd.DataFrame({"trade_date": dates, "nav": [1.0, 1.01, 1.0, 0.99, 1.0, 1.02]})
        base = summarize_performance(nav, benchmark)
        extended = summarize_performance(nav, benchmark, include_extended=True)
        self.assertNotIn("sortino", base)
        self.assertIn("downsideVolatility", extended)
        self.assertIn("sortino", extended)
        self.assertEqual(extended["maxDrawdownDuration"], 3)
        self.assertIsNotNone(extended["beta"])

    def test_execution_metrics_reconcile_turnover_cost_positions_and_statuses(self):
        dates = pd.bdate_range("2026-01-05", periods=3)
        nav = pd.DataFrame(
            {
                "trade_date": dates,
                "nav": [1.0, 0.999, 1.01],
                "cash_weight": [1.0, 0.5, 0.4],
                "one_way_turnover": [0.0, 0.5, 0.1],
                "transaction_cost_rate": [0.0, 0.001, 0.0],
            }
        )
        requests = pd.DataFrame(
            [
                {
                    "execution_date": dates[1],
                    "signal_date": dates[0],
                    "ts_code": "AAA.SZ",
                    "requested_change": 0.5,
                    "side": "buy",
                },
                {
                    "execution_date": dates[2],
                    "signal_date": dates[1],
                    "ts_code": "BBB.SH",
                    "requested_change": 0.2,
                    "side": "buy",
                },
            ]
        )
        executions = pd.DataFrame(
            [
                {
                    **requests.iloc[0].to_dict(),
                    "executed_change": 0.5,
                    "blocked_change": 0.0,
                    "status": "filled",
                    "reason": "",
                    "transaction_cost_rate": 0.001,
                },
                {
                    **requests.iloc[1].to_dict(),
                    "executed_change": 0.1,
                    "blocked_change": 0.1,
                    "status": "partial",
                    "reason": "cash_capacity",
                    "transaction_cost_rate": 0.0,
                },
            ]
        )
        positions = pd.DataFrame(
            [
                {"trade_date": dates[1], "ts_code": "AAA.SZ", "close_weight": 0.5},
                {"trade_date": dates[2], "ts_code": "AAA.SZ", "close_weight": 0.5},
                {"trade_date": dates[2], "ts_code": "BBB.SH", "close_weight": 0.1},
            ]
        )
        metrics = summarize_execution_metrics(nav, requests, executions, positions)
        self.assertEqual(metrics["maxHoldingCount"], 2)
        self.assertAlmostEqual(metrics["averageHoldingCount"], 1.0)
        self.assertEqual(metrics["partialRequestRate"], 0.5)
        self.assertEqual(metrics["blockedRequestRate"], 0.0)
        self.assertAlmostEqual(metrics["cumulativeBlockedChange"], 0.1)
        self.assertAlmostEqual(metrics["cumulativeTransactionCostRate"], 0.001)
        self.assertAlmostEqual(metrics["maxSingleWeight"], 0.5)

        broken = positions.copy()
        broken.loc[0, "close_weight"] = 0.6
        with self.assertRaisesRegex(ValueError, "现金和持仓"):
            summarize_execution_metrics(nav, requests, executions, broken)

        broken_status = executions.copy()
        broken_status.loc[1, "reason"] = "limit_up"
        with self.assertRaisesRegex(ValueError, "status、reason"):
            summarize_execution_metrics(nav, requests, broken_status, positions)

        outside_requests = requests.copy()
        outside_executions = executions.copy()
        outside_date = dates[-1] + pd.offsets.BDay(1)
        outside_requests.loc[1, "execution_date"] = outside_date
        outside_executions.loc[1, "execution_date"] = outside_date
        with self.assertRaisesRegex(ValueError, "日期不属于 NAV 交易日"):
            summarize_execution_metrics(
                nav, outside_requests, outside_executions, positions
            )


if __name__ == "__main__":
    unittest.main()
