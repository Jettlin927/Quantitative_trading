from __future__ import annotations

import math
import unittest

from backend.app.research_engine.metrics import calculate_beta, calculate_nav_metrics


class ResearchEngineMetricsTest(unittest.TestCase):
    def test_calculate_nav_metrics_reports_core_risk_numbers(self):
        stats = calculate_nav_metrics([1.0, 1.2, 0.9, 1.35], trading_days=252)

        self.assertAlmostEqual(stats["total_return"], 0.35)
        self.assertAlmostEqual(stats["max_drawdown"], -0.25)
        self.assertTrue(math.isfinite(stats["annual_return"]))
        self.assertTrue(math.isfinite(stats["sharpe"]))
        self.assertTrue(math.isnan(stats["beta"]))
        self.assertTrue(math.isfinite(stats["calmar"]))

    def test_calculate_beta_uses_sample_covariance_against_benchmark(self):
        strategy_returns = [0.10, 0.0909090909, 0.125]
        benchmark_returns = [0.05, 0.0476190476, 0.0909090909]

        beta = calculate_beta(strategy_returns, benchmark_returns)

        avg_strategy = sum(strategy_returns) / len(strategy_returns)
        avg_benchmark = sum(benchmark_returns) / len(benchmark_returns)
        covariance = sum((strategy - avg_strategy) * (benchmark - avg_benchmark) for strategy, benchmark in zip(strategy_returns, benchmark_returns)) / (
            len(strategy_returns) - 1
        )
        variance = sum((value - avg_benchmark) ** 2 for value in benchmark_returns) / (len(benchmark_returns) - 1)
        self.assertAlmostEqual(beta, covariance / variance)


if __name__ == "__main__":
    unittest.main()
