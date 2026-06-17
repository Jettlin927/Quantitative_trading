import math
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.experiment.backtest import run_weighted_nav
from my_quant.strategy_research.experiment.config import ALL_ASSETS, DEFENSE_ASSET, StrategyConfig
from my_quant.strategy_research.experiment.factor_diagnostics import summarize_factor_ic
from my_quant.strategy_research.experiment.metrics import calculate_metrics
from my_quant.strategy_research.experiment.reports import build_manifest_payload
from my_quant.strategy_research.experiment.satellite import (
    SatelliteConfig,
    build_satellite_final_markdown,
    build_satellite_target_weights,
    compute_asset_diagnostics,
    drawdown_scale,
    run_fixed_blend_config,
    run_satellite_config,
    select_satellite_candidate,
)
from my_quant.strategy_research.experiment.satellite_pipeline import satellite_date_windows
from my_quant.strategy_research.experiment.strategies import make_ram_topn, normalize_weights
from my_quant.strategy_research.experiment.validation import walk_forward_analysis


class ExperimentEngineTest(unittest.TestCase):
    def test_normalize_weights_uses_defense_when_total_is_zero(self):
        weights = normalize_weights({"510300": 0.0, "513100": 0.0}, ALL_ASSETS)

        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertEqual(weights[DEFENSE_ASSET], 1.0)

    def test_normalize_weights_rejects_unknown_symbol(self):
        with self.assertRaisesRegex(ValueError, "Unknown symbol"):
            normalize_weights({"999999": 1.0}, ALL_ASSETS)

    def test_sina_symbol_resolves_exchange_prefixes(self):
        from my_quant.strategy_research.experiment.data import repair_large_price_jumps, sina_symbol

        self.assertEqual(sina_symbol("510300"), "sh510300")
        self.assertEqual(sina_symbol("513100"), "sh513100")
        self.assertEqual(sina_symbol("159915"), "sz159915")
        self.assertEqual(sina_symbol("159985"), "sz159985")

        raw = pd.Series([5.0, 5.2, 1.04, 1.10], index=pd.date_range("2024-01-01", periods=4))
        repaired = repair_large_price_jumps(raw)
        self.assertGreater(repaired.pct_change().min(), -0.35)

    def test_satellite_universe_has_defense_and_high_beta_assets(self):
        from my_quant.strategy_research.experiment.config import (
            SATELLITE_DEFENSE_ASSET,
            SATELLITE_RISK_ASSETS,
            SATELLITE_UNIVERSE,
        )

        self.assertEqual(SATELLITE_DEFENSE_ASSET, "511880")
        self.assertIn("513100", SATELLITE_RISK_ASSETS)
        self.assertIn("159915", SATELLITE_RISK_ASSETS)
        self.assertIn(SATELLITE_DEFENSE_ASSET, SATELLITE_UNIVERSE)
        self.assertTrue(set(SATELLITE_RISK_ASSETS).issubset(set(SATELLITE_UNIVERSE)))

    def test_calculate_metrics_reports_drawdown_and_total_return(self):
        nav = pd.Series([1.0, 1.2, 0.9, 1.35], index=pd.date_range("2024-01-01", periods=4))

        stats = calculate_metrics(nav)

        self.assertAlmostEqual(stats["total_return"], 0.35)
        self.assertAlmostEqual(stats["max_drawdown"], -0.25)
        self.assertTrue(math.isfinite(stats["annual_return"]))
        self.assertTrue(math.isfinite(stats["calmar"]))

    def test_run_weighted_nav_applies_initial_turnover_cost_and_next_day_return(self):
        dates = pd.date_range("2024-01-01", periods=3)
        prices = pd.DataFrame(
            {
                "510300": [100.0, 110.0, 121.0],
                "513100": [100.0, 100.0, 100.0],
                "518880": [100.0, 100.0, 100.0],
                "511260": [100.0, 100.0, 100.0],
                "511880": [100.0, 100.0, 100.0],
            },
            index=dates,
        )

        nav, weights, run_stats = run_weighted_nav(
            prices=prices,
            eval_start="2024-01-01",
            eval_end="2024-01-03",
            rebalance_dates={dates[0]},
            make_weights=lambda _date: {"510300": 1.0},
            cost_rate=0.001,
        )

        self.assertAlmostEqual(nav.iloc[0], 0.999)
        self.assertAlmostEqual(nav.iloc[-1], 0.999 * 1.1 * 1.1)
        self.assertAlmostEqual(weights.loc[dates[0], "510300"], 1.0)
        self.assertEqual(run_stats["rebalance_count"], 1.0)
        self.assertAlmostEqual(run_stats["estimated_cost"], 0.001)

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

        make_weights = make_ram_topn(prices, top_n=2, momentum_window=3, volatility_window=3)
        weights = make_weights(dates[-1])

        self.assertIn("510300", weights)
        self.assertIn("518880", weights)
        self.assertNotIn("513100", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_ram_topn_moves_to_defense_when_all_scores_are_negative(self):
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

        make_weights = make_ram_topn(prices, top_n=2, momentum_window=3, volatility_window=3)
        weights = make_weights(dates[-1])

        self.assertEqual(weights, {DEFENSE_ASSET: 1.0})

    def test_satellite_signal_requires_positive_ram_and_trend(self):
        dates = pd.date_range("2024-01-01", periods=8)
        prices = pd.DataFrame(
            {
                "510300": [100, 101, 102, 103, 104, 106, 108, 111],
                "513100": [100, 99, 98, 97, 96, 95, 94, 93],
                "511880": [100.0] * 8,
            },
            index=dates,
            dtype=float,
        )
        config = SatelliteConfig(
            name="unit_satellite",
            risk_assets=("510300", "513100"),
            defense_asset="511880",
            top_n=1,
            momentum_window=3,
            volatility_window=3,
            trend_window=3,
            rebalance_interval=1,
            cost_rate=0.0,
        )

        weights = build_satellite_target_weights(prices, dates[-1], config)

        self.assertEqual(set(weights), {"510300"})
        self.assertAlmostEqual(weights["510300"], 1.0)

    def test_satellite_signal_uses_defense_when_no_asset_passes(self):
        dates = pd.date_range("2024-01-01", periods=8)
        prices = pd.DataFrame(
            {
                "510300": [100, 99, 98, 97, 96, 95, 94, 93],
                "513100": [100, 99, 98, 97, 96, 95, 94, 93],
                "511880": [100.0] * 8,
            },
            index=dates,
            dtype=float,
        )
        config = SatelliteConfig(
            name="unit_satellite",
            risk_assets=("510300", "513100"),
            defense_asset="511880",
            top_n=1,
            momentum_window=3,
            volatility_window=3,
            trend_window=3,
            rebalance_interval=1,
            cost_rate=0.0,
        )

        weights = build_satellite_target_weights(prices, dates[-1], config)

        self.assertEqual(weights, {"511880": 1.0})

    def test_drawdown_scale_steps_down_before_thirty_percent(self):
        config = SatelliteConfig(name="unit_satellite")

        self.assertEqual(drawdown_scale(-0.10, config), 1.0)
        self.assertEqual(drawdown_scale(-0.16, config), 0.5)
        self.assertEqual(drawdown_scale(-0.23, config), 0.25)
        self.assertEqual(drawdown_scale(-0.30, config), 0.0)

    def test_run_satellite_config_reports_gate_columns(self):
        dates = pd.date_range("2024-01-01", periods=40)
        prices = pd.DataFrame(
            {
                "510300": [100 + i for i in range(40)],
                "513100": [100.0] * 40,
                "511880": [100.0] * 40,
            },
            index=dates,
            dtype=float,
        )
        config = SatelliteConfig(
            name="unit_satellite",
            risk_assets=("510300", "513100"),
            defense_asset="511880",
            top_n=1,
            momentum_window=3,
            volatility_window=3,
            trend_window=3,
            rebalance_interval=5,
            cost_rate=0.0,
        )

        result = run_satellite_config(prices, config, "2024-01-01", "2024-02-09")

        self.assertEqual(result["strategy"], "unit_satellite")
        self.assertIn("passes_return_gate", result)
        self.assertIn("passes_drawdown_gate", result)
        self.assertIn("cooldown_days", result)

    def test_run_fixed_blend_config_reports_exposure_and_gates(self):
        dates = pd.date_range("2024-01-01", periods=40)
        prices = pd.DataFrame(
            {
                "513100": [100 + i for i in range(40)],
                "518880": [100.0 + i * 0.5 for i in range(40)],
                "511880": [100.0] * 40,
            },
            index=dates,
            dtype=float,
        )

        result = run_fixed_blend_config(
            prices=prices,
            name="fixed_unit",
            weights={"513100": 0.5, "518880": 0.5},
            gross_exposure=1.5,
            eval_start="2024-01-01",
            eval_end="2024-02-09",
        )

        self.assertEqual(result["strategy"], "fixed_unit")
        self.assertEqual(result["strategy_family"], "fixed_blend")
        self.assertAlmostEqual(result["gross_exposure"], 1.5)
        self.assertIn("passes_return_gate", result)
        self.assertIn("passes_drawdown_gate", result)

    def test_select_satellite_candidate_prefers_passing_rows(self):
        scan = pd.DataFrame(
            [
                {
                    "strategy": "near_miss",
                    "annual_return": 0.49,
                    "max_drawdown": -0.12,
                    "calmar": 4.0,
                    "passes_return_gate": False,
                    "passes_drawdown_gate": True,
                },
                {
                    "strategy": "pass",
                    "annual_return": 0.51,
                    "max_drawdown": -0.20,
                    "calmar": 2.55,
                    "passes_return_gate": True,
                    "passes_drawdown_gate": True,
                },
            ]
        )

        candidate, gate = select_satellite_candidate(scan)

        self.assertEqual(candidate["strategy"], "pass")
        self.assertTrue(gate["has_passing_candidate"])

    def test_select_satellite_candidate_returns_best_near_miss(self):
        scan = pd.DataFrame(
            [
                {
                    "strategy": "dd_fail",
                    "annual_return": 0.90,
                    "max_drawdown": -0.45,
                    "calmar": 2.0,
                    "passes_return_gate": True,
                    "passes_drawdown_gate": False,
                },
                {
                    "strategy": "return_fail",
                    "annual_return": 0.30,
                    "max_drawdown": -0.08,
                    "calmar": 3.75,
                    "passes_return_gate": False,
                    "passes_drawdown_gate": True,
                },
            ]
        )

        candidate, gate = select_satellite_candidate(scan)

        self.assertEqual(candidate["strategy"], "return_fail")
        self.assertFalse(gate["has_passing_candidate"])

    def test_select_satellite_candidate_near_miss_prioritizes_return_after_drawdown_gate(self):
        scan = pd.DataFrame(
            [
                {
                    "strategy": "high_calmar_low_return",
                    "annual_return": 0.20,
                    "max_drawdown": -0.10,
                    "calmar": 2.00,
                    "passes_return_gate": False,
                    "passes_drawdown_gate": True,
                },
                {
                    "strategy": "higher_return_near_miss",
                    "annual_return": 0.28,
                    "max_drawdown": -0.26,
                    "calmar": 1.08,
                    "passes_return_gate": False,
                    "passes_drawdown_gate": True,
                },
            ]
        )

        candidate, gate = select_satellite_candidate(scan)

        self.assertEqual(candidate["strategy"], "higher_return_near_miss")
        self.assertFalse(gate["has_passing_candidate"])

    def test_asset_diagnostics_reports_each_symbol(self):
        dates = pd.date_range("2024-01-01", periods=5)
        prices = pd.DataFrame(
            {
                "510300": [100, 101, 102, 103, 104],
                "511880": [100, 100.01, 100.02, 100.03, 100.04],
            },
            index=dates,
            dtype=float,
        )

        diagnostics = compute_asset_diagnostics(prices)

        self.assertEqual(set(diagnostics["symbol"]), {"510300", "511880"})
        self.assertIn("annual_return", diagnostics.columns)
        self.assertIn("max_drawdown", diagnostics.columns)

    def test_satellite_final_markdown_names_candidate_and_gate(self):
        scan = pd.DataFrame(
            [
                {
                    "strategy": "pass",
                    "annual_return": 0.51,
                    "max_drawdown": -0.20,
                    "calmar": 2.55,
                    "passes_return_gate": True,
                    "passes_drawdown_gate": True,
                    "rebalance_count": 10.0,
                    "estimated_cost": 0.03,
                    "cooldown_days": 0.0,
                    "risk_asset_exposure": 0.9,
                }
            ]
        )
        candidate, gate = select_satellite_candidate(scan)

        markdown = build_satellite_final_markdown(candidate, gate, scan)

        self.assertIn("pass", markdown)
        self.assertIn("50%", markdown)
        self.assertIn("-30%", markdown)

    def test_satellite_date_windows_handle_late_common_start(self):
        index = pd.date_range("2021-05-25", periods=700, freq="B")

        windows = satellite_date_windows(index)

        self.assertEqual(windows["eval_start"], "2021-05-25")
        self.assertLess(windows["train_start"], windows["train_end"])
        self.assertLess(windows["train_end"], windows["oos_start"])
        self.assertLess(windows["oos_start"], windows["oos_end"])

    def test_build_manifest_payload_records_artifacts_and_best_candidate(self):
        base_df = pd.DataFrame(
            [
                {
                    "strategy": "baseline_china_permanent_25_annual_no_cost",
                    "annual_return": 0.05,
                    "calmar": 0.5,
                    "max_drawdown": -0.10,
                }
            ]
        )
        scan_df = pd.DataFrame(
            [
                {
                    "strategy": "ram_top2_m20_v120_f21_cost",
                    "annual_return": 0.12,
                    "calmar": 1.1,
                    "max_drawdown": -0.09,
                }
            ]
        )

        payload = build_manifest_payload(
            latest_price_date="2026-06-15",
            base_df=base_df,
            scan_df=scan_df,
            train_df=scan_df,
        )

        self.assertEqual(payload["best_research_candidate"], "ram_top2_m20_v120_f21_cost")
        self.assertEqual(payload["base_strategy_rows"], 1)
        self.assertEqual(payload["ram_scan_rows"], 1)
        self.assertIn("latest_summary.md", payload["artifacts"])

    def test_markdown_table_falls_back_when_tabulate_is_missing(self):
        from my_quant.strategy_research.experiment.reports import markdown_table

        original = pd.DataFrame.to_markdown

        def missing_tabulate(_frame, *args, **kwargs):
            raise ImportError("Missing optional dependency 'tabulate'")

        try:
            pd.DataFrame.to_markdown = missing_tabulate
            table = markdown_table(pd.DataFrame({"annual_return": [0.12345], "strategy": ["unit"]}), index=False, floatfmt=".2f")
        finally:
            pd.DataFrame.to_markdown = original

        self.assertIn("| annual_return | strategy |", table)
        self.assertIn("| 0.12", table)
        self.assertIn("unit", table)

    def test_external_price_loader_drops_legacy_ticker_header_row(self):
        from my_quant.strategy_research.experiment.external_probe import load_external_prices

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            dates = ["2017-01-03", "2017-01-04", "2017-01-05"]
            values = {
                "TQQQ": [10.0, 10.5, 11.0],
                "SOXL": [20.0, 19.5, 21.0],
                "TECL": [30.0, 31.0, 32.0],
                "UPRO": [40.0, 41.0, 42.0],
                "BTC_USD": [1000.0, 1010.0, 1020.0],
                "ETH_USD": [100.0, 110.0, 120.0],
                "GLD": [110.0, 111.0, 112.0],
                "TLT": [90.0, 89.0, 88.0],
            }
            for filename, closes in values.items():
                symbol = filename.replace("_", "-")
                lines = ["date,close", f",{symbol}"]
                lines.extend(f"{date},{close}" for date, close in zip(dates, closes))
                (cache_dir / f"{filename}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

            prices = load_external_prices(cache_dir, refresh=False)

        self.assertEqual(float(prices.loc[pd.Timestamp("2017-01-03"), "TQQQ"]), 10.0)
        self.assertIn("CASH", prices.columns)

    def test_walk_forward_analysis_returns_rolling_and_anchored_rows(self):
        dates = pd.date_range("2024-01-01", periods=18)
        prices = pd.DataFrame(
            {
                "510300": range(100, 118),
                "513100": range(100, 118),
                "518880": range(100, 118),
                "511260": range(100, 118),
                "511880": [100.0] * 18,
            },
            index=dates,
            dtype=float,
        )
        configs = [
            StrategyConfig("baseline_china_permanent_25_annual_no_cost", "permanent"),
            StrategyConfig("equal_weight_5_assets_monthly_cost", "equal_weight", interval_days=3, cost_rate=0.0),
        ]

        result = walk_forward_analysis(prices, configs, train_size=6, test_size=4, step_size=4)

        self.assertEqual(set(result["mode"]), {"rolling", "anchored"})
        self.assertIn("best_strategy", result.columns)
        self.assertIn("oos_annual_return", result.columns)

    def test_factor_ic_summary_reports_core_factors(self):
        dates = pd.date_range("2024-01-01", periods=12)
        prices = pd.DataFrame(
            {
                "510300": [100, 101, 102, 103, 105, 107, 106, 108, 110, 111, 113, 115],
                "513100": [100, 99, 100, 98, 99, 101, 102, 103, 104, 106, 107, 108],
                "518880": [100, 100, 101, 102, 102, 103, 104, 104, 105, 106, 106, 107],
                "511260": [100, 100, 100, 101, 101, 101, 102, 102, 102, 103, 103, 103],
                "511880": [100.0] * 12,
            },
            index=dates,
            dtype=float,
        )

        summary = summarize_factor_ic(prices, momentum_window=3, volatility_window=3, forward_window=2)

        self.assertTrue({"momentum", "ram", "low_volatility", "trend_strength"}.issubset(set(summary["factor"])))
        self.assertIn("mean_ic", summary.columns)

    def test_b1_indicators_compute_trend_and_pullback_columns(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import compute_b1_frame

        dates = pd.date_range("2024-01-01", periods=160, freq="B")
        close = pd.Series([100 + i * 0.45 for i in range(160)], index=dates, dtype=float)
        close.iloc[-10:] = [200, 196, 192, 188, 184, 180, 176, 172, 168, 165]
        high = close + 3.0
        high.iloc[-10:] = 200.0
        bars = pd.DataFrame(
            {
                "open": close,
                "high": high,
                "low": close - 1.0,
                "close": close,
            },
            index=dates,
        )

        frame = compute_b1_frame(bars)

        self.assertIn("bbi", frame.columns)
        self.assertIn("double_ema10", frame.columns)
        self.assertIn("kdj_j", frame.columns)
        self.assertIn("entry_signal", frame.columns)
        self.assertTrue(bool(frame["entry_signal"].iloc[-1]))
        self.assertGreater(float(frame["b1_score"].iloc[-1]), 0.0)

    def test_b1_entry_quality_filter_requires_minimum_momentum(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, compute_b1_frame

        dates = pd.date_range("2024-01-01", periods=160, freq="B")
        close = pd.Series([100 + i * 0.45 for i in range(160)], index=dates, dtype=float)
        close.iloc[-10:] = [200, 196, 192, 188, 184, 180, 176, 172, 168, 165]
        high = close + 3.0
        high.iloc[-10:] = 200.0
        bars = pd.DataFrame({"open": close, "high": high, "low": close - 1.0, "close": close}, index=dates)

        base = compute_b1_frame(bars)
        filtered = compute_b1_frame(bars, B1BacktestConfig(min_entry_mom20=0.02))

        self.assertTrue(bool(base["entry_signal"].iloc[-1]))
        self.assertFalse(bool(filtered["entry_signal"].iloc[-1]))

    def test_b1_entry_quality_filter_blocks_overextended_price_vs_bbi(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, compute_b1_frame

        dates = pd.date_range("2024-01-01", periods=160, freq="B")
        close = pd.Series([100 + i * 0.45 for i in range(160)], index=dates, dtype=float)
        close.iloc[-10:] = [200, 196, 192, 188, 184, 180, 176, 172, 168, 165]
        high = close + 3.0
        high.iloc[-10:] = 200.0
        bars = pd.DataFrame({"open": close, "high": high, "low": close - 1.0, "close": close}, index=dates)

        filtered = compute_b1_frame(bars, B1BacktestConfig(max_entry_close_bbi=0.005))

        self.assertFalse(bool(filtered["entry_signal"].iloc[-1]))

    def test_b1_entry_quality_filter_blocks_overheated_momentum(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, compute_b1_frame

        dates = pd.date_range("2024-01-01", periods=160, freq="B")
        close = pd.Series([100 + i * 0.45 for i in range(160)], index=dates, dtype=float)
        close.iloc[-10:] = [200, 196, 192, 188, 184, 180, 176, 172, 168, 165]
        high = close + 3.0
        high.iloc[-10:] = 200.0
        bars = pd.DataFrame({"open": close, "high": high, "low": close - 1.0, "close": close}, index=dates)

        filtered = compute_b1_frame(bars, B1BacktestConfig(max_entry_mom20=0.01))

        self.assertFalse(bool(filtered["entry_signal"].iloc[-1]))

    def test_b1_score_weights_are_configurable_for_calibration(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, compute_b1_frame

        dates = pd.date_range("2024-01-01", periods=160, freq="B")
        close = pd.Series([100 + i * 0.45 for i in range(160)], index=dates, dtype=float)
        close.iloc[-10:] = [200, 196, 192, 188, 184, 180, 176, 172, 168, 165]
        high = close + 3.0
        high.iloc[-10:] = 200.0
        bars = pd.DataFrame({"open": close, "high": high, "low": close - 1.0, "close": close}, index=dates)
        config = B1BacktestConfig(
            score_trend_weight=1.0,
            score_pullback_weight=2.0,
            score_price_buffer_weight=3.0,
        )

        frame = compute_b1_frame(bars, config)
        row = frame.iloc[-1]
        expected_score = (
            (float(row["double_ema10"]) / float(row["bbi"]) - 1.0) * config.score_trend_weight
            + max(config.kdj_j_threshold - float(row["kdj_j"]), 0.0)
            / config.kdj_j_threshold
            * config.score_pullback_weight
            + (float(row["close"]) / float(row["bbi"]) - 1.0) * config.score_price_buffer_weight
        )

        self.assertTrue(bool(row["entry_signal"]))
        self.assertAlmostEqual(float(row["b1_score"]), expected_score)

    def test_b1_candidate_ranking_limits_top_two_to_half_weight(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, rank_b1_candidates

        date = pd.Timestamp("2024-01-02")
        panels = {
            "000001": pd.DataFrame({"entry_signal": [True], "b1_score": [10.0]}, index=[date]),
            "000002": pd.DataFrame({"entry_signal": [True], "b1_score": [30.0]}, index=[date]),
            "000003": pd.DataFrame({"entry_signal": [True], "b1_score": [20.0]}, index=[date]),
        }

        candidates = rank_b1_candidates(panels, date, B1BacktestConfig(top_n=2, max_position=0.5))

        self.assertEqual([row["symbol"] for row in candidates], ["000002", "000003"])
        self.assertEqual([row["target_weight"] for row in candidates], [0.5, 0.5])

    def test_b1_market_filter_blocks_new_entries_below_bbi(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import market_allows_entry

        date = pd.Timestamp("2024-01-02")
        market = pd.DataFrame({"close": [99.0], "bbi": [100.0]}, index=[date])

        self.assertFalse(market_allows_entry(market, date))

    def test_b1_market_regime_filter_blocks_entries_when_ma20_below_ma60(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import market_allows_entry
        from my_quant.strategy_research.run_b1_trend_pullback import apply_market_regime_filter

        date = pd.Timestamp("2024-01-02")
        market = pd.DataFrame(
            {"close": [105.0], "bbi": [100.0], "ma20": [99.0], "ma60": [101.0]},
            index=[date],
        )

        filtered = apply_market_regime_filter(market, require_ma20_gt_ma60=True)

        self.assertFalse(market_allows_entry(filtered, date))

    def test_b1_backtest_buys_next_day_and_sells_partial_take_profit(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, run_b1_backtest

        dates = pd.date_range("2024-01-01", periods=6, freq="B")
        symbol_frame = pd.DataFrame(
            {
                "close": [100.0, 100.0, 111.0, 122.0, 123.0, 121.0],
                "bbi": [90.0, 90.0, 91.0, 92.0, 93.0, 94.0],
                "double_ema10": [95.0, 95.0, 96.0, 97.0, 98.0, 99.0],
                "kdj_j": [10.0, 20.0, 20.0, 20.0, 20.0, 20.0],
                "entry_signal": [True, False, False, False, False, False],
                "b1_score": [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            },
            index=dates,
        )
        market = pd.DataFrame({"close": [100.0] * 6, "bbi": [90.0] * 6}, index=dates)

        result = run_b1_backtest(
            {"000001": symbol_frame},
            market,
            B1BacktestConfig(cost_rate=0.0, take_profit_levels=(0.10,), take_profit_fractions=(0.5,)),
        )

        trades = result.trades
        self.assertEqual(trades.iloc[0]["date"], dates[1])
        self.assertEqual(trades.iloc[0]["side"], "buy")
        self.assertEqual(trades.iloc[1]["side"], "sell")
        self.assertEqual(trades.iloc[1]["reason"], "take_profit_10")
        self.assertLess(float(trades.iloc[1]["shares"]), float(trades.iloc[0]["shares"]))

    def test_b1_realistic_execution_uses_open_price_and_round_lot(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, run_b1_backtest

        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        symbol_frame = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "close": [10.0, 12.0, 13.0],
                "bbi": [9.0, 9.0, 9.0],
                "entry_signal": [True, False, False],
                "b1_score": [10.0, 0.0, 0.0],
            },
            index=dates,
        )
        market = pd.DataFrame({"close": [100.0] * 3, "bbi": [90.0] * 3}, index=dates)

        result = run_b1_backtest(
            {"000001": symbol_frame},
            market,
            B1BacktestConfig(
                initial_cash=100_000.0,
                cost_rate=0.0,
                buy_price_column="open",
                sell_price_column="open",
                lot_size=100,
            ),
        )

        first_trade = result.trades.iloc[0]
        self.assertEqual(first_trade["side"], "buy")
        self.assertEqual(first_trade["date"], dates[1])
        self.assertAlmostEqual(float(first_trade["price"]), 11.0)
        self.assertEqual(float(first_trade["shares"]), 4500.0)
        self.assertEqual(float(first_trade["value"]), 49_500.0)

    def test_b1_realistic_execution_blocks_limit_up_buy_and_limit_down_sell(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig, run_b1_backtest

        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        limit_up_frame = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0, 12.0, 12.0],
                "close": [10.0, 11.0, 12.0, 12.0, 12.0],
                "bbi": [9.0] * 5,
                "entry_signal": [True, False, False, False, False],
                "b1_score": [10.0, 0.0, 0.0, 0.0, 0.0],
            },
            index=dates,
        )
        sell_block_frame = pd.DataFrame(
            {
                "open": [10.0, 9.5, 10.5, 9.45, 9.0],
                "close": [10.0, 10.0, 10.5, 9.5, 9.0],
                "bbi": [9.0, 9.0, 9.0, 10.0, 10.0],
                "entry_signal": [False, True, False, False, False],
                "b1_score": [0.0, 10.0, 0.0, 0.0, 0.0],
            },
            index=dates,
        )
        market = pd.DataFrame({"close": [100.0] * 5, "bbi": [90.0] * 5}, index=dates)

        result = run_b1_backtest(
            {"000001": limit_up_frame, "000002": sell_block_frame},
            market,
            B1BacktestConfig(
                initial_cash=100_000.0,
                cost_rate=0.0,
                buy_price_column="open",
                sell_price_column="open",
                lot_size=100,
                limit_up_pct=0.10,
                limit_down_pct=0.10,
            ),
        )

        trades = result.trades
        self.assertNotIn("000001", set(trades["symbol"]))
        self.assertEqual(list(trades["side"]), ["buy", "sell"])
        self.assertEqual(trades.iloc[0]["symbol"], "000002")
        self.assertEqual(trades.iloc[0]["date"], dates[2])
        self.assertEqual(trades.iloc[1]["symbol"], "000002")
        self.assertEqual(trades.iloc[1]["date"], dates[4])

    def test_b1_retry_call_recovers_from_transient_error(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import retry_call

        calls = []

        def flaky_call():
            calls.append("call")
            if len(calls) == 1:
                raise ConnectionError("temporary disconnect")
            return "ok"

        self.assertEqual(retry_call(flaky_call, attempts=3, delay_seconds=0.0), "ok")
        self.assertEqual(len(calls), 2)

    def test_b1_tx_symbol_adds_exchange_prefix(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import a_share_tx_symbol

        self.assertEqual(a_share_tx_symbol("000001"), "sz000001")
        self.assertEqual(a_share_tx_symbol("300750"), "sz300750")
        self.assertEqual(a_share_tx_symbol("600000"), "sh600000")
        self.assertEqual(a_share_tx_symbol("688702"), "sh688702")

    def test_b1_tushare_symbol_adds_exchange_suffix(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import a_share_tushare_code

        self.assertEqual(a_share_tushare_code("000001"), "000001.SZ")
        self.assertEqual(a_share_tushare_code("300750"), "300750.SZ")
        self.assertEqual(a_share_tushare_code("600000"), "600000.SH")
        self.assertEqual(a_share_tushare_code("688702"), "688702.SH")

    def test_b1_tushare_token_is_required_before_data_calls(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import resolve_tushare_token

        with self.assertRaisesRegex(RuntimeError, "TUSHARE_TOKEN"):
            resolve_tushare_token(env={})

        self.assertEqual(resolve_tushare_token(token="abc", env={}), "abc")
        self.assertEqual(resolve_tushare_token(env={"TUSHARE_TOKEN": "from-env"}), "from-env")

    def test_b1_normalize_tushare_bars_sorts_dates_and_keeps_ohlc(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import normalize_tushare_bars

        raw = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": ["20240103", "20240102"],
                "open": [11.0, 10.0],
                "high": [12.0, 11.0],
                "low": [10.5, 9.5],
                "close": [11.5, 10.5],
                "vol": [100.0, 80.0],
                "amount": [1200.0, 900.0],
            }
        )

        bars = normalize_tushare_bars(raw)

        self.assertEqual(list(bars.index), [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")])
        self.assertEqual(list(bars[["open", "high", "low", "close"]].iloc[0]), [10.0, 11.0, 9.5, 10.5])
        self.assertIn("volume", bars.columns)
        self.assertIn("amount", bars.columns)

    def test_b1_tushare_fetch_reads_normalized_cache_without_network(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import fetch_a_share_bars_tushare

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "000001_20240101_20240103_tushare_qfq.csv"
            pd.DataFrame(
                {
                    "date": ["2024-01-03", "2024-01-02"],
                    "open": [11.0, 10.0],
                    "high": [12.0, 11.0],
                    "low": [10.5, 9.5],
                    "close": [11.5, 10.5],
                }
            ).to_csv(cache_path, index=False)

            bars = fetch_a_share_bars_tushare("000001", "20240101", "20240103", Path(tmpdir))

        self.assertEqual(list(bars.index), [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")])
        self.assertAlmostEqual(float(bars.loc[pd.Timestamp("2024-01-03"), "close"]), 11.5)

    def test_b1_tushare_index_fetch_reads_cache_without_network(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import fetch_tushare_index_bars

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "000300.SH_20240101_20240103_tushare_index.csv"
            pd.DataFrame(
                {
                    "date": ["2024-01-03", "2024-01-02"],
                    "open": [3100.0, 3000.0],
                    "high": [3120.0, 3010.0],
                    "low": [3080.0, 2980.0],
                    "close": [3110.0, 3005.0],
                }
            ).to_csv(cache_path, index=False)

            bars = fetch_tushare_index_bars("000300.SH", "20240101", "20240103", Path(tmpdir))

        self.assertEqual(list(bars.index), [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")])
        self.assertAlmostEqual(float(bars.loc[pd.Timestamp("2024-01-02"), "close"]), 3005.0)

    def test_b1_market_frame_can_use_tushare_index_cache(self):
        from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame

        with tempfile.TemporaryDirectory() as tmpdir:
            dates = pd.date_range("2023-08-01", periods=130, freq="B")
            pd.DataFrame(
                {
                    "date": [date.strftime("%Y-%m-%d") for date in dates],
                    "open": [3000.0 + i for i in range(len(dates))],
                    "high": [3010.0 + i for i in range(len(dates))],
                    "low": [2990.0 + i for i in range(len(dates))],
                    "close": [3005.0 + i for i in range(len(dates))],
                }
            ).to_csv(Path(tmpdir) / "000300.SH_20230801_20240131_tushare_index.csv", index=False)

            market = build_market_frame(
                "2023-08-01",
                "2024-01-31",
                "2024-01-01",
                data_provider="tushare",
                data_dir=Path(tmpdir),
            )

        self.assertGreaterEqual(market.index.min(), pd.Timestamp("2024-01-01"))
        self.assertIn("bbi", market.columns)

    def test_b1_tushare_symbol_selection_filters_st_and_keeps_main_markets(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import select_eligible_tushare_symbols

        raw = pd.DataFrame(
            {
                "symbol": ["000001", "300750", "688702", "430001", "600001"],
                "name": ["平安银行", "宁德时代", "盛科通信", "北交所样本", "ST测试"],
            }
        )

        self.assertEqual(select_eligible_tushare_symbols(raw), ["000001", "300750", "688702"])

    def test_b1_active_universe_uses_asof_daily_basic_without_future_rows(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import select_active_tushare_universe

        stock_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "300001.SZ", "600001.SH", "300002.SZ"],
                "symbol": ["000001", "300001", "600001", "300002"],
                "name": ["平安银行", "特锐德", "ST测试", "低市值"],
            }
        )
        daily_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "300001.SZ", "600001.SH", "300002.SZ"],
                "trade_date": ["20240105", "20240110", "20240105", "20240105", "20240105"],
                "turnover_rate": [2.0, 100.0, 5.0, 9.0, 20.0],
                "circ_mv": [400_000.0, 400_000.0, 300_000.0, 300_000.0, 100_000.0],
                "pb": [1.2, 1.2, 2.0, 1.0, 1.5],
            }
        )

        universe = select_active_tushare_universe(
            daily_basic=daily_basic,
            stock_basic=stock_basic,
            as_of_date="2024-01-05",
            limit=10,
        )

        self.assertEqual(list(universe["symbol"]), ["300001", "000001"])
        self.assertAlmostEqual(float(universe.loc[universe["symbol"] == "000001", "turnover_rate"].iloc[0]), 2.0)
        self.assertTrue((universe["active_score"] > 0).all())

    def test_b1_trend_pullback_cli_accepts_tushare_provider(self):
        from my_quant.strategy_research.run_b1_trend_pullback import parse_args

        args = parse_args(["--data-provider", "tushare"])

        self.assertEqual(args.data_provider, "tushare")

    def test_b1_walk_forward_cli_accepts_entry_quality_and_market_filters(self):
        from my_quant.strategy_research.run_b1_walk_forward import parse_args

        args = parse_args(
            [
                "--symbols-file",
                "active.csv",
                "--max-entry-close-bbi",
                "0.275",
                "--min-entry-mom20",
                "0.02",
                "--max-entry-mom20",
                "0.75",
                "--market-ma20-gt-ma60",
            ]
        )

        self.assertEqual(args.symbols_file, Path("active.csv"))
        self.assertAlmostEqual(args.max_entry_close_bbi, 0.275)
        self.assertAlmostEqual(args.min_entry_mom20, 0.02)
        self.assertAlmostEqual(args.max_entry_mom20, 0.75)
        self.assertTrue(args.market_ma20_gt_ma60)

    def test_b1_walk_forward_builds_rolling_active_universe_map(self):
        from my_quant.strategy_research.run_b1_walk_forward import build_active_universe_symbol_map

        windows = [
            ("early", "2024-01-05", "2024-01-31"),
            ("late", "2024-02-05", "2024-02-29"),
        ]
        stock_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "300001.SZ"],
                "symbol": ["000001", "300001"],
                "name": ["平安银行", "特锐德"],
            }
        )
        daily_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "300001.SZ", "000001.SZ", "300001.SZ"],
                "trade_date": ["20240105", "20240105", "20240205", "20240205"],
                "turnover_rate": [5.0, 1.0, 1.0, 9.0],
                "circ_mv": [300_000.0, 300_000.0, 300_000.0, 300_000.0],
                "pb": [1.0, 1.0, 1.0, 1.0],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            daily_path = Path(tmpdir) / "daily_basic.csv"
            stock_path = Path(tmpdir) / "stock_basic.csv"
            daily_basic.to_csv(daily_path, index=False)
            stock_basic.to_csv(stock_path, index=False)

            symbol_map = build_active_universe_symbol_map(
                daily_basic_file=daily_path,
                stock_basic_file=stock_path,
                windows=windows,
                limit=1,
                rolling=True,
            )

        self.assertEqual(symbol_map, {"early": ["000001"], "late": ["300001"]})

    def test_b1_load_symbols_from_csv_file_reads_symbol_column(self):
        from my_quant.strategy_research.run_b1_walk_forward import load_symbols_from_csv_file

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "symbols.csv"
            pd.DataFrame({"symbol": ["1", "300750"], "name": ["one", "catl"]}).to_csv(path, index=False)

            symbols = load_symbols_from_csv_file(path)

        self.assertEqual(symbols, ["000001", "300750"])

    def test_b1_trend_pullback_import_does_not_require_akshare(self):
        code = """
import builtins
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "akshare":
        raise ModuleNotFoundError("No module named 'akshare'")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import my_quant.strategy_research.run_b1_trend_pullback
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_b1_select_symbol_sample_supports_offset_stride_and_limit(self):
        from my_quant.strategy_research.run_b1_trend_pullback import select_symbol_sample

        symbols = ["000001", "000002", "000003", "000004", "000005", "000006"]

        self.assertEqual(select_symbol_sample(symbols, limit=2, offset=1, stride=2), ["000002", "000004"])

    def test_b1_write_artifacts_uses_output_prefix(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import (
            B1BacktestConfig,
            run_b1_backtest,
            write_b1_artifacts,
        )

        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        frame = pd.DataFrame(
            {
                "close": [100.0, 101.0, 102.0],
                "bbi": [90.0, 90.0, 90.0],
                "entry_signal": [False, False, False],
                "b1_score": [0.0, 0.0, 0.0],
            },
            index=dates,
        )
        result = run_b1_backtest({"000001": frame}, frame, B1BacktestConfig())
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_b1_artifacts(
                result,
                "2024-01-01",
                "2024-01-03",
                1,
                results_dir=Path(tmpdir),
                artifact_prefix="unit_b1",
            )

        self.assertTrue(paths["summary"].endswith("unit_b1_summary.md"))
        self.assertTrue(paths["manifest"].endswith("unit_b1_manifest.json"))

    def test_b1_exit_scan_configs_name_thresholds_and_fractions(self):
        from my_quant.strategy_research.run_b1_exit_scan import exit_scan_configs

        configs = exit_scan_configs(levels_list=[(0.12, 0.24)], fractions_list=[(1.0, 1.0)])

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["name"], "tp12_24_f100_100")
        self.assertEqual(configs[0]["levels"], (0.12, 0.24))
        self.assertEqual(configs[0]["fractions"], (1.0, 1.0))

    def test_b1_walk_forward_gate_requires_every_window_to_pass(self):
        from my_quant.strategy_research.run_b1_walk_forward import summarize_window_gates

        rows = pd.DataFrame(
            [
                {"strategy": "candidate", "window": "full", "passes_return_gate": True, "passes_drawdown_gate": True},
                {"strategy": "candidate", "window": "train", "passes_return_gate": True, "passes_drawdown_gate": True},
                {"strategy": "candidate", "window": "oos", "passes_return_gate": False, "passes_drawdown_gate": True},
            ]
        )

        summary = summarize_window_gates(rows)

        self.assertEqual(summary.loc[0, "strategy"], "candidate")
        self.assertFalse(bool(summary.loc[0, "passes_all_windows"]))
        self.assertEqual(int(summary.loc[0, "return_fail_windows"]), 1)
        self.assertEqual(int(summary.loc[0, "drawdown_fail_windows"]), 0)

    def test_b1_walk_forward_builds_exit_config_with_entry_quality_filters(self):
        from my_quant.strategy_research.run_b1_walk_forward import build_b1_config_from_exit_config

        config = build_b1_config_from_exit_config(
            {"levels": (0.08, 0.16), "fractions": (1.0, 1.0)},
            max_entry_close_bbi=0.275,
            min_entry_mom20=0.02,
            max_entry_mom20=0.75,
        )

        self.assertEqual(config.take_profit_levels, (0.08, 0.16))
        self.assertEqual(config.take_profit_fractions, (1.0, 1.0))
        self.assertAlmostEqual(config.max_entry_close_bbi, 0.275)
        self.assertAlmostEqual(config.min_entry_mom20, 0.02)
        self.assertAlmostEqual(config.max_entry_mom20, 0.75)

    def test_b1_report_round_trips_group_partial_exits(self):
        from my_quant.strategy_research.web_report.build_b1_quality_report import build_round_trips

        trades = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2024-01-02"),
                    "symbol": "000001",
                    "side": "buy",
                    "shares": 10.0,
                    "price": 100.0,
                    "value": 1000.0,
                    "reason": "next_day_entry",
                    "cash_after": 0.0,
                },
                {
                    "date": pd.Timestamp("2024-01-04"),
                    "symbol": "000001",
                    "side": "sell",
                    "shares": 5.0,
                    "price": 110.0,
                    "value": 550.0,
                    "reason": "take_profit_10",
                    "cash_after": 550.0,
                },
                {
                    "date": pd.Timestamp("2024-01-08"),
                    "symbol": "000001",
                    "side": "sell",
                    "shares": 5.0,
                    "price": 90.0,
                    "value": 450.0,
                    "reason": "break_bbi",
                    "cash_after": 1000.0,
                },
            ]
        )

        round_trips = build_round_trips(trades, cost_rate=0.0, display_capital=1.0)

        self.assertEqual(len(round_trips), 1)
        row = round_trips.iloc[0]
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["sell_count"], 2)
        self.assertEqual(row["sell_date"], pd.Timestamp("2024-01-08"))
        self.assertAlmostEqual(row["average_sell_price"], 100.0)
        self.assertAlmostEqual(row["net_pnl"], 0.0)
        self.assertAlmostEqual(row["net_return_pct"], 0.0)
        self.assertIn("take_profit_10", row["exit_reasons"])
        self.assertIn("break_bbi", row["exit_reasons"])

    def test_b1_report_html_uses_runtime_generated_at(self):
        from my_quant.strategy_research.web_report.build_b1_quality_report import ReportData, build_html

        data = ReportData(
            nav=pd.DataFrame({"date": [pd.Timestamp("2025-01-01")], "nav": [1.0], "drawdown": [0.0]}),
            trades=pd.DataFrame(),
            round_trips=pd.DataFrame(),
            summary={"annual_return": 0.5, "max_drawdown": -0.1, "calmar": 5.0, "trade_count": 0.0},
            symbol_names={},
            symbol_count=0,
            artifacts={},
        )

        html = build_html(data, "unit_strategy", "2025-01-01", "2025-01-01", generated_at="2099-01-02 18:00:00")

        self.assertIn("生成时间：2099-01-02 18:00:00", html)
        self.assertNotIn("生成时间：2026-06-17", html)

    def test_b1_daily_launchd_plist_runs_runner_at_1800(self):
        plist_path = Path("my_quant/strategy_research/automation/com.jettlin.xquant.b1-daily.plist")

        payload = plistlib.loads(plist_path.read_bytes())

        self.assertEqual(payload["Label"], "com.jettlin.xquant.b1-daily")
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 18, "Minute": 0})
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/bin/bash",
                "/Users/jettlin/Documents/xquant-beginner/my_quant/strategy_research/automation/run_b1_daily.sh",
            ],
        )
        self.assertEqual(payload["WorkingDirectory"], "/Users/jettlin/Documents/xquant-beginner")
        self.assertTrue(payload["StandardOutPath"].endswith("my_quant/strategy_research/logs/b1_premarket.out.log"))
        self.assertTrue(payload["StandardErrorPath"].endswith("my_quant/strategy_research/logs/b1_premarket.err.log"))

    def test_b1_daily_runner_loads_env_and_writes_dated_latest_reports(self):
        script_path = Path("my_quant/strategy_research/automation/run_b1_daily.sh")

        script = script_path.read_text(encoding="utf-8")

        self.assertIn(".env.local", script)
        self.assertIn("TUSHARE_TOKEN", script)
        self.assertIn("B1_END_DATE", script)
        self.assertIn("build_b1_premarket_plan", script)
        self.assertIn("b1_premarket_plan_${RUN_DATE}", script)
        self.assertIn("b1_premarket_plan_latest.html", script)

    def test_b1_report_manifest_records_requested_html_path(self):
        from my_quant.strategy_research.web_report.build_b1_quality_report import build_report_manifest_payload

        payload = build_report_manifest_payload(
            strategy="unit_daily",
            html_path=Path("/tmp/unit_daily.html"),
            display_capital=1_000_000.0,
            artifacts={"nav": "/tmp/unit_nav.csv"},
        )

        self.assertEqual(payload["html"], "/tmp/unit_daily.html")
        self.assertEqual(payload["artifacts"]["nav"], "unit_nav.csv")

    def test_b1_premarket_plan_turns_close_signal_into_next_day_buy_plan(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig
        from my_quant.strategy_research.web_report.build_b1_premarket_plan import build_entry_plan_rows

        signal_date = pd.Timestamp("2026-06-17")
        panel = pd.DataFrame(
            {
                "close": [10.0],
                "bbi": [9.0],
                "double_ema10": [9.5],
                "kdj_j": [8.0],
                "entry_signal": [True],
                "b1_score": [12.5],
                "entry_close_bbi": [0.1111],
                "entry_mom20": [0.08],
            },
            index=[signal_date],
        )
        market = pd.DataFrame({"close": [4100.0], "bbi": [4000.0], "ma20": [4050.0], "ma60": [3900.0]}, index=[signal_date])

        rows = build_entry_plan_rows(
            panels={"000001": panel},
            market=market,
            signal_date=signal_date,
            plan_date=pd.Timestamp("2026-06-18"),
            config=B1BacktestConfig(),
            held_symbols=set(),
            symbol_names={"000001": "平安银行"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "candidate_buy")
        self.assertEqual(rows[0]["plan_date"], "2026-06-18")
        self.assertEqual(rows[0]["symbol"], "000001")
        self.assertEqual(rows[0]["name"], "平安银行")
        self.assertAlmostEqual(rows[0]["target_weight"], 0.5)
        self.assertIn("不追高", rows[0]["guardrail"])

    def test_b1_premarket_plan_flags_break_bbi_exit_for_existing_position(self):
        from my_quant.strategy_research.experiment.b1_trend_pullback import B1BacktestConfig
        from my_quant.strategy_research.web_report.build_b1_premarket_plan import build_exit_plan_rows

        signal_date = pd.Timestamp("2026-06-17")
        panel = pd.DataFrame({"close": [8.8], "bbi": [9.0]}, index=[signal_date])
        open_positions = [
            {
                "symbol": "000001",
                "buy_date": pd.Timestamp("2026-06-10"),
                "buy_price": 10.0,
                "shares": 100.0,
                "cost_basis": 10.0,
            }
        ]

        rows = build_exit_plan_rows(
            open_positions=open_positions,
            panels={"000001": panel},
            signal_date=signal_date,
            plan_date=pd.Timestamp("2026-06-18"),
            config=B1BacktestConfig(),
            symbol_names={"000001": "平安银行"},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "exit_sell")
        self.assertEqual(rows[0]["reason"], "break_bbi")
        self.assertEqual(rows[0]["priority"], "先卖出/降风险")
        self.assertLess(rows[0]["unrealized_return"], 0.0)


if __name__ == "__main__":
    unittest.main()
