from __future__ import annotations

import unittest

import pandas as pd

from backend.app.research_engine.portfolio import make_ram_topn, normalize_weights


class ResearchEnginePortfolioTest(unittest.TestCase):
    def test_normalize_weights_uses_defense_when_total_is_zero(self):
        weights = normalize_weights({"510300": 0.0, "513100": 0.0}, ["510300", "513100", "511880"], "511880")

        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertEqual(weights["511880"], 1.0)

    def test_normalize_weights_rejects_unknown_symbol(self):
        with self.assertRaisesRegex(ValueError, "Unknown symbol"):
            normalize_weights({"999999": 1.0}, ["510300", "511880"], "511880")

    def test_ram_topn_selects_positive_scores_and_normalizes(self):
        dates = pd.date_range("2024-01-01", periods=6)
        prices = pd.DataFrame(
            {
                "510300": [100, 101, 102, 105, 108, 112],
                "513100": [100, 99, 98, 97, 96, 95],
                "518880": [100, 100, 101, 102, 104, 106],
                "511260": [100, 100, 100, 100, 100, 100],
                "511880": [100, 100, 100, 100, 100, 100],
            },
            index=dates,
            dtype=float,
        )

        make_weights = make_ram_topn(
            prices,
            top_n=2,
            momentum_window=3,
            volatility_window=3,
            risk_assets=["510300", "513100", "518880", "511260"],
            defense_asset="511880",
        )
        weights = make_weights(dates[-1])

        self.assertIn("510300", weights)
        self.assertIn("518880", weights)
        self.assertNotIn("513100", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_ram_topn_uses_defense_when_all_scores_are_negative(self):
        dates = pd.date_range("2024-01-01", periods=6)
        prices = pd.DataFrame(
            {
                "510300": [100, 99, 98, 97, 96, 95],
                "513100": [100, 99, 98, 97, 96, 95],
                "518880": [100, 99, 98, 97, 96, 95],
                "511260": [100, 99, 98, 97, 96, 95],
                "511880": [100, 100, 100, 100, 100, 100],
            },
            index=dates,
            dtype=float,
        )

        make_weights = make_ram_topn(
            prices,
            top_n=2,
            momentum_window=3,
            volatility_window=3,
            risk_assets=["510300", "513100", "518880", "511260"],
            defense_asset="511880",
        )
        weights = make_weights(dates[-1])

        self.assertEqual(weights, {"511880": 1.0})


if __name__ == "__main__":
    unittest.main()
