from __future__ import annotations

from datetime import date, timedelta
import unittest

from backend.app.b1_strategy import (
    build_b1_config,
    build_b1_market_frame_from_rows,
    filter_mainboard_stock_codes,
    rows_to_b1_panel,
)
from backend.app.schemas import B1BacktestRequest


def make_rows(days: int, start: date = date(2025, 1, 1), close_start: float = 10.0, step: float = 0.05):
    rows = []
    for index in range(days):
        trade_date = start + timedelta(days=index)
        close = close_start + step * index
        rows.append(
            (
                trade_date.isoformat(),
                close * 0.99,
                close * 1.02,
                close * 0.98,
                close,
                1000.0,
            )
        )
    return rows


class B1StrategyAdapterTest(unittest.TestCase):
    def test_default_backend_config_uses_realistic_execution(self):
        config = build_b1_config({})

        self.assertEqual(config.initial_cash, 20_000.0)
        self.assertEqual(config.top_n, 1)
        self.assertEqual(config.max_position, 1.0)
        self.assertEqual(config.buy_price_column, "open")
        self.assertEqual(config.sell_price_column, "close")
        self.assertEqual(config.lot_size, 100)
        self.assertEqual(config.limit_up_pct, 0.10)
        self.assertEqual(config.limit_down_pct, 0.10)
        self.assertTrue(config.require_affordable_lot)
        self.assertEqual(config.stop_loss_pct, 0.05)
        self.assertEqual(config.take_profit_levels, (0.05,))
        self.assertEqual(config.take_profit_fractions, (1.0,))

    def test_filter_mainboard_stock_codes_excludes_permission_boards(self):
        codes = ["000001.SZ", "002130.SZ", "300750.SZ", "688981.SH", "830799.BJ", "600519.SH"]

        self.assertEqual(filter_mainboard_stock_codes(codes), ["000001.SZ", "002130.SZ", "600519.SH"])

    def test_b1_request_defaults_enable_mainboard_style_gate(self):
        payload = B1BacktestRequest(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))

        self.assertTrue(payload.exclude_permission_boards)
        self.assertTrue(payload.use_mainboard_style_gate)
        self.assertEqual(payload.style_gate_min_above_bbi_pct, 0.30)
        self.assertEqual(payload.style_gate_min_median_mom20, 0.0)
        self.assertEqual(payload.style_gate_min_sample_size, 20)

    def test_rows_to_b1_panel_converts_tushare_hands_to_shares(self):
        panel = rows_to_b1_panel(make_rows(140), build_b1_config({}), volume_unit="hand")

        self.assertEqual(float(panel.iloc[0]["volume"]), 100_000.0)
        self.assertIn("bbi", panel.columns)
        self.assertIn("double_ema10", panel.columns)
        self.assertIn("kdj_j", panel.columns)
        self.assertIn("entry_signal", panel.columns)
        self.assertIn("b1_score", panel.columns)

    def test_market_frame_can_apply_ma20_above_ma60_gate(self):
        market = build_b1_market_frame_from_rows(
            make_rows(140, close_start=20.0, step=-0.05),
            build_b1_config({}),
            eval_start=date(2025, 5, 1),
            eval_end=date(2025, 5, 20),
            require_ma20_gt_ma60=True,
        )

        blocked = market[market["ma20"] <= market["ma60"]]
        self.assertFalse(blocked.empty)
        self.assertTrue((blocked["bbi"] == blocked["close"] * 2.0).all())


if __name__ == "__main__":
    unittest.main()
