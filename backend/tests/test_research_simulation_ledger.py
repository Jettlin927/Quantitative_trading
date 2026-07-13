from __future__ import annotations

import unittest

import pandas as pd

from backend.app.quant_research.dataset import build_adjusted_price_panel
from backend.app.quant_research.portfolio import (
    CostModel,
    simulate_target_weights_with_ledger,
)
from backend.tests.test_quant_trust_contract import fixture_trade_calendar, read_fixture


class ResearchSimulationLedgerTest(unittest.TestCase):
    def setUp(self):
        bars = read_fixture("fund_daily_bars.csv")
        factors = read_fixture("fund_adjust_factors.csv")
        self.prices = build_adjusted_price_panel(bars, factors)
        self.prices["is_buyable_at_open"] = True
        self.prices["is_sellable_at_open"] = True
        self.targets = read_fixture("target_weights.csv")
        self.calendar = fixture_trade_calendar()

    def test_filled_execution_cost_cash_and_positions_reconcile(self):
        result = simulate_target_weights_with_ledger(
            self.prices,
            self.targets,
            trade_calendar=self.calendar,
            cost=CostModel(buy_rate=0.001, sell_rate=0.002, slippage_rate=0.001),
        )
        self.assertEqual(result.rebalance_requests["side"].tolist(), ["buy"])
        execution = result.rebalance_executions.iloc[0]
        self.assertEqual(execution["status"], "filled")
        self.assertEqual(execution["reason"], "")
        self.assertAlmostEqual(execution["requested_change"], execution["executed_change"])
        self.assertEqual(execution["blocked_change"], 0)

        costs = result.rebalance_executions.groupby("execution_date")["transaction_cost_rate"].sum()
        nav_costs = result.nav.set_index("trade_date")["transaction_cost_rate"]
        for trade_date, value in costs.items():
            self.assertAlmostEqual(value, nav_costs.loc[trade_date], places=13)

        position_totals = result.positions.groupby("trade_date")["close_weight"].sum()
        for row in result.nav.itertuples(index=False):
            invested = float(position_totals.get(row.trade_date, 0.0))
            self.assertAlmostEqual(invested + float(row.cash_weight), 1.0, places=12)

    def test_market_block_and_cash_capacity_are_separate_reasons(self):
        prices = pd.concat(
            [
                self.prices,
                self.prices.assign(ts_code="OTHER.SH"),
            ],
            ignore_index=True,
        ).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        blocked_date = pd.Timestamp("2026-01-13")
        prices.loc[
            prices["ts_code"].eq("SYNETF.SZ") & prices["trade_date"].eq(blocked_date),
            "is_sellable_at_open",
        ] = False
        targets = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-09",
                    "available_date": "2026-01-09",
                    "ts_code": "SYNETF.SZ",
                    "target_weight": 1.0,
                },
                {
                    "signal_date": "2026-01-12",
                    "available_date": "2026-01-12",
                    "ts_code": "OTHER.SH",
                    "target_weight": 1.0,
                },
            ]
        )
        result = simulate_target_weights_with_ledger(
            prices,
            targets,
            trade_calendar=self.calendar,
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )
        blocked = result.rebalance_executions[
            result.rebalance_executions["execution_date"].eq(blocked_date)
        ].set_index("ts_code")
        self.assertEqual(blocked.loc["SYNETF.SZ", "reason"], "limit_down")
        self.assertEqual(blocked.loc["OTHER.SH", "reason"], "cash_capacity")
        self.assertTrue(blocked["status"].eq("blocked").all())
        self.assertTrue(blocked["executed_change"].eq(0).all())
        self.assertGreater(blocked["blocked_change"].sum(), 0)

    def test_open_suspension_reason_is_not_collapsed_into_price_limit(self):
        prices = self.prices.copy()
        execution_date = pd.Timestamp("2026-01-12")
        mask = prices["trade_date"].eq(execution_date)
        prices.loc[mask, "is_buyable_at_open"] = False
        prices["is_suspended_at_open"] = False
        prices.loc[mask, "is_suspended_at_open"] = True
        result = simulate_target_weights_with_ledger(
            prices,
            self.targets,
            trade_calendar=self.calendar,
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )
        execution = result.rebalance_executions.iloc[0]
        self.assertEqual(execution["status"], "blocked")
        self.assertEqual(execution["reason"], "suspended_at_open")

    def test_cash_capacity_can_create_partial_execution(self):
        prices = pd.concat(
            [
                self.prices,
                self.prices.assign(ts_code="OTHER.SH"),
            ],
            ignore_index=True,
        ).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        targets = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-09",
                    "available_date": "2026-01-09",
                    "ts_code": "SYNETF.SZ",
                    "target_weight": 0.8,
                },
                {
                    "signal_date": "2026-01-12",
                    "available_date": "2026-01-12",
                    "ts_code": "SYNETF.SZ",
                    "target_weight": 0.5,
                },
                {
                    "signal_date": "2026-01-12",
                    "available_date": "2026-01-12",
                    "ts_code": "OTHER.SH",
                    "target_weight": 0.5,
                },
            ]
        )
        execution_date = pd.Timestamp("2026-01-13")
        prices.loc[
            prices["ts_code"].eq("SYNETF.SZ")
            & prices["trade_date"].eq(execution_date),
            "is_sellable_at_open",
        ] = False
        result = simulate_target_weights_with_ledger(
            prices,
            targets,
            trade_calendar=self.calendar,
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )
        execution = result.rebalance_executions[
            result.rebalance_executions["execution_date"].eq(execution_date)
            & result.rebalance_executions["ts_code"].eq("OTHER.SH")
        ].iloc[0]
        self.assertEqual(execution["status"], "partial")
        self.assertEqual(execution["reason"], "cash_capacity")
        self.assertGreater(execution["executed_change"], 0)
        self.assertGreater(execution["blocked_change"], 0)

    def test_carried_valuation_has_distinct_reason(self):
        prices = self.prices.copy()
        execution_date = pd.Timestamp("2026-01-12")
        mask = prices["trade_date"].eq(execution_date)
        prices["is_valuation_carried"] = False
        prices["valuation_carry_reason"] = ""
        prices["is_suspended"] = False
        prices["is_suspended_at_open"] = False
        prices.loc[mask, "is_valuation_carried"] = True
        prices.loc[mask, "valuation_carry_reason"] = "full_day_suspension"
        prices.loc[mask, "is_suspended"] = True
        prices.loc[mask, "is_suspended_at_open"] = True
        prices.loc[mask, "is_buyable_at_open"] = False
        prices.loc[mask, "is_sellable_at_open"] = False
        result = simulate_target_weights_with_ledger(
            prices,
            self.targets,
            trade_calendar=self.calendar,
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )
        execution = result.rebalance_executions.iloc[0]
        self.assertEqual(execution["status"], "blocked")
        self.assertEqual(execution["reason"], "valuation_carried")


if __name__ == "__main__":
    unittest.main()
