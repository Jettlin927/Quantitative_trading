from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models import StockAdjustFactor, StockDailyBar, StockLimitPrice, StockListing
from backend.app.quant_research.dataset import active_members_as_of, attach_fundamentals_asof, build_adjusted_price_panel
from backend.app.quant_research.manifest import build_run_manifest
from backend.app.quant_research.metrics import summarize_performance
from backend.app.quant_research.portfolio import CostModel, simulate_target_weights
from backend.app.quant_research.readiness import evaluate_research_readiness
from backend.app.quant_research.repository import load_stock_research_panel
from backend.app.quant_research.validation import build_walk_forward_windows


class QuantResearchDatasetTest(unittest.TestCase):
    def test_builds_end_anchored_adjusted_prices(self):
        bars = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "high": 10.5, "low": 9.5, "close": 10, "amount": 1000},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "open": 11, "high": 12, "low": 10.5, "close": 11, "amount": 1200},
            ]
        )
        factors = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "adj_factor": 1},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "adj_factor": 2},
            ]
        )

        adjusted = build_adjusted_price_panel(bars, factors)

        self.assertEqual(adjusted["adj_close"].tolist(), [5.0, 11.0])
        self.assertAlmostEqual(adjusted.iloc[1]["adjusted_return"], 1.2)

    def test_rejects_missing_adjust_factor(self):
        bars = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10}])
        factors = pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

        with self.assertRaisesRegex(ValueError, "复权因子"):
            build_adjusted_price_panel(bars, factors)

    def test_attaches_only_announced_fundamentals(self):
        panel = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "adj_close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-09", "adj_close": 11},
            ]
        )
        fundamentals = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "ann_date": "2026-01-07", "end_date": "2025-12-31", "roe": 12.5},
            ]
        )

        merged = attach_fundamentals_asof(panel, fundamentals)

        self.assertTrue(pd.isna(merged.iloc[0]["roe"]))
        self.assertEqual(merged.iloc[1]["roe"], 12.5)
        self.assertLessEqual(merged.iloc[1]["ann_date"], merged.iloc[1]["trade_date"])

    def test_filters_historical_industry_membership(self):
        memberships = pd.DataFrame(
            [
                {"index_code": "801080.SI", "con_code": "A", "in_date": "2020-01-01", "out_date": "2024-12-31"},
                {"index_code": "801080.SI", "con_code": "B", "in_date": "2025-01-01", "out_date": None},
            ]
        )

        self.assertEqual(active_members_as_of(memberships, "2024-06-30", "801080.SI"), {"A"})
        self.assertEqual(active_members_as_of(memberships, "2025-06-30", "801080.SI"), {"B"})

    def test_repository_requires_explicit_historical_universe_and_tradability(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(StockListing(ts_code="000001.SZ", symbol="000001", name="平安银行", list_status="L", list_date=pd.Timestamp("1991-04-03").date()))
            db.add(StockDailyBar(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), open=10, high=11, low=9, close=10, pre_close=9.8))
            db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), adj_factor=2))
            db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), pre_close=9.8, up_limit=10.78, down_limit=8.82))
            db.commit()

        panel = load_stock_research_panel(engine, ["000001.SZ"], pd.Timestamp("2026-01-02").date(), pd.Timestamp("2026-01-02").date())

        self.assertEqual(panel.iloc[0]["adj_close"], 10)
        self.assertTrue(panel.iloc[0]["is_buyable_at_open"])
        with self.assertRaisesRegex(ValueError, "显式提供研究股票池"):
            load_stock_research_panel(engine, [], pd.Timestamp("2026-01-02").date(), pd.Timestamp("2026-01-02").date())


class QuantResearchPortfolioTest(unittest.TestCase):
    def test_executes_close_signal_at_next_trade_open(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 11, "adj_close": 12, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 12, "adj_close": 12, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = pd.DataFrame([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        nav = simulate_target_weights(prices, targets, cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0))

        self.assertEqual(nav.iloc[0]["nav"], 1.0)
        self.assertEqual(str(nav.iloc[1]["executed_signal_date"].date()), "2026-01-02")
        self.assertAlmostEqual(nav.iloc[1]["nav"], 12 / 11)

    def test_rejects_missing_price_for_held_asset(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "B", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = pd.DataFrame([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        with self.assertRaisesRegex(ValueError, "缺少持仓价格"):
            simulate_target_weights(prices, targets, cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0))

    def test_zero_weight_target_moves_portfolio_to_cash(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = pd.DataFrame(
            [
                {"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0},
                {"signal_date": "2026-01-05", "ts_code": "A", "target_weight": 0.0},
            ]
        )

        nav = simulate_target_weights(prices, targets, cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0))

        self.assertEqual(nav.iloc[-1]["gross_exposure"], 0.0)
        self.assertEqual(nav.iloc[-1]["cash_weight"], 1.0)

    def test_rejects_target_that_cannot_trade_at_open(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 11, "adj_close": 11, "is_buyable_at_open": False, "is_sellable_at_open": True},
            ]
        )
        targets = pd.DataFrame([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        with self.assertRaisesRegex(ValueError, "不可买入=A"):
            simulate_target_weights(prices, targets, cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0))


class QuantResearchEvaluationTest(unittest.TestCase):
    def test_builds_absolute_and_benchmark_metrics(self):
        nav = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.1},
                {"trade_date": "2026-01-06", "nav": 1.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.05},
                {"trade_date": "2026-01-06", "nav": 1.02},
            ]
        )

        summary = summarize_performance(nav, benchmark)

        self.assertAlmostEqual(summary["totalReturn"], 0.0)
        self.assertAlmostEqual(summary["maxDrawdown"], 1 / 1.1 - 1)
        self.assertIn("trackingError", summary)
        self.assertIn("informationRatio", summary)

    def test_excess_return_uses_only_overlapping_benchmark_dates(self):
        nav = pd.DataFrame(
            [
                {"trade_date": "2026-01-01", "nav": 0.5},
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.1},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.05},
            ]
        )

        summary = summarize_performance(nav, benchmark)

        self.assertAlmostEqual(summary["excessTotalReturn"], 0.05)

    def test_walk_forward_windows_never_overlap_train_and_test(self):
        dates = pd.bdate_range("2025-01-01", periods=18)

        anchored = build_walk_forward_windows(dates, train_periods=8, test_periods=4, step_periods=4, anchored=True)
        rolling = build_walk_forward_windows(dates, train_periods=8, test_periods=4, step_periods=4, anchored=False)

        self.assertEqual(len(anchored), 2)
        self.assertEqual(len(rolling), 2)
        for window in anchored + rolling:
            self.assertLess(window.train_end, window.test_start)
        self.assertEqual(anchored[1].train_start, dates[0])
        self.assertGreater(rolling[1].train_start, dates[0])

    def test_manifest_is_reproducible_and_research_only(self):
        generated_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        first = build_run_manifest(
            strategy_id="teaching-baseline",
            config={"lookback": 20, "topN": 5},
            data_snapshot={"stockDailyBars": {"maxDate": "2026-07-09", "rows": 100}},
            git_commit="abc123",
            limitations=["no_real_broker"],
            generated_at=generated_at,
        )
        second = build_run_manifest(
            strategy_id="teaching-baseline",
            config={"topN": 5, "lookback": 20},
            data_snapshot={"stockDailyBars": {"maxDate": "2026-07-09", "rows": 100}},
            git_commit="abc123",
            limitations=["no_real_broker"],
            generated_at=generated_at,
        )

        self.assertEqual(first["configSha256"], second["configSha256"])
        self.assertTrue(first["boundaries"]["researchOnly"])
        self.assertFalse(first["boundaries"]["executionEnabled"])

    def test_readiness_separates_etf_and_stock_research(self):
        available = {
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "stocks",
            "stock_daily_bars",
            "stock_adjust_factors",
        }
        counts = {table: 1 for table in available}

        etf = evaluate_research_readiness("etf_time_series", available, counts)
        stocks = evaluate_research_readiness("a_share_cross_section", available, counts)

        self.assertEqual(etf["status"], "ready")
        self.assertEqual(stocks["status"], "blocked")
        self.assertIn("stock_listings", stocks["missingTables"])
        self.assertIn("stock_limit_prices", stocks["missingTables"])


if __name__ == "__main__":
    unittest.main()
