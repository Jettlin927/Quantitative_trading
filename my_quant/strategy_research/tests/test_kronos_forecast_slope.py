import subprocess
import sys
import unittest
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.experiment.kronos_forecast_slope import evaluate_kronos_slope_signal


class KronosForecastSlopeTest(unittest.TestCase):
    def test_hk_forecast_cli_help_does_not_require_kronos_dependencies(self):
        script_path = Path(__file__).resolve().parents[1] / "run_kronos_hk_forecast.py"

        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--kronos-dir", result.stdout)

    def test_positive_forecast_slope_with_safe_downside_is_buy(self):
        stats = pd.DataFrame(
            {
                "median": [100.5 + i * 0.3 for i in range(20)],
                "p10": [99.0 + i * 0.05 for i in range(20)],
            }
        )

        signal = evaluate_kronos_slope_signal(stats, last_close=100.0)

        self.assertEqual(signal.action, "buy")
        self.assertGreater(signal.daily_log_slope, 0.0)
        self.assertGreaterEqual(signal.horizon_return, 0.03)
        self.assertGreaterEqual(signal.downside_return, -0.03)

    def test_positive_slope_without_enough_edge_is_hold(self):
        stats = pd.DataFrame(
            {
                "median": [100.05 + i * 0.03 for i in range(20)],
                "p10": [99.0 for _ in range(20)],
            }
        )

        signal = evaluate_kronos_slope_signal(stats, last_close=100.0)

        self.assertEqual(signal.action, "hold")
        self.assertGreater(signal.daily_log_slope, 0.0)
        self.assertLess(signal.horizon_return, 0.03)

    def test_negative_forecast_slope_is_sell(self):
        stats = pd.DataFrame(
            {
                "median": [99.5 - i * 0.3 for i in range(20)],
                "p10": [98.0 - i * 0.2 for i in range(20)],
            }
        )

        signal = evaluate_kronos_slope_signal(stats, last_close=100.0)

        self.assertEqual(signal.action, "sell")
        self.assertLess(signal.daily_log_slope, 0.0)


if __name__ == "__main__":
    unittest.main()
