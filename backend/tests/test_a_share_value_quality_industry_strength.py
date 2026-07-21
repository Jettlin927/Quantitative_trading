from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.database import Base
from backend.app.models import (
    Index,
    IndexDailyBar,
    IndustryClassification,
    IndustryMember,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    TradeCalendar,
)
from backend.app.quant_research.a_share_value_quality_industry_strength import (
    attach_value_quality_fundamentals,
    build_value_quality_target_frame,
    calculate_industry_strength,
    rebalance_signal_dates,
    score_value_quality_candidates,
    select_buffered_targets,
    simulate_value_quality_portfolio,
    summarize_value_quality_artifacts,
)
from backend.app.quant_research.run_config import validate_run_config
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.app.quant_research.strategy_registry import resolve_strategy_definition
from backend.tests.test_quant_research_foundation import calendar_for_dates


REPO_ROOT = Path(__file__).resolve().parents[2]


class AShareValueQualityIndustryStrengthConfigTest(unittest.TestCase):
    def test_two_predeclared_variants_resolve_to_one_stable_strategy(self) -> None:
        variants = {}
        for name in (
            "a_share_value_quality_baseline.json",
            "a_share_value_quality_industry_strength.json",
        ):
            config = json.loads((REPO_ROOT / "configs" / "research" / name).read_text())
            config["qualityRunId"] = "quality-test"
            normalized = validate_run_config(config, verify_universe_source=False)
            definition = resolve_strategy_definition(normalized)
            variants[normalized["variantId"]] = normalized
            self.assertEqual(
                (definition.strategy_id, definition.strategy_version, definition.scope),
                (
                    "a_share_value_quality_industry_strength",
                    "1",
                    "a_share_cross_section",
                ),
            )
            self.assertEqual(
                definition.required_tables,
                (
                    "trade_calendars",
                    "stock_listings",
                    "stock_daily_bars",
                    "stock_adjust_factors",
                    "stock_limit_prices",
                    "stock_suspend_events",
                    "industry_classifications",
                    "industry_members",
                    "stock_daily_basic",
                    "stock_financial_indicators",
                    "indices",
                    "index_daily_bars",
                    "universe",
                ),
            )
        self.assertEqual(
            set(variants),
            {"value_quality_baseline", "value_quality_industry_strength"},
        )
        self.assertEqual(
            variants["value_quality_baseline"]["trialRegistry"],
            variants["value_quality_industry_strength"]["trialRegistry"],
        )

    def test_static_contract_rejects_unregistered_parameter_changes(self) -> None:
        path = REPO_ROOT / "configs" / "research" / "a_share_value_quality_industry_strength.json"
        config = json.loads(path.read_text())
        config["qualityRunId"] = "quality-test"
        cases = (
            ("行业强度窗口", ("featureParameters", "industryStrengthWindow"), 121),
            ("调仓间隔", ("targetWeightParameters", "rebalanceEveryOpenDays"), 61),
            ("参与率上限", ("liquidityPolicy", "maxParticipationRate"), "0.10"),
        )
        for message, path_parts, value in cases:
            with self.subTest(field=path_parts), self.assertRaisesRegex(ValueError, message):
                changed = deepcopy(config)
                target = changed
                for part in path_parts[:-1]:
                    target = target[part]
                target[path_parts[-1]] = value
                resolve_strategy_definition(
                    validate_run_config(changed, verify_universe_source=False)
                )


class AShareValueQualityTargetBuilderTest(unittest.TestCase):
    def test_canonical_builder_uses_pit_inputs_and_future_rows_do_not_change_targets(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=253)
        signal_date = dates[251]
        config_path = REPO_ROOT / "configs" / "research" / "a_share_value_quality_industry_strength.json"
        config = json.loads(config_path.read_text())
        config.update(
            {
                "qualityRunId": "quality-test",
                "warmupStart": dates[0].date().isoformat(),
                "startDate": signal_date.date().isoformat(),
                "endDate": dates[-1].date().isoformat(),
            }
        )
        universe_rows = []
        price_rows = []
        valuation_rows = []
        financial_rows = []
        listing_rows = []
        signal_datetime = signal_date.to_pydatetime()
        for number in range(40):
            symbol = f"S{number:02d}.SZ"
            industry = f"I{number // 4:02d}"
            listing_rows.append({"ts_code": symbol, "list_date": dates[0], "delist_date": None})
            industry_return = (10 - number // 4) / 10_000
            for offset, trade_date in enumerate(dates):
                universe_rows.append(
                    {
                        "trade_date": trade_date,
                        "ts_code": symbol,
                        "industry_index_code": industry,
                    }
                )
                price_rows.append(
                    {
                        "trade_date": trade_date,
                        "ts_code": symbol,
                        "adj_close": 10 * ((1 + industry_return) ** offset),
                    }
                )
            valuation_rows.append(
                {
                    "trade_date": signal_date,
                    "ts_code": symbol,
                    "pe_ttm": 8 + number % 4,
                    "pb": 1 + number % 4 / 10,
                }
            )
            financial_rows.append(
                {
                    "ts_code": symbol,
                    "ann_date": signal_datetime - timedelta(days=10),
                    "end_date": signal_datetime - timedelta(days=20),
                    "roe": 20 - number % 4,
                    "netprofit_margin": 15 - number % 4,
                    "debt_to_assets": 30 + number % 4,
                    "source_update_flag": "0",
                    "source_revision_sha256": f"{number:064x}",
                    "source_observed_at": signal_datetime - timedelta(days=9),
                    "available_from": signal_datetime - timedelta(days=8),
                    "revision_status": "observed",
                }
            )
        calendar = calendar_for_dates(dates)
        base_args = (
            calendar,
            pd.DataFrame(universe_rows),
            pd.DataFrame(listing_rows),
            pd.DataFrame(price_rows),
            pd.DataFrame(valuation_rows),
            pd.DataFrame(financial_rows),
            config,
        )

        baseline = build_value_quality_target_frame(*base_args)

        self.assertEqual(len(baseline), 20)
        self.assertTrue(baseline["target_weight"].eq(0.05).all())
        self.assertEqual(
            set(baseline["industry_index_code"]),
            {"I00", "I01", "I02", "I03", "I04"},
        )
        future_date = dates[-1] + pd.offsets.BDay(1)
        extended = build_value_quality_target_frame(
            calendar,
            pd.concat(
                [
                    pd.DataFrame(universe_rows),
                    pd.DataFrame(
                        [
                            {
                                "trade_date": future_date,
                                "ts_code": "FUTURE.SZ",
                                "industry_index_code": "I99",
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            ),
            pd.concat(
                [
                    pd.DataFrame(listing_rows),
                    pd.DataFrame(
                        [{"ts_code": "FUTURE.SZ", "list_date": future_date, "delist_date": None}]
                    ),
                ],
                ignore_index=True,
            ),
            pd.concat(
                [
                    pd.DataFrame(price_rows),
                    pd.DataFrame(
                        [{"trade_date": future_date, "ts_code": "FUTURE.SZ", "adj_close": 100}]
                    ),
                ],
                ignore_index=True,
            ),
            pd.DataFrame(valuation_rows),
            pd.concat(
                [
                    pd.DataFrame(financial_rows),
                    pd.DataFrame(
                        [
                            {
                                **financial_rows[0],
                                "roe": 999,
                                "source_revision_sha256": "f" * 64,
                                "source_observed_at": future_date,
                                "available_from": future_date,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            ),
            config,
        )
        pd.testing.assert_frame_equal(baseline, extended)

    def test_financial_available_from_is_strict_and_future_revision_does_not_rewrite_signal(self) -> None:
        signal = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-09",
                    "ts_code": "AAA.SZ",
                    "industry_index_code": "I1",
                }
            ]
        )
        valuation = pd.DataFrame(
            [{"trade_date": "2026-01-09", "ts_code": "AAA.SZ", "pe_ttm": 10, "pb": 1}]
        )
        available = {
            "ts_code": "AAA.SZ",
            "ann_date": "2026-01-05",
            "end_date": "2025-12-31",
            "roe": 12,
            "netprofit_margin": 8,
            "debt_to_assets": 40,
            "source_update_flag": "0",
            "source_revision_sha256": "a" * 64,
            "source_observed_at": "2026-01-05T08:00:00+00:00",
            "available_from": "2026-01-06",
            "revision_status": "observed",
        }
        future = {
            **available,
            "roe": 99,
            "source_update_flag": "1",
            "source_revision_sha256": "b" * 64,
            "source_observed_at": "2026-01-11T08:00:00+00:00",
            "available_from": "2026-01-12",
        }

        baseline = attach_value_quality_fundamentals(
            signal,
            valuation,
            pd.DataFrame([available]),
            trade_calendar=calendar_for_dates(["2026-01-09"]),
        )
        extended = attach_value_quality_fundamentals(
            signal,
            valuation,
            pd.DataFrame([available, future]),
            trade_calendar=calendar_for_dates(["2026-01-09"]),
        )

        self.assertEqual(float(baseline.iloc[0]["roe"]), 12)
        pd.testing.assert_frame_equal(baseline, extended)
        same_day = pd.DataFrame([{**available, "available_from": "2026-01-09", "roe": 77}])
        visible = attach_value_quality_fundamentals(
            signal,
            valuation,
            same_day,
            trade_calendar=calendar_for_dates(["2026-01-09"]),
        )
        self.assertEqual(float(visible.iloc[0]["roe"]), 77)

    def test_industry_strength_uses_trailing_equal_weight_returns_and_top_half_gate(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=122)
        daily_returns = {"I1": 0.01, "I2": 0.005, "I3": 0.0, "I4": -0.005}
        prices = []
        universe = []
        for industry, daily_return in daily_returns.items():
            for suffix in ("A", "B"):
                symbol = f"{industry}{suffix}.SZ"
                for offset, trade_date in enumerate(dates):
                    prices.append(
                        {
                            "trade_date": trade_date,
                            "ts_code": symbol,
                            "adj_close": 10 * ((1 + daily_return) ** offset),
                        }
                    )
                    universe.append(
                        {
                            "trade_date": trade_date,
                            "ts_code": symbol,
                            "industry_index_code": industry,
                        }
                    )
        result = calculate_industry_strength(
            pd.DataFrame(prices),
            pd.DataFrame(universe),
            window=120,
            top_fraction=0.5,
        )
        latest = result[result["trade_date"].eq(dates[-1])]
        self.assertEqual(
            set(latest.loc[latest["industry_strength_eligible"], "industry_index_code"]),
            {"I1", "I2"},
        )
        self.assertEqual(
            list(latest.sort_values("industry_strength_rank")["industry_index_code"]),
            ["I1", "I2", "I3", "I4"],
        )

        future_date = dates[-1] + pd.offsets.BDay(1)
        future_prices = pd.concat(
            [
                pd.DataFrame(prices),
                pd.DataFrame(
                    [
                        {
                            "trade_date": future_date,
                            "ts_code": row["ts_code"],
                            "adj_close": 1,
                        }
                        for row in universe
                        if row["trade_date"] == dates[-1]
                    ]
                ),
            ],
            ignore_index=True,
        )
        future_universe = pd.concat(
            [
                pd.DataFrame(universe),
                pd.DataFrame(
                    [{**row, "trade_date": future_date} for row in universe if row["trade_date"] == dates[-1]]
                ),
            ],
            ignore_index=True,
        )
        extended = calculate_industry_strength(
            future_prices,
            future_universe,
            window=120,
            top_fraction=0.5,
        )
        pd.testing.assert_frame_equal(
            result,
            extended[extended["trade_date"] <= dates[-1]].reset_index(drop=True),
        )

    def test_industry_relative_score_excludes_missing_and_rewards_low_value_high_quality(self) -> None:
        candidates = pd.DataFrame(
            [
                {"signal_date": "2026-01-09", "ts_code": "A.SZ", "industry_index_code": "I1", "pe_ttm": 8, "pb": 1, "roe": 20, "netprofit_margin": 15, "debt_to_assets": 30},
                {"signal_date": "2026-01-09", "ts_code": "B.SZ", "industry_index_code": "I1", "pe_ttm": 12, "pb": 2, "roe": 10, "netprofit_margin": 8, "debt_to_assets": 50},
                {"signal_date": "2026-01-09", "ts_code": "C.SZ", "industry_index_code": "I1", "pe_ttm": 6, "pb": None, "roe": 30, "netprofit_margin": 20, "debt_to_assets": 20},
                {"signal_date": "2026-01-09", "ts_code": "D.SZ", "industry_index_code": "I2", "pe_ttm": 9, "pb": 1.1, "roe": 18, "netprofit_margin": 14, "debt_to_assets": 35},
                {"signal_date": "2026-01-09", "ts_code": "E.SZ", "industry_index_code": "I2", "pe_ttm": 18, "pb": 3, "roe": 6, "netprofit_margin": 4, "debt_to_assets": 60},
            ]
        )

        ranked = score_value_quality_candidates(candidates)

        self.assertNotIn("C.SZ", set(ranked["ts_code"]))
        by_code = ranked.set_index("ts_code")
        self.assertGreater(by_code.loc["A.SZ", "value_quality_score"], by_code.loc["B.SZ", "value_quality_score"])
        self.assertGreater(by_code.loc["D.SZ", "value_quality_score"], by_code.loc["E.SZ", "value_quality_score"])
        self.assertEqual(list(ranked.sort_values("total_rank")["ts_code"][:2]), ["A.SZ", "D.SZ"])

    def test_top20_30_buffer_and_industry_cap_are_deterministic(self) -> None:
        rows = []
        for rank in range(1, 41):
            rows.append(
                {
                    "signal_date": pd.Timestamp("2026-01-09"),
                    "ts_code": f"S{rank:02d}.SZ",
                    "industry_index_code": f"I{(rank - 1) // 4:02d}",
                    "total_rank": rank,
                    "value_quality_score": 1 - rank / 100,
                    "industry_strength_eligible": True,
                }
            )
        ranked = pd.DataFrame(rows)

        targets = select_buffered_targets(
            ranked,
            previous_symbols={"S25.SZ", "S31.SZ"},
            target_count=20,
            entry_rank=20,
            exit_rank=30,
            single_name_cap=0.05,
            industry_cap=0.20,
        )

        self.assertIn("S25.SZ", set(targets["ts_code"]))
        self.assertNotIn("S31.SZ", set(targets["ts_code"]))
        self.assertEqual(len(targets), 20)
        self.assertTrue(targets["target_weight"].eq(0.05).all())
        self.assertLessEqual(
            targets.groupby("industry_index_code")["target_weight"].sum().max(),
            0.20 + 1e-12,
        )

    def test_rebalance_dates_advance_only_every_sixty_open_days(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=181)
        signals = rebalance_signal_dates(dates, dates[0], dates[-1], every=60)
        self.assertEqual(list(signals), [dates[0], dates[60], dates[120], dates[180]])


class AShareValueQualitySimulatorTest(unittest.TestCase):
    def test_next_open_board_lot_t_plus_one_and_double_cost(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=62)
        config = self._config(dates)
        prices = self._prices(dates, ["A.SZ", "B.SZ"], amount=100_000_000)
        targets = self._targets(
            dates[60],
            [("A.SZ", "I1", 0.05), ("B.SZ", "I2", 0.05)],
        )

        base = simulate_value_quality_portfolio(prices, calendar_for_dates(dates), targets, config)

        executions = base.rebalance_executions
        self.assertTrue(executions["execution_date"].eq(dates[61]).all())
        self.assertTrue(executions["t_plus_one_enforced"].all())
        self.assertTrue((executions["executed_shares"] % 100).eq(0).all())
        self.assertTrue(
            (
                executions["commission_cost"]
                + executions["sell_tax_cost"]
                + executions["slippage_cost"]
            ).gt(0).all()
        )
        doubled = deepcopy(config)
        doubled["costModel"] = {
            "buyRate": "0.00070",
            "sellRate": "0.00170",
            "slippageRate": "0.002",
        }
        stressed = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            doubled,
        )
        self.assertLess(stressed.nav.iloc[-1]["nav"], base.nav.iloc[-1]["nav"])

    def test_suspension_limit_up_and_limit_down_are_written_to_ledger(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=122)
        config = self._config(dates)
        prices = self._prices(dates, ["A.SZ", "B.SZ", "C.SZ"], amount=100_000_000)
        first_execution = dates[61]
        prices.loc[
            prices["trade_date"].eq(first_execution) & prices["ts_code"].eq("B.SZ"),
            ["is_buyable_at_open", "is_suspended_at_open"],
        ] = [False, True]
        prices.loc[
            prices["trade_date"].eq(first_execution) & prices["ts_code"].eq("C.SZ"),
            "is_buyable_at_open",
        ] = False
        second_execution = dates[121]
        prices.loc[
            prices["trade_date"].eq(second_execution) & prices["ts_code"].eq("A.SZ"),
            "is_sellable_at_open",
        ] = False
        targets = pd.concat(
            [
                self._targets(
                    dates[60],
                    [("A.SZ", "I1", 0.05), ("B.SZ", "I2", 0.05), ("C.SZ", "I3", 0.05)],
                ),
                self._targets(dates[120], [("A.SZ", "I1", 0.0)]),
            ],
            ignore_index=True,
        )

        result = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            config,
        )
        reasons = result.rebalance_executions.set_index(
            ["execution_date", "ts_code"]
        )["reason"]
        self.assertEqual(reasons.loc[(first_execution, "B.SZ")], "suspended_at_open")
        self.assertEqual(reasons.loc[(first_execution, "C.SZ")], "limit_up")
        self.assertEqual(reasons.loc[(second_execution, "A.SZ")], "limit_down")

    def test_adv_cap_partially_fills_and_missing_capacity_blocks(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=62)
        config = self._config(dates)
        prices = self._prices(dates, ["A.SZ", "B.SZ"], amount=1_000)
        prices.loc[prices["ts_code"].eq("B.SZ"), "amount"] = float("nan")
        targets = self._targets(
            dates[60],
            [("A.SZ", "I1", 0.05), ("B.SZ", "I2", 0.05)],
        )

        result = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            config,
        )
        executions = result.rebalance_executions.set_index("ts_code")
        self.assertEqual(executions.loc["A.SZ", "status"], "partial")
        self.assertEqual(executions.loc["A.SZ", "reason"], "adv_capacity")
        self.assertAlmostEqual(executions.loc["A.SZ", "participation_rate_20"], 0.05)
        self.assertGreater(executions.loc["A.SZ", "requested_participation_rate_20"], 0.05)
        self.assertEqual(executions.loc["B.SZ", "status"], "blocked")
        self.assertEqual(executions.loc["B.SZ", "reason"], "missing_capacity")

    def test_one_way_turnover_cap_is_enforced_without_concentrating_orders(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=62)
        config = self._config(dates)
        symbols = [f"S{number:02d}.SZ" for number in range(20)]
        prices = self._prices(dates, symbols, amount=100_000_000)
        targets = self._targets(
            dates[60],
            [(symbol, f"I{number // 4}", 0.05) for number, symbol in enumerate(symbols)],
        )

        result = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            config,
        )

        executions = result.rebalance_executions
        self.assertLessEqual(executions["executed_order_amount"].sum(), 2_500_000 + 1e-6)
        self.assertTrue(executions["status"].eq("partial").all())
        self.assertTrue(executions["reason"].eq("turnover_cap").all())
        self.assertEqual(executions["executed_shares"].nunique(), 1)

    def test_cash_shortage_is_explicit_when_blocked_sells_cannot_fund_rotation(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=302)
        config = self._config(dates)
        old_symbols = [f"O{number:02d}.SZ" for number in range(20)]
        new_symbols = [f"N{number:02d}.SZ" for number in range(20)]
        prices = self._prices(dates, old_symbols + new_symbols, amount=100_000_000)
        rotation_execution = dates[301]
        prices.loc[
            prices["trade_date"].eq(rotation_execution)
            & prices["ts_code"].isin(old_symbols),
            "is_sellable_at_open",
        ] = False
        old_targets = [
            (symbol, f"I{number // 4}", 0.05)
            for number, symbol in enumerate(old_symbols)
        ]
        targets = pd.concat(
            [
                self._targets(dates[offset], old_targets)
                for offset in (60, 120, 180, 240)
            ]
            + [
                self._targets(
                    dates[300],
                    [
                        (symbol, f"J{number // 4}", 0.05)
                        for number, symbol in enumerate(new_symbols)
                    ],
                )
            ],
            ignore_index=True,
        )

        result = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            config,
        )

        rotation = result.rebalance_executions[
            result.rebalance_executions["execution_date"].eq(rotation_execution)
        ]
        self.assertTrue(
            rotation[
                rotation["ts_code"].isin(old_symbols)
            ]["reason"].eq("limit_down").all()
        )
        self.assertTrue(
            rotation[
                rotation["ts_code"].isin(new_symbols)
            ]["reason"].eq("cash_capacity").any()
        )

    @staticmethod
    def _config(dates: pd.DatetimeIndex) -> dict[str, object]:
        path = REPO_ROOT / "configs" / "research" / "a_share_value_quality_industry_strength.json"
        config = json.loads(path.read_text())
        config.update(
            {
                "qualityRunId": "quality-test",
                "warmupStart": dates[0].date().isoformat(),
                "startDate": dates[0].date().isoformat(),
                "endDate": dates[-1].date().isoformat(),
            }
        )
        return config

    @staticmethod
    def _prices(
        dates: pd.DatetimeIndex,
        symbols: list[str],
        *,
        amount: float,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "ts_code": symbol,
                    "open": 10.0,
                    "close": 10.0,
                    "adj_open": 10.0,
                    "adj_close": 10.0,
                    "amount": amount,
                    "is_buyable_at_open": True,
                    "is_sellable_at_open": True,
                    "is_suspended_at_open": False,
                    "is_valuation_carried": False,
                }
                for trade_date in dates
                for symbol in symbols
            ]
        )

    @staticmethod
    def _targets(
        signal_date: pd.Timestamp,
        rows: list[tuple[str, str, float]],
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "signal_date": signal_date,
                    "available_date": signal_date,
                    "ts_code": symbol,
                    "industry_index_code": industry,
                    "target_weight": weight,
                }
                for symbol, industry, weight in rows
            ]
        )


class AShareValueQualityMetricsTest(unittest.TestCase):
    def test_canonical_artifact_metrics_cover_benchmark_comparison_execution_and_capacity(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=62)
        config = AShareValueQualitySimulatorTest._config(dates)
        prices = AShareValueQualitySimulatorTest._prices(
            dates,
            ["A.SZ"],
            amount=1_000,
        )
        targets = AShareValueQualitySimulatorTest._targets(
            dates[60],
            [("A.SZ", "I1", 0.05)],
        )
        simulation = simulate_value_quality_portfolio(
            prices,
            calendar_for_dates(dates),
            targets,
            config,
        )
        benchmark = pd.DataFrame(
            {"trade_date": dates, "nav": [1 + index / 10_000 for index in range(len(dates))]}
        )
        comparison = pd.DataFrame(
            {"trade_date": dates, "nav": [1 + index / 20_000 for index in range(len(dates))]}
        )
        environment = pd.DataFrame(
            {"trade_date": dates, "nav": [1 + ((-1) ** index) / 10_000 for index in range(len(dates))]}
        )

        metrics = summarize_value_quality_artifacts(
            simulation.nav,
            simulation.rebalance_requests,
            simulation.rebalance_executions,
            simulation.positions,
            config,
            benchmark_nav=benchmark,
            comparison_nav=comparison,
            environment_nav=environment,
            sample_role="test_oos",
        )

        self.assertEqual(metrics["schemaVersion"], "a-share-value-quality-metrics/v1")
        self.assertEqual(metrics["source"], "canonical_simulation_artifacts")
        self.assertEqual(metrics["sampleRole"], "test_oos")
        self.assertEqual(metrics["benchmarkComparison"]["status"], "complete")
        self.assertEqual(metrics["valueQualityComparison"]["status"], "complete")
        self.assertEqual(metrics["capacity"]["status"], "complete")
        self.assertFalse(metrics["capacity"]["passed"])
        self.assertAlmostEqual(metrics["capacity"]["maxExecutedParticipationRate20"], 0.05)
        self.assertGreater(metrics["capacity"]["maxRequestedParticipationRate20"], 0.05)
        self.assertEqual(metrics["marketEnvironment"]["status"], "complete")
        self.assertIn("maxWeight", metrics["concentration"])

    def test_missing_benchmark_or_capacity_returns_explicit_not_available_semantics(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=2)
        nav = pd.DataFrame(
            {
                "trade_date": dates,
                "nav": [1.0, 1.01],
                "cash_weight": [1.0, 1.0],
                "gross_exposure": [0.0, 0.0],
                "executed_signal_date": [pd.NaT, pd.NaT],
                "traded_weight": [0.0, 0.0],
                "one_way_turnover": [0.0, 0.0],
                "transaction_cost_rate": [0.0, 0.0],
                "blocked_buys": ["", ""],
                "blocked_sells": ["", ""],
                "unfilled_target_weight": [0.0, 0.0],
                "carried_valuation_count": [0, 0],
            }
        )
        requests = pd.DataFrame(
            columns=["execution_date", "signal_date", "ts_code", "requested_change", "side"]
        )
        executions = pd.DataFrame(
            columns=[
                "execution_date",
                "signal_date",
                "ts_code",
                "requested_change",
                "executed_change",
                "blocked_change",
                "status",
                "reason",
                "transaction_cost_rate",
            ]
        )
        positions = pd.DataFrame(columns=["trade_date", "ts_code", "close_weight"])
        config = AShareValueQualitySimulatorTest._config(dates)

        metrics = summarize_value_quality_artifacts(
            nav,
            requests,
            executions,
            positions,
            config,
            benchmark_nav=None,
            comparison_nav=None,
            environment_nav=None,
            sample_role="test_oos",
        )

        self.assertEqual(metrics["benchmarkComparison"]["status"], "not_available")
        self.assertIn("缺少", metrics["benchmarkComparison"]["reason"])
        self.assertEqual(metrics["capacity"]["status"], "not_available")
        self.assertIn("没有", metrics["capacity"]["reason"])
        self.assertEqual(metrics["marketEnvironment"]["status"], "not_available")


class AShareValueQualityRunnerIntegrationTest(unittest.TestCase):
    def test_formal_runner_archives_capacity_ledger_and_reproduces_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = create_engine(f"sqlite+pysqlite:///{root / 'formal.sqlite'}")
            Base.metadata.create_all(engine)
            dates = pd.bdate_range("2025-01-02", periods=253)
            try:
                with Session(engine) as db:
                    self._seed_database(db, dates)
                    contract = QualityCheckContract.create(
                        scope="a_share_cross_section",
                        start_date=dates[0].date(),
                        end_date=dates[-1].date(),
                        universe=[],
                        required_datasets=[
                            "stock_daily_basic",
                            "stock_financial_indicators",
                        ],
                        benchmark="H00985.CSI",
                        universe_type="industry_level_membership",
                        universe_source="industry_classifications+industry_members",
                        universe_classification_src="SW2021",
                        universe_classification_level="L1",
                    )
                    quality = run_data_quality_check(db, contract, code_commit="value-quality-test")
                    self.assertEqual(quality["status"], "ready")
                    config_path = (
                        REPO_ROOT
                        / "configs"
                        / "research"
                        / "a_share_value_quality_industry_strength.json"
                    )
                    config = json.loads(config_path.read_text())
                    config.update(
                        {
                            "qualityRunId": quality["qualityRunId"],
                            "warmupStart": dates[0].date().isoformat(),
                            "startDate": dates[251].date().isoformat(),
                            "endDate": dates[-1].date().isoformat(),
                            "validationPolicy": {"mode": "none"},
                            "riskPolicy": {"mode": "none"},
                        }
                    )
                    result = run_quant_research(
                        db,
                        config,
                        root / "research-runs",
                        code_commit="value-quality-test",
                        schema_revision="test-schema",
                        test_mode=True,
                        capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                    )
                executions = pd.read_csv(
                    result.path / "rebalance_executions.csv.gz",
                    compression="gzip",
                )
                metrics = json.loads((result.path / "metrics.json").read_text())
                self.assertIn("requested_order_amount", executions.columns)
                self.assertIn("participation_rate_60", executions.columns)
                self.assertEqual(metrics["capacity"]["status"], "complete")
                self.assertEqual(metrics["benchmarkComparison"]["status"], "complete")
                self.assertEqual(metrics["valueQualityComparison"]["status"], "complete")
                self.assertEqual(metrics["marketEnvironment"]["status"], "complete")
                self.assertTrue(reproduce_quant_research(result.path)["matches"])
            finally:
                engine.dispose()

    @staticmethod
    def _seed_database(db: Session, dates: pd.DatetimeIndex) -> None:
        symbols = [f"S{number:02d}.SZ" for number in range(20)]
        db.add(
            TradeCalendar(
                exchange="SSE",
                cal_date=pd.Timestamp("2010-01-04").date(),
                is_open=True,
            )
        )
        for industry_number in range(5):
            industry = f"I{industry_number:02d}.SI"
            db.add(
                IndustryClassification(
                    index_code=industry,
                    industry_name=f"合成行业{industry_number}",
                    level="L1",
                    industry_code=f"I{industry_number:02d}",
                    parent_code=None,
                    src="SW2021",
                )
            )
        db.add_all(
            [
                Index(
                    ts_code=code,
                    name=name,
                    market="CSI",
                    publisher="test",
                    category="综合",
                    base_date=dates[0].date(),
                    list_date=dates[0].date(),
                )
                for code, name in (
                    ("H00985.CSI", "中证全指全收益"),
                    ("000985.CSI", "中证全指"),
                )
            ]
        )
        for number, symbol in enumerate(symbols):
            industry_number = number // 4
            db.add(
                IndustryMember(
                    index_code=f"I{industry_number:02d}.SI",
                    con_code=symbol,
                    con_name=f"合成股票{number}",
                    in_date=dates[0].date(),
                )
            )
            db.add(
                StockListing(
                    ts_code=symbol,
                    symbol=f"S{number:02d}",
                    name=f"合成股票{number}",
                    exchange="SZSE",
                    list_status="L",
                    list_date=pd.Timestamp("2010-01-04").date(),
                )
            )
            db.add(
                StockFinancialIndicator(
                    ts_code=symbol,
                    ann_date=dates[0].date(),
                    end_date=dates[0].date() - timedelta(days=1),
                    roe=20 - number % 4,
                    netprofit_margin=15 - number % 4,
                    debt_to_assets=30 + number % 4,
                    source_update_flag="0",
                    source_revision_sha256=f"{number:064x}",
                    source_observed_at=datetime.combine(
                        dates[0].date(),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ),
                    available_from=dates[0].date(),
                    revision_status="observed",
                )
            )
        previous_index = {"H00985.CSI": 100.0, "000985.CSI": 100.0}
        previous_stock = {symbol: 10.0 for symbol in symbols}
        for offset, trade_date in enumerate(dates):
            db.add(TradeCalendar(exchange="SSE", cal_date=trade_date.date(), is_open=True))
            for code, daily_return in (("H00985.CSI", 0.0002), ("000985.CSI", 0.0001)):
                close = 100 * ((1 + daily_return) ** offset)
                db.add(
                    IndexDailyBar(
                        ts_code=code,
                        trade_date=trade_date.date(),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        pre_close=previous_index[code],
                        vol=1_000_000,
                        amount=1_000_000,
                    )
                )
                previous_index[code] = close
            for number, symbol in enumerate(symbols):
                daily_return = (5 - number // 4) / 10_000
                close = 10 * ((1 + daily_return) ** offset)
                db.add(
                    StockDailyBar(
                        ts_code=symbol,
                        trade_date=trade_date.date(),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        pre_close=previous_stock[symbol],
                        vol=100_000_000,
                        amount=100_000_000,
                    )
                )
                db.add(
                    StockAdjustFactor(
                        ts_code=symbol,
                        trade_date=trade_date.date(),
                        adj_factor=1,
                    )
                )
                db.add(
                    StockLimitPrice(
                        ts_code=symbol,
                        trade_date=trade_date.date(),
                        pre_close=previous_stock[symbol],
                        up_limit=close * 1.1,
                        down_limit=close * 0.9,
                    )
                )
                db.add(
                    StockDailyBasic(
                        ts_code=symbol,
                        trade_date=trade_date.date(),
                        close=close,
                        pe_ttm=8 + number % 4,
                        pb=1 + number % 4 / 10,
                    )
                )
                previous_stock[symbol] = close
        db.commit()



if __name__ == "__main__":
    unittest.main()
