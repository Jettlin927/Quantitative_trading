from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from backend.app.quant_research.dataset import attach_fundamentals_asof, build_adjusted_price_panel
from backend.app.quant_research.metrics import summarize_performance
from backend.app.quant_research.portfolio import CostModel, simulate_target_weights


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "quant_research_golden"
CONTRACT_PATH = Path(__file__).parents[2] / "docs" / "research" / "quant-foundation-trust-contract.md"


def read_fixture(name: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURE_DIR / name)


class QuantTrustContractDocumentationTest(unittest.TestCase):
    def test_contract_freezes_required_terms_tables_and_examples(self):
        contract = CONTRACT_PATH.read_text(encoding="utf-8")

        required_terms = (
            "quality_scope",
            "universe_provenance",
            "available_from",
            "signal_date",
            "execution_date",
            "data_snapshot_id",
            "reproducibility_key",
        )
        required_tables = (
            "stock_daily_bars",
            "stock_daily_basic",
            "stock_financial_indicators",
            "stock_adjust_factors",
            "fund_adjust_factors",
            "stock_limit_prices",
            "stock_suspend_events",
            "stock_listings",
            "industry_members",
        )
        for value in (*required_terms, *required_tables):
            self.assertIn(value, contract)
        self.assertIn("ETF 时序研究", contract)
        self.assertIn("A 股横截面研究", contract)
        self.assertIn("旧策略 archive 边界", contract)


class QuantTrustGoldenFixtureTest(unittest.TestCase):
    SORT_KEYS = {
        "trade_calendars.csv": ["exchange", "cal_date"],
        "stock_listings.csv": ["ts_code"],
        "stock_daily_bars.csv": ["ts_code", "trade_date"],
        "stock_adjust_factors.csv": ["ts_code", "trade_date"],
        "stock_limit_prices.csv": ["ts_code", "trade_date"],
        "stock_suspend_events.csv": ["ts_code", "trade_date", "suspend_type", "suspend_timing"],
        "stock_financial_indicators.csv": ["ts_code", "end_date", "ann_date"],
        "industry_members.csv": ["index_code", "con_code", "in_date"],
        "funds.csv": ["ts_code"],
        "fund_daily_bars.csv": ["ts_code", "trade_date"],
        "fund_adjust_factors.csv": ["ts_code", "trade_date"],
        "indices.csv": ["ts_code"],
        "index_daily_bars.csv": ["ts_code", "trade_date"],
        "target_weights.csv": ["signal_date", "ts_code"],
        "expected_fundamental_availability.csv": ["ts_code", "end_date", "ann_date"],
        "expected_execution_dates.csv": ["signal_date", "ts_code"],
        "expected_nav.csv": ["trade_date"],
    }

    def test_files_are_stably_sorted_and_natural_keys_are_unique(self):
        for name, keys in self.SORT_KEYS.items():
            with self.subTest(name=name):
                frame = read_fixture(name)
                expected = frame.sort_values(keys, kind="stable", na_position="last").reset_index(drop=True)
                pd.testing.assert_frame_equal(frame.reset_index(drop=True), expected)
                self.assertFalse(frame.duplicated(keys).any())

    def test_fixture_has_two_stocks_one_etf_one_index_and_fifteen_trade_days(self):
        calendar = read_fixture("trade_calendars.csv")
        listings = read_fixture("stock_listings.csv")
        funds = read_fixture("funds.csv")
        indices = read_fixture("indices.csv")

        self.assertEqual(calendar.loc[calendar["is_open"] == 1, "cal_date"].nunique(), 15)
        self.assertEqual(
            set(calendar.loc[calendar["is_open"] == 0, "cal_date"]),
            {"2026-01-10", "2026-01-11", "2026-01-17", "2026-01-18"},
        )
        self.assertEqual(set(listings["ts_code"]), {"SYN001.SZ", "SYN002.SH"})
        self.assertEqual(funds["ts_code"].tolist(), ["SYNETF.SZ"])
        self.assertEqual(indices["ts_code"].tolist(), ["SYNIDX.SH"])
        self.assertTrue(listings["source"].eq("synthetic").all())
        self.assertTrue(funds["source"].eq("synthetic").all())
        self.assertTrue(indices["source"].eq("synthetic").all())

    def test_fixture_covers_suspension_limits_adjustment_and_delisting(self):
        bars = read_fixture("stock_daily_bars.csv")
        factors = read_fixture("stock_adjust_factors.csv")
        limits = read_fixture("stock_limit_prices.csv")
        suspensions = read_fixture("stock_suspend_events.csv")
        listings = read_fixture("stock_listings.csv")

        suspended = suspensions.iloc[0]
        self.assertEqual((suspended["ts_code"], suspended["trade_date"], suspended["suspend_timing"]), ("SYN001.SZ", "2026-01-12", "全天"))
        self.assertFalse(((bars["ts_code"] == "SYN001.SZ") & (bars["trade_date"] == "2026-01-12")).any())

        merged = bars.merge(limits, on=["ts_code", "trade_date"], validate="one_to_one")
        limit_up = merged[(merged["ts_code"] == "SYN001.SZ") & (merged["trade_date"] == "2026-01-14")].iloc[0]
        limit_down = merged[(merged["ts_code"] == "SYN002.SH") & (merged["trade_date"] == "2026-01-15")].iloc[0]
        self.assertEqual(limit_up["open"], limit_up["up_limit"])
        self.assertEqual(limit_down["open"], limit_down["down_limit"])

        stock_one_factors = factors[factors["ts_code"] == "SYN001.SZ"].set_index("trade_date")["adj_factor"]
        self.assertEqual(stock_one_factors.loc["2026-01-15"], 1.0)
        self.assertEqual(stock_one_factors.loc["2026-01-16"], 1.2)

        delist_date = listings.set_index("ts_code").loc["SYN002.SH", "delist_date"]
        stock_two_dates = bars.loc[bars["ts_code"] == "SYN002.SH", "trade_date"]
        self.assertEqual(stock_two_dates.max(), delist_date)
        self.assertFalse((stock_two_dates > delist_date).any())

    def test_financial_availability_is_the_next_open_trade_day(self):
        calendar = read_fixture("trade_calendars.csv")
        fundamentals = read_fixture("stock_financial_indicators.csv")
        expected = read_fixture("expected_fundamental_availability.csv")
        open_dates = sorted(calendar.loc[calendar["is_open"] == 1, "cal_date"])

        joined = fundamentals.merge(
            expected,
            on=["ts_code", "end_date", "ann_date"],
            how="inner",
            validate="one_to_one",
        )
        self.assertEqual(len(joined), len(fundamentals))
        for row in joined.itertuples(index=False):
            next_open = next(trade_date for trade_date in open_dates if trade_date > row.ann_date)
            self.assertEqual(row.available_from, next_open)

    def test_etf_signal_executes_next_trade_open_and_matches_golden_outputs(self):
        fund_bars = read_fixture("fund_daily_bars.csv")
        fund_factors = read_fixture("fund_adjust_factors.csv")
        calendar = read_fixture("trade_calendars.csv")
        targets = read_fixture("target_weights.csv")
        expected_execution = read_fixture("expected_execution_dates.csv")
        expected_nav = read_fixture("expected_nav.csv")

        prices = build_adjusted_price_panel(fund_bars, fund_factors)
        prices["is_buyable_at_open"] = True
        prices["is_sellable_at_open"] = True
        actual_nav = simulate_target_weights(
            prices,
            targets,
            open_trade_dates=calendar.loc[calendar["is_open"] == 1, "cal_date"],
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )
        actual_nav["trade_date"] = actual_nav["trade_date"].dt.strftime("%Y-%m-%d")
        actual_nav["executed_signal_date"] = actual_nav["executed_signal_date"].dt.strftime("%Y-%m-%d")

        executed = actual_nav.dropna(subset=["executed_signal_date"])
        self.assertEqual(len(executed), len(expected_execution))
        self.assertEqual(executed.iloc[0]["trade_date"], expected_execution.iloc[0]["execution_date"])
        self.assertEqual(executed.iloc[0]["executed_signal_date"], expected_execution.iloc[0]["signal_date"])

        self.assertEqual(actual_nav["trade_date"].tolist(), expected_nav["trade_date"].tolist())
        self.assertEqual(
            actual_nav["executed_signal_date"].fillna("").tolist(),
            expected_nav["executed_signal_date"].fillna("").tolist(),
        )
        numeric_columns = [
            "nav",
            "cash_weight",
            "gross_exposure",
            "traded_weight",
            "one_way_turnover",
            "transaction_cost_rate",
        ]
        for column in numeric_columns:
            with self.subTest(column=column):
                pd.testing.assert_series_equal(
                    actual_nav[column].astype(float),
                    expected_nav[column].astype(float),
                    check_names=False,
                    check_exact=False,
                    rtol=1e-13,
                    atol=1e-13,
                )

        benchmark = read_fixture("index_daily_bars.csv")[["trade_date", "close"]].copy()
        benchmark["nav"] = benchmark["close"] / benchmark["close"].iloc[0]
        actual_metrics = summarize_performance(actual_nav[["trade_date", "nav"]], benchmark[["trade_date", "nav"]])
        expected_metrics = json.loads((FIXTURE_DIR / "expected_metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(set(actual_metrics), set(expected_metrics))
        for key, expected_value in expected_metrics.items():
            with self.subTest(metric=key):
                if isinstance(expected_value, float):
                    self.assertAlmostEqual(actual_metrics[key], expected_value, places=13)
                else:
                    self.assertEqual(actual_metrics[key], expected_value)


class QuantTrustNoLookaheadTest(unittest.TestCase):
    def test_future_adjustment_factor_does_not_reanchor_historical_prefix(self):
        """追加未来复权因子不得重标已有历史前缀。"""

        bars = read_fixture("stock_daily_bars.csv")
        factors = read_fixture("stock_adjust_factors.csv")
        bars = bars[bars["ts_code"] == "SYN001.SZ"]
        factors = factors[factors["ts_code"] == "SYN001.SZ"]
        cutoff = "2026-01-15"

        prefix = build_adjusted_price_panel(
            bars[bars["trade_date"] <= cutoff],
            factors[factors["trade_date"] <= cutoff],
        )
        extended = build_adjusted_price_panel(
            bars[bars["trade_date"] <= "2026-01-16"],
            factors[factors["trade_date"] <= "2026-01-16"],
        )
        extended_prefix = extended[extended["trade_date"] <= pd.Timestamp(cutoff)]

        pd.testing.assert_series_equal(
            prefix["adj_close"].reset_index(drop=True),
            extended_prefix["adj_close"].reset_index(drop=True),
            check_names=False,
        )

    def test_unknown_time_announcement_is_not_visible_on_announcement_date(self):
        """公告时刻未知时，财务数据从下一交易日起可见。"""

        calendar = read_fixture("trade_calendars.csv")
        open_dates = calendar.loc[calendar["is_open"] == 1, "cal_date"]
        panel = pd.DataFrame(
            {
                "ts_code": "SYN001.SZ",
                "trade_date": open_dates,
            }
        )
        fundamentals = read_fixture("stock_financial_indicators.csv")
        fundamentals = fundamentals[fundamentals["ts_code"] == "SYN001.SZ"]

        merged = attach_fundamentals_asof(panel, fundamentals, trade_dates=open_dates)
        by_date = merged.set_index(merged["trade_date"].dt.strftime("%Y-%m-%d"))

        self.assertTrue(pd.isna(by_date.loc["2026-01-09", "roe"]))
        self.assertEqual(by_date.loc["2026-01-12", "roe"], 12.5)


if __name__ == "__main__":
    unittest.main()
