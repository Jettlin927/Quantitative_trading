import unittest

import pandas as pd

from my_quant.strategy_research.experiment.stoploss_trend_filter import run_stoploss_overlay_nav


class StoplossTrendFilterTest(unittest.TestCase):
    def test_stoploss_moves_weight_to_defense_before_next_down_leg(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        prices = pd.DataFrame(
            {
                "510300": [100.0, 100.0, 94.0, 80.0, 80.0],
                "511880": [100.0, 100.0, 100.0, 100.0, 100.0],
            },
            index=dates,
        )

        nav, weights, stats = run_stoploss_overlay_nav(
            prices=prices,
            eval_start="2024-01-01",
            eval_end="2024-01-05",
            rebalance_dates={dates[0]},
            make_weights=lambda _date: {"510300": 1.0},
            cost_rate=0.0,
            stop_loss_pct=0.05,
            defense_asset="511880",
        )

        self.assertAlmostEqual(nav.loc[dates[2]], 0.94)
        self.assertAlmostEqual(nav.loc[dates[-1]], 0.94)
        self.assertAlmostEqual(weights.loc[dates[2], "510300"], 0.0)
        self.assertAlmostEqual(weights.loc[dates[2], "511880"], 1.0)
        self.assertEqual(stats["stop_count"], 1.0)


if __name__ == "__main__":
    unittest.main()
