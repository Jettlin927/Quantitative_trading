from __future__ import annotations

from datetime import date
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
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
)
from backend.app.quant_research.a_share_price_baseline import (
    build_a_share_price_targets,
    simulate_a_share_price_targets,
)
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.app.quant_research.universe import build_industry_membership_universe


class ASharePriceBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.frames, self.dates = _synthetic_frames()
        self.config = _config("quality-placeholder", self.dates)
        self.input_root = self.root / "plain-inputs"
        self.input_root.mkdir()
        _write_plain_inputs(self.input_root, self.frames)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_price_features_use_historical_members_and_next_open_execution(self):
        targets = build_a_share_price_targets(
            self.input_root,
            self.config,
            compressed=False,
        )
        signal_groups = {
            trade_date.date().isoformat(): set(group["ts_code"])
            for trade_date, group in targets.groupby("signal_date")
        }
        self.assertEqual(signal_groups["2025-11-28"], {"SYN001.SZ", "SYN002.SH"})
        self.assertEqual(signal_groups["2025-12-31"], {"SYN001.SZ", "SYN003.SZ"})
        self.assertTrue(targets["target_weight"].eq(0.5).all())

        simulation, _calendar = simulate_a_share_price_targets(
            self.input_root,
            self.config,
            targets,
            compressed=False,
        )
        first_execution = simulation.rebalance_executions[
            simulation.rebalance_executions["signal_date"].eq(pd.Timestamp("2025-11-28"))
        ].set_index("ts_code")
        self.assertEqual(
            first_execution.loc["SYN002.SH", "execution_date"],
            pd.Timestamp("2025-12-01"),
        )
        self.assertEqual(first_execution.loc["SYN002.SH", "status"], "blocked")
        self.assertEqual(first_execution.loc["SYN002.SH", "reason"], "valuation_carried")

    def test_appending_future_rows_does_not_change_targets_or_ledger_prefix(self):
        base_targets = build_a_share_price_targets(
            self.input_root,
            self.config,
            compressed=False,
        )
        base_simulation, _ = simulate_a_share_price_targets(
            self.input_root,
            self.config,
            base_targets,
            compressed=False,
        )
        extended_frames, _ = _synthetic_frames(extra_periods=10)
        extended_frames["trade_calendars"] = self.frames["trade_calendars"]
        extended_frames["universe"] = self.frames["universe"]
        extended_root = self.root / "extended-inputs"
        extended_root.mkdir()
        _write_plain_inputs(extended_root, extended_frames)
        extended_targets = build_a_share_price_targets(
            extended_root,
            self.config,
            compressed=False,
        )
        extended_simulation, _ = simulate_a_share_price_targets(
            extended_root,
            self.config,
            extended_targets,
            compressed=False,
        )

        pd.testing.assert_frame_equal(base_targets, extended_targets)
        pd.testing.assert_frame_equal(
            base_simulation.rebalance_executions,
            extended_simulation.rebalance_executions[
                extended_simulation.rebalance_executions["execution_date"]
                <= pd.Timestamp(self.config["endDate"])
            ].reset_index(drop=True),
        )

    def test_a_share_execution_uses_frozen_limit_up_and_limit_down_prices(self):
        execution_date = pd.Timestamp("2026-01-01")
        modified = {name: frame.copy() for name, frame in self.frames.items()}
        for symbol, limit_column in (
            ("SYN001.SZ", "down_limit"),
            ("SYN003.SZ", "up_limit"),
        ):
            limit_value = modified["stock_limit_prices"].loc[
                modified["stock_limit_prices"]["ts_code"].eq(symbol)
                & modified["stock_limit_prices"]["trade_date"].eq(execution_date),
                limit_column,
            ].iloc[0]
            modified["stock_daily_bars"].loc[
                modified["stock_daily_bars"]["ts_code"].eq(symbol)
                & modified["stock_daily_bars"]["trade_date"].eq(execution_date),
                "open",
            ] = limit_value
        root = self.root / "limit-inputs"
        root.mkdir()
        _write_plain_inputs(root, modified)
        targets = pd.DataFrame(
            [
                {
                    "signal_date": pd.Timestamp("2025-11-28"),
                    "available_date": pd.Timestamp("2025-11-28"),
                    "ts_code": "SYN001.SZ",
                    "target_weight": 0.5,
                },
                {
                    "signal_date": pd.Timestamp("2025-12-31"),
                    "available_date": pd.Timestamp("2025-12-31"),
                    "ts_code": "SYN003.SZ",
                    "target_weight": 0.5,
                },
            ]
        )

        simulation, _ = simulate_a_share_price_targets(
            root,
            self.config,
            targets,
            compressed=False,
        )
        executions = simulation.rebalance_executions[
            simulation.rebalance_executions["execution_date"].eq(execution_date)
        ].set_index("ts_code")
        self.assertEqual(executions.loc["SYN001.SZ", "reason"], "limit_down")
        self.assertEqual(executions.loc["SYN003.SZ", "reason"], "limit_up")

    def test_formal_a_share_run_archives_and_reproduces_without_database(self):
        engine = create_engine(f"sqlite+pysqlite:///{self.root / 'formal.sqlite'}")
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                _seed_database(db, self.frames)
                report = run_data_quality_check(
                    db,
                    QualityCheckContract.create(
                        scope="a_share_cross_section",
                        start_date=self.dates[0].date(),
                        end_date=self.dates[-1].date(),
                        universe=[],
                        universe_type="industry_membership",
                        universe_source="industry_members",
                        universe_source_key="SYNIND.SI",
                        benchmark="SYNIDX.SH",
                    ),
                    code_commit="a-share-test",
                )
                self.assertEqual(report["status"], "ready")
                result = run_quant_research(
                    db,
                    _config(report["qualityRunId"], self.dates),
                    self.root / "research-runs",
                    code_commit="a-share-test",
                    schema_revision="test-schema",
                    test_mode=True,
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                )
            self.assertEqual(result.manifest["strategyId"], "a_share_price_baseline")
            self.assertEqual(result.manifest["dataSnapshot"]["scope"], "a_share_cross_section")
            self.assertIn(
                "walk_forward_metrics.csv.gz",
                result.manifest["artifactHashes"],
            )
            self.assertIn(
                "risk_exposures.csv.gz",
                result.manifest["artifactHashes"],
            )
            self.assertIn(
                "risk_contributions.csv.gz",
                result.manifest["artifactHashes"],
            )
            self.assertTrue(reproduce_quant_research(result.path)["matches"])
        finally:
            engine.dispose()


def _config(quality_run_id: str, dates: pd.DatetimeIndex) -> dict[str, object]:
    return {
        "strategyId": "a_share_price_baseline",
        "strategyVersion": "1",
        "scope": "a_share_cross_section",
        "universe": build_industry_membership_universe("SYNIND.SI"),
        "warmupStart": dates[0].date().isoformat(),
        "startDate": dates[125].date().isoformat(),
        "endDate": dates[169].date().isoformat(),
        "benchmark": "SYNIDX.SH",
        "featureParameters": {
            "momentumLongWindow": 120,
            "momentumSkipWindow": 20,
            "volatilityWindow": 60,
        },
        "targetWeightParameters": {
            "rebalanceFrequency": "month_end",
            "topN": 2,
            "maxWeight": "0.5",
        },
        "executionPolicy": {
            "signalPrice": "close",
            "executionPrice": "next_trade_open",
        },
        "costModel": {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        },
        "randomSeed": 7,
        "timezone": "Asia/Shanghai",
        "qualityRunId": quality_run_id,
        "allowedWarnings": [],
        "validationPolicy": {
            "mode": "anchored",
            "trainPeriods": 20,
            "testPeriods": 10,
            "stepPeriods": 10,
        },
        "riskPolicy": {
            "mode": "rolling_covariance",
            "lookbackPeriods": 60,
            "minPeriods": 20,
        },
    }


def _synthetic_frames(
    *,
    extra_periods: int = 0,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    all_dates = pd.bdate_range("2025-06-02", periods=170 + extra_periods)
    contract_dates = all_dates[:170]
    symbols = {
        "SYN001.SZ": (10.0, 0.001),
        "SYN002.SH": (20.0, 0.002),
        "SYN003.SZ": (30.0, 0.003),
    }
    suspension_date = pd.Timestamp("2025-12-01")
    bars: list[dict[str, object]] = []
    factors: list[dict[str, object]] = []
    limits: list[dict[str, object]] = []
    for symbol, (base, daily_return) in symbols.items():
        previous = base
        for offset, trade_date in enumerate(all_dates):
            close = base * ((1 + daily_return) ** offset)
            factors.append(
                {"ts_code": symbol, "trade_date": trade_date, "adj_factor": 1}
            )
            limits.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date,
                    "pre_close": previous,
                    "up_limit": close * 1.1,
                    "down_limit": close * 0.9,
                }
            )
            if not (symbol == "SYN002.SH" and trade_date == suspension_date):
                bars.append(
                    {
                        "ts_code": symbol,
                        "trade_date": trade_date,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "pre_close": previous,
                        "vol": 1000,
                        "amount": 10000,
                    }
                )
            previous = close
    universe_rows: list[dict[str, object]] = []
    for trade_date in all_dates:
        members = ["SYN001.SZ"]
        members.append("SYN002.SH" if trade_date < pd.Timestamp("2025-12-01") else "SYN003.SZ")
        universe_rows.extend(
            {"trade_date": trade_date, "ts_code": symbol}
            for symbol in members
        )
    frames = {
        "trade_calendars": pd.DataFrame(
            {
                "exchange": "SSE",
                "cal_date": all_dates,
                "is_open": 1,
            }
        ),
        "stock_listings": pd.DataFrame(
            [
                {
                    "ts_code": symbol,
                    "symbol": symbol.split(".")[0],
                    "name": symbol,
                    "exchange": "SSE" if symbol.endswith(".SH") else "SZSE",
                    "list_status": "L",
                    "list_date": date(2020, 1, 1),
                    "delist_date": None,
                }
                for symbol in symbols
            ]
        ),
        "stock_daily_bars": pd.DataFrame(bars),
        "stock_adjust_factors": pd.DataFrame(factors),
        "stock_limit_prices": pd.DataFrame(limits),
        "stock_suspend_events": pd.DataFrame(
            [
                {
                    "ts_code": "SYN002.SH",
                    "trade_date": suspension_date,
                    "suspend_type": "S",
                    "suspend_timing": "全天",
                }
            ]
        ),
        "industry_members": pd.DataFrame(
            [
                {
                    "index_code": "SYNIND.SI",
                    "con_code": "SYN001.SZ",
                    "con_name": "SYN001.SZ",
                    "in_date": date(2020, 1, 1),
                    "out_date": None,
                    "is_new": False,
                },
                {
                    "index_code": "SYNIND.SI",
                    "con_code": "SYN002.SH",
                    "con_name": "SYN002.SH",
                    "in_date": date(2020, 1, 1),
                    "out_date": date(2025, 11, 30),
                    "is_new": False,
                },
                {
                    "index_code": "SYNIND.SI",
                    "con_code": "SYN003.SZ",
                    "con_name": "SYN003.SZ",
                    "in_date": date(2025, 12, 1),
                    "out_date": None,
                    "is_new": True,
                },
            ]
        ),
        "indices": pd.DataFrame(
            [
                {
                    "ts_code": "SYNIDX.SH",
                    "name": "合成基准",
                    "market": "CSI",
                    "publisher": "test",
                    "category": "综合",
                    "base_date": date(2020, 1, 1),
                    "list_date": date(2020, 1, 1),
                }
            ]
        ),
        "index_daily_bars": pd.DataFrame(
            [
                {
                    "ts_code": "SYNIDX.SH",
                    "trade_date": trade_date,
                    "open": 100 + offset * 0.1,
                    "high": 101 + offset * 0.1,
                    "low": 99 + offset * 0.1,
                    "close": 100 + offset * 0.1,
                    "pre_close": 100 + max(offset - 1, 0) * 0.1,
                    "vol": 1000,
                    "amount": 10000,
                }
                for offset, trade_date in enumerate(all_dates)
            ]
        ),
        "universe": pd.DataFrame(universe_rows),
    }
    return frames, contract_dates


def _write_plain_inputs(root: Path, frames: dict[str, pd.DataFrame]) -> None:
    for name, frame in frames.items():
        frame.to_csv(root / f"{name}.csv", index=False)


def _seed_database(db: Session, frames: dict[str, pd.DataFrame]) -> None:
    db.add(
        IndustryClassification(
            index_code="SYNIND.SI",
            industry_name="合成行业",
            level="L1",
            src="test",
        )
    )
    for row in frames["industry_members"].to_dict("records"):
        db.add(IndustryMember(**row))
    for row in frames["stock_listings"].to_dict("records"):
        db.add(StockListing(**row))
    for row in frames["trade_calendars"].iloc[:170].to_dict("records"):
        db.add(
            TradeCalendar(
                exchange=row["exchange"],
                cal_date=pd.Timestamp(row["cal_date"]).date(),
                is_open=bool(row["is_open"]),
            )
        )
    db.add(Index(**frames["indices"].iloc[0].to_dict()))
    for row in frames["index_daily_bars"].iloc[:170].to_dict("records"):
        db.add(
            IndexDailyBar(
                **{
                    **row,
                    "trade_date": pd.Timestamp(row["trade_date"]).date(),
                }
            )
        )
    end_date = frames["trade_calendars"].iloc[169]["cal_date"]
    for row in frames["stock_daily_bars"].to_dict("records"):
        if row["trade_date"] <= end_date:
            db.add(
                StockDailyBar(
                    **{
                        **row,
                        "trade_date": pd.Timestamp(row["trade_date"]).date(),
                    }
                )
            )
    for row in frames["stock_adjust_factors"].to_dict("records"):
        if row["trade_date"] <= end_date:
            db.add(
                StockAdjustFactor(
                    **{
                        **row,
                        "trade_date": pd.Timestamp(row["trade_date"]).date(),
                    }
                )
            )
    for row in frames["stock_limit_prices"].to_dict("records"):
        if row["trade_date"] <= end_date:
            db.add(
                StockLimitPrice(
                    **{
                        **row,
                        "trade_date": pd.Timestamp(row["trade_date"]).date(),
                    }
                )
            )
    for row in frames["stock_suspend_events"].to_dict("records"):
        db.add(
            StockSuspendEvent(
                **{
                    **row,
                    "trade_date": pd.Timestamp(row["trade_date"]).date(),
                }
            )
        )
    db.commit()


if __name__ == "__main__":
    unittest.main()
