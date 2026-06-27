import unittest

import pandas as pd

from my_quant.strategy_research.experiment.no_chase import (
    NoChaseConfig,
    build_no_chase_report_html,
    evaluate_no_chase_for_symbol,
    summarize_no_chase_trades,
)


def _synthetic_extended_bars() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=45, freq="B")
    close = [10.0 + index * 0.05 for index in range(45)]
    open_ = close.copy()
    high = [value * 1.01 for value in close]
    low = [value * 0.99 for value in close]

    trigger_index = 24
    close[trigger_index - 5] = 10.2
    close[trigger_index] = 13.0
    open_[trigger_index] = 12.4
    high[trigger_index] = 13.2
    low[trigger_index] = 12.2

    close[trigger_index + 1] = 12.6
    open_[trigger_index + 1] = 13.1
    high[trigger_index + 1] = 13.2
    low[trigger_index + 1] = 12.4

    close[trigger_index + 2] = 11.8
    open_[trigger_index + 2] = 12.0
    high[trigger_index + 2] = 12.1
    low[trigger_index + 2] = 11.6

    close[trigger_index + 3] = 12.05
    open_[trigger_index + 3] = 11.9
    high[trigger_index + 3] = 12.2
    low[trigger_index + 3] = 11.7

    close[trigger_index + 4] = 12.3
    open_[trigger_index + 4] = 12.1
    high[trigger_index + 4] = 12.4
    low[trigger_index + 4] = 11.95

    for index in range(trigger_index + 5, 45):
        close[index] = 12.3 + (index - trigger_index - 4) * 0.08
        open_[index] = close[index - 1] * 1.005
        high[index] = close[index] * 1.01
        low[index] = close[index] * 0.99

    frame = pd.DataFrame(
        {
            "trade_date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pd.Series(close).shift(1),
            "amount": [100000.0] * 45,
        }
    )
    frame.loc[0, "pre_close"] = frame.loc[0, "open"]
    return frame


class NoChaseValidationTest(unittest.TestCase):
    def test_evaluate_no_chase_builds_direct_and_wait_confirmed_trades(self):
        config = NoChaseConfig(
            breakout_window=20,
            min_gap_pct=0.04,
            min_mom5_pct=0.12,
            min_close_ma20_pct=0.05,
            pullback_pct=0.05,
            max_wait_days=5,
            hold_days=5,
            min_amount=0.0,
            round_trip_cost_rate=0.0,
        )

        trades = evaluate_no_chase_for_symbol("600000.SH", _synthetic_extended_bars(), config)

        self.assertEqual(set(trades["group"]), {"direct_all", "direct_matched", "wait_confirmed"})
        wait_trade = trades.loc[trades["group"] == "wait_confirmed"].iloc[0]
        direct_trade = trades.loc[trades["group"] == "direct_matched"].iloc[0]
        self.assertEqual(wait_trade["signal"], "wait_pullback_confirm")
        self.assertGreater(wait_trade["wait_days"], 0)
        self.assertLess(wait_trade["entry_price"], direct_trade["entry_price"])
        self.assertLessEqual(wait_trade["mae"], 0.0)

    def test_summarize_no_chase_trades_reports_missed_confirmations_and_label(self):
        trades = pd.DataFrame(
            [
                {"group": "direct_all", "return": -0.03, "mae": -0.08, "wait_days": 0},
                {"group": "direct_all", "return": 0.02, "mae": -0.02, "wait_days": 0},
                {"group": "direct_matched", "return": -0.03, "mae": -0.08, "wait_days": 0},
                {"group": "wait_confirmed", "return": 0.01, "mae": -0.03, "wait_days": 3},
            ]
        )

        summary = summarize_no_chase_trades(trades)

        self.assertEqual(summary["label"], "只等回调")
        self.assertEqual(summary["trigger_count"], 2)
        self.assertEqual(summary["confirmed_count"], 1)
        self.assertEqual(summary["missed_count"], 1)
        self.assertGreater(summary["mae_improvement"], 0.0)
        self.assertIn("wait_confirmed", summary["groups"])

    def test_build_no_chase_report_html_contains_research_boundaries(self):
        summary = {
            "label": "只等回调",
            "conclusion": "观察",
            "trigger_count": 2,
            "confirmed_count": 1,
            "missed_count": 1,
            "mae_improvement": 0.05,
            "tail_loss_improvement": 0.03,
            "groups": {
                "direct_all": {"trade_count": 2, "avg_return": -0.005, "win_rate": 0.5, "mean_mae": -0.05},
                "wait_confirmed": {"trade_count": 1, "avg_return": 0.01, "win_rate": 1.0, "mean_mae": -0.03},
            },
        }

        html = build_no_chase_report_html(summary, generated_at="2026-06-26 20:00 +08:00")

        self.assertIn("规则 002", html)
        self.assertIn("只等回调", html)
        self.assertIn("A 股验证不能直接证明美股单票收益", html)
        self.assertIn("研究辅助", html)


if __name__ == "__main__":
    unittest.main()
