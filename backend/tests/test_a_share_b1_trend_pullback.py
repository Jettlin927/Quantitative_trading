from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from backend.app.quant_research.a_share_b1_trend_pullback import (
    calculate_b1_feature_frame,
    simulate_b1_portfolio,
    validate_a_share_b1_config,
)
from backend.app.quant_research.calendar import (
    build_open_trade_calendar,
    trade_calendar_content_sha256,
)
from backend.app.quant_research.metrics import summarize_execution_metrics


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "configs" / "research"


class AShareB1TrendPullbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _load_config("a_share_b1_source_period_realistic.json")
        self.config["qualityRunId"] = "test-quality"

    def test_all_preregistered_configs_keep_fixed_rules_and_only_declared_variants(self) -> None:
        paths = {
            "网页机械口径对照": "a_share_b1_source_period_close_ideal.json",
            "同周期现实成交": "a_share_b1_source_period_realistic.json",
            "长历史主版本": "a_share_b1_long_history.json",
            "页面参数一致性对照": "a_share_b1_long_history_declared_t3_off.json",
            "双倍成本压力": "a_share_b1_long_history_double_cost.json",
        }
        configs = {name: _load_config(filename) for name, filename in paths.items()}
        for config in configs.values():
            config["qualityRunId"] = "test-quality"
            validate_a_share_b1_config(config)
            self.assertEqual(config["initialCapital"], "100000")
            self.assertEqual(config["universe"]["sourceKey"], "801890.SI")
            self.assertEqual(config["featureParameters"]["bbiWindows"], [14, 28, 57, 114])
            self.assertEqual(config["featureParameters"]["kdjJThreshold"], "13")
            self.assertEqual(config["targetWeightParameters"]["topN"], 2)

        self.assertTrue(configs["长历史主版本"]["exitParameters"]["t3WeakEnabled"])
        self.assertFalse(configs["页面参数一致性对照"]["exitParameters"]["t3WeakEnabled"])
        self.assertEqual(
            configs["长历史主版本"]["featureParameters"],
            configs["页面参数一致性对照"]["featureParameters"],
        )
        self.assertEqual(
            tuple(float(configs["双倍成本压力"]["costModel"][key]) for key in ("buyRate", "sellRate", "slippageRate")),
            (0.0007, 0.0017, 0.002),
        )

    def test_parameter_search_or_unregistered_execution_is_rejected(self) -> None:
        invalid = json.loads(json.dumps(self.config))
        invalid["featureParameters"]["bbiWindows"] = [14, 28, 57, 120]
        with self.assertRaisesRegex(ValueError, "BBI"):
            validate_a_share_b1_config(invalid)

        invalid = json.loads(json.dumps(self.config))
        invalid["executionPolicy"]["executionPrice"] = "same_day_open"
        with self.assertRaisesRegex(ValueError, "成交"):
            validate_a_share_b1_config(invalid)

    def test_feature_prefix_is_unchanged_when_future_rows_are_appended(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=140)
        close = pd.Series([10 + index * 0.05 for index in range(len(dates))], dtype=float)
        bars = pd.DataFrame(
            {
                "trade_date": dates,
                "ts_code": "SYN001.SZ",
                "adj_open": close,
                "adj_high": close + 0.2,
                "adj_low": close - 0.2,
                "adj_close": close,
                "vol": 1000.0,
                "is_valuation_carried": False,
            }
        )
        expected = calculate_b1_feature_frame(bars, self.config)
        row = expected.iloc[113]
        expected_bbi = sum(close.iloc[:114].tail(window).mean() for window in (14, 28, 57, 114)) / 4
        self.assertAlmostEqual(float(row["bbi"]), float(expected_bbi), places=12)

        future = bars.iloc[-1:].copy()
        future["trade_date"] = dates[-1] + pd.Timedelta(days=3)
        future[["adj_open", "adj_high", "adj_low", "adj_close"]] = 1.0
        actual = calculate_b1_feature_frame(pd.concat([bars, future], ignore_index=True), self.config)
        pd.testing.assert_frame_equal(expected, actual.iloc[: len(expected)].reset_index(drop=True))

    def test_next_open_t3_exit_and_declared_t3_off_are_distinct(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=6)
        calendar = _calendar(dates, self.addCleanup)
        prices = _simulation_prices(dates)
        features = _simulation_features(dates)
        market = pd.DataFrame({"trade_date": dates, "market_allows_entry": True})
        targets = pd.DataFrame(
            [
                {
                    "signal_date": dates[0],
                    "available_date": dates[0],
                    "ts_code": "SYN001.SZ",
                    "target_weight": 0.5,
                }
            ]
        )

        enabled = simulate_b1_portfolio(prices, features, market, calendar, targets, self.config)
        enabled_requests = enabled.rebalance_requests
        buy = enabled_requests[enabled_requests["side"].eq("buy")].iloc[0]
        sell = enabled_requests[enabled_requests["side"].eq("sell")].iloc[0]
        self.assertEqual(buy["execution_date"], dates[1])
        self.assertEqual(buy["signal_date"], dates[0])
        self.assertEqual(sell["execution_date"], dates[4])
        self.assertEqual(sell["signal_date"], dates[3])
        self.assertLess(enabled.nav.iloc[-1]["gross_exposure"], 1e-12)
        execution_metrics = summarize_execution_metrics(
            enabled.nav,
            enabled.rebalance_requests,
            enabled.rebalance_executions,
            enabled.positions,
        )
        self.assertEqual(execution_metrics["maxHoldingCount"], 1)
        self.assertGreater(execution_metrics["cumulativeTransactionCostRate"], 0)

        disabled_config = json.loads(json.dumps(self.config))
        disabled_config["exitParameters"]["t3WeakEnabled"] = False
        disabled = simulate_b1_portfolio(prices, features, market, calendar, targets, disabled_config)
        self.assertTrue(disabled.rebalance_requests["side"].eq("sell").sum() == 0)
        self.assertGreater(disabled.nav.iloc[-1]["gross_exposure"], 0.4)

    def test_realistic_buy_uses_100_share_lots_and_limit_up_can_block(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=3)
        calendar = _calendar(dates, self.addCleanup)
        prices = _simulation_prices(dates)
        features = _simulation_features(dates)
        market = pd.DataFrame({"trade_date": dates, "market_allows_entry": True})
        targets = pd.DataFrame(
            [
                {
                    "signal_date": dates[0],
                    "available_date": dates[0],
                    "ts_code": "SYN001.SZ",
                    "target_weight": 0.5,
                }
            ]
        )

        prices.loc[prices["trade_date"].eq(dates[1]), "open"] = 10.03
        prices.loc[prices["trade_date"].eq(dates[1]), "adj_open"] = 10.03
        filled = simulate_b1_portfolio(prices, features, market, calendar, targets, self.config)
        execution = filled.rebalance_executions.iloc[0]
        gross_value = execution["executed_change"] * 100000
        inferred_shares = gross_value / 10.03
        self.assertAlmostEqual(inferred_shares / 100, round(inferred_shares / 100), places=8)

        blocked_prices = prices.copy()
        blocked_prices.loc[blocked_prices["trade_date"].eq(dates[1]), "is_buyable_at_open"] = False
        blocked = simulate_b1_portfolio(blocked_prices, features, market, calendar, targets, self.config)
        blocked_execution = blocked.rebalance_executions.iloc[0]
        self.assertEqual(blocked_execution["status"], "blocked")
        self.assertEqual(blocked_execution["reason"], "limit_up")
        self.assertEqual(blocked_execution["executed_change"], 0)

    def test_web_mechanical_control_executes_on_signal_close(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=2)
        calendar = _calendar(dates, self.addCleanup)
        config = _load_config("a_share_b1_source_period_close_ideal.json")
        config["qualityRunId"] = "test-quality"
        result = simulate_b1_portfolio(
            _simulation_prices(dates),
            _simulation_features(dates),
            pd.DataFrame({"trade_date": dates, "market_allows_entry": True}),
            calendar,
            pd.DataFrame(
                [
                    {
                        "signal_date": dates[0],
                        "available_date": dates[0],
                        "ts_code": "SYN001.SZ",
                        "target_weight": 0.5,
                    }
                ]
            ),
            config,
        )
        execution = result.rebalance_executions.iloc[0]
        self.assertEqual(execution["execution_date"], dates[0])
        self.assertEqual(execution["signal_date"], dates[0])
        self.assertEqual(execution["status"], "filled")


def _load_config(filename: str) -> dict[str, object]:
    return json.loads((CONFIG_ROOT / filename).read_text(encoding="utf-8"))


def _calendar(dates: pd.DatetimeIndex, add_cleanup):
    records = [
        {"exchange": "SSE", "cal_date": value.date().isoformat(), "is_open": 1}
        for value in dates
    ]
    temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    try:
        pd.DataFrame(records).to_csv(temporary.name, index=False)
        add_cleanup(Path(temporary.name).unlink, missing_ok=True)
        return build_open_trade_calendar(
            records,
            source_artifact=temporary.name,
            source_artifact_sha256=trade_calendar_content_sha256(records),
        )
    finally:
        temporary.close()


def _simulation_prices(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": "SYN001.SZ",
            "open": 10.0,
            "close": 10.0,
            "adj_open": 10.0,
            "adj_close": 10.0,
            "is_buyable_at_open": True,
            "is_sellable_at_open": True,
            "is_valuation_carried": False,
            "is_suspended": False,
            "is_suspended_at_open": False,
            "valuation_carry_reason": "",
        }
    )


def _simulation_features(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": "SYN001.SZ",
            "adj_close": 10.0,
            "bbi": 8.0,
            "double_ema_10": 9.0,
            "bearish_heavy_volume": False,
        }
    )


if __name__ == "__main__":
    unittest.main()
