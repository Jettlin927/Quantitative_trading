from __future__ import annotations

import unittest

import pandas as pd

from backend.app.ma_strategy_stats import (
    RULE_FILTERED_TREND_ENTRY,
    RULE_FILTERED_TREND_ENTRY_TOP10,
    RULE_TREND_FOLLOWING,
    RULE_TREND_REVERSAL,
    TradeCost,
    attach_forward_returns,
    detect_filtered_trend_entry_signals,
    detect_ma_signals,
    select_daily_top_signals,
    simulate_horizon_portfolio,
    summarize_event_returns,
)


class MaStrategyStatsTest(unittest.TestCase):
    def test_detects_trend_following_only_when_three_day_confirmation_first_appears(self):
        rows = [
            self.row("000001.SZ", "2026-01-01", ma5=106, ma20=100, ma60=95),
            self.row("000001.SZ", "2026-01-02", ma5=106, ma20=100, ma60=95),
            self.row("000001.SZ", "2026-01-05", ma5=107, ma20=100, ma60=95),
            self.row("000001.SZ", "2026-01-06", ma5=108, ma20=100, ma60=95),
        ]

        signals = detect_ma_signals(pd.DataFrame(rows))

        trend_following = signals[signals["rule"] == RULE_TREND_FOLLOWING]
        self.assertEqual(len(trend_following), 1)
        self.assertEqual(str(trend_following.iloc[0]["signal_date"].date()), "2026-01-05")

    def test_detects_trend_reversal_when_ma5_crosses_up_above_ma20_and_ma60(self):
        rows = [
            self.row("000001.SZ", "2026-01-01", ma5=99, ma20=100, ma60=90),
            self.row("000001.SZ", "2026-01-02", ma5=101, ma20=100, ma60=90),
            self.row("000002.SZ", "2026-01-01", ma5=99, ma20=100, ma60=100),
            self.row("000002.SZ", "2026-01-02", ma5=101, ma20=100, ma60=100.5),
        ]

        signals = detect_ma_signals(pd.DataFrame(rows))

        trend_reversal = signals[signals["rule"] == RULE_TREND_REVERSAL]
        self.assertEqual(len(trend_reversal), 1)
        self.assertEqual(trend_reversal.iloc[0]["ts_code"], "000001.SZ")
        self.assertEqual(str(trend_reversal.iloc[0]["signal_date"].date()), "2026-01-02")

    def test_attaches_forward_returns_from_next_open_to_horizon_close(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-01", "open": 9.5, "close": 10.0},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10.0, "close": 10.5},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "open": 10.6, "close": 11.0},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-06", "open": 11.1, "close": 12.0},
            ]
        )
        signals = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "signal_date": pd.Timestamp("2026-01-01"),
                    "rule": RULE_TREND_REVERSAL,
                }
            ]
        )

        events = attach_forward_returns(prices, signals, horizons=(2,), round_trip_cost_rate=0)

        self.assertEqual(str(events.iloc[0]["entry_date"].date()), "2026-01-02")
        self.assertEqual(str(events.iloc[0]["exit_2d_date"].date()), "2026-01-05")
        self.assertAlmostEqual(events.iloc[0]["return_2d"], 0.10)

    def test_summarizes_event_returns_by_rule_and_horizon(self):
        events = pd.DataFrame(
            [
                {"rule": RULE_TREND_REVERSAL, "return_5d": 0.10},
                {"rule": RULE_TREND_REVERSAL, "return_5d": -0.02},
                {"rule": RULE_TREND_FOLLOWING, "return_5d": 0.03},
            ]
        )

        summary = summarize_event_returns(events, horizons=(5,))

        reversal = summary[(summary["rule"] == RULE_TREND_REVERSAL) & (summary["horizon"] == 5)].iloc[0]
        self.assertEqual(reversal["event_count"], 2)
        self.assertAlmostEqual(reversal["mean_return"], 0.04)
        self.assertAlmostEqual(reversal["win_rate"], 0.5)

    def test_detects_filtered_trend_entry_when_all_filters_first_align(self):
        rows = []
        for day in range(1, 76):
            trade_date = pd.Timestamp("2026-03-01") + pd.Timedelta(days=day - 1)
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "open": 10 + day * 0.02,
                    "high": 10 + day * 0.02,
                    "low": 10 + day * 0.02,
                    "close": 10 + day * 0.02,
                    "pre_close": 10 + (day - 1) * 0.02,
                    "amount": 80_000,
                    "market_filter_pass": True,
                    "market_regime": "risk_on",
                }
            )
        panel = pd.DataFrame(rows)
        grouped = panel.groupby("ts_code", sort=False)
        panel["ma5"] = grouped["close"].transform(lambda series: series.rolling(5, min_periods=5).mean())
        panel["ma20"] = grouped["close"].transform(lambda series: series.rolling(20, min_periods=20).mean())
        panel["ma60"] = grouped["close"].transform(lambda series: series.rolling(60, min_periods=60).mean())
        panel["amount_ma20"] = grouped["amount"].transform(lambda series: series.rolling(20, min_periods=20).mean())

        signals = detect_filtered_trend_entry_signals(panel, cooldown_days=20, min_amount_20d=50_000)

        self.assertGreaterEqual(len(signals), 1)
        self.assertTrue((signals["rule"] == RULE_FILTERED_TREND_ENTRY).all())
        self.assertLessEqual(signals.iloc[0]["close_ma20_distance"], 0.08)

    def test_filtered_trend_entry_rejects_chasing_far_above_ma20(self):
        rows = []
        for day in range(1, 61):
            trade_date = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1)
            close = 10.0
            if day >= 58:
                close = 12.0
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "pre_close": 10.0,
                    "amount": 80_000,
                    "market_filter_pass": True,
                    "market_regime": "risk_on",
                }
            )
        panel = pd.DataFrame(rows)
        grouped = panel.groupby("ts_code", sort=False)
        panel["ma5"] = grouped["close"].transform(lambda series: series.rolling(5, min_periods=5).mean())
        panel["ma20"] = grouped["close"].transform(lambda series: series.rolling(20, min_periods=20).mean())
        panel["ma60"] = grouped["close"].transform(lambda series: series.rolling(60, min_periods=60).mean())
        panel["amount_ma20"] = grouped["amount"].transform(lambda series: series.rolling(20, min_periods=20).mean())

        signals = detect_filtered_trend_entry_signals(panel, max_close_ma20_distance=0.08, min_amount_20d=50_000)

        self.assertTrue(signals.empty)

    def test_selects_daily_top_signals_by_strength_score(self):
        signals = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "signal_date": "2026-01-02",
                    "rule": RULE_FILTERED_TREND_ENTRY,
                    "ma_spread": 0.02,
                    "close_ma20_distance": 0.02,
                    "ma20_slope_5d": 0.01,
                    "ma60_slope_10d": 0.0,
                    "amount_ma20": 50_000,
                },
                {
                    "ts_code": "000002.SZ",
                    "signal_date": "2026-01-02",
                    "rule": RULE_FILTERED_TREND_ENTRY,
                    "ma_spread": 0.04,
                    "close_ma20_distance": 0.01,
                    "ma20_slope_5d": 0.03,
                    "ma60_slope_10d": 0.01,
                    "amount_ma20": 100_000,
                },
            ]
        )

        selected = select_daily_top_signals(signals, max_per_day=1)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected.iloc[0]["ts_code"], "000002.SZ")
        self.assertEqual(selected.iloc[0]["rule"], RULE_FILTERED_TREND_ENTRY_TOP10)

    def test_simulates_fixed_horizon_portfolio_with_board_lot_execution(self):
        events = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "rule": RULE_TREND_REVERSAL,
                    "entry_date": "2026-01-02",
                    "entry_open": 10.0,
                    "exit_5d_date": "2026-01-08",
                    "exit_5d_close": 11.0,
                }
            ]
        )

        result = simulate_horizon_portfolio(
            events,
            horizon=5,
            initial_cash=10_000,
            target_position_pct=1,
            trade_cost=TradeCost(buy_cost_rate=0, sell_cost_rate=0, slippage_rate=0),
        )

        self.assertEqual(result["trade_count"], 1)
        self.assertAlmostEqual(result["ending_equity"], 11_000)
        self.assertAlmostEqual(result["total_return"], 0.10)

    def test_simulates_portfolio_with_volume_capacity_limit(self):
        events = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "rule": RULE_TREND_REVERSAL,
                    "entry_date": "2026-01-02",
                    "entry_open": 10.0,
                    "entry_vol": 10,
                    "exit_5d_date": "2026-01-08",
                    "exit_5d_close": 11.0,
                }
            ]
        )

        result = simulate_horizon_portfolio(
            events,
            horizon=5,
            initial_cash=10_000,
            target_position_pct=1,
            volume_capacity_pct=0.05,
            trade_cost=TradeCost(buy_cost_rate=0, sell_cost_rate=0, slippage_rate=0),
        )

        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["skipped_count"], 1)

    @staticmethod
    def row(ts_code: str, trade_date: str, ma5: float, ma20: float, ma60: float) -> dict[str, object]:
        return {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "open": ma5,
            "close": ma5,
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
            "market_filter_pass": True,
        }


if __name__ == "__main__":
    unittest.main()
