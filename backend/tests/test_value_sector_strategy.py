from __future__ import annotations

import unittest

import pandas as pd

from backend.app.value_sector_strategy import (
    RULE_VALUE_SECTOR_STOPFALL,
    attach_value_forward_returns,
    build_industry_stopfall_features,
    detect_value_sector_signals,
    simulate_rebalanced_account,
)


class ValueSectorStrategyTest(unittest.TestCase):
    def test_builds_industry_stopfall_after_rebound_from_recent_low(self):
        rows = []
        for day in range(1, 36):
            price = 10 - min(day, 20) * 0.1
            if day > 25:
                price = 8 + (day - 25) * 0.08
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "industry": "测试行业",
                    "trade_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1),
                    "close": price,
                    "bar_return": 0.01 if day > 25 else -0.005,
                }
            )

        features = build_industry_stopfall_features(pd.DataFrame(rows))

        self.assertTrue(bool(features.iloc[-1]["industry_stopfall"]))

    def test_detects_value_signal_only_after_undervaluation_persists(self):
        rows = []
        for day in range(1, 66):
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "industry": "测试行业",
                    "trade_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1),
                    "open": 10,
                    "close": 10,
                    "pe_ttm": 10,
                    "pb": 1,
                    "total_mv": 800_000,
                    "amount": 80_000,
                    "roe": 12,
                    "netprofit_margin": 8,
                    "debt_to_assets": 40,
                    "tr_yoy": 2,
                    "netprofit_yoy": 1,
                    "industry_stopfall": day >= 60,
                    "industry_rebound_20d": 0.04,
                    "industry_ma20_slope_5d": 0.01,
                }
            )
        panel = pd.DataFrame(rows)

        signals = detect_value_sector_signals(panel, min_undervalued_days=45, lookback_days=60, max_per_day=5)

        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals.iloc[0]["rule"], RULE_VALUE_SECTOR_STOPFALL)
        self.assertGreaterEqual(signals.iloc[0]["undervalued_days_60"], 45)

    def test_simulates_rebalanced_account_from_selected_signals(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-01", "open": 10, "close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "close": 11},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-03", "open": 11, "close": 12},
            ]
        )
        signals = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "signal_date": pd.Timestamp("2026-01-01"),
                    "score": 1.0,
                    "rule": RULE_VALUE_SECTOR_STOPFALL,
                }
            ]
        )

        nav = simulate_rebalanced_account(prices, signals, initial_cash=10_000, rebalance_interval=60, max_positions=1)

        self.assertGreater(nav.iloc[-1]["equity"], 11_900)
        self.assertEqual(nav.iloc[0]["selected_count"], 0)
        self.assertEqual(nav.iloc[1]["selected_count"], 1)

    def test_account_curve_carries_last_close_when_position_price_is_missing(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-01", "open": 10, "close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "close": 11},
                {"ts_code": "000002.SZ", "trade_date": "2026-01-03", "open": 5, "close": 5},
            ]
        )
        signals = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "signal_date": pd.Timestamp("2026-01-01"),
                    "score": 1.0,
                    "rule": RULE_VALUE_SECTOR_STOPFALL,
                }
            ]
        )

        nav = simulate_rebalanced_account(prices, signals, initial_cash=10_000, rebalance_interval=60, max_positions=1)

        self.assertGreater(nav.iloc[-1]["equity"], 10_900)

    def test_attaches_long_horizon_returns(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-01", "open": 10, "close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "close": 11},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-03", "open": 11, "close": 12},
            ]
        )
        signals = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "signal_date": pd.Timestamp("2026-01-01"),
                    "rule": RULE_VALUE_SECTOR_STOPFALL,
                }
            ]
        )

        events = attach_value_forward_returns(prices, signals, horizons=(2,), round_trip_cost_rate=0)

        self.assertAlmostEqual(events.iloc[0]["return_2d"], 0.2)


if __name__ == "__main__":
    unittest.main()
